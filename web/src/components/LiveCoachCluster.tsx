import type { ReactNode } from "react";
import { parseSentiment, sentimentFill } from "@/lib/sentiment";
import type { SpeakerShareSlice } from "@/lib/speakerShare";
import { speakerStrokeClass } from "@/lib/speakerColor";
import SentimentFace from "@/components/SentimentFace";

const SIZE = 72;
const STROKE = 4;
const R = (SIZE - STROKE) / 2;
const C = 2 * Math.PI * R;
const CX = SIZE / 2;

function MiniRing({ children }: { children?: ReactNode }) {
  return (
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} className="-rotate-90" aria-hidden>
      <circle
        cx={CX}
        cy={CX}
        r={R}
        fill="none"
        className="stroke-border dark:stroke-border-dark"
        strokeWidth={STROKE}
      />
      {children}
    </svg>
  );
}

function FillArc({ frac, className }: { frac: number; className: string }) {
  const clamped = Math.max(0, Math.min(1, frac));
  if (clamped <= 0) return null;
  return (
    <circle
      cx={CX}
      cy={CX}
      r={R}
      fill="none"
      className={className}
      strokeWidth={STROKE}
      strokeLinecap="round"
      strokeDasharray={C}
      strokeDashoffset={C * (1 - clamped)}
    />
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
  const share = (slices ?? []).filter((row) => row.pct > 0);
  const mePct = share.find((row) => row.label === "Me")?.pct ?? null;

  let shareCursor = 0;

  return (
    <div className="grid w-full grid-cols-3 gap-2">
      <div className="flex min-w-0 flex-col items-center">
        <div className="relative" style={{ width: SIZE, height: SIZE }}>
          <MiniRing>
            <FillArc frac={clamped / 100} className="stroke-accent dark:stroke-ink-inverted" />
          </MiniRing>
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <p className="font-serif text-xl leading-none text-ink dark:text-ink-inverted">
              {score == null ? 50 : clamped}
            </p>
          </div>
        </div>
        <p className="mt-2 text-center text-[10px] font-medium leading-none text-ink-subtle">Score</p>
      </div>

      <div className="flex min-w-0 flex-col items-center">
        <div className="relative" style={{ width: SIZE, height: SIZE }} title={shownSentiment}>
          <MiniRing>
            <FillArc frac={fill} className="stroke-status-info" />
          </MiniRing>
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <SentimentFace sentiment={shownSentiment} />
          </div>
        </div>
        <p className="mt-2 text-center text-[10px] font-medium leading-none text-ink-subtle">Sentiment</p>
      </div>

      <div className="flex min-w-0 flex-col items-center">
        <div className="relative" style={{ width: SIZE, height: SIZE }}>
          <MiniRing>
            {share.map((slice) => {
              const seg = C * (slice.pct / 100);
              const offset = shareCursor;
              shareCursor += seg;
              return (
                <circle
                  key={slice.colorKey + slice.label}
                  cx={CX}
                  cy={CX}
                  r={R}
                  fill="none"
                  className={speakerStrokeClass(slice.colorIndex)}
                  strokeWidth={STROKE}
                  strokeDasharray={`${Math.max(seg, 1)} ${C - Math.max(seg, 1)}`}
                  strokeDashoffset={-offset}
                >
                  <title>{`${slice.label} ${slice.pct}%`}</title>
                </circle>
              );
            })}
          </MiniRing>
          {mePct != null && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <p className="font-serif text-xl leading-none text-ink dark:text-ink-inverted">{mePct}</p>
            </div>
          )}
        </div>
        <p className="mt-2 text-center text-[10px] font-medium leading-none text-ink-subtle">Share</p>
      </div>
    </div>
  );
}
