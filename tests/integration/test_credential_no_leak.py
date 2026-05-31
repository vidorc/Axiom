"""Credential HTTP surface + the no-leak guarantee (SECURITY.md §5, PHASE_1.md §6).

Two things proven here against the real app + real Postgres:

  1. **The HTTP surface behaves** — bind/list/get/rotate/delete work, every
     response is masked (label + last4, never the secret), credentials are
     org-scoped, and the secret is write-only (accepted, never echoed).

  2. **The no-leak guarantee** — the single most important credential test
     (PHASE_1.md §6). After binding a known secret through the API we assert it
     appears in **no database column**, **no API response body**, and **no log
     event**. This is the structural proof behind "never expose user credentials"
     (BLUEPRINT.md principle 6).

Requires the test datastore (``make test-stack-up``); the session fixture skips
cleanly if Postgres is unreachable.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
import structlog
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.api.app import create_app
from axiom.composition import build_durable_engine

pytestmark = pytest.mark.integration

# A long, unique value so an accidental substring match against unrelated data is
# impossible — if these bytes appear anywhere, it is genuinely our secret leaking.
SECRET = "sk-live-NOLEAK-7f3a9c1e5b2d8a4f6e0c-apollo-DO-NOT-LEAK"


def _client(session_factory: async_sessionmaker[AsyncSession]) -> httpx.AsyncClient:
    """An async HTTP client over the app, sharing one durable engine (and vault)."""
    app = create_app()
    app.state.durable = build_durable_engine(session_factory)
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ── HTTP surface behaviour ──────────────────────────────────────────────────────


async def test_bind_returns_masked_view_not_the_secret(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.post(
            "/v1/credentials",
            json={"provider": "apollo", "label": "prod", "secret": SECRET},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "apollo"
    assert body["label"] == "prod"
    assert body["last4"] == SECRET[-4:]
    # The response carries no secret field, and the secret value is absent entirely.
    assert "secret" not in body
    assert SECRET not in resp.text


async def test_list_and_get_are_masked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        created = (
            await client.post(
                "/v1/credentials",
                json={"provider": "openai", "label": "k", "secret": SECRET},
            )
        ).json()

        listed = await client.get("/v1/credentials")
        assert listed.status_code == 200
        assert SECRET not in listed.text
        assert any(c["id"] == created["id"] for c in listed.json())

        got = await client.get(f"/v1/credentials/{created['id']}")
        assert got.status_code == 200
        assert SECRET not in got.text
        assert got.json()["last4"] == SECRET[-4:]


async def test_rotate_changes_last4_without_revealing_secret(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        created = (
            await client.post(
                "/v1/credentials",
                json={"provider": "apollo", "label": "k", "secret": SECRET},
            )
        ).json()

        new_secret = "sk-live-ROTATED-value-2222"
        rotated = await client.post(
            f"/v1/credentials/{created['id']}/rotate", json={"secret": new_secret}
        )
        assert rotated.status_code == 200
        assert rotated.json()["id"] == created["id"]
        assert rotated.json()["last4"] == new_secret[-4:]
        assert new_secret not in rotated.text


async def test_delete_then_get_is_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        created = (
            await client.post(
                "/v1/credentials",
                json={"provider": "apollo", "label": "k", "secret": SECRET},
            )
        ).json()
        assert (await client.delete(f"/v1/credentials/{created['id']}")).status_code == 204
        assert (await client.get(f"/v1/credentials/{created['id']}")).status_code == 404


async def test_credentials_are_org_scoped_over_http(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    other_org = str(uuid4())
    async with _client(session_factory) as client:
        created = (
            await client.post(
                "/v1/credentials",
                json={"provider": "apollo", "label": "k", "secret": SECRET},
            )
        ).json()
        # A different org (via X-Org-Id) cannot see or fetch it.
        headers = {"X-Org-Id": other_org}
        assert (await client.get("/v1/credentials", headers=headers)).json() == []
        assert (
            await client.get(f"/v1/credentials/{created['id']}", headers=headers)
        ).status_code == 404


async def test_empty_secret_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.post(
            "/v1/credentials", json={"provider": "apollo", "label": "k", "secret": ""}
        )
    assert resp.status_code == 422  # min_length=1 on the write-only secret field


# ── The no-leak guarantee (PHASE_1.md §6) ───────────────────────────────────────


async def _all_credential_cell_values(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[object]:
    """Every cell of every credential row — what is actually persisted on disk."""
    async with session_factory() as session:
        result = await session.execute(text("SELECT * FROM credential"))
        return [value for row in result.mappings().all() for value in row.values()]


async def test_secret_never_appears_in_database_response_or_logs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Bind a known secret, then prove it leaked into nothing (PHASE_1.md §6).

    The strongest form of the guarantee: capture log events at the source (before
    any rendering/redaction), drive every read path, and scan every persisted
    column — the secret must be absent from all three.
    """
    secret_bytes = SECRET.encode("utf-8")

    # Capture log events as the vault emits them — this proves the vault never even
    # hands the secret to logging, independent of the redaction safety net.
    with structlog.testing.capture_logs() as captured:
        async with _client(session_factory) as client:
            created = (
                await client.post(
                    "/v1/credentials",
                    json={"provider": "apollo", "label": "prod key", "secret": SECRET},
                )
            ).json()
            await client.get("/v1/credentials")
            await client.get(f"/v1/credentials/{created['id']}")
            await client.post(
                f"/v1/credentials/{created['id']}/rotate",
                json={"secret": SECRET + "-rotated"},
            )

    # 1. No log event — at any level — carries the secret.
    log_blob = repr(captured)
    assert SECRET not in log_blob, "secret leaked into a log event"

    # 2. No persisted column carries the secret, as text or as raw bytes. The
    #    ciphertext/wrapped_dek are bytea; the rest are text — none may contain it.
    for value in await _all_credential_cell_values(session_factory):
        if isinstance(value, (bytes, bytearray)):
            assert secret_bytes not in bytes(value), "secret bytes found in a bytea column"
        else:
            assert SECRET not in str(value), "secret found in a text column"


async def test_resolve_recovers_the_secret_proving_storage_is_real(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The flip side of no-leak: the secret IS recoverable on the engine-only path.

    Guards against a false pass where "no leak" is trivially true because nothing
    was stored. The engine's internal resolve must round-trip the exact secret,
    even though no HTTP/DB-scan path ever reveals it.
    """
    async with _client(session_factory) as client:
        created = (
            await client.post(
                "/v1/credentials",
                json={"provider": "apollo", "label": "k", "secret": SECRET},
            )
        ).json()
        durable = client._transport.app.state.durable  # type: ignore[attr-defined]

    from axiom.api.dependencies import DEV_ORG_ID

    recovered = await durable.vault.resolve(org_id=DEV_ORG_ID, credential_id=created["id"])
    assert recovered == SECRET
