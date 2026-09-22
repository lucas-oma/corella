import type { ReactNode } from "react";
import type { Sentiment } from "@/lib/sentiment";

/** Line-drawn faces for the live sentiment ring — same two dots and a
 * mouth as the sketch, just smoother strokes. */
export default function SentimentFace({ sentiment }: { sentiment: Sentiment }) {
  return (
    <svg
      width="36"
      height="36"
      viewBox="0 0 32 32"
      aria-hidden
      className="text-ink dark:text-ink-inverted"
    >
      <Eyes />
      {FACE[sentiment]}
    </svg>
  );
}

function Eyes() {
  return (
    <>
      <circle cx="11.5" cy="13" r="1.5" fill="currentColor" />
      <circle cx="20.5" cy="13" r="1.5" fill="currentColor" />
    </>
  );
}

const mouth = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.85,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const brow = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
};

const FACE: Record<Sentiment, ReactNode> = {
  Hostile: (
    <>
      <path d="M9 9.4 L13.3 11.6" {...brow} />
      <path d="M23 9.4 L18.7 11.6" {...brow} />
      <path d="M11 21.2 H21" {...mouth} />
    </>
  ),
  Tense: (
    <>
      <path d="M9.6 10.2 L13.2 11.5" {...brow} />
      <path d="M22.4 10.2 L18.8 11.5" {...brow} />
      <path d="M11.2 21 Q16 19.4 20.8 21" {...mouth} />
    </>
  ),
  Frustrated: <path d="M10.4 22.2 Q16 16.6 21.6 22.2" {...mouth} />,
  Skeptical: <path d="M11.2 21.4 Q16 18.6 20.8 21.4" {...mouth} />,
  Neutral: <path d="M11 20.4 H21" {...mouth} />,
  Engaged: <path d="M11.4 19.2 Q16 23 20.6 19.2" {...mouth} />,
  Positive: <path d="M10.4 18.6 Q16 24.4 21.6 18.6" {...mouth} />,
  Enthusiastic: <path d="M10 18 A 6 7.2 0 0 0 22 18 Z" {...mouth} />,
};
