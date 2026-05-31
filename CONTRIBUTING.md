# Contributing to Axiom

Thanks for considering a contribution. Axiom's bet is that the **ecosystem is the moat** ([ADR-0006](docs/adr/0006-ecosystem-as-moat.md)) — so making contribution low-friction is a first-class goal, not an afterthought. This document explains how to contribute and the one legal formality we require (the DCO — a one-line git trailer, no paperwork).

> **Project status: foundation / pre-code.** Axiom is currently architecture + planning documents plus a feature-free project skeleton. There is no product to build features against yet. The build begins with Phase 0 (`PHASE_0.md`). **Right now, the highest-value contribution is pressure-testing the architecture documents** (see "Contributing to the design" below). Node/SDK contribution mechanics arrive in Phase 2.

---

## License & the DCO (read this once)

- Axiom's core is licensed under **Apache-2.0** (see `LICENSE`). By contributing, you agree your contribution is licensed under Apache-2.0.
- We use the **Developer Certificate of Origin (DCO)**, not a CLA. You do not sign anything or assign copyright. You simply certify — via a `Signed-off-by` line on each commit — that you have the right to submit the work. The full text is in the `DCO` file at the repo root, and the rationale is [ADR-0007](docs/adr/0007-licensing-apache-dco.md).

### How to sign off

Add the `-s` flag when you commit:

```bash
git commit -s -m "Fix ready-set evaluation for conditional edges"
```

This appends a trailer to your commit message:

```
Signed-off-by: Jane Developer <jane@example.com>
```

The name and email must match your git author identity. CI checks that **every commit** in a PR is signed off; a missing sign-off blocks the merge until you amend.

If you forgot to sign off, fix the last commit with:

```bash
git commit --amend -s --no-edit
```

…or, for a whole branch, rebase with sign-off:

```bash
git rebase --signoff main
```

### Make it automatic

Add a prepare-commit-msg hook so you never forget (this repo's `make setup` offers to install it):

```bash
# .git/hooks/prepare-commit-msg  (chmod +x)
# Appends Signed-off-by using your git identity if absent.
```

---

## Getting set up

Requirements: **Python 3.12+**, **uv**, **Docker** + Docker Compose, **make**.

```bash
git clone <repo> && cd axiom
make setup          # install uv, sync deps, install pre-commit hooks
make up             # start postgres + redis (+ api/worker) via docker compose
make check          # run the full local gate: lint + typecheck + boundaries + tests
```

See `docs/DEVELOPMENT.md` for the full local-development workflow.

---

## The local quality gate

Before you open a PR, `make check` must pass. It runs exactly what CI runs:

| Step | Tool | What it enforces |
|---|---|---|
| Format | `ruff format` | Consistent formatting (no debate, the tool decides) |
| Lint | `ruff check` | Lint rules, import order, common bugs |
| Types | `mypy` | Strict type safety (the codebase is fully typed) |
| **Boundaries** | `import-linter` | The modular-monolith dependency rules ([ADR-0002](docs/adr/0002-modular-monolith.md)) — **an illegal cross-module import fails the build** |
| Tests | `pytest` | Unit tests by default; `make test-integration` for the Postgres/Redis-backed lane |

The **boundaries** check is the one newcomers hit unexpectedly: domain modules may only depend inward (see the contracts in `pyproject.toml` under `[tool.importlinter]` and the rule in `BLUEPRINT.md` §4). If you get a contract violation, you've reached across a boundary that's meant to go through an interface or the event log.

---

## How we work

- **Architecture is decided in ADRs.** If your change contradicts a committed decision in `docs/adr/`, don't work around it — open a PR proposing a *superseding* ADR with the trade-offs. Decisions are frozen unless a real flaw is found, and the way to challenge one is in the open, with reasoning.
- **Match the surrounding code.** Style, naming, and patterns should look like what's already there.
- **Tests come with the change.** New behavior ships with tests (`PHASE_1.md` §6 sets the bar). The engine is written test-first — its correctness is the product.
- **Security-sensitive changes** (anything touching credentials, tenancy, egress, or untrusted input) must satisfy the checklist in `SECURITY.md` §12. Reviewers will hold this line.
- **Conventional, scoped commits.** Keep commits focused; write a clear subject line in the imperative mood.

---

## Contributing to the design (the most valuable thing right now)

Until Phase 2 ships the node SDK, the codebase is a skeleton — the substance is in the documents. If you want to help today:

1. Read `BLUEPRINT.md`, then the ADR index (`docs/adr/`).
2. Pressure-test a decision. The ADRs each list **revisit triggers** — if you see one being met, or a contradiction between documents, open an issue.
3. Propose superseding ADRs for decisions you think are wrong, with the trade-offs spelled out. Honest disagreement backed by reasoning is exactly what we want — performative agreement is not useful.

---

## Reporting security issues

Do **not** open a public issue for a vulnerability. See `SECURITY.md` (and `.github/SECURITY.md` once published) for private disclosure. Given Axiom holds users' provider credentials, security reports are triaged ahead of everything else.

---

## Code of Conduct

Be decent. Harassment, discrimination, and bad-faith participation are not tolerated. (A formal `CODE_OF_CONDUCT.md` will be added before the public launch in Phase 3.)
