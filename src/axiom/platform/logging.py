"""Structured logging with mandatory secret redaction.

This module is the single home for the redaction layer required by SECURITY.md
§5.4 — "the most common real-world secret leak is logging a request with an
Authorization header." Redaction is a structlog processor applied to EVERY log
event; node authors cannot opt out (the SDK's ctx.log routes through here).

Phase 0 ships the redaction processor + a basic structlog config. The instrumented
ctx.http client (SDK_SPEC.md §4.4) will reuse this same redaction in Phase 1.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

import structlog

# Keys whose values are masked anywhere they appear in a log event's key/value
# pairs. Matched case-insensitively. This list is deliberately broad — over-
# redaction is safe; under-redaction is a credential leak.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "credential",
        "credentials",
        "master_key",
        "ciphertext",
        "x-api-key",
        "cookie",
        "set-cookie",
    }
)

_MASK = "***REDACTED***"


def _redact(value: Any) -> Any:
    """Recursively mask sensitive keys in mappings and sequences."""
    if isinstance(value, Mapping):
        return {
            k: (_MASK if str(k).lower() in SENSITIVE_KEYS else _redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(v) for v in value)
    return value


def redaction_processor(
    _logger: object, _method: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """structlog processor that masks sensitive values in every event.

    Applied to all events before rendering. See SECURITY.md §5.4. A test asserts
    that a known secret value never survives this processor (PHASE_1.md §6).
    """
    return cast("dict[str, Any]", _redact(dict(event_dict)))


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure process-wide structlog. Idempotent; call once at startup."""
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redaction_processor,  # MUST run before the renderer (SECURITY.md §5.4)
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. The redaction processor is always applied."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
