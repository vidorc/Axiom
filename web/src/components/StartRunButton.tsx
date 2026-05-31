"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";

/**
 * Start a run of a workflow's current version, then navigate to the live run
 * view. Client-side because it's an imperative action with pending/error state.
 */
export function StartRunButton({ workflowId }: { workflowId: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setPending(true);
    setError(null);
    try {
      const run = await api.startRun(workflowId, { input: {} });
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "failed to start run");
      setPending(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={start}
        disabled={pending}
        className="inline-flex h-10 items-center rounded-full bg-ink px-5 text-sm font-medium text-on-ink transition hover:opacity-90 disabled:opacity-50"
      >
        {pending ? "Starting…" : "Run workflow"}
      </button>
      {error && <span className="text-sm text-error">{error}</span>}
    </div>
  );
}
