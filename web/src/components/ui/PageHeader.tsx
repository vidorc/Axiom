import type { ReactNode } from "react";

/**
 * The standard page header: a mono eyebrow, a display-scale title, an optional
 * subtitle, and a right-aligned actions slot. Gives every route the same
 * confident, generous-whitespace top (DESIGN.md "engineered" rhythm).
 */
export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow text-link">{eyebrow}</p>}
        <h1 className="t-display-lg mt-1.5">{title}</h1>
        {subtitle && <p className="mt-2 max-w-2xl text-sm text-body">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-3">{actions}</div>}
    </header>
  );
}
