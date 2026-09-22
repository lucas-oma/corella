import { describe, expect, it } from "vitest";
import { applyLiveMessage, createStore, snapshotTranscript } from "./store.js";

describe("applyLiveMessage", () => {
  it("keeps a label when an unlabeled transcript arrives after diarization_update", () => {
    const store = createStore();
    applyLiveMessage(store, {
      type: "diarization_update",
      removed_segment_ids: [],
      segments: [
        {
          id: "a",
          channel: "me",
          start_ms: 0,
          end_ms: 1000,
          text: "hello",
          speaker_label: "Speaker 1",
        },
      ],
    });
    applyLiveMessage(store, {
      type: "transcript",
      segment: { id: "a", channel: "me", start_ms: 0, end_ms: 1000, text: "hello" },
    });
    const lines = snapshotTranscript(store);
    expect(lines).toHaveLength(1);
    expect(lines[0].speakerLabel).toBe("Speaker 1");
    expect(lines[0].text).toBe("hello");
  });

  it("applies a speaker-change split as remove + labeled upserts", () => {
    const store = createStore();
    applyLiveMessage(store, {
      type: "transcript",
      segment: { id: "coarse", channel: "me", start_ms: 0, end_ms: 2000, text: "hey there" },
    });
    applyLiveMessage(store, {
      type: "diarization_update",
      removed_segment_ids: ["coarse"],
      segments: [
        { id: "a", channel: "me", start_ms: 0, end_ms: 900, text: "hey", speaker_label: "Speaker 1" },
        { id: "b", channel: "me", start_ms: 900, end_ms: 2000, text: "there", speaker_label: "Speaker 2" },
      ],
    });
    const lines = snapshotTranscript(store);
    expect(lines.map((l) => l.id)).toEqual(["a", "b"]);
    expect(lines.map((l) => l.speakerLabel)).toEqual(["Speaker 1", "Speaker 2"]);
  });

  it("treats speaker_hint like diarization_update", () => {
    const store = createStore();
    applyLiveMessage(store, {
      type: "speaker_hint",
      segments: [
        { id: "a", channel: "me", start_ms: 0, end_ms: 500, text: "hi", speaker_label: "Jane" },
      ],
    });
    expect(snapshotTranscript(store)[0].speakerLabel).toBe("Jane");
  });

  it("clears the matching channel partial when a transcript lands", () => {
    const store = createStore();
    applyLiveMessage(store, { type: "partial_transcript", channel: "me", text: "hel" });
    expect(store.partials.me).toBe("hel");
    applyLiveMessage(store, {
      type: "transcript",
      segment: { id: "a", channel: "me", start_ms: 0, end_ms: 100, text: "hello" },
    });
    expect(store.partials.me).toBe("");
  });

  it("stores copilot sentiment from the closed set and drops junk", () => {
    const store = createStore();
    applyLiveMessage(store, {
      type: "copilot",
      suggestion: null,
      blockers: [],
      action_items: [],
      coach_score: 70,
      sentiment: "skeptical",
    });
    expect(store.copilot?.sentiment).toBe("Skeptical");
    applyLiveMessage(store, {
      type: "copilot",
      suggestion: null,
      blockers: [],
      action_items: [],
      coach_score: 70,
      sentiment: "Mixed",
    });
    expect(store.copilot?.sentiment).toBeNull();
    expect(store.copilot?.speaker_share).toBeNull();
  });

  it("stores live speaker_share from a copilot frame", () => {
    const store = createStore();
    applyLiveMessage(store, {
      type: "copilot",
      suggestion: null,
      blockers: [],
      action_items: [],
      coach_score: 70,
      sentiment: "Neutral",
      speaker_share: [
        { label: "Me", pct: 40 },
        { label: "Speaker 1", pct: 60 },
      ],
    });
    expect(store.copilot?.speaker_share).toEqual([
      { label: "Me", pct: 40 },
      { label: "Speaker 1", pct: 60 },
    ]);
  });
});
