import Link from "next/link";
import { notFound } from "next/navigation";

import { RunViewer } from "@/components/RunViewer";
import { api, ApiError } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function RunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params; // Next 16: params is a Promise.

  const run = await api.getRun(id).catch((e) => {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  });
  // Seed the live viewer with the full log so far; it tails from here if running.
  const initialEvents = await api.getEvents(id);

  return (
    <div className="space-y-8">
      <header>
        <Link
          href={`/workflows/${run.workflow_id}`}
          className="font-mono text-xs text-mute hover:text-ink"
        >
          ← workflow
        </Link>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Run.</h1>
        <p className="mt-1 font-mono text-xs text-mute">{run.id}</p>
      </header>

      <RunViewer run={run} initialEvents={initialEvents} />
    </div>
  );
}
