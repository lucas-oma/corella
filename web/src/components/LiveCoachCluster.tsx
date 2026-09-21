import type { ReactNode } from "react";
import { parseSentiment, sentimentFill } from "@/lib/sentiment";
import type { SpeakerShareSlice } from "@/lib/speakerShare";
import { speakerStrokeClass } from "@/lib/speakerColor";

const SIZE = 144;
const CX = SIZE / 2;
const CY = SIZE / 2;
const STROKE_MAIN = 4;
const STROKE_SIDE = 4;
const R_MAIN = 42;
const R_SIDE = 56;
const C_MAIN = 2 * Math.PI * R_MAIN;
const C_SIDE = 2 * Math.PI * R_SIDE;
const ARC_FRAC = 0.28;
const ARC_LEN = C_SIDE * ARC_FRAC;
const HALF_ARC_DEG = (ARC_FRAC * 360) / 2;
const LEFT_ROT = 270 - HALF_ARC_DEG;
const RIGHT_ROT = 90 - HALF_ARC_DEG;

function SideGroup({
  rotate,
  children,
}: {
  rotate: number;
  children?: ReactNode;
}) {
  return (
    <g transform={`rotate(${rotate} ${CX} ${CY})`}>
      <circle
        cx={CX}
        cy={CY}
        r={R_SIDE}
        fill="none"
        className="stroke-border dark:stroke-border-dark"
        strokeWidth={STROKE_SIDE}
        strokeDasharray={`${ARC_LEN} ${C_SIDE - ARC_LEN}`}
      />
      {children}
    </g>
  );
}

export default function LiveCoachCluster({
  score,
  sentiment,
  slices,
}: {
  score: number | null;
  sentiment: string | null;
  slices: SpeakerShareSlice[] | null;
}) {
  const parsed = parseSentiment(sentiment);
  const shownSentiment = parsed ?? "Neutral";
  const fill = sentimentFill(shownSentiment);
  const clamped = score == null ? 50 : Math.max(0, Math.min(100, score));
  const scoreOffset = C_MAIN * (1 - clamped / 100);
  const share = (slices ?? []).filter((row) => row.pct > 0);

  let shareCursor = 0;

  return (
    <div className="mx-auto w-fit">
      <div className="relative overflow-hidden" style={{ width: SIZE, height: 124 }}>
        <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden>
          <g transform={`rotate(-90 ${CX} ${CY})`}>
            <circle
              cx={CX}
              cy={CY}
              r={R_MAIN}
              fill="none"
              className="stroke-border dark:stroke-border-dark"
              strokeWidth={STROKE_MAIN}
            />
            <circle
              cx={CX}
              cy={CY}
              r={R_MAIN}
              fill="none"
              className="stroke-accent dark:stroke-ink-inverted"
              strokeWidth={STROKE_MAIN}
              strokeDasharray={C_MAIN}
              strokeDashoffset={scoreOffset}
            />
            <SideGroup rotate={LEFT_ROT}>
              <circle
                cx={CX}
                cy={CY}
                r={R_SIDE}
                fill="none"
                className="stroke-status-info"
                strokeWidth={STROKE_SIDE}
                strokeDasharray={`${ARC_LEN * fill} ${C_SIDE - ARC_LEN * fill}`}
              />
            </SideGroup>
            <SideGroup rotate={RIGHT_ROT}>
              {share.map((slice) => {
                const seg = ARC_LEN * (slice.pct / 100);
                const offset = shareCursor;
                shareCursor += seg;
                return (
                  <circle
                    key={slice.colorKey + slice.label}
                    cx={CX}
                    cy={CY}
                    r={R_SIDE}
                    fill="none"
                    className={speakerStrokeClass(slice.colorKey)}
                    strokeWidth={STROKE_SIDE}
                    strokeDasharray={`${Math.max(seg, 1)} ${C_SIDE - Math.max(seg, 1)}`}
                    strokeDashoffset={-offset}
                  >
                    <title>{`${slice.label} ${slice.pct}%`}</title>
                  </circle>
                );
              })}
            </SideGroup>
          </g>
        </svg>
        <div className="pointer-events-none absolute left-0 top-0 flex items-center justify-center" style={{ width: SIZE, height: SIZE }}>
          <p className="font-serif text-[28px] font-normal leading-none text-ink dark:text-ink-inverted">
            {score == null ? 50 : clamped}
          </p>
        </div>
      </div>
      <div className="mt-2 flex items-baseline justify-between gap-4 px-1">
        <p className="w-14 text-left text-[10px] font-medium leading-none text-ink dark:text-ink-inverted">
          {shownSentiment}
        </p>
        <p className="whitespace-nowrap text-[10px] font-medium uppercase leading-none tracking-wide text-ink-subtle">
          Score
        </p>
        <p className="w-14 text-right text-[10px] font-medium uppercase leading-none tracking-wide text-ink-subtle">
          Share
        </p>
      </div>
    </div>
  );
}
