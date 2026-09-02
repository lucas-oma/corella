import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import AppShell from "@/components/AppShell";
import {
  ApiError,
  api,
  CALL_TYPE_LABEL,
  type ActionItem,
  type Meeting,
  type TranscriptSegment,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

const POLL_INTERVAL_MS = 3000;

function formatTimestamp(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/** Live-recorded segments have no Speaker row (channel already says who) —
 * fall back to "Me"/"Them" so they aren't unlabeled. */
function speakerLabel(segment: TranscriptSegment): string | null {
  if (segment.speaker_label) return segment.speaker_label;
  if (segment.channel === "me") return "Me";
  if (segment.channel === "them") return "Them";
  return null;
}

export default function MeetingDetail() {
  const { meetingId } = useParams<{ meetingId: string }>();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const audioRef = useRef<HTMLAudioElement>(null);
  const seekedFromSearchRef = useRef(false);

  const [meeting, setMeeting] = useState<Meeting | null>(null);
  // A group-mate's meeting, opened from the Dashboard's Group tab — report
  // only (summary/talk-ratio/action items, read-only), never the raw
  // transcript/audio/delete/report-generation controls. The server enforces
  // this too (GET /{id} itself 404s for anyone else), this is just what
  // the UI shows once it *has* been let in.
  const isOwner = meeting ? meeting.owner_id === user?.id : true;
  // An admin gets full read-only access system-wide (audio + transcript,
  // not just the report) — but never the write controls below, which stay
  // strictly isOwner. Server-enforced too (GET .../audio and .../transcript
  // 404 for anyone else, admin included, on write routes).
  const canViewFull = isOwner || user?.role === "admin";
  const [transcript, setTranscript] = useState<TranscriptSegment[] | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [actionItems, setActionItems] = useState<ActionItem[]>([]);
  const [talkRatio, setTalkRatio] = useState<{ me: number; them: number } | null>(null);
  const [providerConnected, setProviderConnected] = useState<boolean | null>(null);
  const [generatingReport, setGeneratingReport] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  async function onDelete() {
    if (!meetingId) return;
    setDeleting(true);
    try {
      await api.deleteMeeting(meetingId);
      navigate("/dashboard");
    } finally {
      setDeleting(false);
    }
  }

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
  // since <audio src> can't carry our Authorization header) — for the owner
  // or an admin. Skipped for a group-mate's meeting — the server would 404
  // both anyway (never group-visible, only report-visible), no point
  // attempting either fetch.
  useEffect(() => {
    if (!meetingId || meeting?.status !== "ready") return;

    if (canViewFull) {
      api.getTranscript(meetingId).then(setTranscript);
      if (meeting.has_audio) {
        api.getAudioObjectUrl(meetingId).then(setAudioUrl);
      }
    }
    if (isOwner) {
      api.getProviderStatus().then((statuses) => setProviderConnected(statuses.some((s) => s.connected)));
    }
    api.listActionItems(meetingId).then(setActionItems);
  }, [meetingId, meeting?.status, meeting?.has_audio, isOwner, canViewFull]);

  async function onGenerateReport() {
    if (!meetingId) return;
    setReportError(null);
    setGeneratingReport(true);
    try {
      const report = await api.generateReport(meetingId);
      setMeeting((prev) =>
        prev
          ? {
              ...prev,
              title: report.title,
              summary: report.summary,
              key_topics: report.key_topics,
              sentiment: report.sentiment,
              notable_quotes: report.notable_quotes,
              coach_score: report.coach_score,
            }
          : prev,
      );
      setActionItems(report.action_items);
      setTalkRatio(report.talk_ratio);
    } catch (err) {
      setReportError(err instanceof ApiError ? err.message : "Couldn't generate the report");
    } finally {
      setGeneratingReport(false);
    }
  }

  async function onToggleActionItem(item: ActionItem) {
    if (!meetingId) return;
    const nextStatus = item.status === "open" ? "done" : "open";
    setActionItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, status: nextStatus } : i)));
    try {
      await api.updateActionItem(meetingId, item.id, nextStatus);
    } catch {
      setActionItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, status: item.status } : i)));
    }
  }

  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  // Deep-linked from a Dashboard search result (?t=<start_ms>) — position
  // the playhead at the matched moment once the audio is actually mounted,
  // without auto-playing (a search click is a real user gesture, but
  // suddenly-playing audio on arrival is still a surprising thing to do to
  // someone). Only once per page load — audioUrl can change identity again
  // later (e.g. StrictMode/effect re-runs) and shouldn't re-trigger this.
  useEffect(() => {
    if (seekedFromSearchRef.current || !audioUrl || !audioRef.current) return;
    const t = searchParams.get("t");
    if (t === null) return;
    seekedFromSearchRef.current = true;
    seekTo(Number(t), false);
  }, [audioUrl, searchParams]);

  function seekTo(ms: number, autoplay = true) {
    if (!audioRef.current) return;
    audioRef.current.currentTime = ms / 1000;
    if (autoplay) audioRef.current.play().catch(() => {});
  }

  return (
    <AppShell>
      <div className="flex items-center justify-between">
        <Link to="/dashboard" className="text-sm text-ink-muted hover:text-ink dark:hover:text-ink-inverted">
          ← Meetings
        </Link>
        {meeting && isOwner && (
          <button
            onClick={onDelete}
            disabled={deleting}
            className="text-sm text-ink-subtle hover:text-status-danger"
          >
            {deleting ? "Deleting…" : "Delete meeting"}
          </button>
        )}
      </div>

      {!meeting && <p className="mt-6 text-sm text-ink-muted">Loading…</p>}

      {meeting && (
        <>
          <div className="mt-2 flex items-center gap-2">
            <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">{meeting.title}</h1>
            <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
              {CALL_TYPE_LABEL[meeting.call_type]}
            </span>
          </div>
          <p className="mt-1 text-sm text-ink-muted">
            {!isOwner && <>{meeting.owner_name} · </>}
            {new Date(meeting.created_at).toLocaleString()}
          </p>

          {!isOwner && !canViewFull && (
            <p className="mt-2 text-xs text-ink-subtle">
              Shared from your group — you can see the report below, not the full recording.
            </p>
          )}

          {!isOwner && canViewFull && (
            <p className="mt-2 text-xs text-ink-subtle">
              Viewing as admin — full recording and transcript.
            </p>
          )}

          {meeting.status === "processing" && (
            <div className="card mt-6 p-6 text-center">
              <p className="text-sm text-ink-muted">
                {isOwner
                  ? "Transcribing your recording — this page will update automatically."
                  : "This meeting is still being processed."}
              </p>
            </div>
          )}

          {meeting.status === "recording" && (
            <div className="card mt-6 p-6 text-center">
              <p className="text-sm text-ink-muted">This meeting hasn't been recorded yet.</p>
              {isOwner && (
                <Link to={`/meetings/${meeting.id}/live`} className="btn-primary mt-4 inline-flex">
                  Go to live session
                </Link>
              )}
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

              <div className="card p-6">
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Report</h2>
                    {meeting.sentiment && (
                      <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                        {meeting.sentiment}
                      </span>
                    )}
                    {meeting.coach_score !== null && meeting.coach_score !== undefined && (
                      <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                        Score: {meeting.coach_score}/100
                      </span>
                    )}
                  </div>
                  {isOwner && (meeting.summary || actionItems.length > 0) && (
                    <button
                      onClick={onGenerateReport}
                      disabled={generatingReport || providerConnected === false}
                      className="text-xs text-ink-muted hover:text-ink dark:hover:text-ink-inverted"
                    >
                      {generatingReport ? "Regenerating…" : "Regenerate"}
                    </button>
                  )}
                </div>

                {reportError && <p className="mb-3 text-sm text-status-danger">{reportError}</p>}

                {!isOwner && !meeting.summary && actionItems.length === 0 && (
                  <p className="text-sm text-ink-muted">No report yet.</p>
                )}

                {isOwner && !meeting.summary && actionItems.length === 0 && (
                  <>
                    {providerConnected === false ? (
                      <p className="text-sm text-ink-muted">
                        Connect an LLM provider in Settings to generate a summary and action items.
                      </p>
                    ) : (
                      <button
                        onClick={onGenerateReport}
                        disabled={generatingReport || providerConnected === null}
                        className="btn-secondary"
                      >
                        {generatingReport ? "Generating…" : "Generate report"}
                      </button>
                    )}
                  </>
                )}

                {meeting.summary && (
                  <p className="text-sm text-ink dark:text-ink-inverted">{meeting.summary}</p>
                )}

                {meeting.key_topics && meeting.key_topics.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {meeting.key_topics.map((topic) => (
                      <span
                        key={topic}
                        className="rounded-full bg-black/[0.03] px-2.5 py-0.5 text-xs text-ink-muted dark:bg-white/[0.06]"
                      >
                        {topic}
                      </span>
                    ))}
                  </div>
                )}

                {meeting.notable_quotes && meeting.notable_quotes.length > 0 && (
                  <ul className="mt-4 space-y-2">
                    {meeting.notable_quotes.map((quote, i) => (
                      <li
                        key={i}
                        className="border-l-2 border-border pl-3 text-sm italic text-ink-muted dark:border-border-dark"
                      >
                        "{quote}"
                      </li>
                    ))}
                  </ul>
                )}

                {talkRatio && (
                  <div className="mt-4">
                    <p className="label mb-1">Talk ratio</p>
                    <div className="flex h-2 overflow-hidden rounded-full bg-border dark:bg-border-dark">
                      <div className="bg-accent" style={{ width: `${talkRatio.me}%` }} />
                    </div>
                    <p className="mt-1 text-xs text-ink-subtle">
                      Me {talkRatio.me}% · Them {talkRatio.them}%
                    </p>
                  </div>
                )}

                {actionItems.length > 0 && (
                  <ul className="mt-4 space-y-1.5">
                    {actionItems.map((item) => (
                      <li key={item.id} className="flex items-start gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={item.status === "done"}
                          onChange={isOwner ? () => onToggleActionItem(item) : undefined}
                          disabled={!isOwner}
                          className="mt-0.5"
                        />
                        <span
                          className={
                            item.status === "done"
                              ? "text-ink-subtle line-through"
                              : "text-ink dark:text-ink-inverted"
                          }
                        >
                          {item.text}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {canViewFull && transcript === null && (
                <p className="text-sm text-ink-muted">Loading transcript…</p>
              )}

              {canViewFull && transcript?.length === 0 && (
                <div className="card p-10 text-center">
                  <p className="text-sm text-ink-muted">No speech was detected in this recording.</p>
                </div>
              )}

              {canViewFull && transcript && transcript.length > 0 && (
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
                          {speakerLabel(segment) && (
                            <span className="mr-2 text-xs font-medium text-ink-muted">
                              {speakerLabel(segment)}
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
