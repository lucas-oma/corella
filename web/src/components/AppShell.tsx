import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import logoDark from "@/assets/logo-dark.svg";
import logoLight from "@/assets/logo-light.svg";
import Footer from "@/components/Footer";
import UserAvatar from "@/components/UserAvatar";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";
import { meetingsListPath } from "@/lib/meetingsTab";
import { activeMembership, isOrgAdmin } from "@/lib/org";
import { useAuthConfig } from "@/lib/useAuthConfig";

const NAV = [
  { to: "/dashboard", label: "Meetings" },
  { to: "/knowledge-base", label: "Knowledge base" },
  { to: "/settings", label: "Settings" },
];

function navClass(active: boolean) {
  return `rounded px-3 py-1.5 text-sm transition-colors ${
    active
      ? "bg-accent text-accent-foreground"
      : "text-ink-muted hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
  }`;
}

function OrgLabel({
  user,
  membership,
  onSwitch,
}: {
  user: NonNullable<ReturnType<typeof useAuth>["user"]>;
  membership: ReturnType<typeof activeMembership>;
  onSwitch: (id: string) => void;
}) {
  if (user.organizations.length > 1) {
    return (
      <select
        className="min-w-0 max-w-[9rem] truncate bg-transparent py-1 text-right text-sm text-ink-muted outline-none md:max-w-[14rem] md:text-left"
        value={user.active_organization_id ?? ""}
        onChange={(e) => onSwitch(e.target.value)}
        aria-label="Switch organization"
      >
        {user.organizations.map((org) => (
          <option key={org.id} value={org.id}>
            {org.name}
          </option>
        ))}
      </select>
    );
  }
  if (!membership) return null;
  return (
    <span className="block min-w-0 max-w-[9rem] truncate text-right text-sm text-ink-muted md:max-w-[14rem] md:text-left">
      {membership.name}
    </span>
  );
}

export default function AppShell({
  children,
  fill = false,
}: {
  children: ReactNode;
  fill?: boolean;
}) {
  const { user, switchOrganization } = useAuth();
  const location = useLocation();
  const membership = activeMembership(user);

  return (
    <div className={`flex flex-col ${fill ? "h-dvh overflow-hidden" : "min-h-screen"}`}>
      <header className="sticky top-0 z-40 shrink-0 border-b border-border bg-surface dark:border-border-dark dark:bg-surface-dark">
        <div className="mx-auto flex max-w-5xl items-center px-4 py-3 md:px-6 md:py-4">
          <Link to={meetingsListPath()} aria-label="Corella" className="flex shrink-0 items-end gap-1.5">
            <img src={logoLight} alt="" className="h-7 dark:hidden" />
            <img src={logoDark} alt="" className="hidden h-7 dark:block" />
            <span className="font-serif text-2xl leading-none text-ink dark:text-ink-inverted">
              Corella
            </span>
          </Link>
          <nav className="ml-8 hidden items-center gap-1 md:flex">
            {NAV.map((item) => {
              const active = location.pathname.startsWith(item.to);
              return (
                <Link
                  key={item.to}
                  to={item.to === "/dashboard" ? meetingsListPath() : item.to}
                  className={navClass(active)}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          {user && (
            <div className="ml-auto flex min-w-0 items-center gap-2 md:gap-3">
              <OrgLabel
                user={user}
                membership={membership}
                onSwitch={(id) => void switchOrganization(id)}
              />
              <span className="h-4 w-px shrink-0 bg-border dark:bg-border-dark" aria-hidden />
              <AccountMenu />
            </div>
          )}
        </div>
      </header>
      <main
        className={`mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 py-6 md:px-6 md:py-8 ${
          fill ? "min-h-0" : ""
        }`}
      >
        {children}
      </main>
      <Footer />
    </div>
  );
}

function AccountMenu() {
  const { user, logout, refreshUser } = useAuth();
  const confirm = useConfirm();
  const location = useLocation();
  const authConfig = useAuthConfig();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [creatingOrg, setCreatingOrg] = useState(false);
  const [newOrgName, setNewOrgName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const ownedCount = user?.organizations.filter((org) => org.role === "owner").length ?? 0;
  const canCreateOrg =
    Boolean(authConfig?.allow_public_registration) &&
    ownedCount < (authConfig?.max_orgs_per_user ?? 1);

  useEffect(() => {
    setOpen(false);
    setCreatingOrg(false);
    setCreateError(null);
  }, [location.pathname]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!user) return null;

  async function onSignOut() {
    setOpen(false);
    const ok = await confirm({
      title: "Sign out?",
      description: "You'll need to sign in again to get back to your meetings.",
      confirmLabel: "Sign out",
    });
    if (!ok) return;
    logout();
  }

  async function onCreateOrg(e: FormEvent) {
    e.preventDefault();
    const name = newOrgName.trim();
    if (!name) return;
    setCreateError(null);
    try {
      await api.createOrganization(name);
      await refreshUser();
      setCreatingOrg(false);
      setNewOrgName("");
      setOpen(false);
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Couldn't create organization");
    }
  }

  const menuItem = (active: boolean) =>
    `block rounded px-3 py-2.5 text-sm transition-colors md:py-1.5 ${
      active
        ? "bg-accent text-accent-foreground"
        : "text-ink dark:text-ink-inverted hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
    }`;

  return (
    <div ref={wrapRef} className="relative shrink-0">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        onClick={() => setOpen((prev) => !prev)}
        className={`flex cursor-pointer items-center gap-1 rounded p-0.5 pr-1.5 transition-colors ${
          open
            ? "bg-black/[0.04] dark:bg-white/[0.06]"
            : "hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
        }`}
      >
        <UserAvatar
          email={user.email}
          size={32}
          className="h-8 w-8 rounded border border-border dark:border-border-dark"
        />
        <ChevronIcon open={open} />
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="card absolute right-0 z-30 mt-2 w-64 max-h-[min(24rem,calc(100dvh-5rem))] overflow-y-auto overflow-x-hidden p-1 max-md:fixed max-md:inset-x-3 max-md:top-14 max-md:mt-0 max-md:w-auto"
        >
          <div className="flex items-center gap-3 px-3 py-2.5">
            <UserAvatar
              email={user.email}
              size={36}
              className="h-9 w-9 shrink-0 rounded border border-border dark:border-border-dark"
            />
            <div className="min-w-0">
              <p className="truncate text-sm text-ink dark:text-ink-inverted">{user.full_name}</p>
              <p className="truncate text-xs text-ink-subtle">{user.email}</p>
            </div>
          </div>
          <div className="md:hidden">
            <div className="my-1 border-t border-border dark:border-border-dark" />
            {NAV.map((item) => (
              <Link
                key={item.to}
                role="menuitem"
                to={item.to === "/dashboard" ? meetingsListPath() : item.to}
                onClick={() => setOpen(false)}
                className={menuItem(location.pathname.startsWith(item.to))}
              >
                {item.label}
              </Link>
            ))}
          </div>
          {(isOrgAdmin(user) || user.is_super_admin || canCreateOrg || creatingOrg) && (
            <>
              <div className="my-1 border-t border-border dark:border-border-dark" />
              {isOrgAdmin(user) && (
            <Link
              role="menuitem"
              to="/organization"
              onClick={() => setOpen(false)}
              className={menuItem(location.pathname.startsWith("/organization"))}
            >
              Organization
            </Link>
          )}
          {user.is_super_admin && (
            <Link
              role="menuitem"
              to="/admin"
              onClick={() => setOpen(false)}
              className={menuItem(location.pathname.startsWith("/admin"))}
            >
              Super admin
            </Link>
          )}
          {canCreateOrg && !creatingOrg && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setCreateError(null);
                setCreatingOrg(true);
              }}
              className={`${menuItem(false)} w-full text-left`}
            >
              New organization
            </button>
          )}
          {creatingOrg && (
            <form onSubmit={onCreateOrg} className="space-y-2 px-3 py-2">
              <input
                autoFocus
                className="field py-1.5 text-sm"
                placeholder="Organization name"
                value={newOrgName}
                onChange={(e) => setNewOrgName(e.target.value)}
              />
              {createError && <p className="text-xs text-status-danger">{createError}</p>}
              <div className="flex items-center gap-3">
                <button type="submit" className="text-xs text-accent hover:underline">
                  Create
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCreatingOrg(false);
                    setNewOrgName("");
                    setCreateError(null);
                  }}
                  className="text-xs text-ink-muted hover:underline"
                >
                  Cancel
                </button>
              </div>
            </form>
              )}
            </>
          )}
          <div className="my-1 border-t border-border dark:border-border-dark" />
          <button
            type="button"
            role="menuitem"
            onClick={() => void onSignOut()}
            className="block w-full rounded px-3 py-1.5 text-left text-sm text-ink-muted hover:bg-black/[0.03] hover:text-status-danger dark:hover:bg-white/[0.04]"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden
      className={`text-ink-subtle transition-transform ${open ? "rotate-180" : ""}`}
    >
      <path
        d="M3 4.5L6 7.5L9 4.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
