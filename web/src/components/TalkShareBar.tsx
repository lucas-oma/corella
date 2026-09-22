import type { SpeakerShareSlice } from "@/lib/speakerShare";
import { speakerDotForSlice } from "@/lib/speakerShare";

export default function TalkShareBar({
  slices,
  size = "md",
}: {
  slices: SpeakerShareSlice[];
  size?: "sm" | "md";
}) {
  const track = size === "sm" ? "h-12 w-1" : "h-16 w-1";
  return (
    <div className={`flex items-end ${size === "md" ? "justify-center gap-5" : "gap-3.5"}`}>
      {slices.map((slice) => (
        <div
          key={slice.colorKey + slice.label}
          className="flex min-w-0 flex-col items-center gap-1.5"
          title={`${slice.label} ${slice.pct}%`}
        >
          <div
            className={`relative overflow-hidden rounded-full bg-border dark:bg-border-dark ${track}`}
          >
            <div
              className={`absolute inset-x-0 bottom-0 rounded-full ${speakerDotForSlice(slice)}`}
              style={{ height: `${Math.max(slice.pct, 2)}%` }}
            />
          </div>
          <p className="text-[10px] tabular-nums leading-none text-ink dark:text-ink-inverted">
            {slice.pct}%
          </p>
          <p className="max-w-[4.5rem] truncate text-[10px] leading-none text-ink-subtle">
            {slice.label}
          </p>
        </div>
      ))}
    </div>
  );
}
