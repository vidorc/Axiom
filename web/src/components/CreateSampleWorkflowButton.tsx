"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { WorkflowGraph } from "@/lib/types";

/**
 * Seed a sample workflow so a fresh install isn't a dead end. Mirrors
 * examples/workflows/hello.yaml — a fan-in DAG over credential-free built-ins
 * (echo + set-fields), so the created workflow actually runs end to end.
 */
const SAMPLE_GRAPH: WorkflowGraph = {
  nodes: [
    { id: "prepare", node_ref: "axiom/echo" },
    { id: "tag_lead", node_ref: "axiom/set-fields", config: { fields: { stage: "lead" } } },
    {
      id: "tag_source",
      node_ref: "axiom/set-fields",
      config: { fields: { source: "console-sample" } },
    },
    { id: "finalize", node_ref: "axiom/echo" },
  ],
  edges: [
    { from: "prepare", to: "tag_lead" },
    { from: "prepare", to: "tag_source" },
    { from: "tag_lead", to: "finalize" },
    { from: "tag_source", to: "finalize" },
  ],
};

export function CreateSampleWorkflowButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setPending(true);
    setError(null);
    try {
      const { workflow_id } = await api.createWorkflow({
        name: "Sample — cold outbound",
        graph: SAMPLE_GRAPH,
      });
      router.push(`/workflows/${workflow_id}`);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "failed to create workflow");
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <button
        type="button"
        onClick={create}
        disabled={pending}
        className="inline-flex h-10 items-center rounded-full bg-ink px-5 text-sm font-medium text-on-ink transition hover:opacity-90 disabled:opacity-50"
      >
        {pending ? "Creating…" : "Create a sample workflow"}
      </button>
      {error && <span className="text-sm text-error">{error}</span>}
    </div>
  );
}
