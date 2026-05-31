"""The orchestrator loop — the worker's claim/run/settle/reap cycle.

This is what turns the engine's ``tick`` + ``reap`` primitives into a
long-running process. It is deliberately separated from the process bootstrap
(``entrypoint.py``) so it can be unit/integration tested without signals or a
real event loop lifecycle: drive ``drain`` to completion against a real Postgres
and assert the run finished.

Two entry points:
  * ``drain`` — reap once, then tick until nothing is claimable; returns the
    number of nodes processed. Deterministic, used by tests and as a catch-up
    pass. No sleeping.
  * ``run`` — the production loop: tick continuously, reap on a time cadence,
    and poll (sleep briefly) when the queue is idle, until a stop event is set.

Timing (``monotonic`` + ``sleep``) is injected so the cadence is testable and
the idle sleep is interruptible by the stop event.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable

from axiom.domains.execution.engine import WorkflowEngine
from axiom.domains.execution.protocols import GraphResolver
from axiom.platform.logging import get_logger

logger = get_logger(__name__)


class OrchestratorLoop:
    """Drives a durable engine: claim/run/settle nodes and reap dead leases."""

    def __init__(
        self,
        *,
        engine: WorkflowEngine,
        resolver: GraphResolver,
        idle_sleep_seconds: float = 0.5,
        reap_interval_seconds: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._resolver = resolver
        self._idle_sleep = idle_sleep_seconds
        self._reap_interval = reap_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_reap = 0.0

    async def _maybe_reap(self) -> None:
        """Reap expired leases if the cadence is due (crash recovery)."""
        now = self._monotonic()
        if now - self._last_reap >= self._reap_interval:
            self._last_reap = now
            await self._engine.reap()

    async def drain(self, *, max_iterations: int = 1_000_000) -> int:
        """Reap once, then tick until nothing is claimable. Returns nodes run.

        Deterministic and sleep-free — the test/catch-up entry point. The
        ``max_iterations`` guard turns a scheduling bug (a node that never
        terminates) into a loud failure instead of a hang.
        """
        await self._engine.reap()
        processed = 0
        for _ in range(max_iterations):
            if not await self._engine.tick(self._resolver):
                return processed
            processed += 1
        raise RuntimeError("orchestrator drain exceeded max_iterations — scheduling bug?")

    async def run(self, stop: asyncio.Event) -> None:
        """Production loop: process work continuously until ``stop`` is set.

        When work is available, ticks back-to-back (no sleeping between nodes).
        When the queue is idle, reaps if due and waits ``idle_sleep`` — but the
        wait is interruptible by ``stop`` so shutdown is immediate, not delayed
        by a full poll interval.
        """
        logger.info("orchestrator.started", idle_sleep=self._idle_sleep)
        while not stop.is_set():
            await self._maybe_reap()
            try:
                did_work = await self._engine.tick(self._resolver)
            except Exception:
                logger.exception("orchestrator.tick_error")
                did_work = False
            if not did_work:
                # Idle: wait briefly, but wake immediately on shutdown. The
                # TimeoutError when the wait elapses without stop being set is
                # the normal idle case, not an error — suppress it.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._idle_sleep)
        logger.info("orchestrator.stopped")
