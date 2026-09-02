import type { ReactNode } from "react";

import logoDark from "@/assets/logo-dark.svg";
import logoLight from "@/assets/logo-light.svg";

export default function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <img
            src={logoLight}
            alt=""
            className="mx-auto mb-4 h-12 dark:hidden"
          />
          <img
            src={logoDark}
            alt=""
            className="mx-auto mb-4 hidden h-12 dark:block"
          />
          <h1 className="font-serif text-3xl text-ink dark:text-ink-inverted">Corella</h1>
          <p className="mt-1 text-sm text-ink-muted">{subtitle}</p>
        </div>
        <div className="card p-6">
          <h2 className="mb-5 font-serif text-xl text-ink dark:text-ink-inverted">{title}</h2>
          {children}
        </div>
      </div>
    </div>
  );
}
