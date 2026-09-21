/** Which Meetings list tab is open. Lives on /dashboard?tab= so the list
 * is the source of truth — not a `from` query on /meetings/:id, which
 * would leak navigation context into shareable meeting URLs. */

export type MeetingsTab = "mine" | "group" | "all";

const STORAGE_KEY = "corella.meetingsTab";

export function parseMeetingsTab(value: string | null | undefined): MeetingsTab {
  if (value === "group" || value === "all") return value;
  return "mine";
}

export function meetingsListPath(tab?: MeetingsTab): string {
  const resolved = tab ?? readMeetingsTab();
  return resolved === "mine" ? "/dashboard" : `/dashboard?tab=${resolved}`;
}

export function readMeetingsTab(): MeetingsTab {
  try {
    return parseMeetingsTab(sessionStorage.getItem(STORAGE_KEY));
  } catch {
    return "mine";
  }
}

export function writeMeetingsTab(tab: MeetingsTab): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, tab);
  } catch {
    /* private mode / quota */
  }
}

export function meetingDetailLocation(id: string, tab: MeetingsTab, search = "") {
  return {
    pathname: `/meetings/${id}`,
    search,
    state: { meetingsTab: tab },
  };
}

export function meetingsTabFromState(state: unknown): MeetingsTab | undefined {
  if (!state || typeof state !== "object" || !("meetingsTab" in state)) return undefined;
  const tab = (state as { meetingsTab?: unknown }).meetingsTab;
  if (tab === "mine" || tab === "group" || tab === "all") return tab;
  return undefined;
}
