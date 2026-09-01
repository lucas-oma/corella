import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import AppShell from "@/components/AppShell";
import { ApiError, api, type Meeting } from "@/lib/api";

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
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    api.listMeetings().then(setMeetings);
  }, []);

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
        <div>
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
            className="btn-primary"
          >
            {uploading ? "Uploading…" : "Upload recording"}
          </button>
        </div>
      </div>

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

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
    </AppShell>
  );
}
