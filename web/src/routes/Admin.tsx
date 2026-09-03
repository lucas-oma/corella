import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import {
  ApiError,
  api,
  type CallTypeConfig,
  type CostPeriod,
  type CostSummary,
  type Group,
  type User,
} from "@/lib/api";

const NO_GROUP = "__none__";

const COST_PERIODS: { id: CostPeriod; label: string }[] = [
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "month", label: "Month" },
  { id: "year", label: "Year" },
];

function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

type CallTypeDraft = {
  name: string;
  slug: string;
  report_guidance: string;
  is_default: boolean;
  webhook_enabled: boolean;
  webhook_url: string;
  webhook_method: string;
  webhook_headers: string;
  webhook_body_template: string;
};

const EMPTY_CALL_TYPE_DRAFT: CallTypeDraft = {
  name: "",
  slug: "",
  report_guidance: "",
  is_default: false,
  webhook_enabled: false,
  webhook_url: "",
  webhook_method: "POST",
  webhook_headers: "",
  webhook_body_template: "",
};

function draftFromCallType(ct: CallTypeConfig): CallTypeDraft {
  return {
    name: ct.name,
    slug: ct.slug,
    report_guidance: ct.report_guidance ?? "",
    is_default: ct.is_default,
    webhook_enabled: ct.webhook_enabled,
    webhook_url: ct.webhook_url ?? "",
    webhook_method: ct.webhook_method,
    webhook_headers: "", // write-only, never returned — blank means "leave unchanged" on save
    webhook_body_template: ct.webhook_body_template ?? "",
  };
}

/** Same sub-cent precision rule as MeetingDetail's per-meeting badge —
 * "$0.00" would misleadingly read as free for a genuinely small amount. */
function formatUsd(usd: number): string {
  return usd < 0.01 && usd > 0 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

function formatDayLabel(day: string, period: CostPeriod, index: number, total: number): string {
  // day is ISO date "YYYY-MM-DD"
  const [, month, d] = day.split("-");
  const dayNum = String(Number(d));
  if (period === "7d") {
    const weekday = new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" });
    return `${weekday} ${dayNum}`;
  }
  if (period === "30d" || period === "month") {
    // Label first, last, and roughly weekly ticks so the axis stays readable.
    if (index === 0 || index === total - 1 || index % 7 === 0) {
      return `${Number(month)}/${dayNum}`;
    }
    return "";
  }
  // year — month starts only
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

export default function Admin() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [users, setUsers] = useState<User[] | null>(null);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [costPeriod, setCostPeriod] = useState<CostPeriod>("30d");
  const [callTypes, setCallTypes] = useState<CallTypeConfig[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // "new" for the create form, an id for editing an existing row, null for
  // neither — only one call-type row (or the create form) expanded at once,
  // same pattern as Settings.tsx's "AI models in use" edit affordance.
  const [editingCallTypeId, setEditingCallTypeId] = useState<string | "new" | null>(null);
  const [callTypeDraft, setCallTypeDraft] = useState<CallTypeDraft>(EMPTY_CALL_TYPE_DRAFT);

  const [newGroupName, setNewGroupName] = useState("");
  const [newUser, setNewUser] = useState({
    email: "",
    password: "",
    full_name: "",
    role: "member" as User["role"],
    group_id: NO_GROUP,
  });

  useEffect(() => {
    api.adminListGroups().then(setGroups);
    api.adminListUsers().then(setUsers);
    api.adminListCallTypes().then(setCallTypes);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.adminGetCostSummary(costPeriod).then((data) => {
      if (!cancelled) setCosts(data);
    });
    return () => {
      cancelled = true;
    };
  }, [costPeriod]);

  function groupName(groupId: string | null): string {
    if (!groupId) return "No group";
    return groups?.find((g) => g.id === groupId)?.name ?? "—";
  }

  async function onCreateGroup() {
    const name = newGroupName.trim();
    if (!name) return;
    setError(null);
    setBusy("new-group");
    try {
      const group = await api.adminCreateGroup(name);
      setGroups((prev) => [...(prev ?? []), group]);
      setNewGroupName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create group");
    } finally {
      setBusy(null);
    }
  }

  async function onDeleteGroup(group: Group) {
    setError(null);
    setBusy(group.id);
    try {
      await api.adminDeleteGroup(group.id);
      setGroups((prev) => prev?.filter((g) => g.id !== group.id) ?? null);
      setUsers(
        (prev) =>
          prev?.map((u) => (u.group_id === group.id ? { ...u, group_id: null } : u)) ?? null,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete group");
    } finally {
      setBusy(null);
    }
  }

  async function onCreateUser() {
    if (!newUser.email.trim() || !newUser.password || !newUser.full_name.trim()) return;
    setError(null);
    setBusy("new-user");
    try {
      const user = await api.adminCreateUser({
        email: newUser.email.trim(),
        password: newUser.password,
        full_name: newUser.full_name.trim(),
        role: newUser.role,
        group_id: newUser.group_id === NO_GROUP ? null : newUser.group_id,
      });
      setUsers((prev) => [...(prev ?? []), user]);
      setGroups(
        (prev) =>
          prev?.map((g) => (g.id === user.group_id ? { ...g, member_count: g.member_count + 1 } : g)) ??
          null,
      );
      setNewUser({ email: "", password: "", full_name: "", role: "member", group_id: NO_GROUP });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create user");
    } finally {
      setBusy(null);
    }
  }

  async function onUpdateUser(user: User, patch: { role?: User["role"]; group_id?: string | null }) {
    setError(null);
    setBusy(user.id);
    try {
      const updated = await api.adminUpdateUser(user.id, {
        ...patch,
        clear_group: "group_id" in patch && patch.group_id === null,
      });
      setUsers((prev) => prev?.map((u) => (u.id === user.id ? updated : u)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update user");
    } finally {
      setBusy(null);
    }
  }

  function onNewCallType() {
    setError(null);
    setCallTypeDraft(EMPTY_CALL_TYPE_DRAFT);
    setEditingCallTypeId("new");
  }

  function onEditCallType(ct: CallTypeConfig) {
    setError(null);
    setCallTypeDraft(draftFromCallType(ct));
    setEditingCallTypeId(ct.id);
  }

  function onCallTypeNameChange(name: string) {
    setCallTypeDraft((prev) => ({
      ...prev,
      name,
      // Only auto-derive the slug while creating, and only until the admin
      // has actually typed their own — editing an existing type's name
      // never silently changes its stable slug out from under it.
      slug: editingCallTypeId === "new" ? slugify(name) : prev.slug,
    }));
  }

  async function onSaveCallType() {
    const d = callTypeDraft;
    if (!d.name.trim() || !d.slug.trim()) return;
    setError(null);
    setBusy("call-type-save");
    try {
      const payload = {
        name: d.name.trim(),
        slug: d.slug.trim(),
        report_guidance: d.report_guidance.trim() || null,
        is_default: d.is_default,
        webhook_enabled: d.webhook_enabled,
        webhook_url: d.webhook_url.trim() || null,
        webhook_method: d.webhook_method,
        webhook_body_template: d.webhook_body_template.trim() || null,
        // Omitted entirely (not even as an empty string) unless the admin
        // actually typed something this session — it's write-only and
        // never comes back from the API, so an empty draft field means
        // "leave whatever's already saved alone," not "clear it."
        ...(d.webhook_headers.trim() ? { webhook_headers: d.webhook_headers.trim() } : {}),
      };

      if (editingCallTypeId === "new") {
        const created = await api.adminCreateCallType(payload);
        setCallTypes((prev) => [...(prev ?? []), created]);
      } else if (editingCallTypeId) {
        const updated = await api.adminUpdateCallType(editingCallTypeId, payload);
        setCallTypes((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
      }
      if (d.is_default) {
        setCallTypes((prev) => prev?.map((c) => ({ ...c, is_default: c.slug === d.slug })) ?? null);
      }
      setEditingCallTypeId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save call type");
    } finally {
      setBusy(null);
    }
  }

  async function onDeleteCallType(ct: CallTypeConfig) {
    setError(null);
    setBusy(ct.id);
    try {
      await api.adminDeleteCallType(ct.id);
      setCallTypes((prev) => prev?.filter((c) => c.id !== ct.id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete call type");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-8">
        <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Admin</h1>
        <p className="mt-1 text-sm text-ink-muted">Manage accounts and groups.</p>
      </div>

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      <section className="card p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Groups</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Members of a group share a knowledge base and can see each other's call reports.
          Deleting a group only unassigns its members — their accounts aren't affected.
        </p>

        <ul className="mt-5 divide-y divide-border dark:divide-border-dark">
          {groups === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {groups?.length === 0 && (
            <li className="py-3 text-sm text-ink-muted">No groups yet.</li>
          )}
          {groups?.map((group) => (
            <li key={group.id} className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">{group.name}</p>
                <p className="text-xs text-ink-subtle">
                  {group.member_count} member{group.member_count === 1 ? "" : "s"}
                </p>
              </div>
              <button
                onClick={() => onDeleteGroup(group)}
                disabled={busy === group.id}
                className="text-xs text-ink-subtle hover:text-status-danger"
              >
                {busy === group.id ? "Deleting…" : "Delete"}
              </button>
            </li>
          ))}
        </ul>

        <div className="mt-4 flex gap-2">
          <input
            type="text"
            placeholder="New group name"
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            className="field flex-1 text-sm"
          />
          <button
            onClick={onCreateGroup}
            disabled={busy === "new-group" || !newGroupName.trim()}
            className="btn-secondary shrink-0"
          >
            {busy === "new-group" ? "Creating…" : "Create group"}
          </button>
        </div>
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Users</h2>
        <p className="mt-1 text-sm text-ink-muted">All accounts, their role, and group.</p>

        <ul className="mt-5 divide-y divide-border dark:divide-border-dark">
          {users === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {users?.map((user) => (
            <li key={user.id} className="py-3">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink dark:text-ink-inverted">
                    {user.full_name}
                  </p>
                  <p className="truncate text-xs text-ink-subtle">{user.email}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <select
                    value={user.role}
                    onChange={(e) => onUpdateUser(user, { role: e.target.value as User["role"] })}
                    disabled={busy === user.id}
                    className="field w-auto py-1 text-xs"
                  >
                    <option value="member">Member</option>
                    <option value="admin">Admin</option>
                  </select>
                  <select
                    value={user.group_id ?? NO_GROUP}
                    onChange={(e) =>
                      onUpdateUser(user, {
                        group_id: e.target.value === NO_GROUP ? null : e.target.value,
                      })
                    }
                    disabled={busy === user.id}
                    className="field w-auto py-1 text-xs"
                  >
                    <option value={NO_GROUP}>No group</option>
                    {groups?.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <p className="mt-1 text-xs text-ink-subtle">{groupName(user.group_id)}</p>
            </li>
          ))}
        </ul>

        <div className="mt-5 border-t border-border pt-4 dark:border-border-dark">
          <p className="label mb-2">New user</p>
          <div className="grid grid-cols-2 gap-2">
            <input
              type="text"
              placeholder="Full name"
              value={newUser.full_name}
              onChange={(e) => setNewUser((prev) => ({ ...prev, full_name: e.target.value }))}
              className="field text-sm"
            />
            <input
              type="email"
              placeholder="Email"
              value={newUser.email}
              onChange={(e) => setNewUser((prev) => ({ ...prev, email: e.target.value }))}
              className="field text-sm"
            />
            <input
              type="password"
              placeholder="Password"
              value={newUser.password}
              onChange={(e) => setNewUser((prev) => ({ ...prev, password: e.target.value }))}
              className="field text-sm"
            />
            <select
              value={newUser.role}
              onChange={(e) =>
                setNewUser((prev) => ({ ...prev, role: e.target.value as User["role"] }))
              }
              className="field text-sm"
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
            <select
              value={newUser.group_id}
              onChange={(e) => setNewUser((prev) => ({ ...prev, group_id: e.target.value }))}
              className="field col-span-2 text-sm"
            >
              <option value={NO_GROUP}>No group</option>
              {groups?.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={onCreateUser}
            disabled={
              busy === "new-user" ||
              !newUser.email.trim() ||
              !newUser.password ||
              !newUser.full_name.trim()
            }
            className="btn-secondary mt-3"
          >
            {busy === "new-user" ? "Creating…" : "Create user"}
          </button>
        </div>
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Call types</h2>
        <p className="mt-1 text-sm text-ink-muted">
          What steers the post-call report's focus, and an optional webhook fired once a call of
          that type finishes automatic processing.
        </p>

        <ul className="mt-5 divide-y divide-border dark:divide-border-dark">
          {callTypes === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {callTypes?.map((ct) => (
            <li key={ct.id} className="py-3">
              {editingCallTypeId === ct.id ? (
                <CallTypeForm
                  draft={callTypeDraft}
                  setDraft={setCallTypeDraft}
                  onNameChange={onCallTypeNameChange}
                  onSave={onSaveCallType}
                  onCancel={() => setEditingCallTypeId(null)}
                  saving={busy === "call-type-save"}
                />
              ) : (
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-ink dark:text-ink-inverted">
                      {ct.name}
                      {ct.is_default && <span className="ml-1.5 text-xs text-ink-subtle">(default)</span>}
                    </p>
                    <p className="text-xs text-ink-subtle">
                      {ct.slug}
                      {ct.webhook_enabled && " · webhook configured"}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => onEditCallType(ct)}
                      className="text-xs text-accent hover:underline"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => onDeleteCallType(ct)}
                      disabled={busy === ct.id || ct.is_default}
                      title={ct.is_default ? "Mark a different type as default first" : undefined}
                      className="text-xs text-ink-subtle hover:text-status-danger disabled:opacity-40 disabled:hover:text-ink-subtle"
                    >
                      {busy === ct.id ? "Deleting…" : "Delete"}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>

        <div className="mt-4 border-t border-border pt-4 dark:border-border-dark">
          {editingCallTypeId === "new" ? (
            <CallTypeForm
              draft={callTypeDraft}
              setDraft={setCallTypeDraft}
              onNameChange={onCallTypeNameChange}
              onSave={onSaveCallType}
              onCancel={() => setEditingCallTypeId(null)}
              saving={busy === "call-type-save"}
            />
          ) : (
            <button onClick={onNewCallType} className="btn-secondary">
              New call type
            </button>
          )}
        </div>
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Costs</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Best-effort LLM spend estimate — token usage priced against a point-in-time table
          (app/services/llm/pricing.py), not an authoritative bill.
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
                          Period total{" "}
                          <span className="text-ink-muted">{formatUsd(periodTotal)}</span>
                        </p>
                        <p className="text-xs text-ink-subtle">
                          Peak day{" "}
                          <span className="text-ink-muted">{formatUsd(max)}</span>
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
                <p className="text-sm text-ink-muted">No LLM calls logged yet.</p>
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
          </>
        )}
      </section>
    </AppShell>
  );
}

/** The create-and-edit form for one call type — shared by both flows in
 * the Call types section above (new-row create, and expand-to-edit on an
 * existing row), same pattern as Settings.tsx's "AI models in use" inline
 * edit forms. */
function CallTypeForm({
  draft,
  setDraft,
  onNameChange,
  onSave,
  onCancel,
  saving,
}: {
  draft: CallTypeDraft;
  setDraft: React.Dispatch<React.SetStateAction<CallTypeDraft>>;
  onNameChange: (name: string) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
}) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <input
          type="text"
          placeholder="Name, e.g. Sales call"
          value={draft.name}
          onChange={(e) => onNameChange(e.target.value)}
          className="field text-sm"
        />
        <input
          type="text"
          placeholder="slug"
          value={draft.slug}
          onChange={(e) => setDraft((prev) => ({ ...prev, slug: e.target.value }))}
          className="field text-sm"
        />
      </div>
      <textarea
        placeholder="Report guidance — appended to the post-call report prompt to steer what it focuses on"
        value={draft.report_guidance}
        onChange={(e) => setDraft((prev) => ({ ...prev, report_guidance: e.target.value }))}
        rows={3}
        className="field text-sm"
      />
      <label className="flex items-center gap-2 text-sm text-ink dark:text-ink-inverted">
        <input
          type="checkbox"
          checked={draft.is_default}
          onChange={(e) => setDraft((prev) => ({ ...prev, is_default: e.target.checked }))}
          className="accent-accent"
        />
        Default for new meetings
      </label>

      <div className="border-t border-border pt-2 dark:border-border-dark">
        <label className="flex items-center gap-2 text-sm text-ink dark:text-ink-inverted">
          <input
            type="checkbox"
            checked={draft.webhook_enabled}
            onChange={(e) => setDraft((prev) => ({ ...prev, webhook_enabled: e.target.checked }))}
            className="accent-accent"
          />
          Fire a webhook once a call of this type finishes processing
        </label>

        {draft.webhook_enabled && (
          <div className="mt-2 space-y-2 border-l-2 border-border pl-3 dark:border-border-dark">
            <div className="flex gap-2">
              <select
                value={draft.webhook_method}
                onChange={(e) => setDraft((prev) => ({ ...prev, webhook_method: e.target.value }))}
                className="field w-24 text-sm"
              >
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
              </select>
              <input
                type="text"
                placeholder="https://example.com/webhook"
                value={draft.webhook_url}
                onChange={(e) => setDraft((prev) => ({ ...prev, webhook_url: e.target.value }))}
                className="field flex-1 text-sm"
              />
            </div>
            <textarea
              placeholder='Headers, as JSON — e.g. {"Authorization": "Bearer ..."}. Leave blank to keep whatever is already saved.'
              value={draft.webhook_headers}
              onChange={(e) => setDraft((prev) => ({ ...prev, webhook_headers: e.target.value }))}
              rows={2}
              className="field text-sm"
            />
            <textarea
              placeholder='Body template, as JSON — e.g. {"meeting": "{{meeting_id}}", "summary": "{{summary}}"}'
              value={draft.webhook_body_template}
              onChange={(e) => setDraft((prev) => ({ ...prev, webhook_body_template: e.target.value }))}
              rows={3}
              className="field text-sm"
            />
            <p className="text-xs text-ink-subtle">
              Placeholders (place inside quotes in the JSON): {"{{meeting_id}}"}, {"{{owner_name}}"},{" "}
              {"{{title}}"}, {"{{call_type}}"}, {"{{status}}"}, {"{{summary}}"}, {"{{key_topics}}"},{" "}
              {"{{sentiment}}"}, {"{{coach_score}}"}, {"{{action_items}}"}, {"{{transcript}}"},{" "}
              {"{{created_at}}"}, {"{{duration_seconds}}"}.
            </p>
          </div>
        )}
      </div>

      <div className="flex gap-2 pt-1">
        <button
          onClick={onSave}
          disabled={saving || !draft.name.trim() || !draft.slug.trim()}
          className="btn-secondary"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button onClick={onCancel} className="text-xs text-ink-subtle">
          Cancel
        </button>
      </div>
    </div>
  );
}
