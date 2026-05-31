"""Integration tests for the CredentialVault against real Postgres (SECURITY.md §5).

Proves the vault's persistence + crypto contract end to end: a secret round-trips
through envelope encryption and the database, reads come back masked (label +
last4, never the secret), credentials are strictly org-scoped (cross-org reads are
indistinguishable from absent), and rotate/delete behave. The companion no-leak
test (``test_credential_no_leak.py``) asserts the secret never appears in any row.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.composition.invoker import build_vault_resolver
from axiom.domains.credentials import CredentialNotFoundError, CredentialVault
from axiom.platform.crypto import EnvelopeCipher, FileKeyProvider

pytestmark = pytest.mark.integration

SECRET = "sk-live-apollo-DO-NOT-LEAK-1234"


def _vault(session_factory: async_sessionmaker[AsyncSession]) -> CredentialVault:
    cipher = EnvelopeCipher(FileKeyProvider(b"integration-test-key-material-0001"))
    return CredentialVault(session_factory, cipher)


async def test_bind_then_resolve_round_trips(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    org_id = uuid4()

    view = await vault.bind(org_id=org_id, provider="apollo", label="prod key", secret=SECRET)
    # The bind result is masked — last4 only, no secret anywhere on it.
    assert view.provider == "apollo"
    assert view.label == "prod key"
    assert view.last4 == SECRET[-4:]
    assert SECRET not in repr(view)

    # The engine-only resolve path recovers the plaintext.
    plaintext = await vault.resolve(org_id=org_id, credential_id=view.id)
    assert plaintext == SECRET


async def test_list_and_get_return_masked_views(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    org_id = uuid4()
    await vault.bind(org_id=org_id, provider="openai", label="a", secret=SECRET)
    await vault.bind(org_id=org_id, provider="anthropic", label="b", secret=SECRET + "x")

    listed = await vault.list(org_id=org_id)
    assert {v.provider for v in listed} == {"openai", "anthropic"}
    # No view carries the secret.
    assert all(SECRET not in repr(v) for v in listed)

    one = await vault.get(org_id=org_id, credential_id=listed[0].id)
    assert one.id == listed[0].id


async def test_credentials_are_org_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    owner, intruder = uuid4(), uuid4()
    view = await vault.bind(org_id=owner, provider="apollo", label="k", secret=SECRET)

    # Another org cannot read, resolve, rotate, or delete it — all indistinguishable
    # from "not found" so the vault can't be probed across tenants.
    with pytest.raises(CredentialNotFoundError):
        await vault.get(org_id=intruder, credential_id=view.id)
    with pytest.raises(CredentialNotFoundError):
        await vault.resolve(org_id=intruder, credential_id=view.id)
    with pytest.raises(CredentialNotFoundError):
        await vault.rotate(org_id=intruder, credential_id=view.id, secret="new")
    with pytest.raises(CredentialNotFoundError):
        await vault.delete(org_id=intruder, credential_id=view.id)

    # The intruder's listing never sees the owner's credential.
    assert await vault.list(org_id=intruder) == []


async def test_rotate_replaces_secret_keeping_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    org_id = uuid4()
    view = await vault.bind(org_id=org_id, provider="apollo", label="k", secret=SECRET)

    new_secret = "sk-live-rotated-secret-value-9999"
    rotated = await vault.rotate(org_id=org_id, credential_id=view.id, secret=new_secret)
    assert rotated.id == view.id  # binding is stable across rotation
    assert rotated.last4 == new_secret[-4:]
    assert await vault.resolve(org_id=org_id, credential_id=view.id) == new_secret


async def test_delete_removes_credential(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    org_id = uuid4()
    view = await vault.bind(org_id=org_id, provider="apollo", label="k", secret=SECRET)

    await vault.delete(org_id=org_id, credential_id=view.id)
    with pytest.raises(CredentialNotFoundError):
        await vault.get(org_id=org_id, credential_id=view.id)


async def test_resolve_many_maps_providers_to_plaintext(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    org_id = uuid4()
    apollo = await vault.bind(org_id=org_id, provider="apollo", label="k", secret=SECRET)
    openai = await vault.bind(org_id=org_id, provider="openai", label="k", secret=SECRET + "oa")

    resolved = await vault.resolve_many(
        org_id=org_id,
        refs={"apollo": str(apollo.id), "openai": str(openai.id)},
    )
    assert resolved == {"apollo": SECRET, "openai": SECRET + "oa"}


async def test_resolve_many_rejects_cross_org_reference(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    owner, intruder = uuid4(), uuid4()
    cred = await vault.bind(org_id=owner, provider="apollo", label="k", secret=SECRET)

    # A node in another org cannot resolve the owner's credential even by id.
    with pytest.raises(CredentialNotFoundError):
        await vault.resolve_many(org_id=intruder, refs={"apollo": str(cred.id)})


async def test_resolve_many_rejects_malformed_reference(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    vault = _vault(session_factory)
    with pytest.raises(CredentialNotFoundError):
        await vault.resolve_many(org_id=uuid4(), refs={"apollo": "not-a-uuid"})


# ── The composition resolver adapter (what the engine actually calls) ───────────


def _shared_cipher() -> EnvelopeCipher:
    return EnvelopeCipher(FileKeyProvider(b"integration-test-key-material-0001"))


async def test_vault_resolver_adapter_resolves_declared_credentials(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The engine calls the resolver positionally: resolver(org_id, refs). It must
    # return {provider: plaintext} for exactly the bound credentials.
    cipher = _shared_cipher()
    vault = CredentialVault(session_factory, cipher)
    org_id = uuid4()
    apollo = await vault.bind(org_id=org_id, provider="apollo", label="k", secret=SECRET)

    resolver = build_vault_resolver(session_factory, cipher=cipher)
    resolved = await resolver(org_id, {"apollo": str(apollo.id)})
    assert resolved == {"apollo": SECRET}


async def test_vault_resolver_adapter_short_circuits_empty_refs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A credential-free node passes no refs; the resolver returns {} without a
    # database round-trip.
    resolver = build_vault_resolver(session_factory, cipher=_shared_cipher())
    assert await resolver(uuid4(), {}) == {}


async def test_vault_resolver_adapter_is_org_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cipher = _shared_cipher()
    vault = CredentialVault(session_factory, cipher)
    owner, intruder = uuid4(), uuid4()
    cred = await vault.bind(org_id=owner, provider="apollo", label="k", secret=SECRET)

    resolver = build_vault_resolver(session_factory, cipher=cipher)
    # Another org's run cannot resolve the owner's credential even with its id.
    with pytest.raises(CredentialNotFoundError):
        await resolver(intruder, {"apollo": str(cred.id)})
