import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import AppShell from "@/components/AppShell";
import CallTypeModal from "@/components/CallTypeModal";
import { ApiError, api, type GroupMeeting, type Meeting, type MeetingSearchResult } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STATUS_LABEL: Record<Meeting["status"], string> = {
  recording: "Recording",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

/** Strip the extension and tidy up a filename for use as a default title,
 * e.g. "sales-call_2026-09-01.m4a" -> "sales-call_2026-09-01". */
function titleFromFilename(name: string): string {
  return name.replace(/\.[^./]+$/, "") || "Untitled meeting";
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MeetingSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [view, setView] = useState<"mine" | "group" | "all">("mine");
  const [groupMeetings, setGroupMeetings] = useState<GroupMeeting[] | null>(null);
  const [allMeetings, setAllMeetings] = useState<GroupMeeting[] | null>(null);
  const [groupFilter, setGroupFilter] = useState("");
  // Which action the call-type popup is currently gating — set by clicking
  // "Record live" or "Upload recording", cleared on cancel/confirm. Call
  // types are admin-managed (Admin.tsx) now, not a fixed list, so there's
  // no static dropdown in the header anymore — the choice happens right
  // when it's needed instead.
  const [pendingAction, setPendingAction] = useState<"live" | "upload" | null>(null);
  const isAdmin = user?.role === "admin";

  // Not semantic — an instant client-side filter over what's already
  // fetched, deliberately: unlike Mine/All (real search over transcript
  // content via meeting_chunks), a group member never gets transcript
  // access (_get_group_visible_meeting stays report-only), so this only
  // matches the report fields they can already see once a meeting is open.
  const filteredGroupMeetings = groupMeetings?.filter((m) => {
    const q = groupFilter.trim().toLowerCase();
    if (!q) return true;
    return (
      m.title.toLowerCase().includes(q) ||
      m.owner_name.toLowerCase().includes(q) ||
      (m.summary?.toLowerCase().includes(q) ?? false) ||
      (m.key_topics?.some((t) => t.toLowerCase().includes(q)) ?? false)
    );
  });

  useEffect(() => {
    api.listMeetings().then(setMeetings);
  }, []);

  // Only fetched once the user actually switches to it — someone who never
  // opens the Group/All tab (the vast majority for "All," since it's
  // admin-only) never needs the request at all.
  useEffect(() => {
    if (view === "group" && groupMeetings === null) {
      api.listGroupMeetings().then(setGroupMeetings);
    }
    if (view === "all" && allMeetings === null) {
      api.listAllMeetings().then(setAllMeetings);
    }
  }, [view, groupMeetings, allMeetings]);

  // Semantic search, not a substring filter over the already-loaded list —
  // needs a real request per query, so debounce it rather than searching on
  // every keystroke. Only for Mine/All — Group uses the instant client-side
  // filter above instead (see filteredGroupMeetings for why).
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed || view === "group") {
      setSearchResults(null);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const search = view === "all" ? api.searchAllMeetings : api.searchMeetings;
    const timer = setTimeout(() => {
      search(trimmed)
        .then((results) => {
          if (!cancelled) setSearchResults(results);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : "Search failed");
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, view]);

  // The chosen call type from the popup, stashed here for onFileSelected —
  // the browser file picker only opens after the popup already closed, so
  // by the time a file is actually picked the popup's own state is gone.
  const pendingCallTypeId = useRef<string | null>(null);

  async function onFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file later
    if (!file) return;

    setError(null);
    setUploading(true);
    let meeting: Meeting | null = null;
    try {
      meeting = await api.createMeeting(titleFromFilename(file.name), pendingCallTypeId.current);
      await api.uploadMeetingAudio(meeting.id, file);
      navigate(`/meetings/${meeting.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
      // Don't leave a stuck, audio-less "recording" meeting behind just
      // because the upload itself failed — clean it back up.
      if (meeting) {
        await api.deleteMeeting(meeting.id).catch(() => {});
      }
    } finally {
      setUploading(false);
    }
  }

  async function onRecordLive(callTypeId: string) {
    setError(null);
    setStarting(true);
    try {
      const meeting = await api.createMeeting("Live recording", callTypeId);
      navigate(`/meetings/${meeting.id}/live`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start a live session");
    } finally {
      setStarting(false);
    }
  }

  function onCallTypeChosen(callTypeId: string) {
    const action = pendingAction;
    setPendingAction(null);
    if (action === "live") {
      onRecordLive(callTypeId);
    } else if (action === "upload") {
      pendingCallTypeId.current = callTypeId;
      // Still inside the same click-driven gesture chain (popup "Continue"
      // was itself a click), so the browser allows programmatically
      // opening the file picker here.
      fileInputRef.current?.click();
    }
  }

  async function onDelete(e: React.MouseEvent, meetingId: string) {
    e.preventDefault();
    e.stopPropagation();
    setDeletingId(meetingId);
    try {
      await api.deleteMeeting(meetingId);
      setMeetings((prev) => prev?.filter((m) => m.id !== meetingId) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete meeting");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Meetings</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Your recorded calls, transcripts, and coaching reports.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setPendingAction("live")} disabled={starting} className="btn-primary">
            {starting ? "Starting…" : "Record live"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            // "audio/*" alone isn't enough — the OS file picker filters by
            // MIME type first, and several accepted extensions (.caf
            // especially) aren't reliably mapped to an audio/* type in
            // every OS's file-type database, so the picker hides them even
            // though the backend (_ALLOWED_AUDIO_EXTENSIONS in
            // api/meetings.py) happily accepts them. Listing the exact
            // extensions alongside the wildcard covers both cases — a
            // browser matches on *either*.
            accept="audio/*,.mp3,.wav,.m4a,.mp4,.webm,.ogg,.oga,.flac,.aac,.opus,.caf"
            className="hidden"
            onChange={onFileSelected}
          />
          <button
            onClick={() => setPendingAction("upload")}
            disabled={uploading}
            className="btn-secondary"
          >
            {uploading ? "Uploading…" : "Upload recording"}
          </button>
        </div>
      </div>

      <CallTypeModal
        open={pendingAction !== null}
        onCancel={() => setPendingAction(null)}
        onConfirm={onCallTypeChosen}
      />

      {(user?.group_id || isAdmin) && (
        <div className="mb-6 flex gap-1 border-b border-border dark:border-border-dark">
          {(["mine", ...(user?.group_id ? (["group"] as const) : []), ...(isAdmin ? (["all"] as const) : [])] as const).map(
            (tab) => (
              <button
                key={tab}
                onClick={() => setView(tab)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
                  view === tab
                    ? "border-accent text-ink dark:border-ink-inverted dark:text-ink-inverted"
                    : "border-transparent text-ink-muted hover:text-ink dark:hover:text-ink-inverted"
                }`}
              >
                {tab === "mine" ? "My meetings" : tab === "group" ? "Group" : "All meetings"}
              </button>
            ),
          )}
        </div>
      )}

      {(view === "mine" || view === "all") && (
        <div className="relative mb-6">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              view === "all"
                ? "Search every meeting by what was said…"
                : "Search meetings by what was said…"
            }
            className="field"
          />
          {searching && (
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-ink-subtle">
              Searching…
            </span>
          )}
        </div>
      )}

      {view === "group" && (
        <div className="relative mb-6">
          <input
            type="search"
            value={groupFilter}
            onChange={(e) => setGroupFilter(e.target.value)}
            placeholder="Filter by title, topic, or who…"
            className="field"
          />
        </div>
      )}

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      {view === "group" ? (
        <>
          {groupMeetings === null && <p className="text-sm text-ink-muted">Loading…</p>}
          {groupMeetings?.length === 0 && (
            <div className="card p-10 text-center">
              <p className="text-sm text-ink-muted">
                No meetings from your group yet — reports show up here once a group-mate
                finishes one.
              </p>
            </div>
          )}
          {groupMeetings && groupMeetings.length > 0 && filteredGroupMeetings?.length === 0 && (
            <div className="card p-10 text-center">
              <p className="text-sm text-ink-muted">No meetings match "{groupFilter.trim()}".</p>
            </div>
          )}
          {filteredGroupMeetings && filteredGroupMeetings.length > 0 && (
            <ul className="card max-h-[70vh] divide-y divide-border overflow-y-auto dark:divide-border-dark">
              {filteredGroupMeetings.map((meeting) => (
                <li key={meeting.id}>
                  <Link
                    to={`/meetings/${meeting.id}`}
                    className="flex items-center justify-between px-5 py-4 transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                  >
                    <div>
                      <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                        {meeting.title}
                      </p>
                      <p className="mt-0.5 text-xs text-ink-subtle">
                        {meeting.owner_name} · {new Date(meeting.created_at).toLocaleString()}
                      </p>
                    </div>
                    <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                      {STATUS_LABEL[meeting.status]}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : searchResults !== null ? (
        <>
          {searchResults.length === 0 && !searching && (
            <div className="card p-10 text-center">
              <p className="text-sm text-ink-muted">No meetings match "{query.trim()}".</p>
            </div>
          )}
          {searchResults.length > 0 && (
            <ul className="card max-h-[70vh] divide-y divide-border overflow-y-auto dark:divide-border-dark">
              {searchResults.map((result) => (
                <li key={result.meeting_id}>
                  <Link
                    to={`/meetings/${result.meeting_id}?t=${result.start_ms}`}
                    className="block px-5 py-4 transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                  >
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                        {result.title}
                      </p>
                      <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                        {STATUS_LABEL[result.status]}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-ink-subtle">
                      {view === "all" && <>{result.owner_name} · </>}
                      {new Date(result.created_at).toLocaleString()}
                    </p>
                    <p className="mt-1.5 text-sm text-ink-muted">"{result.snippet}"</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : view === "all" ? (
        <>
          {allMeetings === null && <p className="text-sm text-ink-muted">Loading…</p>}
          {allMeetings?.length === 0 && (
            <div className="card p-10 text-center">
              <p className="text-sm text-ink-muted">No meetings yet, across any account.</p>
            </div>
          )}
          {allMeetings && allMeetings.length > 0 && (
            <ul className="card max-h-[70vh] divide-y divide-border overflow-y-auto dark:divide-border-dark">
              {allMeetings.map((meeting) => (
                <li key={meeting.id}>
                  <Link
                    to={`/meetings/${meeting.id}`}
                    className="flex items-center justify-between px-5 py-4 transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                  >
                    <div>
                      <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                        {meeting.title}
                      </p>
                      <p className="mt-0.5 text-xs text-ink-subtle">
                        {meeting.owner_name} · {new Date(meeting.created_at).toLocaleString()}
                      </p>
                    </div>
                    <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                      {STATUS_LABEL[meeting.status]}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <>
          {meetings === null && <p className="text-sm text-ink-muted">Loading…</p>}

          {meetings?.length === 0 && (
            <div className="card p-10 text-center">
              <p className="text-sm text-ink-muted">
                No meetings yet. Upload a recording to see it appear here.
              </p>
            </div>
          )}

          {meetings && meetings.length > 0 && (
            <ul className="card max-h-[70vh] divide-y divide-border overflow-y-auto dark:divide-border-dark">
              {meetings.map((meeting) => (
                <li key={meeting.id}>
                  <Link
                    to={`/meetings/${meeting.id}`}
                    className="flex items-center justify-between px-5 py-4 transition-colors hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                  >
                    <div>
                      <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                        {meeting.title}
                      </p>
                      <p className="mt-0.5 text-xs text-ink-subtle">
                        {new Date(meeting.created_at).toLocaleString()}
                      </p>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                        {STATUS_LABEL[meeting.status]}
                      </span>
                      <button
                        onClick={(e) => onDelete(e, meeting.id)}
                        disabled={deletingId === meeting.id}
                        className="text-xs text-ink-subtle hover:text-status-danger"
                        title="Delete meeting"
                      >
                        {deletingId === meeting.id ? "…" : "Delete"}
                      </button>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </AppShell>
  );
}
