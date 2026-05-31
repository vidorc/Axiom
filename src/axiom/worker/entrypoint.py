"""Worker run-mode entrypoint (`axiom-worker`).

Boots, configures logging, verifies it can reach Postgres + Redis, then drives the
durable orchestrator loop: claim a ready node from any run (``SELECT ... FOR UPDATE
SKIP LOCKED``), run it, settle the result, and reap expired leases on a cadence —
until signalled to stop. Many of these processes run side by side and cooperate
through the claim; that is the whole multi-worker design (ADR-0001, PHASE_1.md §6).

The orchestration logic itself lives in ``axiom.worker.loop.OrchestratorLoop`` and
the durable engine assembly in ``axiom.composition.build_durable_engine``; this
module is just the process lifecycle (boot, signals, graceful shutdown) around
them.
"""

from __future__ import annotations

import asyncio
import signal

from axiom import __version__
from axiom.composition import build_durable_engine
from axiom.platform.config import get_settings
from axiom.platform.db import dispose_engine, get_session_factory
from axiom.platform.logging import configure_logging, get_logger
from axiom.platform.redis import close_redis, get_redis
from axiom.worker.loop import OrchestratorLoop

log = get_logger(__name__)


async def _run() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log.info("worker.startup", environment=settings.environment, version=__version__)

    # Verify transport is reachable at boot (fail fast if misconfigured). Postgres
    # reachability is proven implicitly by the first claim; a bad DSN surfaces
    # there as a loud, logged tick error rather than a silent idle.
    await get_redis().ping()

    # Assemble the durable engine (Postgres store + node runtime + authoring) and
    # the orchestrator loop that drives it. One session factory, one database.
    durable = build_durable_engine(get_session_factory())
    loop = OrchestratorLoop(engine=durable.engine, resolver=durable.resolver)
    log.info("worker.ready", note="orchestrator loop engaged")

    stop = asyncio.Event()

    def _signal() -> None:
        log.info("worker.signal", note="shutdown requested")
        stop.set()

    running_loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        running_loop.add_signal_handler(sig, _signal)

    try:
        # Blocks here, processing work, until a signal sets `stop`. The loop's own
        # try/except keeps one bad tick from killing the worker (see loop.run).
        await loop.run(stop)
    finally:
        await close_redis()
        await dispose_engine()
        log.info("worker.shutdown")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
