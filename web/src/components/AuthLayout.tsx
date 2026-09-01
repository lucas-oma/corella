import type { ReactNode } from "react";

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
