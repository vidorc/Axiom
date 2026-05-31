"""Worker delivery layer (orchestrator run mode).

The process that runs the DAG orchestrator + node runtime (ADR-0001, PHASE_1.md
WS-1). One of three run modes of the single axiom package (ADR-0002). Feature-free
in Phase 0: a graceful run loop that proves the process boots, connects, and
shuts down cleanly. The ready-set claim loop lands in Phase 1.
"""
