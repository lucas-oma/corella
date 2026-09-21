import type { CaptureMode, TranscriptSegment } from "@/lib/api";
import { assignSpeakerColors, speakerColorKey, speakerDotClass } from "@/lib/speakerColor";

export type SpeakerShareSlice = {
  label: string;
  pct: number;
  colorKey: string;
  colorIndex: number;
};

const ANON_RE = /^(Speaker|Them) \d+$/;

function isAnonymous(label: string): boolean {
  return label === "Unknown" || label === "Them" || label === "Speaker" || ANON_RE.test(label);
}

function rawLabel(
  segment: {
    speaker_label: string | null;
    linked_user_id: string | null;
    channel: string;
  },
  viewerId: string | undefined,
  captureMode: CaptureMode | undefined,
): string {
  if (segment.linked_user_id && viewerId && segment.linked_user_id === viewerId) return "Me";
  if (segment.speaker_label) return segment.speaker_label;
  if (captureMode === "meeting_tab") {
    if (segment.channel === "me") return "Me";
    if (segment.channel === "them") return "Them";
  }
  return "Unknown";
}

/** Same Me / real-name / Speaker N remapping as server display_labels(). */
export function displayLabels(
  segments: Array<{
    speaker_label: string | null;
    linked_user_id: string | null;
    speaker_id?: string | null;
    channel: string;
    id?: string;
  }>,
  viewerId: string | undefined,
  captureMode: CaptureMode | undefined,
): { label: string; colorKey: string }[] {
  const aliases = new Map<string, string>();
  let nextN = 1;
  return segments.map((segment) => {
    const raw = rawLabel(segment, viewerId, captureMode);
    if (raw === "Me") return { label: "Me", colorKey: speakerColorKey(segment.speaker_id, "Me") };
    if (!isAnonymous(raw)) return { label: raw, colorKey: speakerColorKey(segment.speaker_id, raw) };
    const key = segment.speaker_id || `${segment.channel}:${raw}`;
    if (!aliases.has(key)) {
      aliases.set(key, `Speaker ${nextN}`);
      nextN += 1;
    }
    const label = aliases.get(key)!;
    return { label, colorKey: speakerColorKey(segment.speaker_id, label) };
  });
}

export function speakerShareFromLabeled(
  segments: Array<{
    start_ms: number;
    end_ms: number;
    speaker_label: string | null;
    linked_user_id: string | null;
    speaker_id?: string | null;
    channel: string;
  }>,
  viewerId: string | undefined,
  captureMode: CaptureMode | undefined,
): SpeakerShareSlice[] | null {
  const labeled = displayLabels(segments, viewerId, captureMode);
  const msByLabel = new Map<string, number>();
  const colorByLabel = new Map<string, string>();
  const order: string[] = [];
  for (let i = 0; i < segments.length; i++) {
    const duration = Math.max(0, segments[i].end_ms - segments[i].start_ms);
    if (duration <= 0) continue;
    const { label, colorKey } = labeled[i];
    if (!msByLabel.has(label)) {
      order.push(label);
      msByLabel.set(label, 0);
      colorByLabel.set(label, colorKey);
    }
    msByLabel.set(label, (msByLabel.get(label) ?? 0) + duration);
  }
  const total = order.reduce((sum, label) => sum + (msByLabel.get(label) ?? 0), 0);
  if (total === 0 || order.length === 0) return null;
  const pcts = order.map((label) => Math.round(((msByLabel.get(label) ?? 0) / total) * 100));
  pcts[pcts.length - 1] += 100 - pcts.reduce((sum, pct) => sum + pct, 0);
  const colorIndexByKey = assignSpeakerColors(
    order.map((label) => ({
      colorKey: colorByLabel.get(label) ?? label,
      isMe: label === "Me",
    })),
  );
  return order
    .map((label, i) => {
      const colorKey = colorByLabel.get(label) ?? label;
      return {
        label,
        pct: pcts[i],
        colorKey,
        colorIndex: colorIndexByKey.get(colorKey) ?? 1,
      };
    })
    .filter((row) => row.pct > 0);
}

export function speakerShareFromSegments(
  segments: TranscriptSegment[],
  captureMode: CaptureMode | undefined,
  viewerId: string | undefined,
): SpeakerShareSlice[] | null {
  return speakerShareFromLabeled(segments, viewerId, captureMode);
}

/** Report / copilot payloads only have label + pct — assign sequential colors. */
export function speakerShareFromApi(
  share: Array<{ label: string; pct: number }> | null | undefined,
): SpeakerShareSlice[] | null {
  if (!share?.length) return null;
  const rows = share.filter((row) => row.pct > 0);
  if (!rows.length) return null;
  const colorIndexByKey = assignSpeakerColors(
    rows.map((row) => ({ colorKey: row.label, isMe: row.label === "Me" })),
  );
  return rows.map((row) => ({
    label: row.label,
    pct: row.pct,
    colorKey: row.label,
    colorIndex: colorIndexByKey.get(row.label) ?? 1,
  }));
}

export function speakerDotForSlice(slice: SpeakerShareSlice): string {
  return speakerDotClass(slice.colorIndex);
}
