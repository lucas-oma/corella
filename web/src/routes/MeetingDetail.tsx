import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import AppShell from "@/components/AppShell";
import { ApiError, api, type ActionItem, type Meeting, type TranscriptSegment } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const POLL_INTERVAL_MS = 3000;
// Same-room diarization (server/app/workers/tasks.py:reconcile_diarization)
// runs periodically as a background Celery task, independent of the live WS
// session — it keeps running and correctly labels a segment even after the
// call has ended and this page has already loaded, but nothing pushes that
// update to an already-loaded page on its own. These bound how long the
// transcript-loading effect below re-polls specifically for segments still
// missing a real speaker_label.
//
// A real, non-obvious segment can also never resolve at all — a voice that
// never accumulates enough assigned speech to clear the guest floor
// (diarization_guest_min_ms/diarization_guest_min_share in
// app/core/config.py) across the *whole* call stays permanently unlabeled;
// confirmed live in this app's own real Postgres data: meetings that ended
// 20+ minutes ago still carry unresolved live segments, and nothing is ever
// going to revisit them again. So this poll is gated on the meeting having
// ended *recently* (see isDiarizationRecent below) — an old meeting reopened
// later shows its final state immediately, no fake "Identifying…" spinner
// replaying on every visit; only a meeting that just finished gets the
// benefit of the doubt and a real chance to catch up. The window itself is
// generous (well above the reconciliation loop's own ~25s interval) because
// a real reconciliation pass is a full diarize() call — genuinely tens of
// seconds under load, not the near-instant per-utterance resolution this
// constant was originally sized for before that architecture changed.
const DIARIZATION_GRACE_MS = 90000;
const DIARIZATION_POLL_INTERVAL_MS = 4000;
// The auto-generated post-call report (server/app/workers/tasks.py:
// _generate_report_async, dispatched fire-and-forget once a meeting reaches
// "ready") is a real LLM call, not instant — bounds how long the "ready"
// catch-up effect below re-polls for it before giving up and falling back
// to the manual "Generate report" button.
const REPORT_GRACE_MS = 60000;
const REPORT_POLL_INTERVAL_MS = 4000;

function formatTimestamp(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/** Per-meeting LLM cost is usually a few tenths of a cent — "$0.00" at two
 * decimal places would misleadingly read as free, so show more precision
 * below a cent. */
function formatCost(usd: number): string {
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

/** A resolved identity linked to an enrolled account (segment.linked_user_id)
 * is viewer-relative: the account owner sees "Me" here (first person, their
 * own meeting), anyone else with legitimate access (an admin, per Phase J —
 * a group member never reaches this view at all) sees the real name.
 *
 * A live-recorded segment with no speaker_label is not the same as one
 * that's genuinely just the channel owner talking alone — same-room
 * diarization (server/app/workers/tasks.py:reconcile_diarization) runs
 * periodically as a background task and can still be catching up right
 * after the call ends, and this segment might turn out to belong to a
 * different speaker entirely once it does. Worse, a segment can stay
 * unresolved *permanently* — a voice that never accumulates enough real
 * assigned speech across the whole call to clear the guest floor
 * (diarization_guest_min_ms/_min_share) never gets a real label, by
 * design, not a bug. Guessing "Me" for either case is a real
 * mislabeling risk (a previously-reported bug this project already fixed
 * once, for the "still catching up" half of it) — it falsely claims a
 * specific identity (the account owner) for a segment that was never
 * actually confirmed to be them, which could just as easily be a guest.
 * "Unknown" is the honest label once the catch-up window has actually
 * given up — see DIARIZATION_GRACE_MS and the transcript-loading effect
 * below. */
function speakerLabel(
  segment: TranscriptSegment,
  viewerId: string | undefined,
  diarizationCatchingUp: boolean,
): string | null {
  if (segment.speaker_label) {
    return segment.linked_user_id && segment.linked_user_id === viewerId
      ? "Me"
      : segment.speaker_label;
  }
  if (segment.channel === "me" || segment.channel === "them") {
    return diarizationCatchingUp ? "Identifying…" : "Unknown";
  }
  return null;
}

/** Any live-recorded (me/them) segment whose speaker hasn't been resolved
 * yet — see speakerLabel's docstring for why that's not the same as "just
 * the channel owner." */
function hasUnresolvedLiveSpeaker(segments: TranscriptSegment[]): boolean {
  return segments.some(
    (s) => (s.channel === "me" || s.channel === "them") && !s.speaker_label,
  );
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
  // True while the transcript-loading effect below is still re-polling for
  // same-room diarization to catch up on any segment it loaded without a
  // resolved speaker yet — see speakerLabel's docstring.
  const [diarizationCatchingUp, setDiarizationCatchingUp] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [actionItems, setActionItems] = useState<ActionItem[]>([]);
  const [talkRatio, setTalkRatio] = useState<{ me: number; them: number } | null>(null);
  const [providerConnected, setProviderConnected] = useState<boolean | null>(null);
  const [generatingReport, setGeneratingReport] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  // True while the "ready" catch-up effect below is still waiting on the
  // auto-generated report to land — see REPORT_GRACE_MS's docstring.
  const [reportPending, setReportPending] = useState(false);

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
    let cancelled = false;

    if (canViewFull) {
      // Always fetch the transcript once meeting is ready. Separately,
      // re-poll for a bounded window while a live-recorded segment is still
      // missing a resolved speaker — see speakerLabel's and
      // DIARIZATION_GRACE_MS's docstrings above. Only worth re-polling if
      // the call ended recently enough that the backend could still be
      // working on it; an old meeting (or one with no ended_at) gets its
      // final state immediately ("Unknown" for anything that never
      // resolved) instead of a 90-second spinner — and must still load the
      // transcript either way (previously the !endedRecently branch only
      // cleared the spinner flag and never called getTranscript, leaving
      // the page stuck on "Loading transcript…" forever after refresh).
      const endedRecently =
        !!meeting.ended_at && Date.now() - new Date(meeting.ended_at).getTime() < DIARIZATION_GRACE_MS;
      (async () => {
        if (!endedRecently) {
          setDiarizationCatchingUp(false);
          const fresh = await api.getTranscript(meetingId);
          if (!cancelled) setTranscript(fresh);
          return;
        }
        const deadline = Date.now() + DIARIZATION_GRACE_MS;
        while (!cancelled) {
          const fresh = await api.getTranscript(meetingId);
          if (cancelled) return;
          setTranscript(fresh);
          const stillPending = hasUnresolvedLiveSpeaker(fresh);
          if (!stillPending || Date.now() >= deadline) {
            setDiarizationCatchingUp(false);
            return;
          }
          setDiarizationCatchingUp(true);
          await new Promise((resolve) => setTimeout(resolve, DIARIZATION_POLL_INTERVAL_MS));
        }
      })();
      if (meeting.has_audio) {
        api.getAudioObjectUrl(meetingId).then(setAudioUrl);
      }
    }
    if (isOwner) {
      api.getProviderStatus().then((statuses) => setProviderConnected(statuses.some((s) => s.connected)));
    }

    // Report generation (server/app/workers/tasks.py:_generate_report_async)
    // is dispatched fire-and-forget right after a meeting reaches "ready"
    // (both the upload-processing and live-finalize success paths) — so a
    // page that loads right at that transition fetches action items before
    // the real report has landed, and (since the meeting-status poll above
    // stops the instant it sees "ready") never learns the summary/title/etc.
    // arrived either. Same bounded-catch-up shape as the diarization
    // re-poll above: keep re-fetching for a while, stop the moment real
    // content shows up or the window runs out — a meeting whose owner has
    // no connected provider never gets an auto report at all (the backend
    // skips it silently), so this must give up eventually rather than poll
    // forever, falling back to the "Generate report" button as it always
    // has.
    (async () => {
      const deadline = Date.now() + REPORT_GRACE_MS;
      let items = await api.listActionItems(meetingId);
      if (cancelled) return;
      setActionItems(items);
      if (meeting.summary || items.length > 0) return;
      setReportPending(true);
      while (!cancelled && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, REPORT_POLL_INTERVAL_MS));
        if (cancelled) return;
        const [fresh, freshItems] = await Promise.all([api.getMeeting(meetingId), api.listActionItems(meetingId)]);
        if (cancelled) return;
        items = freshItems;
        if (fresh.summary || items.length > 0) {
          setMeeting(fresh);
          setActionItems(items);
          break;
        }
      }
      if (!cancelled) setReportPending(false);
    })();

    return () => {
      cancelled = true;
    };
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
              estimated_cost_usd: report.estimated_cost_usd,
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
            {meeting.call_type && (
              <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                {meeting.call_type.name}
              </span>
            )}
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
                    {meeting.estimated_cost_usd !== null && meeting.estimated_cost_usd !== undefined && (
                      <span
                        className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark"
                        title="Best-effort estimate based on token usage, not an authoritative bill"
                      >
                        Est. cost: {formatCost(meeting.estimated_cost_usd)}
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
                  <p className="text-sm text-ink-muted">
                    {reportPending ? "Generating report…" : "No report yet."}
                  </p>
                )}

                {isOwner && !meeting.summary && actionItems.length === 0 && (
                  <>
                    {reportPending ? (
                      <p className="text-sm text-ink-muted">Generating report…</p>
                    ) : providerConnected === false ? (
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
                  {transcript.map((segment) => {
                    const label = speakerLabel(segment, user?.id, diarizationCatchingUp);
                    return (
                      <li key={segment.id}>
                        <button
                          onClick={() => seekTo(segment.start_ms)}
                          className="flex w-full gap-4 px-5 py-3 text-left transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                        >
                          <span className="w-12 shrink-0 pt-0.5 font-mono text-xs text-ink-subtle">
                            {formatTimestamp(segment.start_ms)}
                          </span>
                          <span>
                            {label && (
                              <span
                                className={`mr-2 text-xs font-medium text-ink-muted ${
                                  label === "Identifying…" || label === "Unknown" ? "italic opacity-70" : ""
                                }`}
                              >
                                {label}
                              </span>
                            )}
                            <span className="text-sm text-ink dark:text-ink-inverted">{segment.text}</span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>
          )}
        </>
      )}
    </AppShell>
  );
}
