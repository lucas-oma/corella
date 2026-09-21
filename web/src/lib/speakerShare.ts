import type { CaptureMode, TranscriptSegment } from "@/lib/api";

export type SpeakerShareSlice = { label: string; pct: number };

/** Same Me/Them pipe metric the report uses — only honest on meeting_tab. */
export function talkRatioFromSegments(
  segments: TranscriptSegment[],
  captureMode: CaptureMode | undefined,
): { me: number; them: number } | null {
  if (captureMode !== "meeting_tab") return null;
  let meMs = 0;
  let themMs = 0;
  for (const segment of segments) {
    const duration = Math.max(0, segment.end_ms - segment.start_ms);
    if (segment.channel === "me") meMs += duration;
    else if (segment.channel === "them") themMs += duration;
  }
  const total = meMs + themMs;
  if (total === 0 || themMs <= 0) return null;
  const me = Math.round((meMs / total) * 100);
  return { me, them: 100 - me };
}

/** Open-mic / upload share by the same names the transcript list shows. */
export function speakerShareFromSegments(
  segments: TranscriptSegment[],
  captureMode: CaptureMode | undefined,
  viewerId: string | undefined,
): SpeakerShareSlice[] | null {
  if (captureMode === "meeting_tab") return null;
  const msByLabel = new Map<string, number>();
  const order: string[] = [];
  for (const segment of segments) {
    const duration = Math.max(0, segment.end_ms - segment.start_ms);
    if (duration <= 0) continue;
    const label = displayLabel(segment, viewerId);
    if (!msByLabel.has(label)) {
      order.push(label);
      msByLabel.set(label, 0);
    }
    msByLabel.set(label, (msByLabel.get(label) ?? 0) + duration);
  }
  const total = order.reduce((sum, label) => sum + (msByLabel.get(label) ?? 0), 0);
  if (total === 0 || order.length === 0) return null;
  const pcts = order.map((label) => Math.round(((msByLabel.get(label) ?? 0) / total) * 100));
  pcts[pcts.length - 1] += 100 - pcts.reduce((sum, pct) => sum + pct, 0);
  return order
    .map((label, i) => ({ label, pct: pcts[i] }))
    .filter((row) => row.pct > 0);
}

function displayLabel(segment: TranscriptSegment, viewerId: string | undefined): string {
  if (segment.speaker_label) {
    return segment.linked_user_id && segment.linked_user_id === viewerId
      ? "Me"
      : segment.speaker_label;
  }
  return "Unknown";
}

export function sliceOpacity(index: number, count: number): number {
  if (count <= 1) return 1;
  return 1 - (index * 0.55) / (count - 1);
}
