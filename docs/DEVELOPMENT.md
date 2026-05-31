# Local Development

How to set up, run, and work on Axiom locally. The whole workflow goes through
`make` — and CI runs the same commands, so "green locally" means "green in CI."

> **Project status:** foundation / pre-code. The skeleton boots and the quality
> gate is live, but there are no product features yet — the build starts with
> Phase 0 (`PHASE_0.md`). See the repo `README.md` for orientation.

---

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **Python 3.12+** | The backend language (ADR-0003) | pyenv / system / `uv python install` |
| **uv** | Dependency + environment manager (audit G1) | https://docs.astral.sh/uv/ |
| **Docker** + Compose | The local stack (Postgres, Redis, api, worker) | https://docs.docker.com/ |
| **make** | The task interface | usually preinstalled |

---

## First-time setup

```bash
make setup
```

This installs dev dependencies, installs the pre-commit hooks, and creates `.env`
from `.env.example`. The default `.env` is dev-safe out of the box (it uses the
placeholder master key, which is tolerated **only** in dev — `SECURITY.md` §5.5).

---

## Running the stack

```bash
make up          # build + start postgres, redis, api, worker
# API live at http://localhost:8000/health   (readiness probe: /ready)
make logs        # tail logs
make down        # stop (volumes preserved)
```

`make up` is the concrete proof of the "self-host in 10 minutes" promise
(`BLUEPRINT.md` principle 5) and the Phase 0 Spike C criterion: one command brings
up the whole system from a single image run in two modes (`api` and `worker` —
ADR-0002).

### The single-image, multi-mode model

There is one Docker image. The `MODE` env var (`api` | `worker` | `cli`) selects
what the container runs (see `docker/entrypoint.sh`). This is the modular monolith
made concrete — not three images, one.

```bash
docker run --rm -e MODE=cli axiom:dev version    # run the CLI from the image
```

---

## The quality gate

Before opening a PR, run the gate. It is exactly what CI enforces:

```bash
make check       # fmt + lint + typecheck + boundaries + unit tests
```

Individual steps:

```bash
make fmt         # ruff format
make lint        # ruff check
make typecheck   # mypy --strict on src/
make boundaries  # import-linter — the modular-monolith rules (ADR-0002)
make test        # the unit lane
```

### The `boundaries` step is the one newcomers hit

`import-linter` enforces the dependency rule from `BLUEPRINT.md` §4: layers flow
`delivery → domains → platform → shared`, and the node runtime must never import
the execution engine. If you get a contract violation, you've reached across a
boundary that should go through an interface or the event log. The contracts live
in `pyproject.toml` under `[tool.importlinter]`. They were established while the
modules were empty *on purpose* — so the first real code is already constrained
(audit R1).

---

## Tests — the three lanes

The marker taxonomy (audit G6) is declared in `pyproject.toml`:

```bash
make test              # unit       — fast, isolated, no services (default)
make test-integration  # integration — needs Postgres + Redis
make test-chaos        # chaos      — crash recovery / idempotency (PHASE_1.md §6)
```

- **unit** runs on every change and in the default CI lane.
- **integration** runs against the dev stack (or CI service containers).
- **chaos** is reserved now and filled in Phase 1; it will be the most important
  test in the codebase — `kill -9` a worker mid-run and assert exactly-once
  completion (ADR-0001).

Two unit tests already matter: `test_redaction.py` (a known secret never survives
the log redaction layer — `SECURITY.md` §5.4) and `test_config_safety.py` (the app
refuses the placeholder master key outside dev — §5.5).

---

## Database & migrations

```bash
make migrate                      # alembic upgrade head
make revision m="add workflows"   # autogenerate a migration
```

Alembic targets `axiom.platform.db.Base.metadata` and reads the database URL from
settings (so migrations always match the app config). No schema exists in Phase 0;
the first migration lands in Phase 1 with the domain models (`DOMAIN_MODEL.md`).

---

## Commits & the DCO

Every commit must be signed off (`git commit -s`) — Axiom uses the DCO, not a CLA
(`CONTRIBUTING.md`, ADR-0007). CI checks every commit in a PR. If you forget:

```bash
git commit --amend -s --no-edit     # fix the last commit
git rebase --signoff main           # fix a whole branch
```

---

## Project layout

```
src/axiom/
  shared/         base types + errors (depends on nothing)
  platform/       config · logging(+redaction) · db · redis
  domains/        the 8 bounded contexts (BLUEPRINT.md §4) — empty stubs in Phase 0
  sdk/            the public node contract (independent; extractable in Phase 2)
  api/            FastAPI run mode (app factory, health/ready probes)
  worker/         orchestrator run mode (idle loop until Phase 1)
  cli/            the axiom CLI (the only user surface in Phase 1)
migrations/       Alembic (targets Base.metadata)
tests/            unit · integration · chaos
spikes/           THROWAWAY Phase 0 de-risking code (never promoted — PHASE_0.md §7)
web/              Phase 3 frontend placeholder (empty on purpose — audit R4)
```

For *why* it's shaped this way, read `BLUEPRINT.md` §3–4 and the ADRs.
