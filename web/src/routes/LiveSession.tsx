import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AppShell from "@/components/AppShell";
import {
  type CaptureHandle,
  type CopilotEvent,
  type DebugEvent,
  type DiarizationUpdateEvent,
  LiveSessionClient,
  type TranscriptEvent,
  startCapture,
} from "@/lib/live";
import { useAuth } from "@/lib/auth";

type ConnectionState = "connecting" | "connected" | "error";

interface SpeakerInfo {
  label: string;
  linkedUserId: string | null;
}

// Speaker labels arrive live, after the fact — either the moment a voice is
// recognized (the viewer's own enrolled voice, or someone already known in
// their group) or once a second distinct voice is confirmed *on that
// channel* (see app/services/diarization/cluster.py — Me and Them gate
// independently) or once an unrecognized voice's name is spotted live from
// what it said (corella.identify_speaker_name). A small stable color per
// label helps them read as distinct people at a glance rather than just
// more text — hashed from the label itself, not parsed as a number, since a
// resolved name ("Lucas") has no digit to key off of the way "Speaker 2"
// did.
const SPEAKER_DOT_COLORS = ["bg-accent", "bg-status-success", "bg-status-danger", "bg-ink-subtle"];

// Debug aid, not a durable record — nothing persisted server-side, so
// capping client-side is enough to keep the panel from growing unbounded
// on a long call.
const MAX_DEBUG_EVENTS = 200;

function speakerDotColor(label: string): string {
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) >>> 0;
  return SPEAKER_DOT_COLORS[hash % SPEAKER_DOT_COLORS.length];
}

export default function LiveSession() {
  const { meetingId } = useParams<{ meetingId: string }>();
  const navigate = useNavigate();
  const { user } = useAuth();

  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEvent[]>([]);
  const [micActive, setMicActive] = useState(false);
  const [themActive, setThemActive] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [copilot, setCopilot] = useState<CopilotEvent | null>(null);
  const [copilotAvailable, setCopilotAvailable] = useState(true);
  const [speakerLabels, setSpeakerLabels] = useState<Record<string, SpeakerInfo>>({});
  const [debugEnabled, setDebugEnabled] = useState(false);
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);
  const debugEndRef = useRef<HTMLDivElement>(null);

  /** "Me" is viewer-relative, not baked into the stored label — the
   * viewer's own enrolled voice reads as "Me" to themselves, and by their
   * real name to anyone else with legitimate access to this transcript
   * (an admin, per Phase J; never a regular group member — that boundary
   * is unchanged). */
  function displayLabel(segmentId: string): string | null {
    const info = speakerLabels[segmentId];
    if (!info) return null;
    return info.linkedUserId && info.linkedUserId === user?.id ? "Me" : info.label;
  }

  const clientRef = useRef<LiveSessionClient | null>(null);
  const micCaptureRef = useRef<CaptureHandle | null>(null);
  const themCaptureRef = useRef<CaptureHandle | null>(null);
  const streamsRef = useRef<MediaStream[]>([]);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ block: "end" });
  }, [transcript]);

  useEffect(() => {
    debugEndRef.current?.scrollIntoView({ block: "end" });
  }, [debugEvents]);

  // A speaker change mid-utterance means the server deletes the one coarse
  // bubble it first sent (on whichever channel it was) and replaces it with
  // several speaker-labeled ones — removedSegmentIds/segments is always a
  // complete, correct diff (accumulated server-side across the whole
  // meeting, not just this event), so removal + upsert here doesn't need to
  // special-case the first-time "snapshot" backfill vs. a later incremental
  // update. seg.channel (not a hardcoded "me") is what keeps a Them-side
  // split rendering as a Them bubble, not a Me one.
  function applyDiarizationUpdate(event: DiarizationUpdateEvent) {
    setSpeakerLabels((prev) => {
      const next = { ...prev };
      for (const seg of event.segments) {
        next[seg.id] = { label: seg.speaker_label, linkedUserId: seg.linked_user_id };
      }
      return next;
    });
    setTranscript((prev) => {
      const removed = new Set(event.removedSegmentIds);
      const byId = new Map(prev.filter((s) => !removed.has(s.id)).map((s) => [s.id, s]));
      for (const seg of event.segments) {
        byId.set(seg.id, {
          id: seg.id,
          channel: seg.channel,
          start_ms: seg.start_ms,
          end_ms: seg.end_ms,
          text: seg.text,
        });
      }
      return Array.from(byId.values()).sort((a, b) => a.start_ms - b.start_ms);
    });
  }

  useEffect(() => {
    if (!meetingId) return;
    let cancelled = false;

    async function connect() {
      const client = new LiveSessionClient(meetingId!);
      clientRef.current = client;
      client.onTranscript = (event) => setTranscript((prev) => [...prev, event]);
      client.onCopilot = (event) => setCopilot(event);
      client.onCopilotUnavailable = () => setCopilotAvailable(false);
      client.onDiarizationUpdate = (event) => applyDiarizationUpdate(event);
      client.onDebugEvent = (event) =>
        setDebugEvents((prev) => [...prev, event].slice(-MAX_DEBUG_EVENTS));
      client.onError = (message) => setError(message);
      client.onStopped = () => navigate(`/meetings/${meetingId}`);

      try {
        await client.waitUntilReady();
        if (cancelled) return;
        setConnection("connected");

        const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled) {
          micStream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamsRef.current.push(micStream);
        micCaptureRef.current = await startCapture(micStream, (pcm) => client.sendAudio("me", pcm));
        setMicActive(true);
      } catch (err) {
        if (!cancelled) {
          setConnection("error");
          setError(err instanceof Error ? err.message : "Couldn't start the live session");
        }
      }
    }

    connect();

    return () => {
      cancelled = true;
      micCaptureRef.current?.stop();
      themCaptureRef.current?.stop();
      streamsRef.current.forEach((s) => s.getTracks().forEach((t) => t.stop()));
      clientRef.current?.close();
    };
  }, [meetingId, navigate]);

  async function onShareTabAudio() {
    try {
      const displayStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
      });
      displayStream.getVideoTracks().forEach((t) => t.stop());
      const audioTracks = displayStream.getAudioTracks();
      if (audioTracks.length === 0) {
        setError('No audio was shared — pick a tab and check "share tab audio".');
        return;
      }
      const audioOnly = new MediaStream(audioTracks);
      streamsRef.current.push(audioOnly);

      const client = clientRef.current;
      if (!client) return;
      themCaptureRef.current = await startCapture(audioOnly, (pcm) => client.sendAudio("them", pcm));
      setThemActive(true);

      // If the user stops sharing from the browser's own "Stop sharing" UI.
      audioTracks[0].addEventListener("ended", () => {
        themCaptureRef.current?.stop();
        themCaptureRef.current = null;
        setThemActive(false);
      });
    } catch {
      // User cancelled the share picker — not an error worth surfacing.
    }
  }

  function onStop() {
    setStopping(true);
    clientRef.current?.stop();
  }

  function onToggleDebug() {
    const next = !debugEnabled;
    setDebugEnabled(next);
    clientRef.current?.setDebug(next);
    if (!next) setDebugEvents([]);
  }

  return (
    <AppShell>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Recording</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {connection === "connecting" && "Connecting…"}
            {connection === "connected" && "Live — transcript updates a few seconds after each pause."}
            {connection === "error" && "Connection error"}
          </p>
        </div>
        <button onClick={onStop} disabled={stopping || connection !== "connected"} className="btn-primary">
          {stopping ? "Stopping…" : "Stop"}
        </button>
      </div>

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      <div className="mb-4 flex items-center gap-4 text-sm">
        <span className="flex items-center gap-1.5 text-ink-muted">
          <span className={`h-2 w-2 rounded-full ${micActive ? "bg-status-success" : "bg-ink-subtle"}`} />
          Mic
        </span>
        <span className="flex items-center gap-1.5 text-ink-muted">
          <span className={`h-2 w-2 rounded-full ${themActive ? "bg-status-success" : "bg-ink-subtle"}`} />
          Tab audio
        </span>
        {!themActive && connection === "connected" && (
          <button onClick={onShareTabAudio} className="btn-secondary ml-auto">
            Share tab audio
          </button>
        )}
        {user?.role === "admin" && connection === "connected" && (
          <button
            onClick={onToggleDebug}
            className={`btn-secondary ${themActive ? "" : "ml-auto"}`}
            title="Admin-only: live technical events (VAD flushes, STT/LLM requests, diarization dispatch)"
          >
            {debugEnabled ? "Debug: on" : "Debug"}
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_300px]">
        <div className="card flex h-[70vh] flex-col space-y-3 overflow-y-auto p-6">
          {transcript.length === 0 && (
            <p className="text-sm text-ink-muted">Say something — your words will appear here.</p>
          )}
          {transcript.map((segment) => {
            const label = displayLabel(segment.id);
            return (
              <div
                key={segment.id}
                className={`flex ${segment.channel === "me" ? "justify-end" : "justify-start"}`}
              >
                <div className={`max-w-[75%] ${segment.channel === "me" ? "text-right" : "text-left"}`}>
                  {label && (
                    <p className="mb-0.5 flex items-center gap-1.5 text-xs text-ink-subtle">
                      <span className={`h-1.5 w-1.5 rounded-full ${speakerDotColor(label)}`} />
                      {label}
                    </p>
                  )}
                  <div
                    className={`rounded-lg px-4 py-2 text-sm ${
                      segment.channel === "me"
                        ? "bg-accent text-accent-foreground"
                        : "border border-border text-ink dark:border-border-dark dark:text-ink-inverted"
                    }`}
                  >
                    {segment.text}
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={transcriptEndRef} />
        </div>

        <div className="card h-fit space-y-5 p-5">
          <h2 className="font-serif text-base text-ink dark:text-ink-inverted">Copilot</h2>

          {!copilotAvailable && (
            <p className="text-xs text-ink-subtle">
              No LLM provider connected — add one in Settings to enable live suggestions.
            </p>
          )}

          {copilotAvailable && (
            <>
              {copilot?.coach_score !== null && copilot?.coach_score !== undefined && (
                <div>
                  <p className="label mb-1">Coach score</p>
                  <p className="font-serif text-2xl text-ink dark:text-ink-inverted">
                    {copilot.coach_score}
                    <span className="text-sm text-ink-subtle">/100</span>
                  </p>
                </div>
              )}

              <div>
                <p className="label mb-1">Suggestion</p>
                <p className="text-sm text-ink dark:text-ink-inverted">
                  {copilot?.suggestion ?? <span className="text-ink-subtle">Nothing yet.</span>}
                </p>
              </div>

              <div>
                <p className="label mb-1">Blockers</p>
                {copilot?.blockers.length ? (
                  <ul className="space-y-1">
                    {copilot.blockers.map((b, i) => (
                      <li key={i} className="text-sm text-status-danger">
                        {b}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-ink-subtle">None.</p>
                )}
              </div>

              <div>
                <p className="label mb-1">Action items</p>
                {copilot?.action_items.length ? (
                  <ul className="space-y-1">
                    {copilot.action_items.map((item, i) => (
                      <li key={i} className="text-sm text-ink dark:text-ink-inverted">
                        • {item}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-ink-subtle">None yet.</p>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {debugEnabled && (
        <div className="card mt-6 flex h-64 flex-col overflow-y-auto p-4">
          <h2 className="mb-2 font-serif text-base text-ink dark:text-ink-inverted">Debug log</h2>
          {debugEvents.length === 0 && (
            <p className="text-xs text-ink-subtle">Waiting for events…</p>
          )}
          <div className="space-y-1 font-mono text-xs text-ink-subtle">
            {debugEvents.map((event, i) => (
              <p key={i}>
                <span className="text-ink-subtle/70">[+{event.atMs}ms]</span>{" "}
                <span className="text-ink dark:text-ink-inverted">{event.stage}</span>{" "}
                {Object.keys(event.detail).length > 0 && JSON.stringify(event.detail)}
              </p>
            ))}
          </div>
          <div ref={debugEndRef} />
        </div>
      )}
    </AppShell>
  );
}
