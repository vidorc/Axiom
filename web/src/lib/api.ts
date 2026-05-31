/**
 * Typed client for the Axiom API.
 *
 * One module, two transports:
 *   * `api.*` — typed `fetch` wrappers used from Server Components. They pass
 *     `cache: "no-store"` because run/workflow state is live (Next 16 does not
 *     cache fetch by default, but we're explicit so intent survives upgrades).
 *   * `streamRun` — a browser WebSocket onto `/v1/runs/{id}/stream`, used from
 *     Client Components for the live run viewer.
 *
 * Tenancy: the backend resolves org from `X-Org-Id` (REST) or the `org_id` query
 * param (WebSocket), falling back to a dev org when absent — matching the
 * placeholder auth in `src/axiom/api/dependencies.py`. We omit it here to use the
 * dev org; `withOrg` is provided for when real auth lands (WS-4).
 */

import type {
  CreateWorkflowResult,
  Execution,
  ExecutionDetail,
  ExecutionEvent,
  Version,
  VersionSummary,
  Workflow,
  WorkflowGraph,
} from "@/lib/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_AXIOM_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, {
      cache: "no-store",
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
  } catch (cause) {
    // Network-level failure (API down, wrong URL) — surface a clear message
    // rather than an opaque TypeError from fetch.
    throw new ApiError(0, `cannot reach Axiom API at ${BASE_URL}`, cause);
  }

  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = (await resp.json())?.detail;
    } catch {
      detail = await resp.text().catch(() => undefined);
    }
    throw new ApiError(resp.status, `${init?.method ?? "GET"} ${path} → ${resp.status}`, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  // ── Workflows ──
  listWorkflows: () => request<Workflow[]>("/v1/workflows"),

  getWorkflow: (id: string) => request<Workflow>(`/v1/workflows/${id}`),

  createWorkflow: (body: { name: string; graph: WorkflowGraph; folder?: string }) =>
    request<CreateWorkflowResult>("/v1/workflows", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createVersion: (workflowId: string, body: { graph: WorkflowGraph }) =>
    request<CreateWorkflowResult>(`/v1/workflows/${workflowId}/versions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listVersions: (workflowId: string) =>
    request<VersionSummary[]>(`/v1/workflows/${workflowId}/versions`),

  getVersion: (workflowId: string, version: number) =>
    request<Version>(`/v1/workflows/${workflowId}/versions/${version}`),

  // ── Runs ──
  startRun: (workflowId: string, body: { input?: Record<string, unknown>; version?: number }) =>
    request<Execution>(`/v1/workflows/${workflowId}/runs`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),

  getRun: (executionId: string) => request<ExecutionDetail>(`/v1/runs/${executionId}`),

  listRuns: (workflowId: string) =>
    request<Execution[]>(`/v1/workflows/${workflowId}/runs`),

  getEvents: (executionId: string, afterId = 0) =>
    request<ExecutionEvent[]>(`/v1/runs/${executionId}/events?after_id=${afterId}`),
};

/**
 * Open a live WebSocket tail of a run's event log. Browser-only (uses the global
 * `WebSocket`). Returns the socket plus a `close()` so a React effect can tear it
 * down on unmount. The server replays from the start, then pushes new events, and
 * closes after the terminal RunSucceeded/RunFailed/RunCancelled.
 */
export function streamRun(
  executionId: string,
  handlers: {
    onEvent: (event: ExecutionEvent) => void;
    onOpen?: () => void;
    onClose?: () => void;
    onError?: (err: Event) => void;
  },
  orgId?: string,
): { close: () => void } {
  const wsBase = BASE_URL.replace(/^http/, "ws");
  const query = orgId ? `?org_id=${encodeURIComponent(orgId)}` : "";
  const ws = new WebSocket(`${wsBase}/v1/runs/${executionId}/stream${query}`);

  ws.onopen = () => handlers.onOpen?.();
  ws.onmessage = (msg) => {
    try {
      handlers.onEvent(JSON.parse(msg.data) as ExecutionEvent);
    } catch {
      // Ignore a malformed frame rather than tear down the whole stream.
    }
  };
  ws.onclose = () => handlers.onClose?.();
  ws.onerror = (err) => handlers.onError?.(err);

  return {
    close: () => {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    },
  };
}
