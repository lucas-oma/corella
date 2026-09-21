import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import JsonTemplateField, { beautifyJson } from "@/components/JsonTemplateField";
import PageHeader from "@/components/PageHeader";
import {
  ApiError,
  api,
  type AppSecret,
  type CallTypeConfig,
  type CostPeriod,
  type CostSummary,
  type Group,
  type OrgInvite,
  type OrgMember,
  type OrgRole,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { activeMembership, isOrgOwner } from "@/lib/org";

const EMPTY_NEW_USER = {
  email: "",
  password: "",
  full_name: "",
  role: "member" as Exclude<OrgRole, "owner">,
};

const EMPTY_INVITE = {
  email: "",
  role: "member" as Exclude<OrgRole, "owner">,
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

export default function Organization() {
  const { user: me, refreshUser } = useAuth();
  const confirm = useConfirm();
  const orgId = me?.active_organization_id ?? "";
  const owner = isOrgOwner(me);
  const canDeleteOrg = owner && !activeMembership(me)?.is_instance_org;
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [members, setMembers] = useState<OrgMember[] | null>(null);
  const [invites, setInvites] = useState<OrgInvite[] | null>(null);
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
  const [newUser, setNewUser] = useState(EMPTY_NEW_USER);
  const [newInvite, setNewInvite] = useState(EMPTY_INVITE);
  const [copiedInviteId, setCopiedInviteId] = useState<string | null>(null);
  const [orgName, setOrgName] = useState(me?.organizations.find((o) => o.id === orgId)?.name ?? "");
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);

  useEffect(() => {
    if (!orgId) return;
    api.listOrgGroups(orgId).then(setGroups);
    api.listOrgMembers(orgId).then(setMembers);
    api.listOrgInvites(orgId).then(setInvites);
    api.adminListCallTypes().then(setCallTypes);
    api.adminListSecrets().then(setSecrets);
    setOrgName(me?.organizations.find((o) => o.id === orgId)?.name ?? "");
  }, [orgId]);

  useEffect(() => {
    let cancelled = false;
    api.adminGetCostSummary(costPeriod).then((data) => {
      if (!cancelled) setCosts(data);
    });
    return () => {
      cancelled = true;
    };
  }, [costPeriod]);

  function inviteUrl(token: string) {
    return `${window.location.origin}/invite/${token}`;
  }

  async function copyInviteLink(token: string, inviteId: string) {
    await navigator.clipboard.writeText(inviteUrl(token));
    setCopiedInviteId(inviteId);
    window.setTimeout(() => setCopiedInviteId((current) => (current === inviteId ? null : current)), 2000);
  }

  async function onRenameOrg() {
    const name = orgName.trim();
    if (!name || !orgId) return;
    setError(null);
    setBusy("rename-org");
    try {
      await api.renameOrganization(orgId, name);
      await refreshUser();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't rename organization");
    } finally {
      setBusy(null);
    }
  }

  async function onCreateGroup() {
    const name = newGroupName.trim();
    if (!name || !orgId) return;
    setError(null);
    setBusy("new-group");
    try {
      const group = await api.createOrgGroup(orgId, name);
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
    if (!ok || !orgId) return;
    setError(null);
    setBusy(group.id);
    try {
      await api.deleteOrgGroup(orgId, group.id);
      setGroups((prev) => prev?.filter((g) => g.id !== group.id) ?? null);
      setMembers(
        (prev) =>
          prev?.map((m) => ({ ...m, group_ids: m.group_ids.filter((id) => id !== group.id) })) ?? null,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete group");
    } finally {
      setBusy(null);
    }
  }

  async function onToggleGroupMember(group: Group, member: OrgMember, checked: boolean) {
    if (!orgId) return;
    const nextIds = checked
      ? [...member.group_ids, group.id]
      : member.group_ids.filter((id) => id !== group.id);
    const userIds = (members ?? [])
      .filter((m) => (m.id === member.id ? checked : m.group_ids.includes(group.id)))
      .map((m) => m.id);
    setError(null);
    setBusy(`${group.id}:${member.id}`);
    try {
      await api.setOrgGroupMembers(orgId, group.id, userIds);
      setMembers(
        (prev) =>
          prev?.map((m) => (m.id === member.id ? { ...m, group_ids: nextIds } : m)) ?? null,
      );
      setGroups(
        (prev) =>
          prev?.map((g) =>
            g.id === group.id ? { ...g, member_count: userIds.length } : g,
          ) ?? null,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update group members");
    } finally {
      setBusy(null);
    }
  }

  async function onCreateUser() {
    if (!newUser.email.trim() || !newUser.password || !newUser.full_name.trim() || !orgId) return;
    setError(null);
    setBusy("new-user");
    try {
      const created = await api.createOrgMember(orgId, {
        email: newUser.email.trim(),
        password: newUser.password,
        full_name: newUser.full_name.trim(),
        role: newUser.role,
      });
      setMembers((prev) => [...(prev ?? []), created]);
      setNewUser(EMPTY_NEW_USER);
      setAddingUser(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create member");
    } finally {
      setBusy(null);
    }
  }

  async function onUpdateMemberRole(member: OrgMember, role: OrgRole) {
    if (role === member.role || role === "owner" || !orgId) return;
    const ok = await confirm({
      title: "Change this person's role?",
      description: `${member.full_name} will become ${role === "admin" ? "an admin" : "a member"}.`,
      confirmLabel: "Change role",
    });
    if (!ok) return;
    setError(null);
    setBusy(member.id);
    try {
      const updated = await api.updateOrgMember(orgId, member.id, role);
      setMembers((prev) => prev?.map((m) => (m.id === member.id ? updated : m)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't update member");
    } finally {
      setBusy(null);
    }
  }

  async function onRemoveMember(member: OrgMember) {
    const ok = await confirm({
      title: `Remove ${member.full_name}?`,
      description: "They will lose access to this organization.",
      confirmLabel: "Remove",
      variant: "danger",
    });
    if (!ok || !orgId) return;
    setError(null);
    setBusy(member.id);
    try {
      await api.removeOrgMember(orgId, member.id);
      setMembers((prev) => prev?.filter((m) => m.id !== member.id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove member");
    } finally {
      setBusy(null);
    }
  }

  async function onTransfer(member: OrgMember) {
    const ok = await confirm({
      title: "Transfer ownership?",
      description: `${member.full_name} will become the owner. You will remain an admin.`,
      confirmLabel: "Transfer ownership",
    });
    if (!ok || !orgId) return;
    setError(null);
    setBusy(member.id);
    try {
      await api.transferOwnership(orgId, member.id);
      await refreshUser();
      const next = await api.listOrgMembers(orgId);
      setMembers(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't transfer ownership");
    } finally {
      setBusy(null);
    }
  }

  async function onLeave() {
    const ok = await confirm({
      title: "Leave this organization?",
      description: "You will lose access until someone invites you back.",
      confirmLabel: "Leave",
      variant: "danger",
    });
    if (!ok || !orgId) return;
    setError(null);
    try {
      await api.leaveOrganization(orgId);
      await refreshUser();
      window.location.assign("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't leave organization");
    }
  }

  async function onDeleteOrg() {
    const ok = await confirm({
      title: "Delete this organization?",
      description: "Meetings, knowledge base, and members in this organization will be removed.",
      confirmLabel: "Delete organization",
      variant: "danger",
    });
    if (!ok || !orgId) return;
    try {
      await api.deleteOrganization(orgId);
      await refreshUser();
      window.location.assign("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete organization");
    }
  }

  async function onCreateInvite() {
    if (!newInvite.email.trim() || !orgId) return;
    setError(null);
    setBusy("new-invite");
    try {
      const invite = await api.createOrgInvite(orgId, {
        email: newInvite.email.trim(),
        role: newInvite.role,
      });
      setInvites((prev) => [invite, ...(prev ?? [])]);
      setNewInvite(EMPTY_INVITE);
      if (invite.token) await copyInviteLink(invite.token, invite.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create invite");
    } finally {
      setBusy(null);
    }
  }

  async function onResendInvite(invite: OrgInvite) {
    if (!orgId) return;
    setBusy(invite.id);
    try {
      const next = await api.resendOrgInvite(orgId, invite.id);
      setInvites((prev) => prev?.map((row) => (row.id === next.id ? next : row)) ?? null);
      if (next.token) await copyInviteLink(next.token, next.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't resend invite");
    } finally {
      setBusy(null);
    }
  }

  async function onRevokeInvite(invite: OrgInvite) {
    const ok = await confirm({
      title: "Revoke this invite?",
      description: `The link sent to ${invite.email} will stop working.`,
      confirmLabel: "Revoke",
      variant: "danger",
    });
    if (!ok || !orgId) return;
    setBusy(invite.id);
    try {
      await api.revokeOrgInvite(orgId, invite.id);
      setInvites((prev) => prev?.filter((row) => row.id !== invite.id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't revoke invite");
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
      <PageHeader
        title="Organization"
        subtitle="People, groups, invites, secrets, call types, and spend for this organization."
      />
      <div className="mb-8 grid grid-cols-1 items-end gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <label className="label" htmlFor="org-name">
            Name
          </label>
          <input
            id="org-name"
            className="field"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
          />
        </div>
        <button
          type="button"
          onClick={() => void onRenameOrg()}
          disabled={busy === "rename-org" || !orgName.trim()}
          className="btn-secondary w-full sm:w-auto"
        >
          {busy === "rename-org" ? "Saving…" : "Save name"}
        </button>
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
                    onClick={() => setEditingGroupId(editingGroupId === group.id ? null : group.id)}
                    className="shrink-0 text-xs text-accent hover:underline"
                  >
                    {editingGroupId === group.id ? "Done" : "Members"}
                  </button>
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
            {editingGroupId && (
              <ul className="mt-3 space-y-1 rounded border border-border p-3 dark:border-border-dark">
                {members?.map((member) => {
                  const group = groups?.find((g) => g.id === editingGroupId);
                  if (!group) return null;
                  const checked = member.group_ids.includes(group.id);
                  return (
                    <li key={member.id}>
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={busy === `${group.id}:${member.id}`}
                          onChange={(e) => void onToggleGroupMember(group, member, e.target.checked)}
                        />
                        {member.full_name}
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Members — ≈2/3 */}
          <div className="md:col-span-2">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium text-ink dark:text-ink-inverted">Members</p>
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
                  Add member
                </button>
              )}
            </div>

            <ul className="mt-4 divide-y divide-border dark:divide-border-dark">
              {members === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
              {members?.map((member) => (
                <li key={member.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink dark:text-ink-inverted">
                      {member.full_name}
                    </p>
                    <p className="truncate text-xs text-ink-subtle">{member.email}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {member.role === "owner" ? (
                      <span className="text-xs text-ink-muted">Owner</span>
                    ) : (
                      <select
                        aria-label={`Role for ${member.full_name}`}
                        value={member.role}
                        onChange={(e) =>
                          void onUpdateMemberRole(member, e.target.value as OrgRole)
                        }
                        disabled={busy === member.id}
                        className="field w-28 py-1.5 text-xs"
                      >
                        <option value="member">Member</option>
                        <option value="admin">Admin</option>
                      </select>
                    )}
                    {owner && member.role !== "owner" && (
                      <button
                        type="button"
                        onClick={() => void onTransfer(member)}
                        className="text-xs text-accent hover:underline"
                      >
                        Make owner
                      </button>
                    )}
                    {member.role !== "owner" && (
                      <button
                        type="button"
                        onClick={() => void onRemoveMember(member)}
                        className="text-xs text-ink-subtle hover:text-status-danger"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>

            {addingUser && (
              <form
                autoComplete="off"
                className="mt-4 rounded border border-border bg-surface p-4 dark:border-border-dark dark:bg-surface-dark"
                onSubmit={(e) => e.preventDefault()}
              >
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">New member</p>
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
                        setNewUser((prev) => ({
                          ...prev,
                          role: e.target.value as Exclude<OrgRole, "owner">,
                        }))
                      }
                      className="field text-sm"
                    >
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
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
                    {busy === "new-user" ? "Creating…" : "Create member"}
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
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">Invites</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Copy a link to invite someone. They can accept even when public registration is closed.
        </p>
        <div className="mt-4 grid grid-cols-1 items-end gap-3 sm:grid-cols-[minmax(0,1fr)_10rem_auto]">
          <div className="min-w-0">
            <label className="label" htmlFor="invite-email">
              Email
            </label>
            <input
              id="invite-email"
              type="email"
              className="field"
              value={newInvite.email}
              onChange={(e) => setNewInvite((prev) => ({ ...prev, email: e.target.value }))}
            />
          </div>
          <div>
            <label className="label" htmlFor="invite-role">
              Role
            </label>
            <select
              id="invite-role"
              className="field"
              value={newInvite.role}
              onChange={(e) =>
                setNewInvite((prev) => ({
                  ...prev,
                  role: e.target.value as Exclude<OrgRole, "owner">,
                }))
              }
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <button
            type="button"
            onClick={() => void onCreateInvite()}
            disabled={busy === "new-invite" || !newInvite.email.trim()}
            className="btn-secondary w-full sm:w-auto"
          >
            {busy === "new-invite" ? "Creating…" : "Create invite"}
          </button>
        </div>
        <ul className="mt-4 divide-y divide-border dark:divide-border-dark">
          {invites === null && <li className="py-3 text-sm text-ink-muted">Loading…</li>}
          {invites?.length === 0 && (
            <li className="py-3 text-sm text-ink-muted">No pending invites.</li>
          )}
          {invites?.map((invite) => (
            <li key={invite.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div>
                <p className="text-sm font-medium text-ink dark:text-ink-inverted">{invite.email}</p>
                <p className="text-xs text-ink-subtle">
                  {invite.role} · expires {new Date(invite.expires_at).toLocaleDateString()}
                </p>
              </div>
              <div className="flex items-center gap-3">
                {invite.token && (
                  <button
                    type="button"
                    onClick={() => void copyInviteLink(invite.token!, invite.id)}
                    className="text-xs text-accent hover:underline"
                  >
                    {copiedInviteId === invite.id ? "Copied" : "Copy link"}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void onResendInvite(invite)}
                  className="text-xs text-accent hover:underline"
                >
                  Resend
                </button>
                <button
                  type="button"
                  onClick={() => void onRevokeInvite(invite)}
                  className="text-xs text-ink-subtle hover:text-status-danger"
                >
                  Revoke
                </button>
              </div>
            </li>
          ))}
        </ul>
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

      {(!owner || canDeleteOrg) && (
        <div className="mt-8 flex justify-end">
          {!owner && (
            <button type="button" onClick={() => void onLeave()} className="text-sm text-status-danger">
              Leave organization
            </button>
          )}
          {canDeleteOrg && (
            <button type="button" onClick={() => void onDeleteOrg()} className="text-sm text-status-danger">
              Delete organization
            </button>
          )}
        </div>
      )}
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
              X-Corella-User-Id, X-Corella-Org-Id.
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
              X-Corella-User-Id, X-Corella-Org-Id.
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
