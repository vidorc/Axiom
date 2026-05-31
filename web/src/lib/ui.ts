/**
 * Small presentational helpers shared across the console.
 *
 * Status tones map the engine's run/node statuses onto the DESIGN.md palette —
 * ink + gray with the link blue / cyan / error accents, never a sixth color.
 */

export function statusTone(status: string): string {
  switch (status) {
    case "succeeded":
      return "border-cyan-deep/40 text-cyan-deep bg-cyan-deep/5";
    case "running":
    case "ready":
      return "border-link/40 text-link bg-link/5";
    case "failed":
      return "border-error/40 text-error bg-error/5";
    case "cancelled":
    case "skipped":
      return "border-hairline-strong/50 text-mute bg-canvas-soft-2";
    default: // pending / queued
      return "border-hairline text-mute bg-canvas-soft";
  }
}

/** Best-effort local time from an ISO timestamp; em-dash for null. */
export function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

/** Cents → a "$1.23" string (the cost ledger is integer cents). */
export function fmtCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
