"""Unit tests for the SSRF egress filter (SECURITY.md §7, PHASE_1.md §10 gate).

The pure classification half of SSRF protection: given a scheme/host/IP, decide
allow-or-block. The network half (DNS resolution, IP pinning, redirect
re-validation) lives in the instrumented client and is exercised in
``tests/integration/test_instrumented_http.py``. These cases assert the policy
itself — every blocked address class and the classic bypass vectors.
"""

from __future__ import annotations

import pytest

from axiom.platform.egress import (
    EgressBlockedError,
    check_scheme,
    classify_ip,
    ensure_ip_allowed,
    ip_address,
    is_ip_literal_blocked,
    normalize_host,
    validate_url,
)

# ── The metadata endpoint — the single most important block (PHASE_1.md §10) ────


@pytest.mark.unit
def test_cloud_metadata_ip_is_blocked() -> None:
    # 169.254.169.254 is the AWS/GCP/Azure metadata endpoint — the canonical SSRF
    # target for stealing instance credentials. It MUST be blocked.
    assert classify_ip(ip_address("169.254.169.254")) is not None
    with pytest.raises(EgressBlockedError):
        ensure_ip_allowed(ip_address("169.254.169.254"))


# ── Blocked address classes ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",  # RFC-1918
        "10.255.255.255",
        "172.16.0.1",  # RFC-1918
        "172.31.255.255",
        "192.168.1.1",  # RFC-1918
        "127.0.0.1",  # loopback
        "127.1.2.3",
        "0.0.0.0",  # unspecified  # noqa: S104 — a test string for a blocked addr, not a bind
        "169.254.0.1",  # link-local
        "100.64.0.1",  # CGNAT
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
    ],
)
def test_blocked_ipv4_ranges(ip: str) -> None:
    assert classify_ip(ip_address(ip)) is not None, f"{ip} should be blocked"
    with pytest.raises(EgressBlockedError):
        ensure_ip_allowed(ip_address(ip))


@pytest.mark.unit
@pytest.mark.parametrize(
    "ip",
    [
        "::1",  # loopback
        "fe80::1",  # link-local
        "fc00::1",  # unique local
        "fd00::1",  # unique local
        "ff02::1",  # multicast
    ],
)
def test_blocked_ipv6_ranges(ip: str) -> None:
    assert classify_ip(ip_address(ip)) is not None, f"{ip} should be blocked"


# ── Bypass vectors ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ipv4_mapped_ipv6_private_is_unwrapped_and_blocked() -> None:
    # ::ffff:10.0.0.1 actually reaches 10.0.0.1; judging it as opaque IPv6 would
    # be a bypass. It must be unwrapped to the v4 address and blocked.
    assert classify_ip(ip_address("::ffff:10.0.0.1")) is not None
    assert classify_ip(ip_address("::ffff:169.254.169.254")) is not None


@pytest.mark.unit
def test_ipv4_mapped_ipv6_public_is_allowed() -> None:
    # The unwrap must not over-block: a mapped *public* address is still allowed.
    assert classify_ip(ip_address("::ffff:8.8.8.8")) is None


# ── Allowed (public) addresses ──────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert classify_ip(ip_address(ip)) is None, f"{ip} should be allowed"
    ensure_ip_allowed(ip_address(ip))  # does not raise


# ── Scheme allow-list ───────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("scheme", ["https", "http", "HTTPS"])
def test_allowed_schemes(scheme: str) -> None:
    check_scheme(scheme)  # does not raise


@pytest.mark.unit
@pytest.mark.parametrize("scheme", ["file", "gopher", "ftp", "data", "ws", "jar"])
def test_disallowed_schemes(scheme: str) -> None:
    with pytest.raises(EgressBlockedError):
        check_scheme(scheme)


# ── validate_url (scheme + IP-literal host) ─────────────────────────────────────


@pytest.mark.unit
def test_validate_url_allows_public_https() -> None:
    assert validate_url("https://api.example.com/v1/enrich") == "api.example.com"


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # metadata by IP
        "http://127.0.0.1:8000/admin",  # loopback by IP
        "https://10.0.0.5/internal",  # private by IP
        "http://[::1]/",  # ipv6 loopback by IP
    ],
)
def test_validate_url_blocks_ip_literal_private_hosts(url: str) -> None:
    with pytest.raises(EgressBlockedError):
        validate_url(url)


@pytest.mark.unit
@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://evil/", "ftp://host/x"])
def test_validate_url_blocks_disallowed_schemes(url: str) -> None:
    with pytest.raises(EgressBlockedError):
        validate_url(url)


@pytest.mark.unit
def test_validate_url_rejects_missing_host() -> None:
    with pytest.raises(EgressBlockedError):
        validate_url("https:///nohost")


@pytest.mark.unit
def test_hostname_passes_literal_check_for_dns_time_validation() -> None:
    # A hostname is not an IP literal: it passes the literal check and is left for
    # the client to resolve and re-check post-DNS (rebinding defense).
    assert is_ip_literal_blocked("api.example.com") is False
    assert validate_url("https://api.example.com/x") == "api.example.com"


# ── Literal-evasion normalization (trailing dot, IPv6 zone id) ──────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254.",  # trailing-dot metadata IP — must still be seen as a literal
        "127.0.0.1.",  # trailing-dot loopback
        "10.0.0.1.",  # trailing-dot private
    ],
)
def test_trailing_dot_ip_literal_is_still_blocked(host: str) -> None:
    # A trailing dot makes ip_address() raise; without normalization the literal
    # would be mistaken for a hostname and skip the literal check.
    assert is_ip_literal_blocked(host) is True


@pytest.mark.unit
def test_ipv6_zone_id_literal_is_still_blocked() -> None:
    # fe80::1%eth0 is link-local; the zone id must be stripped before classifying.
    assert is_ip_literal_blocked("fe80::1%eth0") is True


@pytest.mark.unit
def test_normalize_host_strips_trailing_dot_and_zone() -> None:
    assert normalize_host("127.0.0.1.") == "127.0.0.1"
    assert normalize_host("fe80::1%eth0") == "fe80::1"
    assert normalize_host("api.example.com") == "api.example.com"
