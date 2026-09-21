import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import AppShell from "@/components/AppShell";
import {
  ApiError,
  api,
  type CostPeriod,
  type CostSummary,
  type GroupMeeting,
  type Meeting,
  type MeetingSearchResult,
  type Organization,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";

const COST_PERIODS: { id: CostPeriod; label: string }[] = [
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "month", label: "Month" },
  { id: "year", label: "Year" },
];

const STATUS_LABEL: Record<Meeting["status"], string> = {
  recording: "Recording",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

const STATUS_CLASS: Record<Meeting["status"], string> = {
  recording: "border-status-info/30 text-status-info",
  processing: "border-border text-ink-muted dark:border-border-dark",
  ready: "border-status-success/30 text-status-success",
  failed: "border-status-danger/30 text-status-danger",
};

function formatUsd(usd: number): string {
  return usd < 0.01 && usd > 0 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

function formatDayLabel(day: string, period: CostPeriod, index: number, total: number): string {
  const [, month, d] = day.split("-");
  const dayNum = String(Number(d));
  if (period === "7d") {
    const weekday = new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" });
    return `${weekday} ${dayNum}`;
  }
  if (period === "30d" || period === "month") {
    if (index === 0 || index === total - 1 || index % 7 === 0) {
      return `${Number(month)}/${dayNum}`;
    }
    return "";
  }
  if (dayNum === "1" || index === 0 || index === total - 1) {
    return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { month: "short" });
  }
  return "";
}

function periodCaption(period: CostPeriod, dayCount: number): string {
  if (period === "7d") return "last 7 days";
  if (period === "30d") return "last 30 days";
  if (period === "month") return `this month (${dayCount} days)`;
  return "last 365 days";
}

type InstanceUser = {
  id: string;
  email: string;
  full_name: string;
  is_super_admin: boolean;
};

export default function Admin() {
  const { user: me } = useAuth();
  const confirm = useConfirm();
  const [orgs, setOrgs] = useState<Organization[] | null>(null);
  const [users, setUsers] = useState<InstanceUser[] | null>(null);
  const [meetings, setMeetings] = useState<GroupMeeting[] | null>(null);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MeetingSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [costPeriod, setCostPeriod] = useState<CostPeriod>("30d");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.adminListOrganizations().then(setOrgs);
    api.adminListUsers().then(setUsers);
    api.listAllMeetings().then(setMeetings);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.adminGetInstanceCostSummary(costPeriod).then((data) => {
      if (!cancelled) setCosts(data);
    });
    return () => {
      cancelled = true;
    };
  }, [costPeriod]);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setSearchResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(() => {
      api
        .searchAllMeetings(q)
        .then(setSearchResults)
        .catch(() => setSearchResults([]))
        .finally(() => setSearching(false));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function onToggleSuperAdmin(user: InstanceUser, next: boolean) {
    if (user.id === me?.id && !next) return;
    const ok = await confirm({
      title: next ? "Promote to super admin?" : "Remove super admin?",
      description: next
        ? `${user.full_name} will be able to manage this instance.`
        : `${user.full_name} will lose instance-wide access.`,
      confirmLabel: next ? "Promote" : "Remove",
    });
    if (!ok) return;
    setError(null);
    setBusy(user.id);
    try {
      const updated = await api.adminSetSuperAdmin(user.id, next);
      setUsers(
        (prev) =>
          prev?.map((row) =>
            row.id === user.id ? { ...row, is_super_admin: updated.is_super_admin } : row,
          ) ?? null,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update super admin");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-8">
        <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Super admin</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Instance-wide organizations, super-admin flags, and spend. Organization people, groups,
          secrets, and call types live under Organization.
        </p>
      </div>

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      <section className="card p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Organizations</h2>
        <ul className="mt-4 divide-y divide-border dark:divide-border-dark">
          {orgs === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {orgs?.length === 0 && <li className="py-3 text-sm text-ink-muted">No organizations yet.</li>}
          {orgs?.map((org) => (
            <li key={org.id} className="flex items-center justify-between gap-3 py-3">
              <div>
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">{org.name}</p>
                <p className="text-xs text-ink-subtle">
                  {org.is_instance_org ? "Instance org · " : ""}
                  created {new Date(org.created_at).toLocaleDateString()}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Super admins</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Super admins can see every organization and this console. The flag is independent of org
          owner/admin.
        </p>
        <ul className="mt-4 divide-y divide-border dark:divide-border-dark">
          {users === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {users?.map((user) => (
            <li key={user.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink dark:text-ink-inverted">
                  {user.full_name}
                </p>
                <p className="truncate text-xs text-ink-subtle">{user.email}</p>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={user.is_super_admin}
                  disabled={busy === user.id || user.id === me?.id}
                  onChange={(e) => void onToggleSuperAdmin(user, e.target.checked)}
                />
                Super admin
              </label>
            </li>
          ))}
        </ul>
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">All meetings</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Cross-organization support view. Org owner/admin &quot;All&quot; on Meetings stays inside
          the active organization.
        </p>
        <div className="relative mt-4">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search every meeting by what was said…"
            className="field"
          />
          {searching && (
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-ink-subtle">
              Searching…
            </span>
          )}
        </div>
        {searchResults !== null ? (
          <>
            {searchResults.length === 0 && !searching && (
              <p className="mt-4 text-sm text-ink-muted">No meetings match &quot;{query.trim()}&quot;.</p>
            )}
            {searchResults.length > 0 && (
              <ul className="mt-4 divide-y divide-border dark:divide-border-dark">
                {searchResults.map((result) => (
                  <li key={result.meeting_id}>
                    <Link
                      to={`/meetings/${result.meeting_id}?t=${result.start_ms}`}
                      className="block py-3"
                    >
                      <div className="flex items-center justify-between">
                        <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                          {result.title}
                        </p>
                        <span className={`rounded-sm border px-2 py-0.5 text-xs ${STATUS_CLASS[result.status]}`}>
                          {STATUS_LABEL[result.status]}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-ink-subtle">
                        {result.owner_name} · {new Date(result.created_at).toLocaleString()}
                      </p>
                      <p className="mt-1.5 text-sm text-ink-muted">&quot;{result.snippet}&quot;</p>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            {meetings === null && <p className="mt-4 text-sm text-ink-muted">Loading…</p>}
            {meetings?.length === 0 && (
              <p className="mt-4 text-sm text-ink-muted">No meetings yet, across any organization.</p>
            )}
            {meetings && meetings.length > 0 && (
              <ul className="mt-4 max-h-[50vh] divide-y divide-border overflow-y-auto dark:divide-border-dark">
                {meetings.map((meeting) => (
                  <li key={meeting.id}>
                    <Link
                      to={`/meetings/${meeting.id}`}
                      className="flex items-center justify-between py-3"
                    >
                      <div>
                        <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                          {meeting.title}
                        </p>
                        <p className="mt-0.5 text-xs text-ink-subtle">
                          {meeting.owner_name} · {new Date(meeting.created_at).toLocaleString()}
                        </p>
                      </div>
                      <span className={`rounded-sm border px-2 py-0.5 text-xs ${STATUS_CLASS[meeting.status]}`}>
                        {STATUS_LABEL[meeting.status]}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Instance costs</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Best-effort LLM spend across every organization — token usage priced against a
          point-in-time table, not an authoritative bill.
        </p>

        {costs === null && <p className="mt-5 text-sm text-ink-muted">Loading…</p>}

        {costs && (
          <>
            <div className="mt-5 grid grid-cols-3 gap-3">
              <div className="rounded-sm border border-border p-4 dark:border-border-dark">
                <p className="label">Total cost</p>
                <p className="mt-1 font-serif text-xl text-ink dark:text-ink-inverted">
                  {formatUsd(costs.total_usd)}
                </p>
                <p className="mt-0.5 text-xs text-ink-subtle">
                  {costs.total_call_count} call{costs.total_call_count === 1 ? "" : "s"}
                  {costs.priced_call_count < costs.total_call_count &&
                    ` (${costs.total_call_count - costs.priced_call_count} unpriced)`}
                </p>
              </div>
              <div className="rounded-sm border border-border p-4 dark:border-border-dark">
                <p className="label">Avg cost / call</p>
                <p className="mt-1 font-serif text-xl text-ink dark:text-ink-inverted">
                  {costs.avg_cost_per_call !== null ? formatUsd(costs.avg_cost_per_call) : "—"}
                </p>
                <p className="mt-0.5 text-xs text-ink-subtle">across priced calls</p>
              </div>
              <div className="rounded-sm border border-border p-4 dark:border-border-dark">
                <p className="label">Next 7 days (est.)</p>
                <p className="mt-1 font-serif text-xl text-ink dark:text-ink-inverted">
                  {costs.projected_next_7_days_usd !== null
                    ? formatUsd(costs.projected_next_7_days_usd)
                    : "—"}
                </p>
                <p className="mt-0.5 text-xs text-ink-subtle">based on the trailing average</p>
              </div>
            </div>

            <div className="mt-6">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm font-medium text-ink-muted">
                  Daily cost
                  <span className="ml-2 font-normal text-ink-subtle">
                    {periodCaption(costPeriod, costs.daily.length)}
                  </span>
                </p>
                <div className="flex items-center gap-1">
                  {COST_PERIODS.map((p) => {
                    const active = costPeriod === p.id;
                    return (
                      <button
                        key={p.id}
                        type="button"
                        onClick={() => setCostPeriod(p.id)}
                        className={`rounded px-2.5 py-1 text-xs transition-colors ${
                          active
                            ? "bg-accent text-accent-foreground"
                            : "text-ink-muted hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
                        }`}
                      >
                        {p.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {costs.daily.length === 0 ? (
                <p className="text-sm text-ink-muted">No daily history yet.</p>
              ) : (
                (() => {
                  const max = Math.max(...costs.daily.map((d) => d.total_usd), 0);
                  const showValues = costs.daily.length <= 14;
                  const periodTotal = costs.daily.reduce((sum, d) => sum + d.total_usd, 0);
                  return (
                    <>
                      <div className="mb-2 flex items-baseline justify-between gap-3">
                        <p className="text-xs text-ink-subtle">
                          Period total <span className="text-ink-muted">{formatUsd(periodTotal)}</span>
                        </p>
                        <p className="text-xs text-ink-subtle">
                          Peak day <span className="text-ink-muted">{formatUsd(max)}</span>
                        </p>
                      </div>
                      <div className="flex items-end gap-px">
                        {costs.daily.map((d, i) => {
                          const pct = max > 0 ? (d.total_usd / max) * 100 : 0;
                          const label = formatDayLabel(d.day, costPeriod, i, costs.daily.length);
                          return (
                            <div
                              key={d.day}
                              className="flex min-w-0 flex-1 flex-col items-center"
                              title={`${d.day}: ${formatUsd(d.total_usd)}`}
                            >
                              {showValues && (
                                <span className="mb-1 h-3 text-[10px] leading-none text-ink-subtle tabular-nums">
                                  {d.total_usd > 0 ? formatUsd(d.total_usd) : ""}
                                </span>
                              )}
                              <div className="flex h-28 w-full items-end justify-center">
                                <div
                                  className={`w-full max-w-[2.5rem] rounded-t-sm ${
                                    d.total_usd > 0
                                      ? "bg-accent"
                                      : "bg-border dark:bg-border-dark"
                                  }`}
                                  style={{ height: `${Math.max(pct, d.total_usd > 0 ? 4 : 1)}%` }}
                                />
                              </div>
                              <span className="mt-1 h-3 text-[10px] leading-none text-ink-subtle">
                                {label}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </>
                  );
                })()
              )}
            </div>

            <div className="mt-6">
              <p className="label mb-2">By user</p>
              {costs.by_user.length === 0 ? (
                <p className="text-sm text-ink-muted">No calls logged yet.</p>
              ) : (
                <ul className="divide-y divide-border dark:divide-border-dark">
                  {costs.by_user.map((u) => (
                    <li
                      key={u.owner_id ?? "deleted"}
                      className="flex items-center justify-between py-2"
                    >
                      <p className="text-sm text-ink dark:text-ink-inverted">{u.owner_name}</p>
                      <p className="text-sm text-ink-muted">
                        {formatUsd(u.total_usd)}{" "}
                        <span className="text-xs text-ink-subtle">
                          ({u.call_count} call{u.call_count === 1 ? "" : "s"})
                        </span>
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="mt-6">
              <p className="label mb-2">By provider</p>
              {costs.by_provider.length === 0 ? (
                <p className="text-sm text-ink-muted">No calls logged yet.</p>
              ) : (
                <ul className="divide-y divide-border dark:divide-border-dark">
                  {costs.by_provider.map((p) => (
                    <li key={p.provider} className="flex items-center justify-between py-2">
                      <p className="text-sm capitalize text-ink dark:text-ink-inverted">{p.provider}</p>
                      <p className="text-sm text-ink-muted">
                        {formatUsd(p.total_usd)}{" "}
                        <span className="text-xs text-ink-subtle">
                          ({p.call_count} call{p.call_count === 1 ? "" : "s"})
                        </span>
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </section>
    </AppShell>
  );
}
