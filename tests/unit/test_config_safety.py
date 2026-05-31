"""Tests for configuration safety invariants (SECURITY.md §5.5).

The load-bearing guarantee: we NEVER run with a default/placeholder master key
outside dev. A misconfigured production process must fail fast at startup rather
than serve with a guessable key.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from axiom.platform.config import PLACEHOLDER_MASTER_KEY, Environment, Settings
from axiom.shared.errors import ConfigError


@pytest.mark.unit
def test_placeholder_master_key_rejected_in_production() -> None:
    with pytest.raises((ConfigError, PydanticValidationError)) as exc_info:
        Settings(
            environment=Environment.PRODUCTION,
            master_key=PLACEHOLDER_MASTER_KEY,
        )
    # The ConfigError message is the human-facing reason, whether raised directly
    # or wrapped by pydantic's validation machinery.
    assert "placeholder" in str(exc_info.value).lower()


@pytest.mark.unit
def test_placeholder_master_key_allowed_in_dev() -> None:
    # Dev convenience: the placeholder is tolerated so `docker compose up` works
    # out of the box. Only non-dev environments refuse it.
    settings = Settings(
        environment=Environment.DEV,
        master_key=PLACEHOLDER_MASTER_KEY,
    )
    assert settings.environment is Environment.DEV


@pytest.mark.unit
def test_real_master_key_accepted_in_production() -> None:
    settings = Settings(
        environment=Environment.PRODUCTION,
        master_key="a-real-generated-key-value",
    )
    assert settings.is_production
