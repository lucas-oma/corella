export const SENTIMENTS = [
  "Hostile",
  "Tense",
  "Frustrated",
  "Skeptical",
  "Neutral",
  "Engaged",
  "Positive",
  "Enthusiastic",
] as const;

export type Sentiment = (typeof SENTIMENTS)[number];

export function parseSentiment(raw: unknown): Sentiment | null {
  if (typeof raw !== "string") return null;
  const needle = raw.trim().toLowerCase();
  return SENTIMENTS.find((value) => value.toLowerCase() === needle) ?? null;
}

/** Discrete fill 0..1 for the live gauge — Hostile empty, Enthusiastic full. */
export function sentimentFill(value: Sentiment): number {
  return SENTIMENTS.indexOf(value) / (SENTIMENTS.length - 1);
}
