/** Stable color for a speaker — same tokens the live transcript dots use.
 * Prefer speaker_id so "Speaker 2" → "Ana" does not recolor; fall back to
 * the display label when the id is not on the wire yet. */
const SPEAKER_DOT_COLORS = ["bg-accent", "bg-status-success", "bg-status-danger", "bg-ink-subtle"] as const;
const SPEAKER_STROKE_COLORS = [
  "stroke-accent dark:stroke-ink-inverted",
  "stroke-status-success",
  "stroke-status-danger",
  "stroke-ink-subtle",
] as const;

export function speakerColorKey(speakerId: string | null | undefined, label: string): string {
  return speakerId || label;
}

function speakerIndex(key: string): number {
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return hash % SPEAKER_DOT_COLORS.length;
}

export function speakerDotClass(key: string): string {
  return SPEAKER_DOT_COLORS[speakerIndex(key)];
}

export function speakerStrokeClass(key: string): string {
  return SPEAKER_STROKE_COLORS[speakerIndex(key)];
}
