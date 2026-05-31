"""Execution / Orchestration domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §4).

Owns: runs, node_states, scheduling, retries, compensation, the append-only
execution event log. The custom Postgres-backed DAG engine (ADR-0001).

The run-time concept and the actual product. Strictly separated from authoring so
the engine can change without touching the editor. Depends on node_runtime
through an interface — and node_runtime must NEVER import this domain
(import-linter Contract 2).

This is the long pole. Phase 0 Spike A de-risks claim/retry/crash-recovery;
Phase 1 (WS-1) builds the production orchestrator behind a `WorkflowEngine`
interface (the Temporal escape hatch from ADR-0001).
"""
