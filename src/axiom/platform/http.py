"""The instrumented outbound HTTP client — ``ctx.http`` (SECURITY.md §7, SDK_SPEC §4.4).

Node authors call ``ctx.http`` exactly like a plain ``httpx.AsyncClient``; the
protections are transparent. This module builds that client by wrapping httpx's
transport with an egress guard so that, on **every** request — including each
redirect hop httpx follows — the destination is validated against the SSRF policy
in ``axiom.platform.egress`` *before* a connection is made:

  1. **Scheme + IP-literal check** (``validate_url``): only http/https; an IP-literal
     host in a blocked range is rejected without DNS.  2. **DNS resolution + per-IP check**: a hostname is resolved and every returned
     address is checked. If *any* resolves into a blocked range, the request is
     refused — this defends against a name that points (or sometimes points) at an
     internal address, e.g. the metadata IP.
  3. **Redirect re-validation**: because httpx issues each redirect as a fresh
     request through this same transport, a public→private redirect is caught by
     the very same guard — no special redirect handling needed here.

The guard raises :class:`~axiom.platform.egress.EgressBlockedError`, which the node
runner maps to a terminal (non-retryable) node error: reaching a forbidden
destination is a bug or an attack, never something to retry.

Known limitation (documented, not hidden): the inner transport re-resolves DNS
independently when it connects, so a racing resolver could in principle return a
public IP to our check and a private IP to the connection (a DNS-rebinding
TOCTOU). Closing it fully requires pinning the socket to the validated IP, which
with httpx breaks TLS SNI/cert validation for https — so Phase 1 ships
resolve-and-check (which blocks the metadata/private/redirect cases the exit gate
requires) and leaves connect-time pinning as a hardening follow-up. The bounded
timeouts and the lease/reaper are the backstop against a hung or abused call.

Note (in-process vs durable): the durable/worker path wires this instrumented
client as ``ctx.http``. The in-process CLI path keeps a plain client so local
development against ``localhost`` still works; multi-tenant safety is owed to the
worker path (the trust-tier reasoning in SECURITY.md §6).
"""

from __future__ import annotations

import socket
from collections.abc import Awaitable, Callable

import httpx

from axiom.platform.egress import (
    EgressBlockedError,
    ensure_ip_allowed,
    ip_address,
    validate_url,
)

# Resolves a hostname to a list of IP strings. Injectable so tests are
# deterministic and network-free (a fake resolver can simulate rebinding).
Resolver = Callable[[str, int], Awaitable[list[str]]]

# Conservative default timeout: connect/read/write/pool all bounded so a slow or
# hung provider can't pin a worker slot indefinitely (the lease/reaper is the
# backstop, but bounded I/O is the first line).
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


async def _system_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` to its IPs via the system resolver (off the event loop)."""
    import asyncio

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port or None, type=socket.SOCK_STREAM)
    # info[4][0] is the IP string for both AF_INET and AF_INET6.
    return [info[4][0] for info in infos]


def _is_ip_literal(host: str) -> bool:
    try:
        ip_address(host)
    except ValueError:
        return False
    return True


class _EgressGuardTransport(httpx.AsyncBaseTransport):
    """Wraps a real transport, enforcing the egress policy before each request.

    Sits in front of httpx's HTTP transport. Every request (and every redirect
    hop, since httpx re-issues redirects through the transport) is validated here
    first; only then is it handed to the inner transport for the actual I/O.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, resolver: Resolver) -> None:
        self._inner = inner
        self._resolve = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        # Scheme + IP-literal host. Raises EgressBlockedError on a bad scheme or a
        # private/metadata IP literal — no DNS needed for a literal.
        validate_url(str(url))

        host = url.host
        if host and not _is_ip_literal(host):
            # A hostname: resolve and check every resolved address. Blocking if
            # ANY resolved IP is private defends against a name that (sometimes)
            # points at an internal address — the rebinding vector.
            ips = await self._resolve(host, url.port or (443 if url.scheme == "https" else 80))
            if not ips:
                raise EgressBlockedError(f"could not resolve host {host!r}")
            for ip in ips:
                ensure_ip_allowed(ip_address(ip))

        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_instrumented_client(
    *,
    timeout: httpx.Timeout | float | None = None,
    resolver: Resolver | None = None,
    inner_transport: httpx.AsyncBaseTransport | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Build the SSRF-guarded ``ctx.http`` client.

    ``resolver`` and ``inner_transport`` are injection seams for tests: a fake
    resolver simulates DNS (including rebinding), and a ``MockTransport`` inner
    lets a test drive redirect behavior without a network. Redirects are followed
    by default so the guard re-validates each hop.
    """
    inner = inner_transport or httpx.AsyncHTTPTransport(retries=0)
    guard = _EgressGuardTransport(inner, resolver or _system_resolver)
    return httpx.AsyncClient(
        transport=guard,
        timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        limits=DEFAULT_LIMITS,
        follow_redirects=follow_redirects,
    )
