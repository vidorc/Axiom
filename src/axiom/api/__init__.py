"""API delivery layer (FastAPI run mode).

A thin entrypoint that wires the platform + domains into an HTTP surface. One of
three run modes of the single axiom package (ADR-0002) — it imports domains, never
the reverse. Feature-free in Phase 0: an app factory + health/readiness probes so
`docker compose up` serves something real and CI can smoke-test it.
"""
