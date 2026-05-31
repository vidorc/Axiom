#!/usr/bin/env bash
# Container entrypoint — dispatches the single Axiom image to a run mode (ADR-0002).
#
# Usage: set MODE to one of: migrate | api | worker | cli
#   migrate → apply database migrations (alembic upgrade head), then exit
#   api     → the FastAPI HTTP surface          (axiom-api)
#   worker  → the orchestrator process          (axiom-worker)
#   cli     → the axiom CLI (args passed through)
#
# This is the concrete expression of "one image, multiple run modes." The same
# build artifact becomes whichever process the deployment needs.
#
# Deploy order: run a one-shot `MODE=migrate` container (k8s init container / job,
# compose `migrate` service, or `docker run -e MODE=migrate`) to bring the schema
# to head BEFORE starting api/worker. The app processes do NOT auto-migrate on
# boot — schema changes are a deliberate, ordered step, never a side effect of a
# rolling restart where N replicas could race the same migration.
set -euo pipefail

MODE="${MODE:-api}"

case "$MODE" in
  migrate)
    echo "[entrypoint] applying database migrations (alembic upgrade head)"
    exec alembic upgrade head
    ;;
  api)
    echo "[entrypoint] starting Axiom API (MODE=api)"
    exec axiom-api
    ;;
  worker)
    echo "[entrypoint] starting Axiom worker (MODE=worker)"
    exec axiom-worker
    ;;
  cli)
    # Pass any extra arguments straight through to the CLI.
    echo "[entrypoint] running Axiom CLI (MODE=cli)"
    exec axiom "$@"
    ;;
  *)
    echo "[entrypoint] ERROR: unknown MODE='$MODE' (expected: migrate | api | worker | cli)" >&2
    exit 64
    ;;
esac
