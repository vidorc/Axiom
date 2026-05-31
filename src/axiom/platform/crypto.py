"""Envelope encryption for the credential vault (SECURITY.md §5).

The vault never encrypts a secret directly under one long-lived key. Instead, the
standard *envelope* scheme:

  1. A fresh random **DEK** (data encryption key, AES-256) is minted per secret.
  2. The DEK encrypts the plaintext with AES-256-GCM (authenticated; a random
     96-bit nonce per encryption).
  3. The DEK itself is **wrapped** (encrypted) by a long-lived **KEK** (key
     encryption key) that lives behind a :class:`KeyProvider`. Only the wrapped
     DEK is stored — the KEK never touches the database.

Why envelope rather than "encrypt under the master key directly":

  * **Rotation.** Rotating the KEK only re-wraps DEKs; the (large, numerous)
    ciphertexts are untouched. ``key_id`` records which KEK wrapped each DEK so a
    rotation can find what still needs re-wrapping.
  * **Backend pluggability (SECURITY.md §5.1).** The KEK can live in a file
    (self-host) or a real KMS (cloud) where the unwrap happens *inside* the KMS
    and the KEK never enters our process. The :class:`KeyProvider` protocol is the
    seam; only the wrap/unwrap operation crosses it.

This module is pure platform infrastructure: it depends on the ``cryptography``
library and ``axiom.platform.config`` only, never on any domain. The credential
*domain* (``axiom.domains.credentials``) composes a cipher with persistence; the
cryptography itself lives here so it has exactly one well-reviewed home.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from axiom.platform.config import (
    PLACEHOLDER_MASTER_KEY,
    Environment,
    SecretBackend,
    Settings,
)
from axiom.platform.config import get_settings as _get_settings
from axiom.platform.logging import get_logger
from axiom.shared.errors import AxiomError, ConfigError

logger = get_logger(__name__)

# AES-256 key + AES-GCM nonce sizes, in bytes.
_KEY_BYTES = 32
_NONCE_BYTES = 12

# HKDF parameters used to normalise arbitrary key *material* (a generated key, a
# dev passphrase, a file's bytes) into a uniform 32-byte KEK. The salt and info
# are not secret — they domain-separate this derivation from any other use of the
# same material elsewhere in the system.
_HKDF_SALT = b"axiom.vault.kek.v1"
_HKDF_INFO = b"axiom-credential-kek"


class CryptoError(AxiomError):
    """Encryption or decryption failed (bad key, tampered ciphertext, etc.).

    Decryption failures are deliberately opaque: the caller learns the operation
    failed, never *why*, so a tampering oracle cannot be built from the message.
    """


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """The stored form of a secret: everything needed to decrypt *except* the KEK.

    ``ciphertext`` is the GCM output (nonce-prefixed) of the plaintext under the
    DEK; ``wrapped_dek`` is the GCM output (nonce-prefixed) of the DEK under the
    KEK; ``key_id`` records which KEK wrapped it (for rotation); ``last4`` is the
    only plaintext-derived value safe to display (SECURITY.md §5 — ``last4``-only).
    """

    ciphertext: bytes
    wrapped_dek: bytes
    key_id: str
    last4: str


@runtime_checkable
class KeyProvider(Protocol):
    """Wraps/unwraps DEKs under a long-lived KEK (SECURITY.md §5.1).

    The one seam between the cipher and where the KEK actually lives. A file-based
    provider holds the KEK in process memory; a KMS-backed provider would forward
    wrap/unwrap to the KMS so the KEK never enters our address space. Either way
    the cipher only ever sees the wrap/unwrap operations, never the KEK itself.
    """

    @property
    def key_id(self) -> str:
        """Stable identifier of the current KEK, recorded on every wrapped DEK."""
        ...

    def wrap(self, dek: bytes) -> bytes:
        """KEK-encrypt a freshly minted DEK for storage."""
        ...

    def unwrap(self, wrapped_dek: bytes, *, key_id: str) -> bytes:
        """KEK-decrypt a stored wrapped DEK. ``key_id`` selects the KEK version."""
        ...


class FileKeyProvider:
    """A KEK held in process memory, for self-host deployments (SecretBackend.FILE).

    The KEK is derived (HKDF-SHA256) from configured key *material* — an inline
    ``AXIOM_MASTER_KEY`` (dev) or the bytes of ``master_key_path`` (self-host,
    generated once at first run). Deriving means any sufficiently-random material
    yields a uniform 32-byte key, and the same material always derives the same
    KEK, so secrets survive a restart.
    """

    __slots__ = ("_kek", "_key_id")

    def __init__(self, key_material: bytes, *, key_id: str = "file-v1") -> None:
        if not key_material:
            raise ConfigError("vault key material is empty")
        self._kek = _derive_key(key_material)
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def wrap(self, dek: bytes) -> bytes:
        return _gcm_encrypt(self._kek, dek)

    def unwrap(self, wrapped_dek: bytes, *, key_id: str) -> bytes:
        # A single in-memory KEK: a mismatching key_id means the wrapped DEK was
        # produced under a KEK this process does not hold — surface it rather than
        # attempt a decryption that can only fail opaquely.
        if key_id != self._key_id:
            raise CryptoError(f"no key for key_id {key_id!r}")
        return _gcm_decrypt(self._kek, wrapped_dek)


class EnvelopeCipher:
    """Encrypt/decrypt secrets under per-secret DEKs wrapped by a :class:`KeyProvider`.

    Stateless aside from the provider. ``encrypt`` mints a fresh DEK every call, so
    two encryptions of the same plaintext yield unrelated ciphertexts (no equality
    oracle). ``decrypt`` unwraps the DEK via the provider, then opens the GCM
    ciphertext — any tampering with either blob fails the GCM tag and raises
    :class:`CryptoError`.
    """

    __slots__ = ("_keys",)

    def __init__(self, key_provider: KeyProvider) -> None:
        self._keys = key_provider

    def encrypt(self, plaintext: str) -> EncryptedSecret:
        if plaintext == "":
            raise CryptoError("refusing to encrypt an empty secret")
        dek = os.urandom(_KEY_BYTES)
        ciphertext = _gcm_encrypt(dek, plaintext.encode("utf-8"))
        wrapped = self._keys.wrap(dek)
        return EncryptedSecret(
            ciphertext=ciphertext,
            wrapped_dek=wrapped,
            key_id=self._keys.key_id,
            last4=_last4(plaintext),
        )

    def decrypt(self, secret: EncryptedSecret) -> str:
        dek = self._keys.unwrap(secret.wrapped_dek, key_id=secret.key_id)
        plaintext = _gcm_decrypt(dek, secret.ciphertext)
        return plaintext.decode("utf-8")


# ── Primitives ──────────────────────────────────────────────────────────────────


def _derive_key(material: bytes) -> bytes:
    """HKDF-SHA256 arbitrary key material into a uniform 32-byte key."""
    return HKDF(algorithm=SHA256(), length=_KEY_BYTES, salt=_HKDF_SALT, info=_HKDF_INFO).derive(
        material
    )


def _gcm_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """AES-256-GCM encrypt, returning ``nonce || ciphertext_with_tag``."""
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def _gcm_decrypt(key: bytes, blob: bytes) -> bytes:
    """Inverse of :func:`_gcm_encrypt`. Raises ``CryptoError`` on any failure."""
    if len(blob) <= _NONCE_BYTES:
        raise CryptoError("ciphertext too short")
    nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:  # InvalidTag and friends — never reveal which/why
        raise CryptoError("decryption failed") from exc


def _last4(plaintext: str) -> str:
    """The last 4 chars of a secret — the only plaintext-derived display value.

    Short secrets reveal nothing: under 8 characters we return no tail at all
    rather than expose most of a weak key.
    """
    return plaintext[-4:] if len(plaintext) >= 8 else ""


# ── Backend selection ─────────────────────────────────────────────────────────


def build_key_provider(settings: Settings | None = None) -> KeyProvider:
    """Construct the configured KEK provider (SECURITY.md §5.1, §5.5).

    ``FILE`` is the self-host backend implemented here. KMS backends are the
    cloud path (Phase 6) and raise a clear "not yet" rather than silently falling
    back to something weaker. The placeholder master key is refused outside
    dev/test — the structural "never ship a default key" guarantee, mirrored from
    config validation so it also bites callers that construct a provider directly.
    """
    settings = settings or _get_settings()

    if settings.secret_backend is not SecretBackend.FILE:
        raise ConfigError(
            f"secret backend {settings.secret_backend} is not yet implemented "
            "(KMS backends land in Phase 6); use the FILE backend for self-host."
        )

    material = _load_file_key_material(settings)
    return FileKeyProvider(material)


def _load_file_key_material(settings: Settings) -> bytes:
    """Resolve the FILE backend's KEK material, enforcing the no-placeholder rule.

    Order: an inline ``AXIOM_MASTER_KEY`` (dev convenience) wins; otherwise the
    bytes of ``master_key_path`` (generated once at first run). In dev/test with
    *nothing* configured we mint an ephemeral key so the stack runs — but we log a
    loud warning, because anything encrypted under it dies with the process. In a
    non-dev environment a missing or placeholder key is fatal.
    """
    is_dev = settings.environment in (Environment.DEV, Environment.TEST)

    if settings.master_key and settings.master_key != PLACEHOLDER_MASTER_KEY:
        return _decode_material(settings.master_key)

    if settings.master_key == PLACEHOLDER_MASTER_KEY and not is_dev:
        raise ConfigError(
            "Refusing to build the vault with the placeholder master key "
            "(SECURITY.md §5.5). Generate a real key."
        )

    key_path = Path(settings.master_key_path)
    if key_path.exists():
        data = key_path.read_bytes()
        if data:
            return data

    if is_dev:
        logger.warning(
            "vault.ephemeral_key",
            detail=(
                "No master key configured; generated an ephemeral in-memory key. "
                "Credentials encrypted now will NOT survive a restart. Set "
                "AXIOM_MASTER_KEY or generate one at AXIOM_MASTER_KEY_PATH."
            ),
        )
        return _ephemeral_key()

    raise ConfigError(
        "No vault master key available: set AXIOM_MASTER_KEY or generate a key at "
        f"{settings.master_key_path} (SECURITY.md §5.5)."
    )


def _decode_material(value: str) -> bytes:
    """Accept either a base64-encoded key or a raw passphrase as key material.

    A base64 value (what ``generate_master_key`` emits) decodes to its bytes; any
    other string is used as-is. Both are run through HKDF downstream, so either
    yields a uniform 32-byte KEK.
    """
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return value.encode("utf-8")
    return decoded or value.encode("utf-8")


def generate_master_key() -> str:
    """Mint a fresh base64-encoded 256-bit master key (for ``axiom`` setup tooling)."""
    return base64.b64encode(os.urandom(_KEY_BYTES)).decode("ascii")


# A single ephemeral key per process, minted lazily on first use. Memoised at
# module scope so that EVERY ``build_key_provider`` call in a process that has no
# master key configured derives the SAME KEK — without this, a second vault built
# in-process (e.g. the API's vault vs. a separately-built resolver) would mint a
# fresh random key and fail to decrypt what the first vault encrypted. It is still
# process-scoped and does NOT survive a restart (the warning above stands); it
# only guarantees intra-process consistency.
_EPHEMERAL_KEY: bytes | None = None


def _ephemeral_key() -> bytes:
    global _EPHEMERAL_KEY
    if _EPHEMERAL_KEY is None:
        _EPHEMERAL_KEY = os.urandom(_KEY_BYTES)
    return _EPHEMERAL_KEY
