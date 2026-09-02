import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { ApiError, api, type CostSummary, type Group, type User } from "@/lib/api";

const NO_GROUP = "__none__";

/** Same sub-cent precision rule as MeetingDetail's per-meeting badge —
 * "$0.00" would misleadingly read as free for a genuinely small amount. */
function formatUsd(usd: number): string {
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export default function Admin() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [users, setUsers] = useState<User[] | null>(null);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    api.adminGetCostSummary().then(setCosts);
  }, []);

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

            {costs.daily.length > 0 && (
              <div className="mt-6">
                <p className="label mb-2">Daily cost (last {costs.daily.length} days)</p>
                <div className="flex h-24 items-end gap-0.5">
                  {(() => {
                    const max = Math.max(...costs.daily.map((d) => d.total_usd), 0.0001);
                    return costs.daily.map((d) => (
                      <div
                        key={d.day}
                        className="min-h-[2px] flex-1 rounded-t-sm bg-accent/70"
                        style={{ height: `${Math.max((d.total_usd / max) * 100, 2)}%` }}
                        title={`${d.day}: ${formatUsd(d.total_usd)}`}
                      />
                    ));
                  })()}
                </div>
              </div>
            )}

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
