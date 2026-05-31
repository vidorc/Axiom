/**
 * Playwright global setup — bring up the full stack before any test runs.
 *
 * Order matters: migrate the schema, start the API and worker (both need the DB),
 * then the frontend (needs the API to render). We poll each for genuine readiness
 * — not just "process spawned" — so a flaky test can never be a race against a
 * still-booting server. The worker has no HTTP surface, so its readiness is an
 * end-to-end probe: enqueue a trivial run via the API and confirm the worker
 * drains it to SUCCEEDED. That single probe proves the entire execution path
 * (API → DB → claim → run → settle) is live before step 10 ever depends on it.
 */

import {
  ALEMBIC,
  API_URL,
  backendEnv,
  launch,
  PYTHON,
  REPO_ROOT,
  run,
  savePids,
  WEB_PORT,
  WEB_ROOT,
  WEB_URL,
  waitForHttp,
} from "./lib/processes";

const SAMPLE_GRAPH = {
  nodes: [{ id: "probe", node_ref: "axiom/echo" }],
  edges: [],
};

async function probeWorker(): Promise<void> {
  // Create a 1-node workflow, start a run, and wait for the worker to finish it.
  const created = await postJson(`${API_URL}/v1/workflows`, {
    name: "e2e-worker-probe",
    graph: SAMPLE_GRAPH,
  });
  const run = await postJson(`${API_URL}/v1/workflows/${created.workflow_id}/runs`, { input: {} });
  await waitForHttp(
    `${API_URL}/v1/runs/${run.id}`,
    (status, body) => status === 200 && JSON.parse(body).status === "succeeded",
    { timeoutMs: 30_000, label: "worker to drain probe run" },
  );
}

async function postJson(url: string, body: unknown): Promise<{ [k: string]: string }> {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`POST ${url} → ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

export default async function globalSetup(): Promise<void> {
  const env = backendEnv();

  // 1) Schema. Idempotent — no-op if already at head.
  await run("migrate", ALEMBIC.command, ALEMBIC.args, { cwd: REPO_ROOT, env });

  // 2) API + worker (both detached process-group leaders).
  const api = launch("api", PYTHON, ["-m", "axiom.api.entrypoint"], { cwd: REPO_ROOT, env });
  const worker = launch("worker", PYTHON, ["-m", "axiom.worker.entrypoint"], {
    cwd: REPO_ROOT,
    env,
  });

  // 3) Frontend dev server, pointed at our API.
  const web = launch("web", "npm", ["run", "dev"], {
    cwd: WEB_ROOT,
    env: {
      ...process.env,
      NEXT_PUBLIC_AXIOM_API_URL: API_URL,
      PORT: String(WEB_PORT),
    },
  });

  savePids([
    { name: "api", pid: api.pid! },
    { name: "worker", pid: worker.pid! },
    { name: "web", pid: web.pid! },
  ]);

  // Readiness gates.
  await waitForHttp(
    `${API_URL}/ready`,
    (status, body) => status === 200 && JSON.parse(body).status === "ready",
    { label: "API /ready" },
  );
  await probeWorker(); // proves the worker is claiming + executing runs
  await waitForHttp(`${WEB_URL}/`, (status) => status === 200, {
    label: "Next dev server",
    timeoutMs: 90_000, // first compile can be slow
  });
}
