import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AppShell from "@/components/AppShell";
import {
  type CaptureHandle,
  type CopilotEvent,
  LiveSessionClient,
  type TranscriptEvent,
  startCapture,
} from "@/lib/live";

type ConnectionState = "connecting" | "connected" | "error";

export default function LiveSession() {
  const { meetingId } = useParams<{ meetingId: string }>();
  const navigate = useNavigate();

  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEvent[]>([]);
  const [micActive, setMicActive] = useState(false);
  const [themActive, setThemActive] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [copilot, setCopilot] = useState<CopilotEvent | null>(null);
  const [copilotAvailable, setCopilotAvailable] = useState(true);

  const clientRef = useRef<LiveSessionClient | null>(null);
  const micCaptureRef = useRef<CaptureHandle | null>(null);
  const themCaptureRef = useRef<CaptureHandle | null>(null);
  const streamsRef = useRef<MediaStream[]>([]);

  useEffect(() => {
    if (!meetingId) return;
    let cancelled = false;

    async function connect() {
      const client = new LiveSessionClient(meetingId!);
      clientRef.current = client;
      client.onTranscript = (event) => setTranscript((prev) => [...prev, event]);
      client.onCopilot = (event) => setCopilot(event);
      client.onCopilotUnavailable = () => setCopilotAvailable(false);
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
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_300px]">
        <div className="card min-h-[300px] space-y-3 p-6">
          {transcript.length === 0 && (
            <p className="text-sm text-ink-muted">Say something — your words will appear here.</p>
          )}
          {transcript.map((segment) => (
            <div
              key={segment.id}
              className={`flex ${segment.channel === "me" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[75%] rounded-lg px-4 py-2 text-sm ${
                  segment.channel === "me"
                    ? "bg-accent text-accent-foreground"
                    : "border border-border text-ink dark:border-border-dark dark:text-ink-inverted"
                }`}
              >
                {segment.text}
              </div>
            </div>
          ))}
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
    </AppShell>
  );
}
