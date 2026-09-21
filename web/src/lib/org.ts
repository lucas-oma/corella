import type { OrgMembership, OrgRole, User } from "@/lib/api";

export function activeMembership(user: User | null | undefined): OrgMembership | undefined {
  if (!user?.active_organization_id) return undefined;
  return user.organizations.find((org) => org.id === user.active_organization_id);
}

export function isOrgAdmin(user: User | null | undefined): boolean {
  const role = activeMembership(user)?.role;
  return role === "owner" || role === "admin";
}

export function isOrgOwner(user: User | null | undefined): boolean {
  return activeMembership(user)?.role === "owner";
}

export function roleLabel(role: OrgRole): string {
  if (role === "owner") return "Owner";
  if (role === "admin") return "Admin";
  return "Member";
}
