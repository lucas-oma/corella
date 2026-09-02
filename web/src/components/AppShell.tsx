import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import logoDark from "@/assets/logo-dark.svg";
import logoLight from "@/assets/logo-light.svg";
import Footer from "@/components/Footer";
import { useAuth } from "@/lib/auth";

const NAV = [
  { to: "/dashboard", label: "Meetings" },
  { to: "/knowledge-base", label: "Knowledge base" },
  { to: "/settings", label: "Settings" },
];

export default function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const nav = user?.role === "admin" ? [...NAV, { to: "/admin", label: "Admin" }] : NAV;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border dark:border-border-dark">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-8">
            <div className="flex items-end gap-1.5">
              <img src={logoLight} alt="" className="h-7 dark:hidden" />
              <img src={logoDark} alt="" className="hidden h-7 dark:block" />
              <span className="font-serif text-2xl leading-none text-ink dark:text-ink-inverted">
                Corella
              </span>
            </div>
            <nav className="flex items-center gap-1">
              {nav.map((item) => {
                const active = location.pathname.startsWith(item.to);
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    className={`rounded px-3 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-accent text-accent-foreground"
                        : "text-ink-muted hover:bg-black/[0.03] dark:hover:bg-white/[0.04]"
                    }`}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-sm text-ink-muted">{user?.full_name}</span>
            <button onClick={logout} className="text-sm text-ink-muted hover:text-ink dark:hover:text-ink-inverted">
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">{children}</main>
      <Footer />
    </div>
  );
}
