import { statusTone } from "@/lib/ui";

/**
 * A small status pill. Presentational and server-safe (no client boundary), so
 * it can be used from both server pages and client components.
 */
export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-xs ${statusTone(status)}`}
    >
      {status}
    </span>
  );
}
