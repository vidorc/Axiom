"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { streamRun } from "@/lib/api";
import type { ExecutionDetail, ExecutionEvent } from "@/lib/types";
import { TERMINAL_EVENT_TYPES } from "@/lib/types";
import { fmtCents, fmtTime } from "@/lib/ui";

/**
 * Live run viewer. Seeds from the server-fetched run detail + event log, then —
 * if the run is still going — opens a WebSocket tail and appends events as they
 * land. When a terminal event arrives it refreshes the server component to pull
 * the final node states. This is the "legibility of execution" surface
 * (UI_SYSTEM.md §1.1): what ran, what it cost, what it returned.
 */
export function RunViewer({
  run,
  initialEvents,
}: {
  run: ExecutionDetail;
  initialEvents: ExecutionEvent[];
}) {
  const router = useRouter();
  const [events, setEvents] = useState<ExecutionEvent[]>(initialEvents);
  const [live, setLive] = useState(false);
  const seenIds = useRef<Set<number>>(new Set(initialEvents.map((e) => e.id)));

  const isTerminal = run.status === "succeeded" || run.status === "failed" || run.status === "cancelled";

  useEffect(() => {
    // A finished run needs no socket — the server already gave us the full log.
    if (isTerminal) return;

    const { close } = streamRun(run.id, {
      onOpen: () => setLive(true),
      onEvent: (ev) => {
        if (seenIds.current.has(ev.id)) return;
        seenIds.current.add(ev.id);
        setEvents((prev) => [...prev, ev]);
        // The terminal event means node states just settled — pull them in.
        if (TERMINAL_EVENT_TYPES.has(ev.type)) {
          router.refresh();
        }
      },
      onClose: () => setLive(false),
      onError: () => setLive(false),
    });
    return close;
  }, [run.id, isTerminal, router]);

  return (
    <div className="space-y-8">
      {/* Run header */}
      <div className="flex flex-wrap items-center gap-4">
        <StatusBadge status={run.status} />
        {live && !isTerminal && (
          <span className="inline-flex items-center gap-1.5 font-mono text-xs text-link">
            <span className="h-2 w-2 animate-pulse rounded-full bg-link" /> live
          </span>
        )}
        <span className="font-mono text-xs text-mute">cost {fmtCents(run.total_cost_cents)}</span>
        <span className="font-mono text-xs text-mute">trigger {run.trigger}</span>
      </div>

      {/* Node states */}
      <section>
        <h2 className="mb-3 text-sm font-medium text-body">Nodes</h2>
        <div className="overflow-hidden rounded-lg border border-hairline bg-canvas">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-hairline bg-canvas-soft text-left font-mono text-xs uppercase text-mute">
                <th className="px-4 py-2 font-normal">Node</th>
                <th className="px-4 py-2 font-normal">Status</th>
                <th className="px-4 py-2 font-normal">Attempt</th>
                <th className="px-4 py-2 font-normal">Cost</th>
                <th className="px-4 py-2 font-normal">Output / error</th>
              </tr>
            </thead>
            <tbody>
              {run.nodes.map((n) => (
                <tr key={n.node_id} className="border-b border-hairline last:border-0">
                  <td className="px-4 py-2 font-mono text-xs">{n.node_id}</td>
                  <td className="px-4 py-2">
                    <StatusBadge status={n.status} />
                  </td>
                  <td className="px-4 py-2 tabular-nums text-body">{n.attempt}</td>
                  <td className="px-4 py-2 tabular-nums text-body">{fmtCents(n.cost_cents)}</td>
                  <td className="px-4 py-2 font-mono text-xs text-mute">
                    {n.error ? (
                      <span className="text-error">
                        [{n.error.error_class}] {n.error.message}
                      </span>
                    ) : n.output ? (
                      <span className="line-clamp-1">{JSON.stringify(n.output)}</span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Event log — the deployment-log feel (UI_SYSTEM.md §2, Vercel row). */}
      <section>
        <h2 className="mb-3 text-sm font-medium text-body">Event log</h2>
        <div className="overflow-hidden rounded-lg border border-hairline bg-ink">
          <ol className="max-h-96 overflow-y-auto p-4 font-mono text-xs leading-relaxed text-on-ink">
            {events.map((ev) => (
              <li key={ev.id} className="flex gap-3 py-0.5">
                <span className="shrink-0 text-mute">{fmtTime(ev.created_at)}</span>
                <span className="shrink-0 text-cyan">{ev.type}</span>
                {ev.node_id && <span className="text-hairline-strong">{ev.node_id}</span>}
              </li>
            ))}
          </ol>
        </div>
      </section>
    </div>
  );
}
