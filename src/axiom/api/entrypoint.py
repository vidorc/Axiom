"""API run-mode entrypoint (`axiom-api`).

Launches uvicorn against the app factory. This is the process the container runs
when MODE=api (see the Docker entrypoint).
"""

from __future__ import annotations

import uvicorn

from axiom.platform.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "axiom.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,  # structlog owns logging (SECURITY.md §5.4 redaction)
    )


if __name__ == "__main__":
    main()
