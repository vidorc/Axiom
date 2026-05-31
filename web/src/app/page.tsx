import Link from "next/link";

import { CreateSampleWorkflowButton } from "@/components/CreateSampleWorkflowButton";
import { buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, ApiError } from "@/lib/api";
import { fmtTime } from "@/lib/ui";

// Live data — never statically cached (the client also sets cache: "no-store").
export const dynamic = "force-dynamic";

export default async function WorkflowsPage() {
  let workflows;
  try {
    workflows = await api.listWorkflows();
  } catch (e) {
    return <ApiDown error={e} />;
  }

  return (
    <div className="space-y-10">
      <PageHeader
        eyebrow="Workflows"
        title="Your workflows."
        subtitle="Compose durable workflows on the canvas, run them on your own keys, and watch every node settle in real time."
        actions={
          <>
            {workflows.length > 0 && <CreateSampleWorkflowButton />}
            <Link href="/workflows/new" className={buttonClasses("secondary")}>
              New workflow
            </Link>
          </>
        }
      />

      {workflows.length === 0 ? (
        <EmptyState
          title="No workflows yet."
          description="Create a sample to see the engine run a real DAG end to end — author, save, run, and watch it reach SUCCEEDED."
          action={<CreateSampleWorkflowButton />}
        />
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {workflows.map((w) => (
            <li key={w.id}>
              <Link href={`/workflows/${w.id}`} className="block focus-visible:outline-none">
                <Card interactive className="h-full p-5">
                  <div className="flex items-start justify-between gap-3">
                    <h2 className="t-display-sm truncate">{w.name}</h2>
                    <PublishedDot published={Boolean(w.current_version_id)} />
                  </div>
                  <p className="mt-3 font-mono text-[11px] text-mute">
                    {w.current_version_id ? "published" : "draft"} · created {fmtTime(w.created_at)}
                  </p>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PublishedDot({ published }: { published: boolean }) {
  return (
    <span
      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
        published ? "bg-cyan-deep" : "bg-hairline-strong"
      }`}
      aria-label={published ? "published" : "draft"}
    />
  );
}

function ApiDown({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? error.message : "the Axiom API is unreachable";
  return (
    <div className="rounded-xl border border-error/30 bg-error-soft/40 p-6">
      <p className="font-medium text-error-deep">Can&apos;t reach the API.</p>
      <p className="mt-1 font-mono text-xs text-error">{msg}</p>
      <p className="mt-3 text-sm text-body">
        Start the backend (<code className="font-mono">make up</code> or{" "}
        <code className="font-mono">axiom-api</code>) and reload. Configure the URL with{" "}
        <code className="font-mono">NEXT_PUBLIC_AXIOM_API_URL</code>.
      </p>
    </div>
  );
}
