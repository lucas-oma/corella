import { parseSentiment, sentimentFill, type Sentiment } from "@/lib/sentiment";

export default function SentimentGauge({ value }: { value: string | null }) {
  const sentiment: Sentiment | null = parseSentiment(value);
  if (!sentiment) return null;
  const fill = sentimentFill(sentiment) * 100;
  return (
    <div className="w-16 shrink-0">
      <p className="label mb-1">Sentiment</p>
      <div className="h-1.5 overflow-hidden rounded-full bg-border dark:bg-border-dark">
        <div className="h-full bg-accent dark:bg-ink-inverted" style={{ width: `${fill}%` }} />
      </div>
      <p className="mt-1 text-xs text-ink dark:text-ink-inverted">{sentiment}</p>
    </div>
  );
}
