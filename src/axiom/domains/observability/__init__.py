"""Observability & Cost domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §8).

Owns: logs, metrics, traces, the per-run cost ledger, audit events.

A cross-cutting READ MODEL built from the execution event stream — never on the
live execution path, so analytics load can never threaten execution. History, the
live run viewer, the audit trail, and cost are all projections of one event log.

Phase 1 (WS-8): the cost ledger projection (required — runs report real spend).
The dashboard UI is Phase 3.
"""
