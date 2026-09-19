import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import JsonTemplateField, { beautifyJson } from "@/components/JsonTemplateField";
import {
  ApiError,
  api,
  type AppSecret,
  type CallTypeConfig,
  type CostPeriod,
  type CostSummary,
  type Group,
  type User,
} from "@/lib/api";
import { useConfirm } from "@/lib/confirm";

const NO_GROUP = "__none__";

const EMPTY_NEW_USER = {
  email: "",
  password: "",
  full_name: "",
  role: "member" as const,
  group_id: NO_GROUP,
};

/** Chrome/1Password treat type=password + a neighboring email/text field
 * as a login form and fill the signed-in admin's credentials. Keep
 * type=text and mask password-ish values in CSS instead. */
const AUTOFILL_GUARD = {
  autoComplete: "off" as const,
  autoCorrect: "off" as const,
  autoCapitalize: "off" as const,
  spellCheck: false,
  "data-1p-ignore": true,
  "data-lpignore": "true",
  "data-form-type": "other",
};

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

  pre_call_enabled: boolean;
  pre_call_url: string;
  pre_call_method: string;
  pre_call_headers: string;
  pre_call_body_template: string;
  pre_call_use_as_context: boolean;
  pre_call_async: boolean;

  post_call_enabled: boolean;
  post_call_url: string;
  post_call_method: string;
  post_call_headers: string;
  post_call_body_template: string;
  post_call_send_full_payload: boolean;
  post_call_async: boolean;
};

const EMPTY_CALL_TYPE_DRAFT: CallTypeDraft = {
  name: "",
  slug: "",
  report_guidance: "",
  is_default: false,

  pre_call_enabled: false,
  pre_call_url: "",
  pre_call_method: "GET",
  pre_call_headers: "",
  pre_call_body_template: "",
  pre_call_use_as_context: false,
  pre_call_async: false,

  post_call_enabled: false,
  post_call_url: "",
  post_call_method: "POST",
  post_call_headers: "",
  post_call_body_template: "",
  post_call_send_full_payload: false,
  post_call_async: false,
};

function draftFromCallType(ct: CallTypeConfig): CallTypeDraft {
  return {
    name: ct.name,
    slug: ct.slug,
    report_guidance: ct.report_guidance ?? "",
    is_default: ct.is_default,

    pre_call_enabled: ct.pre_call_enabled,
    pre_call_url: ct.pre_call_url ?? "",
    pre_call_method: ct.pre_call_method,
    pre_call_headers: beautifyJson(ct.pre_call_headers ?? ""),
    pre_call_body_template: beautifyJson(ct.pre_call_body_template ?? ""),
    pre_call_use_as_context: ct.pre_call_use_as_context,
    pre_call_async: ct.pre_call_async,

    post_call_enabled: ct.post_call_enabled,
    post_call_url: ct.post_call_url ?? "",
    post_call_method: ct.post_call_method,
    post_call_headers: beautifyJson(ct.post_call_headers ?? ""),
    post_call_body_template: beautifyJson(ct.post_call_body_template ?? ""),
    post_call_send_full_payload: ct.post_call_send_full_payload,
    post_call_async: ct.post_call_async,
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
  const confirm = useConfirm();
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [users, setUsers] = useState<User[] | null>(null);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [costPeriod, setCostPeriod] = useState<CostPeriod>("30d");
  const [callTypes, setCallTypes] = useState<CallTypeConfig[] | null>(null);
  const [secrets, setSecrets] = useState<AppSecret[] | null>(null);
  const [newSecretName, setNewSecretName] = useState("");
  const [newSecretValue, setNewSecretValue] = useState("");
  const [creatingSecret, setCreatingSecret] = useState(false);
  const [editingSecretId, setEditingSecretId] = useState<string | null>(null);
  const [secretDraftName, setSecretDraftName] = useState("");
  const [secretDraftValue, setSecretDraftValue] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // "new" for the create form, an id for editing an existing row, null for
  // neither — only one call-type row (or the create form) expanded at once,
  // same pattern as Settings.tsx's "AI models in use" edit affordance.
  const [editingCallTypeId, setEditingCallTypeId] = useState<string | "new" | null>(null);
  const [callTypeDraft, setCallTypeDraft] = useState<CallTypeDraft>(EMPTY_CALL_TYPE_DRAFT);

  const [newGroupName, setNewGroupName] = useState("");
  const [addingGroup, setAddingGroup] = useState(false);
  const [addingUser, setAddingUser] = useState(false);
  const [newUser, setNewUser] = useState<{
    email: string;
    password: string;
    full_name: string;
    role: User["role"];
    group_id: string;
  }>(EMPTY_NEW_USER);

  useEffect(() => {
    api.adminListGroups().then(setGroups);
    api.adminListUsers().then(setUsers);
    api.adminListCallTypes().then(setCallTypes);
    api.adminListSecrets().then(setSecrets);
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

  async function onCreateGroup() {
    const name = newGroupName.trim();
    if (!name) return;
    setError(null);
    setBusy("new-group");
    try {
      const group = await api.adminCreateGroup(name);
      setGroups((prev) => [...(prev ?? []), group]);
      setNewGroupName("");
      setAddingGroup(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create group");
    } finally {
      setBusy(null);
    }
  }

  async function onDeleteGroup(group: Group) {
    const ok = await confirm({
      title: "Delete this group?",
      description: "Members will be unassigned. Their accounts aren't affected.",
      confirmLabel: "Delete group",
      variant: "danger",
    });
    if (!ok) return;
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
      setNewUser(EMPTY_NEW_USER);
      setAddingUser(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create user");
    } finally {
      setBusy(null);
    }
  }

  async function onUpdateUser(user: User, patch: { role?: User["role"]; group_id?: string | null }) {
    if (patch.role && patch.role !== user.role) {
      const ok = await confirm({
        title: "Change this person's role?",
        description:
          patch.role === "admin"
            ? `${user.full_name} will become an admin.`
            : `${user.full_name} will no longer be an admin.`,
        confirmLabel: "Change role",
      });
      if (!ok) return;
    }
    if ("group_id" in patch && patch.group_id === null && user.group_id) {
      const ok = await confirm({
        title: "Remove from this group?",
        description: `${user.full_name} will lose group knowledge-base and report access.`,
        confirmLabel: "Remove",
      });
      if (!ok) return;
    }
    setError(null);
    setBusy(user.id);
    const previousGroupId = user.group_id;
    try {
      const updated = await api.adminUpdateUser(user.id, {
        ...patch,
        clear_group: "group_id" in patch && patch.group_id === null,
      });
      setUsers((prev) => prev?.map((u) => (u.id === user.id ? updated : u)) ?? null);
      if ("group_id" in patch && patch.group_id !== previousGroupId) {
        setGroups(
          (prev) =>
            prev?.map((g) => {
              let count = g.member_count;
              if (g.id === previousGroupId) count = Math.max(0, count - 1);
              if (g.id === patch.group_id) count += 1;
              return count === g.member_count ? g : { ...g, member_count: count };
            }) ?? null,
        );
      }
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

        pre_call_enabled: d.pre_call_enabled,
        pre_call_url: d.pre_call_url.trim() || null,
        pre_call_method: d.pre_call_method,
        pre_call_body_template: beautifyJson(d.pre_call_body_template.trim()) || null,
        pre_call_headers: beautifyJson(d.pre_call_headers.trim()) || null,
        pre_call_use_as_context: d.pre_call_use_as_context,
        pre_call_async: d.pre_call_async,

        post_call_enabled: d.post_call_enabled,
        post_call_url: d.post_call_url.trim() || null,
        post_call_method: d.post_call_method,
        post_call_headers: beautifyJson(d.post_call_headers.trim()) || null,
        post_call_body_template: beautifyJson(d.post_call_body_template.trim()) || null,
        post_call_send_full_payload: d.post_call_send_full_payload,
        post_call_async: d.post_call_async,
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
    const ok = await confirm({
      title: "Delete this call type?",
      description: `${ct.name} will be removed. Existing meetings keep the type they were recorded with.`,
      confirmLabel: "Delete call type",
      variant: "danger",
    });
    if (!ok) return;
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

  async function onCreateSecret() {
    const name = newSecretName.trim();
    if (!name || !newSecretValue) return;
    setError(null);
    setCreatingSecret(true);
    try {
      const created = await api.adminCreateSecret({ name, value: newSecretValue });
      setSecrets((prev) => [...(prev ?? []), created].sort((a, b) => a.name.localeCompare(b.name)));
      setNewSecretName("");
      setNewSecretValue("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create secret");
    } finally {
      setCreatingSecret(false);
    }
  }

  function onEditSecret(secret: AppSecret) {
    setError(null);
    setEditingSecretId(secret.id);
    setSecretDraftName(secret.name);
    setSecretDraftValue("");
  }

  async function onSaveSecret(secret: AppSecret) {
    const name = secretDraftName.trim();
    if (!name) return;
    const payload: { name?: string; value?: string } = {};
    if (name !== secret.name) payload.name = name;
    if (secretDraftValue) payload.value = secretDraftValue;
    if (!payload.name && !payload.value) {
      setEditingSecretId(null);
      return;
    }
    setError(null);
    setBusy(secret.id);
    try {
      const updated = await api.adminUpdateSecret(secret.id, payload);
      setSecrets(
        (prev) =>
          prev?.map((row) => (row.id === updated.id ? updated : row)).sort((a, b) => a.name.localeCompare(b.name)) ??
          null,
      );
      setEditingSecretId(null);
      setSecretDraftValue("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update secret");
    } finally {
      setBusy(null);
    }
  }

  async function onDeleteSecret(secret: AppSecret) {
    const ok = await confirm({
      title: `Delete "${secret.name}"?`,
      description: `Call-type headers that use {{secret.${secret.name}}} will stop resolving until you point them at another secret.`,
      confirmLabel: "Delete secret",
      variant: "danger",
    });
    if (!ok) return;
    setError(null);
    setBusy(secret.id);
    try {
      await api.adminDeleteSecret(secret.id);
      setSecrets((prev) => prev?.filter((row) => row.id !== secret.id) ?? null);
      if (editingSecretId === secret.id) setEditingSecretId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete secret");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-8">
        <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Admin</h1>
        <p className="mt-1 text-sm text-ink-muted">Accounts, groups, secrets, call types, and spend.</p>
      </div>

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      <section className="card p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">People</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Members of a group share a knowledge base (only admins upload and maintain it) and can
          see each other&apos;s call reports. Deleting a group only unassigns its members — their
          accounts aren&apos;t affected.
        </p>

        <div className="mt-6 grid grid-cols-1 gap-8 md:grid-cols-3">
          {/* Groups — ≈1/3 */}
          <div className="md:col-span-1 md:border-r md:border-border md:pr-8 dark:md:border-border-dark">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium text-ink dark:text-ink-inverted">Groups</p>
              {!addingGroup && (
                <button
                  type="button"
                  onClick={() => {
                    setError(null);
                    setNewGroupName("");
                    setAddingGroup(true);
                  }}
                  className="text-xs text-accent hover:underline"
                >
                  Add
                </button>
              )}
            </div>

            {addingGroup && (
              <div className="mt-4 rounded border border-border bg-surface p-4 dark:border-border-dark dark:bg-surface-dark">
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">New group</p>
                <label className="label mt-3" htmlFor="new-group-name">
                  Group name
                </label>
                <input
                  id="new-group-name"
                  type="text"
                  autoFocus
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onCreateGroup();
                  }}
                  className="field text-sm"
                />
                <div className="mt-3 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={onCreateGroup}
                    disabled={busy === "new-group" || !newGroupName.trim()}
                    className="btn-secondary"
                  >
                    {busy === "new-group" ? "Creating…" : "Create group"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setAddingGroup(false);
                      setNewGroupName("");
                    }}
                    className="text-xs text-ink-muted hover:text-ink dark:hover:text-ink-inverted"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <ul className="mt-4 divide-y divide-border dark:divide-border-dark">
              {groups === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
              {groups?.length === 0 && !addingGroup && (
                <li className="py-3 text-sm text-ink-muted">No groups yet.</li>
              )}
              {groups?.map((group) => (
                <li key={group.id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink dark:text-ink-inverted">
                      {group.name}
                    </p>
                    <p className="text-xs text-ink-subtle">
                      {group.member_count} member{group.member_count === 1 ? "" : "s"}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => onDeleteGroup(group)}
                    disabled={busy === group.id}
                    className="shrink-0 text-xs text-ink-subtle hover:text-status-danger"
                  >
                    {busy === group.id ? "Deleting…" : "Delete"}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Users — ≈2/3 */}
          <div className="md:col-span-2">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium text-ink dark:text-ink-inverted">Users</p>
              {!addingUser && (
                <button
                  type="button"
                  onClick={() => {
                    setError(null);
                    setNewUser(EMPTY_NEW_USER);
                    setAddingUser(true);
                  }}
                  className="text-xs text-accent hover:underline"
                >
                  Add user
                </button>
              )}
            </div>

            {users !== null && users.length > 0 && (
              <div className="mt-4 hidden grid-cols-[minmax(0,1fr)_7rem_10rem] gap-3 px-0 text-xs text-ink-subtle sm:grid">
                <span>Name</span>
                <span>Role</span>
                <span>Group</span>
              </div>
            )}

            <ul className="mt-1 divide-y divide-border dark:divide-border-dark sm:mt-0">
              {users === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
              {users?.length === 0 && !addingUser && (
                <li className="py-3 text-sm text-ink-muted">No users yet.</li>
              )}
              {users?.map((user) => (
                <li
                  key={user.id}
                  className="grid grid-cols-1 items-center gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_7rem_10rem] sm:gap-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink dark:text-ink-inverted">
                      {user.full_name}
                    </p>
                    <p className="truncate text-xs text-ink-subtle">{user.email}</p>
                  </div>
                  <select
                    aria-label={`Role for ${user.full_name}`}
                    value={user.role}
                    onChange={(e) => onUpdateUser(user, { role: e.target.value as User["role"] })}
                    disabled={busy === user.id}
                    className="field w-full py-1.5 text-xs"
                  >
                    <option value="member">Member</option>
                    <option value="admin">Admin</option>
                  </select>
                  <select
                    aria-label={`Group for ${user.full_name}`}
                    value={user.group_id ?? NO_GROUP}
                    onChange={(e) =>
                      onUpdateUser(user, {
                        group_id: e.target.value === NO_GROUP ? null : e.target.value,
                      })
                    }
                    disabled={busy === user.id}
                    className="field w-full py-1.5 text-xs"
                  >
                    <option value={NO_GROUP}>No group</option>
                    {groups?.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                </li>
              ))}
            </ul>

            {addingUser && (
              <form
                autoComplete="off"
                className="mt-4 rounded border border-border bg-surface p-4 dark:border-border-dark dark:bg-surface-dark"
                onSubmit={(e) => e.preventDefault()}
              >
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">New user</p>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <label className="label" htmlFor="new-user-name">
                      Full name
                    </label>
                    <input
                      {...AUTOFILL_GUARD}
                      id="new-user-name"
                      type="text"
                      name="corella-new-user-name"
                      autoFocus
                      value={newUser.full_name}
                      onChange={(e) => setNewUser((prev) => ({ ...prev, full_name: e.target.value }))}
                      className="field text-sm"
                    />
                  </div>
                  <div>
                    <label className="label" htmlFor="new-user-email">
                      Email
                    </label>
                    <input
                      {...AUTOFILL_GUARD}
                      id="new-user-email"
                      type="text"
                      inputMode="email"
                      name="corella-new-user-email"
                      value={newUser.email}
                      onChange={(e) => setNewUser((prev) => ({ ...prev, email: e.target.value }))}
                      className="field text-sm"
                    />
                  </div>
                  <div>
                    <label className="label" htmlFor="new-user-password">
                      Password
                    </label>
                    <input
                      {...AUTOFILL_GUARD}
                      id="new-user-password"
                      type="text"
                      name="corella-new-user-password"
                      value={newUser.password}
                      onChange={(e) => setNewUser((prev) => ({ ...prev, password: e.target.value }))}
                      className="field text-sm [-webkit-text-security:disc]"
                    />
                  </div>
                  <div>
                    <label className="label" htmlFor="new-user-role">
                      Role
                    </label>
                    <select
                      id="new-user-role"
                      value={newUser.role}
                      onChange={(e) =>
                        setNewUser((prev) => ({ ...prev, role: e.target.value as User["role"] }))
                      }
                      className="field text-sm"
                    >
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
                    </select>
                  </div>
                  <div className="sm:col-span-2">
                    <label className="label" htmlFor="new-user-group">
                      Group
                    </label>
                    <select
                      id="new-user-group"
                      value={newUser.group_id}
                      onChange={(e) => setNewUser((prev) => ({ ...prev, group_id: e.target.value }))}
                      className="field text-sm"
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
                <div className="mt-4 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={onCreateUser}
                    disabled={
                      busy === "new-user" ||
                      !newUser.email.trim() ||
                      !newUser.password ||
                      !newUser.full_name.trim()
                    }
                    className="btn-secondary"
                  >
                    {busy === "new-user" ? "Creating…" : "Create user"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setAddingUser(false);
                      setNewUser(EMPTY_NEW_USER);
                    }}
                    className="text-xs text-ink-muted hover:text-ink dark:hover:text-ink-inverted"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Secrets</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Named values for call-type pre/post headers. The name is visible on the type; the value
          is stored encrypted and never shown again. Reference as{" "}
          <code className="text-[11px]">{"{{secret.NAME}}"}</code>.
        </p>

        <form
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault();
            onCreateSecret();
          }}
          className="mb-4 mt-5 flex flex-wrap items-center gap-2"
        >
          <SecretNameField
            value={newSecretName}
            onChange={setNewSecretName}
            placeholder="NAME, e.g. WEBHOOK_SECRET"
          />
          <SecretValueField value={newSecretValue} onChange={setNewSecretValue} placeholder="Value" />
          <button
            type="submit"
            disabled={creatingSecret || !newSecretName.trim() || !newSecretValue}
            className="btn-secondary shrink-0"
          >
            {creatingSecret ? "Saving…" : "Add secret"}
          </button>
        </form>

        {secrets === null && <p className="text-sm text-ink-muted">Loading…</p>}
        {secrets?.length === 0 && <p className="text-sm text-ink-muted">No secrets yet.</p>}
        {secrets && secrets.length > 0 && (
          <ul className="divide-y divide-border dark:divide-border-dark">
            {secrets.map((secret) => (
              <li key={secret.id} className="py-2.5">
                {editingSecretId === secret.id ? (
                  <form
                    autoComplete="off"
                    onSubmit={(e) => {
                      e.preventDefault();
                      onSaveSecret(secret);
                    }}
                    className="space-y-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <SecretNameField value={secretDraftName} onChange={setSecretDraftName} />
                      <SecretValueField
                        value={secretDraftValue}
                        onChange={setSecretDraftValue}
                        placeholder="New value — leave blank to keep"
                      />
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="submit"
                        disabled={busy === secret.id || !secretDraftName.trim()}
                        className="btn-secondary"
                      >
                        {busy === secret.id ? "Saving…" : "Save"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingSecretId(null)}
                        className="text-xs text-ink-subtle"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm text-ink dark:text-ink-inverted">{secret.name}</p>
                      <p className="text-xs text-ink-subtle">
                        <code>{`{{secret.${secret.name}}}`}</code>
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <button
                        type="button"
                        onClick={() => onEditSecret(secret)}
                        className="text-xs text-accent hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => onDeleteSecret(secret)}
                        disabled={busy === secret.id}
                        className="text-xs text-ink-subtle hover:text-status-danger"
                      >
                        {busy === secret.id ? "…" : "Delete"}
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card mt-6 p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Call types</h2>
        <p className="mt-1 text-sm text-ink-muted">
          What steers the post-call report&apos;s focus, and optional APIs fired before a call of
          this type starts and after it finishes automatic processing. Put tokens in Secrets above
          and reference them in headers as {"{{secret.NAME}}"}.
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
                  secrets={secrets ?? []}
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
                      {ct.pre_call_enabled && " · pre-call configured"}
                      {ct.post_call_enabled && " · post-call configured"}
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
              secrets={secrets ?? []}
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
              {/* Copilot (LLM) calls and Deepgram STT usage share this same
                  ledger (Phase W5) — this split is what keeps that visible
                  instead of one opaque total. */}
              <p className="label mb-2">By provider</p>
              {costs.by_provider.length === 0 ? (
                <p className="text-sm text-ink-muted">No calls logged yet.</p>
              ) : (
                <ul className="divide-y divide-border dark:divide-border-dark">
                  {costs.by_provider.map((p) => (
                    <li key={p.provider} className="flex items-center justify-between py-2">
                      <p className="text-sm capitalize text-ink dark:text-ink-inverted">
                        {p.provider}
                      </p>
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

/** The create-and-edit form for one call type — shared by both flows in
 * the Call types section above (new-row create, and expand-to-edit on an
 * existing row), same pattern as Settings.tsx's "AI models in use" inline
 * edit forms. */
function SecretNameField({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      {...AUTOFILL_GUARD}
      type="text"
      name="corella-secret-name"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="field min-w-48 flex-1 text-sm"
    />
  );
}

function SecretValueField({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative min-w-48 flex-1">
      <input
        {...AUTOFILL_GUARD}
        type="text"
        name="corella-secret-value"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`field w-full pr-10 text-sm ${visible ? "" : "[-webkit-text-security:disc]"}`}
      />
      <button
        type="button"
        onClick={() => setVisible((prev) => !prev)}
        aria-label={visible ? "Hide secret" : "Show secret"}
        className="absolute inset-y-0 right-0 flex items-center px-2.5 text-ink-subtle hover:text-ink dark:hover:text-ink-inverted"
      >
        {visible ? <EyeOffIcon /> : <EyeIcon />}
      </button>
    </div>
  );
}

function EyeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.75" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 3l18 18M10.5 10.7a3 3 0 0 0 4.2 4.2M9.5 5.2A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a17.3 17.3 0 0 1-3.2 4.4M6.1 6.2A17.6 17.6 0 0 0 2 12s3.5 7 10 7c1.4 0 2.7-.3 3.8-.7"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SecretHeaderHint({ secrets }: { secrets: AppSecret[] }) {
  if (secrets.length === 0) {
    return (
      <p className="text-xs text-ink-subtle">
        Add a secret in Secrets above, then use {"{{secret.NAME}}"} as a header value.
      </p>
    );
  }
  return (
    <p className="text-xs text-ink-subtle">
      Use {"{{secret.NAME}}"}. Available:{" "}
      {secrets.map((s, i) => (
        <span key={s.id}>
          {i > 0 && ", "}
          <code>{`{{secret.${s.name}}}`}</code>
        </span>
      ))}
    </p>
  );
}

function CallTypeForm({
  draft,
  setDraft,
  onNameChange,
  onSave,
  onCancel,
  saving,
  secrets,
}: {
  draft: CallTypeDraft;
  setDraft: React.Dispatch<React.SetStateAction<CallTypeDraft>>;
  onNameChange: (name: string) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  secrets: AppSecret[];
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
            checked={draft.pre_call_enabled}
            onChange={(e) => setDraft((prev) => ({ ...prev, pre_call_enabled: e.target.checked }))}
            className="accent-accent"
          />
          Call an API before a call of this type starts
        </label>

        {draft.pre_call_enabled && (
          <div className="mt-2 space-y-2 border-l-2 border-border pl-3 dark:border-border-dark">
            <div className="flex gap-2">
              <select
                value={draft.pre_call_method}
                onChange={(e) => setDraft((prev) => ({ ...prev, pre_call_method: e.target.value }))}
                className="field w-24 text-sm"
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
              </select>
              <input
                type="text"
                placeholder="https://example.com/lookup"
                value={draft.pre_call_url}
                onChange={(e) => setDraft((prev) => ({ ...prev, pre_call_url: e.target.value }))}
                className="field flex-1 text-sm"
              />
            </div>
            <JsonTemplateField
              placeholder={'{\n  "X-Corella-Webhook-Secret": "{{secret.WEBHOOK_SECRET}}"\n}'}
              value={draft.pre_call_headers}
              onChange={(value) => setDraft((prev) => ({ ...prev, pre_call_headers: value }))}
            />
            <SecretHeaderHint secrets={secrets} />
            <JsonTemplateField
              placeholder={'{\n  "title": "{{title}}"\n}'}
              value={draft.pre_call_body_template}
              onChange={(value) => setDraft((prev) => ({ ...prev, pre_call_body_template: value }))}
            />
            <label className="flex items-center gap-2 text-sm text-ink dark:text-ink-inverted">
              <input
                type="checkbox"
                checked={draft.pre_call_use_as_context}
                onChange={(e) => setDraft((prev) => ({ ...prev, pre_call_use_as_context: e.target.checked }))}
                className="accent-accent"
              />
              Use the response as conversation context, alongside the knowledge base
            </label>
            <label className="flex items-start gap-2 text-sm text-ink dark:text-ink-inverted">
              <input
                type="checkbox"
                checked={draft.pre_call_async}
                onChange={(e) => setDraft((prev) => ({ ...prev, pre_call_async: e.target.checked }))}
                className="mt-0.5 accent-accent"
              />
              <span>
                Don&apos;t wait for this request (async)
                <span className="mt-0.5 block text-xs text-ink-subtle">
                  Start the meeting immediately. If context is enabled, copilot picks it up on the next
                  cycle. Off (default) waits up to a few seconds so context exists before anyone joins.
                </span>
              </span>
            </label>
            <p className="text-xs text-ink-subtle">
              Every request carries these headers automatically: X-Corella-App-Url, X-Corella-Meeting-Id,
              X-Corella-User-Id.
            </p>
          </div>
        )}
      </div>

      <div className="border-t border-border pt-2 dark:border-border-dark">
        <label className="flex items-center gap-2 text-sm text-ink dark:text-ink-inverted">
          <input
            type="checkbox"
            checked={draft.post_call_enabled}
            onChange={(e) => setDraft((prev) => ({ ...prev, post_call_enabled: e.target.checked }))}
            className="accent-accent"
          />
          Call an API once a call of this type finishes processing
        </label>

        {draft.post_call_enabled && (
          <div className="mt-2 space-y-2 border-l-2 border-border pl-3 dark:border-border-dark">
            <div className="flex gap-2">
              <select
                value={draft.post_call_method}
                onChange={(e) => setDraft((prev) => ({ ...prev, post_call_method: e.target.value }))}
                className="field w-24 text-sm"
              >
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
              </select>
              <input
                type="text"
                placeholder="https://example.com/webhook"
                value={draft.post_call_url}
                onChange={(e) => setDraft((prev) => ({ ...prev, post_call_url: e.target.value }))}
                className="field flex-1 text-sm"
              />
            </div>
            <JsonTemplateField
              placeholder={'{\n  "X-Corella-Webhook-Secret": "{{secret.WEBHOOK_SECRET}}"\n}'}
              value={draft.post_call_headers}
              onChange={(value) => setDraft((prev) => ({ ...prev, post_call_headers: value }))}
            />
            <SecretHeaderHint secrets={secrets} />
            <label className="flex items-center gap-2 text-sm text-ink dark:text-ink-inverted">
              <input
                type="checkbox"
                checked={draft.post_call_send_full_payload}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, post_call_send_full_payload: e.target.checked }))
                }
                className="accent-accent"
              />
              Send everything (transcript, report, live-copilot suggestions/blockers/score timeline, action
              items) instead of a custom body
            </label>
            <JsonTemplateField
              placeholder={'{\n  "meeting": "{{meeting_id}}",\n  "summary": "{{summary}}"\n}'}
              value={draft.post_call_body_template}
              onChange={(value) => setDraft((prev) => ({ ...prev, post_call_body_template: value }))}
              disabled={draft.post_call_send_full_payload}
            />
            {!draft.post_call_send_full_payload && (
              <p className="text-xs text-ink-subtle">
                Placeholders (place inside quotes in the JSON): {"{{meeting_id}}"}, {"{{owner_name}}"},{" "}
                {"{{title}}"}, {"{{call_type}}"}, {"{{status}}"}, {"{{summary}}"}, {"{{key_topics}}"},{" "}
                {"{{sentiment}}"}, {"{{notable_quotes}}"}, {"{{coach_score}}"}, {"{{estimated_cost_usd}}"},{" "}
                {"{{talk_ratio}}"}, {"{{action_items}}"}, {"{{copilot_insights}}"}, {"{{transcript}}"},{" "}
                {"{{created_at}}"}, {"{{duration_seconds}}"}, or {"{{full_payload}}"} for everything at once.
              </p>
            )}
            <label className="flex items-start gap-2 text-sm text-ink dark:text-ink-inverted">
              <input
                type="checkbox"
                checked={draft.post_call_async}
                onChange={(e) => setDraft((prev) => ({ ...prev, post_call_async: e.target.checked }))}
                className="mt-0.5 accent-accent"
              />
              <span>
                Don&apos;t wait for this request (async)
                <span className="mt-0.5 block text-xs text-ink-subtle">
                  Finish the report without waiting for this API. Off (default) sends it in the same
                  worker step after the report is saved.
                </span>
              </span>
            </label>
            <p className="text-xs text-ink-subtle">
              Every request carries these headers automatically: X-Corella-App-Url, X-Corella-Meeting-Id,
              X-Corella-User-Id.
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
