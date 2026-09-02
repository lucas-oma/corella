import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { ApiError, api, type Group, type User } from "@/lib/api";

const NO_GROUP = "__none__";

export default function Admin() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [users, setUsers] = useState<User[] | null>(null);
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
    </AppShell>
  );
}
