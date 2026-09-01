import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import AppShell from "@/components/AppShell";
import { api, type Meeting, type TranscriptSegment } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

function formatTimestamp(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export default function MeetingDetail() {
  const { meetingId } = useParams<{ meetingId: string }>();
  const audioRef = useRef<HTMLAudioElement>(null);

  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [transcript, setTranscript] = useState<TranscriptSegment[] | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  // Poll while the recording is still being processed; stop once it lands
  // on a terminal status (ready/failed).
  useEffect(() => {
    if (!meetingId) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      const current = await api.getMeeting(meetingId!);
      if (cancelled) return;
      setMeeting(current);
      if (current.status === "recording" || current.status === "processing") {
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [meetingId]);

  // Once the transcript is ready, load it plus the audio (as an object URL,
  // since <audio src> can't carry our Authorization header).
  useEffect(() => {
    if (!meetingId || meeting?.status !== "ready") return;

    api.getTranscript(meetingId).then(setTranscript);
    if (meeting.has_audio) {
      api.getAudioObjectUrl(meetingId).then(setAudioUrl);
    }
  }, [meetingId, meeting?.status, meeting?.has_audio]);

  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  function seekTo(ms: number) {
    if (!audioRef.current) return;
    audioRef.current.currentTime = ms / 1000;
    audioRef.current.play().catch(() => {});
  }

  return (
    <AppShell>
      <Link to="/dashboard" className="text-sm text-ink-muted hover:text-ink dark:hover:text-ink-inverted">
        ← Meetings
      </Link>

      {!meeting && <p className="mt-6 text-sm text-ink-muted">Loading…</p>}

      {meeting && (
        <>
          <h1 className="mt-2 font-serif text-2xl text-ink dark:text-ink-inverted">{meeting.title}</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {new Date(meeting.created_at).toLocaleString()}
          </p>

          {(meeting.status === "recording" || meeting.status === "processing") && (
            <div className="card mt-6 p-6 text-center">
              <p className="text-sm text-ink-muted">
                Transcribing your recording — this page will update automatically.
              </p>
            </div>
          )}

          {meeting.status === "failed" && (
            <div className="card mt-6 border-status-danger/30 p-6">
              <p className="text-sm font-medium text-status-danger">Processing failed</p>
              {meeting.processing_error && (
                <p className="mt-1 text-sm text-ink-muted">{meeting.processing_error}</p>
              )}
            </div>
          )}

          {meeting.status === "ready" && (
            <div className="mt-6 space-y-4">
              {audioUrl && (
                <audio ref={audioRef} src={audioUrl} controls className="w-full">
                  <track kind="captions" />
                </audio>
              )}

              {transcript === null && <p className="text-sm text-ink-muted">Loading transcript…</p>}

              {transcript?.length === 0 && (
                <div className="card p-10 text-center">
                  <p className="text-sm text-ink-muted">No speech was detected in this recording.</p>
                </div>
              )}

              {transcript && transcript.length > 0 && (
                <ol className="card divide-y divide-border dark:divide-border-dark">
                  {transcript.map((segment) => (
                    <li key={segment.id}>
                      <button
                        onClick={() => seekTo(segment.start_ms)}
                        className="flex w-full gap-4 px-5 py-3 text-left transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                      >
                        <span className="w-12 shrink-0 pt-0.5 font-mono text-xs text-ink-subtle">
                          {formatTimestamp(segment.start_ms)}
                        </span>
                        <span>
                          {segment.speaker_label && (
                            <span className="mr-2 text-xs font-medium text-ink-muted">
                              {segment.speaker_label}
                            </span>
                          )}
                          <span className="text-sm text-ink dark:text-ink-inverted">{segment.text}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </>
      )}
    </AppShell>
  );
}
