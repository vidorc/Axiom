import Link from "next/link";
import { notFound } from "next/navigation";

import { WorkflowBuilder } from "@/components/WorkflowBuilder";
import { api, ApiError } from "@/lib/api";
import type { Version } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function EditWorkflowPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params; // Next 16: params is a Promise.

  const workflow = await api.getWorkflow(id).catch((e) => {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  });

  // Seed the canvas from the current version's graph (if one is published).
  const versions = await api.listVersions(id);
  const current = versions[0]?.version;
  const version: Version | null = current ? await api.getVersion(id, current) : null;

  return (
    <div className="space-y-8">
      <header>
        <Link href={`/workflows/${id}`} className="font-mono text-xs text-mute hover:text-ink">
          ← {workflow.name}
        </Link>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Edit workflow.</h1>
        <p className="mt-1 text-sm text-body">
          Saving creates a new immutable version — runs already in flight keep the
          definition they started with.
        </p>
      </header>

      <WorkflowBuilder
        workflowId={id}
        initialName={workflow.name}
        initialGraph={version?.graph}
      />
    </div>
  );
}
