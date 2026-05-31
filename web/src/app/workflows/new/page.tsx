import Link from "next/link";

import { WorkflowBuilder } from "@/components/WorkflowBuilder";

export const metadata = { title: "New workflow — Axiom" };

export default function NewWorkflowPage() {
  return (
    <div className="space-y-8">
      <header>
        <Link href="/" className="font-mono text-xs text-mute hover:text-ink">
          ← workflows
        </Link>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">New workflow.</h1>
        <p className="mt-1 text-sm text-body">
          Add nodes from the palette, connect them, then create the workflow.
        </p>
      </header>

      <WorkflowBuilder />
    </div>
  );
}
