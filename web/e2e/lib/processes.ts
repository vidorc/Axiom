/**
 * Shared process + path helpers for the Playwright global setup/teardown.
 *
 * The E2E stack is three long-running processes against the isolated test
 * datastore (Postgres :55432, Redis :56379 — `make test-stack-up`):
 *
 *   1. the API     (uvicorn, :8010)        — serves the REST + WS surface
 *   2. the worker  (axiom-worker)          — claims + executes enqueued runs;
 *                                            without it, a started run never
 *                                            leaves "running" and step 10 fails
 *   3. Next dev    (:3010)                  — the app under test
 *
 * Each is spawned detached as its own process group so teardown can kill the
 * whole group (uvicorn/next spawn children). PIDs are persisted to a file so a
 * separate `global-teardown` can clean up even if the runner is interrupted.
 */

import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdirSync, openSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

// Playwright transpiles these helpers to CommonJS (no "type":"module" in
// package.json), so __dirname is available — avoid ESM-only import.meta.url.
export const WEB_ROOT = resolve(__dirname, "..", "..");
export const REPO_ROOT = resolve(WEB_ROOT, "..");
export const RUNTIME_DIR = join(WEB_ROOT, "e2e", ".runtime");
export const PID_FILE = join(RUNTIME_DIR, "pids.json");

export const API_PORT = Number(process.env.E2E_API_PORT ?? 8010);
export const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3010);
export const API_URL = `http://localhost:${API_PORT}`;
export const WEB_URL = `http://localhost:${WEB_PORT}`;

// Python entrypoints run through the project venv by default (matches the
// Makefile's local lane). Override AXIOM_PYTHON in CI if the interpreter differs.
const VENV_PY = join(REPO_ROOT, ".venv", "bin", "python");
export const PYTHON = process.env.AXIOM_PYTHON ?? (existsSync(VENV_PY) ? VENV_PY : "python3");
const VENV_ALEMBIC = join(REPO_ROOT, ".venv", "bin", "alembic");

/**
 * Environment for the Python processes: the isolated test datastore + a non-prod
 * master key, mirroring the Makefile's TEST_DB_ENV. Every value is overridable
 * from the ambient environment so CI can point at its own services.
 */
export function backendEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    AXIOM_DATABASE_URL:
      process.env.AXIOM_DATABASE_URL ??
      "postgresql+asyncpg://axiom:axiom@localhost:55432/axiom",
    AXIOM_REDIS_URL: process.env.AXIOM_REDIS_URL ?? "redis://localhost:56379/0",
    AXIOM_ENVIRONMENT: process.env.AXIOM_ENVIRONMENT ?? "test",
    AXIOM_MASTER_KEY: process.env.AXIOM_MASTER_KEY ?? "test-master-key-not-for-production",
    AXIOM_API_PORT: String(API_PORT),
    // Engine logs are noise for the test runner; keep them but as plain text.
    AXIOM_LOG_JSON: "false",
  };
}

export interface Spawned {
  name: string;
  pid: number;
}

const spawned: ChildProcess[] = [];

function logPath(name: string): string {
  return join(RUNTIME_DIR, `${name}.log`);
}

/** Spawn a detached, process-group-leading child, logging stdout+stderr to a file. */
export function launch(
  name: string,
  command: string,
  args: string[],
  opts: { cwd: string; env: NodeJS.ProcessEnv },
): ChildProcess {
  mkdirSync(RUNTIME_DIR, { recursive: true });
  const fd = openSync(logPath(name), "w");
  const child = spawn(command, args, {
    cwd: opts.cwd,
    env: opts.env,
    detached: true, // own process group → group-kill on teardown
    stdio: ["ignore", fd, fd],
  });
  spawned.push(child);
  return child;
}

/** Run a command to completion (used for `alembic upgrade head`). */
export function run(
  name: string,
  command: string,
  args: string[],
  opts: { cwd: string; env: NodeJS.ProcessEnv },
): Promise<void> {
  mkdirSync(RUNTIME_DIR, { recursive: true });
  const fd = openSync(logPath(name), "w");
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: opts.cwd,
      env: opts.env,
      stdio: ["ignore", fd, fd],
    });
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0
        ? resolvePromise()
        : reject(new Error(`${name} exited ${code} — see ${logPath(name)}`)),
    );
  });
}

export const ALEMBIC: { command: string; args: string[] } = existsSync(VENV_ALEMBIC)
  ? { command: VENV_ALEMBIC, args: ["upgrade", "head"] }
  : { command: PYTHON, args: ["-m", "alembic", "upgrade", "head"] };

/** Poll an HTTP endpoint until `ok(status, body)` is true or the deadline passes. */
export async function waitForHttp(
  url: string,
  ok: (status: number, body: string) => boolean,
  { timeoutMs = 60_000, intervalMs = 500, label = url }: { timeoutMs?: number; intervalMs?: number; label?: string } = {},
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let last = "no attempt yet";
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(url);
      const body = await resp.text();
      if (ok(resp.status, body)) return;
      last = `status ${resp.status}`;
    } catch (e) {
      last = e instanceof Error ? e.message : String(e);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`timed out waiting for ${label} (${last})`);
}

export function savePids(procs: Spawned[]): void {
  mkdirSync(RUNTIME_DIR, { recursive: true });
  writeFileSync(PID_FILE, JSON.stringify(procs, null, 2));
}

/** Kill every recorded process group. Idempotent and tolerant of already-dead PIDs. */
export function killSaved(): void {
  if (!existsSync(PID_FILE)) return;
  let procs: Spawned[] = [];
  try {
    procs = JSON.parse(readFileSync(PID_FILE, "utf-8"));
  } catch {
    /* corrupt/empty — nothing reliable to kill */
  }
  for (const p of procs) {
    killGroup(p.pid);
  }
  rmSync(PID_FILE, { force: true });
}

function killGroup(pid: number): void {
  // Negative pid → signal the whole process group (detached children too).
  try {
    process.kill(-pid, "SIGTERM");
  } catch {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      /* already gone */
    }
  }
  // Hard stop anything that ignored SIGTERM, shortly after.
  setTimeout(() => {
    try {
      process.kill(-pid, "SIGKILL");
    } catch {
      /* gone */
    }
  }, 2_000);
}
