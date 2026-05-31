/**
 * TypeScript mirror of the Axiom API's Pydantic schemas (`src/axiom/api/schemas.py`).
 *
 * Hand-kept in sync with the backend contract. These are the wire types — the
 * shape the FastAPI layer actually returns — not the internal domain model. When
 * the backend grows a typed OpenAPI generator (a later task), this file becomes
 * generated output; until then it is the single source of frontend truth.
 */

// ── Workflow graph (the opaque JSON the backend validates as a DAG) ──────────

export interface GraphNode {
  id: string;
  node_ref: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, string>;
  retry?: Record<string, unknown>;
}

export interface GraphEdge {
  from: string;
  to: string;
  mapping?: Record<string, unknown>;
  condition?: string | null;
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ── Authoring ────────────────────────────────────────────────────────────────

export interface Workflow {
  id: string;
  org_id: string;
  name: string;
  folder: string | null;
  current_version_id: string | null;
  created_at: string;
}

export interface VersionSummary {
  id: string;
  workflow_id: string;
  version: number;
  created_at: string;
}

export interface Version {
  id: string;
  workflow_id: string;
  version: number;
  graph: WorkflowGraph;
  node_pins: Record<string, unknown>;
  created_at: string;
}

export interface CreateWorkflowResult {
  workflow_id: string;
  version_id: string;
}

// ── Execution ──────────────────────────────────────────────────────────────────

/** Mirrors execution.state.ExecutionStatus. */
export type ExecutionStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

/** Mirrors execution.state.NodeStatus. */
export type NodeStatus =
  | "pending"
  | "ready"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "compensating"
  | "compensated";

export interface NodeError {
  error_class: string;
  message: string;
  retryable: boolean;
  retry_after_seconds: number | null;
}

export interface NodeState {
  node_id: string;
  status: NodeStatus;
  attempt: number;
  output: Record<string, unknown> | null;
  cost_cents: number;
  error: NodeError | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Execution {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  status: ExecutionStatus;
  trigger: string;
  total_cost_cents: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface ExecutionDetail extends Execution {
  nodes: NodeState[];
}

export interface ExecutionEvent {
  id: number;
  seq: number;
  type: string;
  node_id: string | null;
  attempt: number | null;
  payload: Record<string, unknown>;
  created_at: string | null;
}

// The set of run-terminal event types (a stream closes after one of these).
export const TERMINAL_EVENT_TYPES = new Set([
  "RunSucceeded",
  "RunFailed",
  "RunCancelled",
]);

export const TERMINAL_STATUSES = new Set<ExecutionStatus>([
  "succeeded",
  "failed",
  "cancelled",
]);
