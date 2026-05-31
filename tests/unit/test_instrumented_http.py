"""SSRF tests for the instrumented ``ctx.http`` client (PHASE_1.md §10 gate).

The network half of SSRF protection: the egress guard transport must block a
request whose *resolved* address is private/metadata, block IP-literal private
hosts, block disallowed schemes, and — critically — re-validate every redirect
hop so a public→private redirect is caught. The pure policy is unit-tested in
``test_egress.py``; here we drive the real client with an injected resolver and a
``MockTransport`` so the cases are deterministic and need no network.

These satisfy the exit-gate clause: "metadata/private/redirect-to-private
destinations are blocked" (PHASE_1.md §10).
"""

from __future__ import annotations

import httpx
import pytest

from axiom.platform.egress import EgressBlockedError
from axiom.platform.http import build_instrumented_client


def _resolver_for(mapping: dict[str, list[str]]):
    """A fake DNS resolver returning the configured IPs for each host."""

    async def _resolve(host: str, port: int) -> list[str]:
        return mapping.get(host, [])

    return _resolve


def _ok_transport() -> httpx.MockTransport:
    """An inner transport that 200s anything (so a blocked request never reaches it)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="reached")

    return httpx.MockTransport(_handler)


@pytest.mark.unit
async def test_hostname_resolving_to_metadata_ip_is_blocked() -> None:
    client = build_instrumented_client(
        resolver=_resolver_for({"evil.example.com": ["169.254.169.254"]}),
        inner_transport=_ok_transport(),
    )
    async with client:
        with pytest.raises(EgressBlockedError):
            await client.get("https://evil.example.com/latest/meta-data/")


@pytest.mark.unit
@pytest.mark.parametrize("private_ip", ["10.0.0.5", "127.0.0.1", "192.168.1.1", "172.16.0.1"])
async def test_hostname_resolving_to_private_ip_is_blocked(private_ip: str) -> None:
    client = build_instrumented_client(
        resolver=_resolver_for({"internal.example.com": [private_ip]}),
        inner_transport=_ok_transport(),
    )
    async with client:
        with pytest.raises(EgressBlockedError):
            await client.get("https://internal.example.com/")


@pytest.mark.unit
async def test_hostname_resolving_to_public_ip_is_allowed() -> None:
    client = build_instrumented_client(
        resolver=_resolver_for({"api.example.com": ["93.184.216.34"]}),
        inner_transport=_ok_transport(),
    )
    async with client:
        resp = await client.get("https://api.example.com/v1/enrich")
    assert resp.status_code == 200
    assert resp.text == "reached"


@pytest.mark.unit
async def test_any_private_resolution_blocks_even_if_one_is_public() -> None:
    # A name resolving to BOTH a public and a private IP must be blocked — the
    # private answer is the rebinding vector, so "any private → block".
    client = build_instrumented_client(
        resolver=_resolver_for({"mixed.example.com": ["93.184.216.34", "10.0.0.1"]}),
        inner_transport=_ok_transport(),
    )
    async with client:
        with pytest.raises(EgressBlockedError):
            await client.get("https://mixed.example.com/")


@pytest.mark.unit
async def test_ip_literal_private_host_is_blocked_without_dns() -> None:
    # No resolver entry needed — an IP literal is judged directly.
    client = build_instrumented_client(
        resolver=_resolver_for({}),
        inner_transport=_ok_transport(),
    )
    async with client:
        with pytest.raises(EgressBlockedError):
            await client.get("http://169.254.169.254/")
        with pytest.raises(EgressBlockedError):
            await client.get("http://127.0.0.1:8000/admin")


@pytest.mark.unit
async def test_disallowed_scheme_is_blocked() -> None:
    client = build_instrumented_client(resolver=_resolver_for({}), inner_transport=_ok_transport())
    async with client:
        with pytest.raises(EgressBlockedError):
            await client.get("file:///etc/passwd")


@pytest.mark.unit
async def test_public_to_private_redirect_is_blocked() -> None:
    """A public URL that 302-redirects to a private host must be blocked on the hop.

    The whole point of re-validating each redirect: the first request is to a
    public host (allowed, reaches the inner transport), which returns a redirect to
    an internal host — and following that hop must be refused.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example.com":
            return httpx.Response(302, headers={"location": "https://internal.example.com/secret"})
        # If the guard ever lets the redirect through, this would 200 — the test
        # asserts we never get here for the private hop.
        return httpx.Response(200, text="LEAKED")

    client = build_instrumented_client(
        resolver=_resolver_for(
            {"public.example.com": ["93.184.216.34"], "internal.example.com": ["10.0.0.9"]}
        ),
        inner_transport=httpx.MockTransport(_handler),
    )
    async with client:
        with pytest.raises(EgressBlockedError):
            await client.get("https://public.example.com/start")


@pytest.mark.unit
async def test_unresolvable_host_is_blocked() -> None:
    client = build_instrumented_client(
        resolver=_resolver_for({}),  # resolves nothing
        inner_transport=_ok_transport(),
    )
    async with client:
        with pytest.raises(EgressBlockedError):
            await client.get("https://nxdomain.example.com/")
