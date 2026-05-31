# Spikes — throwaway de-risking code (Phase 0)

> **Everything in this directory is THROWAWAY.** It exists to answer a question,
> not to become product. Per `PHASE_0.md` §7, spike code is archived/deleted at
> the Phase 0 exit gate — it is never promoted into `src/axiom/`.

This directory resolves the ambiguity the pre-build audit flagged (C1): "begin
Phase 0" can mean *the de-risking spikes* (this directory) or *the permanent
project skeleton* (everything else in the repo). They are kept physically
separate so spike code cannot accidentally become a foundation:

- `spikes/**` is **excluded** from coverage and mypy strictness, and has relaxed
  lint rules (see `pyproject.toml`).
- The permanent skeleton in `src/axiom/` is held to the full quality bar.

## The three spikes (PHASE_0.md §3)

| Spike | Question it answers | PASS criterion |
|---|---|---|
| **A — Orchestrator** | Can a correct, crash-recoverable DAG engine live on Postgres + Redis? (ADR-0001) | A 3-node DAG survives a `kill -9` mid-run and resumes to **exactly-once** completion. Also validate the async `FOR UPDATE SKIP LOCKED` transaction pattern (audit R2). |
| **B — Node + vault** | Can a real provider (Apollo) run as a *manifest*, with a credential that never leaks? (ADR-0004, SECURITY.md §5) | Apollo enrich runs from a manifest against a live key; the key is envelope-encrypted at rest and unwrapped only in-memory; a test proves it never appears in logs. |
| **C — Monolith skeleton** | One image, two run modes, with enforced module boundaries? (ADR-0002) | `docker compose up` runs api+worker on Postgres+Redis in one command; an intentional illegal import **fails CI**. |

> Spike C is effectively *proven by the permanent skeleton itself* — the
> single-package layout, the `api`/`worker` entrypoints, the Docker setup, and the
> import-linter contracts already demonstrate it. Spikes A and B get throwaway
> subdirectories here when their work begins.

Create `spikes/a_orchestrator/`, `spikes/b_node_vault/` as needed. Delete them at
the exit gate.
