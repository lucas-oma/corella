import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { api, type Meeting } from "@/lib/api";

const STATUS_LABEL: Record<Meeting["status"], string> = {
  recording: "Recording",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

export default function Dashboard() {
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api.listMeetings().then(setMeetings);
  }, []);

  async function startMeeting() {
    setCreating(true);
    try {
      const meeting = await api.createMeeting("Untitled meeting");
      setMeetings((prev) => [meeting, ...(prev ?? [])]);
    } finally {
      setCreating(false);
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
        <button onClick={startMeeting} disabled={creating} className="btn-primary">
          {creating ? "Starting…" : "New meeting"}
        </button>
      </div>

      {meetings === null && <p className="text-sm text-ink-muted">Loading…</p>}

      {meetings?.length === 0 && (
        <div className="card p-10 text-center">
          <p className="text-sm text-ink-muted">
            No meetings yet. Start one to see it appear here.
          </p>
        </div>
      )}

      {meetings && meetings.length > 0 && (
        <ul className="card divide-y divide-border dark:divide-border-dark">
          {meetings.map((meeting) => (
            <li key={meeting.id} className="flex items-center justify-between px-5 py-4">
              <div>
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                  {meeting.title}
                </p>
                <p className="mt-0.5 text-xs text-ink-subtle">
                  {new Date(meeting.created_at).toLocaleString()}
                </p>
              </div>
              <span className="rounded-sm border border-border px-2 py-0.5 text-xs text-ink-muted dark:border-border-dark">
                {STATUS_LABEL[meeting.status]}
              </span>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
