import type { ReactNode } from "react";

/** Page-level title + muted subtitle, with optional actions.
 * Mobile: title/subtitle full width, then a row of equal-width buttons
 * underneath so they never squeeze the copy. Desktop: actions sit on the
 * right, subtitle capped at `max-w-xl`. */
export default function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex shrink-0 flex-col gap-4 md:mb-8 md:flex-row md:items-start md:justify-between">
      <div className="min-w-0 md:max-w-xl">
        <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">{title}</h1>
        {subtitle != null && subtitle !== "" && (
          <p className="mt-1 text-sm text-ink-muted">{subtitle}</p>
        )}
      </div>
      {actions != null && (
        <div className="flex w-full gap-2 md:w-auto md:shrink-0 md:justify-end [&_button]:min-w-0 [&_button]:flex-1 [&_button]:whitespace-nowrap md:[&_button]:flex-none">
          {actions}
        </div>
      )}
    </div>
  );
}
