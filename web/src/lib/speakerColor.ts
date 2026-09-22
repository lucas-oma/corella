/** Sequential speaker colors for live dots, talk-share bars, and the share arc.
 * Me is always navy (accent). Everyone else takes the next unused slot in
 * first-seen order. Eight muted hues; wrap after that. */
export const SPEAKER_HEX = [
  "#0B1B33", // navy — Me
  "#2A6A84", // steel
  "#1F7A4D", // green
  "#9A6300", // amber
  "#B3261E", // red
  "#5C4E79", // plum
  "#3E6A58", // sage
  "#8B8F99", // gray
] as const;

const SPEAKER_DOT_CLASSES = [
  "bg-accent",
  "bg-speaker-steel",
  "bg-status-success",
  "bg-status-warning",
  "bg-status-danger",
  "bg-speaker-plum",
  "bg-speaker-sage",
  "bg-ink-subtle",
] as const;

const SPEAKER_STROKE_CLASSES = [
  "stroke-accent dark:stroke-ink-inverted",
  "stroke-speaker-steel",
  "stroke-status-success",
  "stroke-status-warning",
  "stroke-status-danger",
  "stroke-speaker-plum",
  "stroke-speaker-sage",
  "stroke-ink-subtle",
] as const;

export function speakerColorKey(speakerId: string | null | undefined, label: string): string {
  return speakerId || label;
}

function wrapIndex(index: number): number {
  const n = SPEAKER_HEX.length;
  return ((index % n) + n) % n;
}

/** First-seen order. `isMe` always lands on 0 (navy); others 1…7 then wrap. */
export function assignSpeakerColors(
  speakers: Iterable<{ colorKey: string; isMe: boolean }>,
): Map<string, number> {
  const map = new Map<string, number>();
  let nextOther = 1;
  const otherSlots = SPEAKER_HEX.length - 1;
  for (const speaker of speakers) {
    if (map.has(speaker.colorKey)) continue;
    if (speaker.isMe) {
      map.set(speaker.colorKey, 0);
    } else {
      map.set(speaker.colorKey, 1 + ((nextOther - 1) % otherSlots));
      nextOther += 1;
    }
  }
  return map;
}

export function speakerDotClass(index: number): string {
  return SPEAKER_DOT_CLASSES[wrapIndex(index)];
}

export function speakerStrokeClass(index: number): string {
  return SPEAKER_STROKE_CLASSES[wrapIndex(index)];
}

export function speakerHex(index: number): string {
  return SPEAKER_HEX[wrapIndex(index)];
}
