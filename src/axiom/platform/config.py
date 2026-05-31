"""Application configuration.

Environment-driven settings via pydantic-settings. This module also enforces a
critical security invariant at startup: SECURITY.md §5.5 requires that we NEVER
ship or run with a default/placeholder master key. `get_settings()` refuses to
return in a non-dev environment if the secret master key is missing or is the
known placeholder value.
"""

from __future__ import annotations

import functools
from enum import StrEnum

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from axiom.shared.errors import ConfigError

# The sentinel value used in .env.example. If this ever reaches a running
# process outside dev, we refuse to start (SECURITY.md §5.5).
PLACEHOLDER_MASTER_KEY = "CHANGE_ME_GENERATE_A_REAL_KEY"


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class SecretBackend(StrEnum):
    """Pluggable secret backends (SECURITY.md §5.1, §5.5).

    `file` is for local/simple self-host (a generated master key). `aws_kms`,
    `gcp_kms`, and `vault` are the production envelope-encryption backends.
    """

    FILE = "file"
    AWS_KMS = "aws_kms"
    GCP_KMS = "gcp_kms"
    VAULT = "vault"


class Settings(BaseSettings):
    """Top-level settings, populated from environment variables (prefix AXIOM_).

    Feature-free in Phase 0: this holds the wiring every run mode needs and the
    startup safety checks. Domain-specific settings are added by their phases.
    """

    model_config = SettingsConfigDict(
        env_prefix="AXIOM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEV

    # ── Datastore (source of truth — ADR-0001) ──────────────────────────────
    database_url: str = "postgresql+asyncpg://axiom:axiom@localhost:5432/axiom"
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # ── Cache / transport (NOT source of truth — ADR-0001) ──────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Secrets / vault (SECURITY.md §5) ─────────────────────────────────────
    secret_backend: SecretBackend = SecretBackend.FILE
    # For the FILE backend: path to the master key generated at first run.
    # Never committed (.gitignore); never a default value in non-dev.
    master_key_path: str = ".secrets/master.key"
    # For dev/test only: an inline master key. MUST NOT be the placeholder in
    # any non-dev environment (validated below).
    master_key: str | None = Field(default=None, repr=False)

    # ── API ──────────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"  # noqa: S104 — binds all interfaces inside the container
    api_port: int = 8000
    # Browser origins allowed to call the API cross-origin (CORS). The web app is
    # served from a different origin than the API, so without this a browser
    # blocks every request. Defaults to the local dev frontend ports; set
    # AXIOM_API_CORS_ALLOW_ORIGINS (comma-separated) to the real frontend origin
    # in non-dev. Never a wildcard by default — an explicit allowlist is the safe
    # posture (and required once credentialed auth lands in WS-4).
    api_cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:3010"]
    )

    @field_validator("api_cors_allow_origins", mode="before")
    @classmethod
    def _split_csv_origins(cls, v: object) -> object:
        """Accept a comma-separated string from env, not just JSON.

        pydantic-settings parses list env vars as JSON by default, which is a
        sharp edge for operators. Allow the natural
        ``AXIOM_API_CORS_ALLOW_ORIGINS=https://app.example.com,https://admin...``
        form too.
        """
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ── Logging (SECURITY.md §5.4 — redaction is applied in platform.logging) ─
    log_level: str = "INFO"
    log_json: bool = True  # structured JSON in non-dev; pretty console in dev

    @field_validator("master_key")
    @classmethod
    def _reject_placeholder_outside_dev(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Refuse the placeholder master key outside dev/test (SECURITY.md §5.5).

        This is the structural guarantee that we "never ship a default key": a
        misconfigured production process fails fast at startup rather than
        running with a guessable key.
        """
        env = info.data.get("environment", Environment.DEV)
        if env in (Environment.DEV, Environment.TEST):
            return v
        if v == PLACEHOLDER_MASTER_KEY:
            raise ConfigError(
                "Refusing to start: AXIOM_MASTER_KEY is the placeholder value. "
                "Generate a real key (SECURITY.md §5.5)."
            )
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION


@functools.lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so the environment is read once. Raises ConfigError (via the
    validator) if configuration is unsafe.
    """
    return Settings()
