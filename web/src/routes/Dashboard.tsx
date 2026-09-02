import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import AppShell from "@/components/AppShell";
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
  const [view, setView] = useState<"mine" | "group">("mine");
  const [groupMeetings, setGroupMeetings] = useState<GroupMeeting[] | null>(null);

  useEffect(() => {
    api.listMeetings().then(setMeetings);
  }, []);

  // Only fetched once the user actually switches to it — an ungrouped user
  // (the vast majority, and the tab isn't even shown to them) never needs
  // this request at all.
  useEffect(() => {
    if (view === "group" && groupMeetings === null) {
      api.listGroupMeetings().then(setGroupMeetings);
    }
  }, [view, groupMeetings]);

  // Semantic search, not a substring filter over the already-loaded list —
  // needs a real request per query, so debounce it rather than searching on
  // every keystroke.
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchResults(null);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = setTimeout(() => {
      api
        .searchMeetings(trimmed)
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
  }, [query]);

  async function onFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file later
    if (!file) return;

    setError(null);
    setUploading(true);
    let meeting: Meeting | null = null;
    try {
      meeting = await api.createMeeting(titleFromFilename(file.name));
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

  async function onRecordLive() {
    setError(null);
    setStarting(true);
    try {
      const meeting = await api.createMeeting("Live recording");
      navigate(`/meetings/${meeting.id}/live`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start a live session");
    } finally {
      setStarting(false);
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
        <div className="flex gap-2">
          <button onClick={onRecordLive} disabled={starting} className="btn-primary">
            {starting ? "Starting…" : "Record live"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={onFileSelected}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="btn-secondary"
          >
            {uploading ? "Uploading…" : "Upload recording"}
          </button>
        </div>
      </div>

      {user?.group_id && (
        <div className="mb-6 flex gap-1 border-b border-border dark:border-border-dark">
          {(["mine", "group"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setView(tab)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
                view === tab
                  ? "border-accent text-ink dark:border-ink-inverted dark:text-ink-inverted"
                  : "border-transparent text-ink-muted hover:text-ink dark:hover:text-ink-inverted"
              }`}
            >
              {tab === "mine" ? "My meetings" : "Group"}
            </button>
          ))}
        </div>
      )}

      {view === "mine" && (
        <div className="relative mb-6">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search meetings by what was said…"
            className="field"
          />
          {searching && (
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-ink-subtle">
              Searching…
            </span>
          )}
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
          {groupMeetings && groupMeetings.length > 0 && (
            <ul className="card divide-y divide-border dark:divide-border-dark">
              {groupMeetings.map((meeting) => (
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
            <ul className="card divide-y divide-border dark:divide-border-dark">
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
                      {new Date(result.created_at).toLocaleString()}
                    </p>
                    <p className="mt-1.5 text-sm text-ink-muted">"{result.snippet}"</p>
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
            <ul className="card divide-y divide-border dark:divide-border-dark">
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
