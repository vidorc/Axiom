"""Shared test fixtures.

Phase 0 keeps this minimal. Integration/chaos fixtures (a Postgres/Redis-backed
session, a test org, etc.) are added in Phase 1 alongside the code that needs them.
The marker taxonomy (unit/integration/chaos) is declared in pyproject.toml.
"""

from __future__ import annotations
