"""Unit tests for envelope encryption (SECURITY.md §5).

These prove the cryptographic contract the credential vault rests on:
round-trips recover the plaintext, every encryption is unique (no equality
oracle), tampering is detected, ``last4`` never over-discloses, and the
"never ship a default key" rule is enforced when a provider is built.
"""

from __future__ import annotations

import pytest

from axiom.platform.config import (
    PLACEHOLDER_MASTER_KEY,
    Environment,
    SecretBackend,
    Settings,
)
from axiom.platform.crypto import (
    CryptoError,
    EnvelopeCipher,
    FileKeyProvider,
    build_key_provider,
    generate_master_key,
)
from axiom.shared.errors import ConfigError

SECRET = "sk-live-supersecret-DO-NOT-LEAK"


def _cipher() -> EnvelopeCipher:
    return EnvelopeCipher(FileKeyProvider(b"unit-test-key-material-0123456789"))


@pytest.mark.unit
def test_round_trip_recovers_plaintext() -> None:
    cipher = _cipher()
    enc = cipher.encrypt(SECRET)
    assert cipher.decrypt(enc) == SECRET


@pytest.mark.unit
def test_ciphertext_never_contains_plaintext() -> None:
    enc = _cipher().encrypt(SECRET)
    blob = enc.ciphertext + enc.wrapped_dek
    assert SECRET.encode() not in blob
    assert SECRET not in repr(enc)


@pytest.mark.unit
def test_encryption_is_non_deterministic() -> None:
    cipher = _cipher()
    a = cipher.encrypt(SECRET)
    b = cipher.encrypt(SECRET)
    # Fresh DEK + fresh nonce each call → unrelated ciphertexts and wrapped DEKs,
    # so an attacker can't tell two equal secrets apart from their stored form.
    assert a.ciphertext != b.ciphertext
    assert a.wrapped_dek != b.wrapped_dek
    assert cipher.decrypt(a) == cipher.decrypt(b) == SECRET


@pytest.mark.unit
def test_tampered_ciphertext_is_rejected() -> None:
    cipher = _cipher()
    enc = cipher.encrypt(SECRET)
    flipped = bytearray(enc.ciphertext)
    flipped[-1] ^= 0x01  # corrupt the GCM tag
    tampered = enc.__class__(
        ciphertext=bytes(flipped),
        wrapped_dek=enc.wrapped_dek,
        key_id=enc.key_id,
        last4=enc.last4,
    )
    with pytest.raises(CryptoError):
        cipher.decrypt(tampered)


@pytest.mark.unit
def test_tampered_wrapped_dek_is_rejected() -> None:
    cipher = _cipher()
    enc = cipher.encrypt(SECRET)
    flipped = bytearray(enc.wrapped_dek)
    flipped[-1] ^= 0x01
    tampered = enc.__class__(
        ciphertext=enc.ciphertext,
        wrapped_dek=bytes(flipped),
        key_id=enc.key_id,
        last4=enc.last4,
    )
    with pytest.raises(CryptoError):
        cipher.decrypt(tampered)


@pytest.mark.unit
def test_wrong_key_cannot_decrypt() -> None:
    enc = EnvelopeCipher(FileKeyProvider(b"key-material-one-0123456789abcd")).encrypt(SECRET)
    other = EnvelopeCipher(FileKeyProvider(b"key-material-two-0123456789abcd"))
    with pytest.raises(CryptoError):
        other.decrypt(enc)


@pytest.mark.unit
def test_unknown_key_id_is_rejected() -> None:
    enc = _cipher().encrypt(SECRET)
    relabelled = enc.__class__(
        ciphertext=enc.ciphertext,
        wrapped_dek=enc.wrapped_dek,
        key_id="some-other-kek",
        last4=enc.last4,
    )
    with pytest.raises(CryptoError):
        _cipher().decrypt(relabelled)


@pytest.mark.unit
def test_last4_exposes_only_the_tail_of_long_secrets() -> None:
    enc = _cipher().encrypt(SECRET)
    assert enc.last4 == SECRET[-4:]
    assert len(enc.last4) == 4


@pytest.mark.unit
def test_last4_is_empty_for_short_secrets() -> None:
    # A short secret would have most of itself exposed by a 4-char tail, so we
    # disclose nothing instead.
    enc = _cipher().encrypt("abc1234")  # 7 chars, under the threshold
    assert enc.last4 == ""


@pytest.mark.unit
def test_empty_secret_is_refused() -> None:
    with pytest.raises(CryptoError):
        _cipher().encrypt("")


@pytest.mark.unit
def test_key_id_is_recorded_on_encryption() -> None:
    enc = EnvelopeCipher(FileKeyProvider(b"material", key_id="file-v7")).encrypt(SECRET)
    assert enc.key_id == "file-v7"


@pytest.mark.unit
def test_generate_master_key_is_usable_material() -> None:
    from axiom.platform.crypto import _decode_material

    key = generate_master_key()
    cipher = EnvelopeCipher(FileKeyProvider(_decode_material(key)))
    assert cipher.decrypt(cipher.encrypt(SECRET)) == SECRET


# ── Backend selection / the "never ship a default key" rule ─────────────────────


@pytest.mark.unit
def test_placeholder_key_is_refused_outside_dev() -> None:
    # The "never ship a default key" rule (SECURITY.md §5.5) bites at Settings
    # construction: a non-dev environment carrying the placeholder master key is
    # rejected before a vault can ever be built from it.
    with pytest.raises(ConfigError):
        Settings(
            environment=Environment.STAGING,
            secret_backend=SecretBackend.FILE,
            master_key=PLACEHOLDER_MASTER_KEY,
        )


@pytest.mark.unit
def test_build_provider_rejects_unimplemented_kms_backend() -> None:
    settings = Settings(
        environment=Environment.DEV,
        secret_backend=SecretBackend.AWS_KMS,
    )
    with pytest.raises(ConfigError):
        build_key_provider(settings)


@pytest.mark.unit
def test_build_provider_uses_inline_master_key() -> None:
    settings = Settings(
        environment=Environment.DEV,
        secret_backend=SecretBackend.FILE,
        master_key=generate_master_key(),
    )
    cipher = EnvelopeCipher(build_key_provider(settings))
    assert cipher.decrypt(cipher.encrypt(SECRET)) == SECRET
