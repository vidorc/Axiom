"""SSRF egress filtering — decide whether an outbound destination is allowed.

This module is the pure decision core behind SECURITY.md §7 ("SSRF & egress
protection"). A workflow runs user-authored nodes that make outbound HTTP calls;
without a filter, a node (or a manifest, or an attacker-controlled redirect) could
reach the cloud metadata endpoint (``169.254.169.254``), a service on loopback, or
something on the private network. The instrumented ``ctx.http`` client
(``node_runtime``) calls into here before every connection and on every redirect
hop; this module does the classification, the client does the I/O (DNS resolution,
pinning) — so the policy itself stays pure and exhaustively unit-testable.

Posture (deliberate): **allow-list the scheme, deny-list the address space.** A
destination is allowed only if its scheme is permitted *and* its resolved IP is a
*global* address that is not in an explicitly blocked range. Over-blocking is
safe; under-blocking is a vulnerability — so the rule is "globally routable public
IP, or rejected", with redundant explicit blocks for the ranges that matter most
(metadata, RFC-1918, loopback, CGNAT) so the intent is legible and independent of
``ipaddress`` version quirks.
"""

from __future__ import annotations

from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from urllib.parse import urlsplit

from axiom.shared.errors import AxiomError

# Schemes a node may fetch. https is the norm for provider APIs; http is allowed
# (the address filter, not the scheme, is the real protection) so scraping a plain
# public page works. Everything else — file://, gopher://, ftp://, data: — is
# rejected outright, since those are classic SSRF/credential-exfil vectors.
ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "http"})

# Explicit, redundant deny-list. ``_is_global`` already rejects all of these, but
# naming them makes the policy auditable and immune to cross-version differences
# in how ``ipaddress`` classifies a given range.
_BLOCKED_V4: tuple[IPv4Network, ...] = (
    IPv4Network("0.0.0.0/8"),  # "this" network / unspecified
    IPv4Network("10.0.0.0/8"),  # RFC-1918 private
    IPv4Network("100.64.0.0/10"),  # CGNAT (RFC-6598) — routable-looking, not public
    IPv4Network("127.0.0.0/8"),  # loopback
    IPv4Network("169.254.0.0/16"),  # link-local — INCLUDES 169.254.169.254 metadata
    IPv4Network("172.16.0.0/12"),  # RFC-1918 private
    IPv4Network("192.0.0.0/24"),  # IETF protocol assignments
    IPv4Network("192.168.0.0/16"),  # RFC-1918 private
    IPv4Network("198.18.0.0/15"),  # benchmarking
    IPv4Network("224.0.0.0/4"),  # multicast
    IPv4Network("240.0.0.0/4"),  # reserved / future use
)
_BLOCKED_V6: tuple[IPv6Network, ...] = (
    IPv6Network("::1/128"),  # loopback
    IPv6Network("::/128"),  # unspecified
    IPv6Network("fc00::/7"),  # unique local (ULA)
    IPv6Network("fe80::/10"),  # link-local
    IPv6Network("ff00::/8"),  # multicast
    IPv6Network("64:ff9b::/96"),  # NAT64 — embeds a v4 address
)


class EgressBlockedError(AxiomError):
    """An outbound destination is not allowed by the egress policy (SSRF guard).

    Raised by the validators for a disallowed scheme or a blocked address. The
    instrumented client turns this into a terminal node error — a node trying to
    reach a forbidden destination is a bug or an attack, never something to retry.
    """


def check_scheme(scheme: str) -> None:
    """Allow only the permitted URL schemes. Raises :class:`EgressBlockedError`."""
    if scheme.lower() not in ALLOWED_SCHEMES:
        raise EgressBlockedError(
            f"scheme {scheme!r} is not allowed (permitted: {sorted(ALLOWED_SCHEMES)})"
        )


def classify_ip(ip: IPv4Address | IPv6Address) -> str | None:
    """Return a human reason if ``ip`` is blocked, or ``None`` if it is allowed.

    The single source of truth for "may we connect to this IP". Unwraps an
    IPv4-mapped IPv6 address first (``::ffff:10.0.0.1`` must be judged as the v4
    address it really reaches — a common filter bypass), then requires the address
    to be globally routable and outside every explicitly blocked range.
    """
    # Unwrap IPv4-mapped IPv6 so a mapped private address can't slip through as v6.
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if not _is_global(ip):
        return f"{ip} is not a global (public) address"

    blocked = _BLOCKED_V4 if isinstance(ip, IPv4Address) else _BLOCKED_V6
    for net in blocked:
        if ip in net:
            return f"{ip} is in blocked range {net}"
    return None


def ensure_ip_allowed(ip: IPv4Address | IPv6Address) -> None:
    """Raise :class:`EgressBlockedError` if ``ip`` is not an allowed destination."""
    reason = classify_ip(ip)
    if reason is not None:
        raise EgressBlockedError(f"blocked egress to {reason}")


def is_ip_literal_blocked(host: str) -> bool:
    """True if ``host`` is an IP literal that the policy blocks.

    Used to reject a URL whose host is already an IP (no DNS needed). A hostname
    (not an IP literal) returns ``False`` here — it must be resolved and each
    resolved IP checked with :func:`ensure_ip_allowed` by the client.
    """
    try:
        ip = ip_address(host)
    except ValueError:
        return False  # not an IP literal — defer to DNS-time resolution
    return classify_ip(ip) is not None


def validate_url(url: str) -> str:
    """Validate a URL's scheme and (if the host is an IP literal) its address.

    Returns the host for the caller to resolve. Raises :class:`EgressBlockedError`
    for a disallowed scheme or an IP-literal host in a blocked range. A hostname
    passes this gate and is checked again post-DNS — defending against a name that
    only resolves to a private IP, and against DNS rebinding.
    """
    parts = urlsplit(url)
    check_scheme(parts.scheme)
    host = parts.hostname
    if not host:
        raise EgressBlockedError(f"URL has no host: {url!r}")
    if is_ip_literal_blocked(host):
        raise EgressBlockedError(f"blocked egress to IP-literal host {host!r}")
    return host


def _is_global(ip: IPv4Address | IPv6Address) -> bool:
    """Whether the address is globally routable (public).

    ``ipaddress``'s ``is_global`` is the well-defined inverse of the private/
    reserved/loopback/link-local space; we treat anything not global as blocked.
    """
    return bool(ip.is_global)


# Re-exported so callers can build a denylist check without importing ipaddress.
__all__ = [
    "ALLOWED_SCHEMES",
    "EgressBlockedError",
    "check_scheme",
    "classify_ip",
    "ensure_ip_allowed",
    "ip_address",
    "ip_network",
    "is_ip_literal_blocked",
    "validate_url",
]
