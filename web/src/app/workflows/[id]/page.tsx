import Link from "next/link";
import { notFound } from "next/navigation";

import { GraphView } from "@/components/GraphView";
import { StartRunButton } from "@/components/StartRunButton";
import { StatusBadge } from "@/components/StatusBadge";
import { buttonClasses } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, ApiError } from "@/lib/api";
import type { Version } from "@/lib/types";
import { fmtCents, fmtTime } from "@/lib/ui";

export const dynamic = "force-dynamic";

export default async function WorkflowDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params; // Next 16: params is a Promise.

  const workflow = await api.getWorkflow(id).catch((e) => {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  });

  // Pull the current version's graph (if published) + the run history in parallel.
  const versions = await api.listVersions(id);
  const currentVersion = versions[0]?.version;
  const [version, runs] = await Promise.all([
    currentVersion ? api.getVersion(id, currentVersion) : Promise.resolve<Version | null>(null),
    api.listRuns(id),
  ]);

  return (
    <div className="space-y-10">
      <div className="space-y-4">
        <Link
          href="/"
          className="inline-flex font-mono text-xs text-mute transition-colors hover:text-ink"
        >
          ← workflows
        </Link>
        <PageHeader
          title={workflow.name}
          subtitle={
            <span className="font-mono text-xs text-mute">
              {versions.length} version{versions.length === 1 ? "" : "s"} · created{" "}
              {fmtTime(workflow.created_at)}
            </span>
          }
          actions={
            <>
              <Link href={`/workflows/${id}/edit`} className={buttonClasses("secondary")}>
                Edit
              </Link>
              {workflow.current_version_id && <StartRunButton workflowId={id} />}
            </>
          }
        />
      </div>

      {/* Read-only graph of the current version. */}
      <section>
        <h2 className="eyebrow mb-3">Graph (current version)</h2>
        {version ? (
          <GraphView graph={version.graph} />
        ) : (
          <p className="dot-grid rounded-xl border border-hairline bg-canvas-soft p-12 text-center text-mute">
            No published version yet.
          </p>
        )}
      </section>

      {/* Run history. */}
      <section>
        <h2 className="eyebrow mb-3">Runs</h2>
        {runs.length === 0 ? (
          <p className="dot-grid rounded-xl border border-hairline bg-canvas-soft p-12 text-center text-mute">
            No runs yet. Start one above.
          </p>
        ) : (
          <ul className="divide-y divide-hairline overflow-hidden rounded-xl border border-hairline bg-canvas shadow-[var(--shadow-e2)]">
            {runs.map((r) => (
              <li key={r.id}>
                <Link
                  href={`/runs/${r.id}`}
                  className="flex items-center justify-between px-5 py-3.5 transition-colors hover:bg-canvas-soft"
                >
                  <span className="flex items-center gap-3">
                    <StatusBadge status={r.status} />
                    <span className="font-mono text-xs text-mute">{r.id.slice(0, 8)}</span>
                  </span>
                  <span className="flex items-center gap-5 font-mono text-xs text-mute tnum">
                    <span>{fmtCents(r.total_cost_cents)}</span>
                    <span>{fmtTime(r.started_at)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
