# Axiom — single image, multiple run modes (ADR-0002).
#
# The SAME image runs as api, worker, or cli — selected by the MODE env var at
# container start (see docker/entrypoint.sh). This is the modular monolith made
# concrete: one build artifact, no per-service images. Keeps the self-host story
# trivial (BLUEPRINT.md principle 5).
#
# Multi-stage: a builder that installs deps with uv, then a slim runtime.

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv: fast, reproducible installs from the committed lockfile (audit G1).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached layer — only re-runs when deps change).
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

# Now install the project itself.
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Run as a non-root user (defense-in-depth; SECURITY.md posture).
RUN groupadd --system axiom && useradd --system --gid axiom --create-home axiom

WORKDIR /app

# Bring over the resolved virtualenv and the app source.
COPY --from=builder --chown=axiom:axiom /app/.venv /app/.venv
COPY --from=builder --chown=axiom:axiom /app/src /app/src
COPY --from=builder --chown=axiom:axiom /app/migrations /app/migrations
COPY --from=builder --chown=axiom:axiom /app/alembic.ini /app/alembic.ini
COPY --chown=axiom:axiom docker/entrypoint.sh /app/docker/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODE=api

RUN chmod +x /app/docker/entrypoint.sh

USER axiom

# The entrypoint dispatches on $MODE (api | worker | cli).
ENTRYPOINT ["/app/docker/entrypoint.sh"]
