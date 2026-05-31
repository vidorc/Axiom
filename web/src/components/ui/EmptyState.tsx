import type { ReactNode } from "react";

/**
 * A generous, centered empty-state panel on canvas-soft (DESIGN.md ex-empty-state
 * pattern). Keeps a fresh install from being a dead end — a clear line and a
 * primary action, never a blank screen.
 */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="dot-grid flex flex-col items-center justify-center rounded-xl border border-hairline bg-canvas-soft px-6 py-20 text-center">
      <p className="t-display-sm">{title}</p>
      {description && <p className="mt-2 max-w-sm text-sm text-body">{description}</p>}
      {action && <div className="mt-7">{action}</div>}
    </div>
  );
}
