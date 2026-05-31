"""The `axiom` CLI entrypoint.

Commands:
  * ``version`` / ``info`` — environment + build introspection (no secrets).
  * ``run <workflow.yaml>`` — load a workflow definition, validate its graph,
    and execute it in-process against the built-in node registry, printing
    per-node status and output. This is the "touch real software" surface: no
    database, no services — a workflow of real nodes runs end to end.
  * ``creds add/list/rotate/delete`` — manage the credential vault (WS-2). These
    talk to the real (database-backed) vault and NEVER print a secret back: the
    CLI is bound by the same "use ≠ read" rule as the API (SECURITY.md §5).

The durable, multi-worker execution path is the ``axiom-worker`` process; this
command is the local, synchronous driver for authoring and demos.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import typer
import yaml

from axiom import __version__
from axiom.composition import build_inprocess_engine
from axiom.composition.invoker import build_vault
from axiom.domains.credentials import CredentialNotFoundError, CredentialVault
from axiom.domains.execution.state import ExecutionStatus, NodeStatus
from axiom.domains.workflow_authoring.graph import GraphValidationError, WorkflowGraph
from axiom.platform.config import get_settings
from axiom.platform.db import get_session_factory
from axiom.platform.logging import configure_logging

# The CLI's default tenant — the same dev org the API falls back to, so a key
# added here is visible to a run started through the API in dev. Real auth (WS-4)
# replaces this with the authenticated principal's org.
DEV_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

app = typer.Typer(
    name="axiom",
    help="The orchestration layer for GTM engineers.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the Axiom version."""
    typer.echo(__version__)


@app.command()
def info() -> None:
    """Show the resolved environment configuration (secrets are never printed)."""
    settings = get_settings()
    typer.echo(f"Axiom {__version__}")
    typer.echo(f"  environment:     {settings.environment}")
    typer.echo(f"  secret backend:  {settings.secret_backend}")
    # Note: database_url may contain a password in dev; we show only the scheme/host
    # to avoid leaking credentials to the terminal (SECURITY.md §5.4).
    scheme = settings.database_url.split("://", 1)[0]
    typer.echo(f"  database:        {scheme}://… (redacted)")


# Status → terminal glyph, so a run reads at a glance.
_STATUS_GLYPH = {
    NodeStatus.SUCCEEDED: "✓",
    NodeStatus.FAILED: "✗",
    NodeStatus.SKIPPED: "-",
}


@app.command()
def run(
    workflow: Path = typer.Argument(  # noqa: B008 - Typer declares options as defaults
        ..., exists=True, readable=True, help="Path to a workflow YAML file."
    ),
    input_json: str = typer.Option("{}", "--input", "-i", help="Run input as a JSON object."),
    show_output: bool = typer.Option(
        True, "--output/--no-output", help="Print each node's output object."
    ),
) -> None:
    """Run a workflow YAML in-process against the built-in nodes.

    Loads + validates the graph (cycles, dangling edges, and bad references fail
    here, before anything runs), executes every node in dependency order, and
    prints the result. Exits non-zero if the run fails — so it composes in shell
    scripts and CI.
    """
    raw = _load_yaml(workflow)
    run_input = _parse_input(input_json)

    # The engine logs node lifecycle at INFO; for an interactive command that is
    # noise, so quiet it to WARNING. The human-readable run summary below is the
    # CLI's actual output. (Warnings/errors — e.g. lease recovery — still surface.)
    configure_logging(level="WARNING", json_output=False)

    try:
        graph = WorkflowGraph.from_dict(raw.get("graph", raw))
    except GraphValidationError as exc:
        typer.secho(f"Invalid workflow graph in {workflow}:", fg=typer.colors.RED, err=True)
        for problem in exc.problems:
            typer.secho(f"  • {problem}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    name = raw.get("name", workflow.stem)
    typer.echo(f"Running workflow '{name}' ({len(graph.node_ids())} nodes)…\n")

    result = asyncio.run(_execute(graph, run_input, show_output))
    raise typer.Exit(code=0 if result else 1)


async def _execute(graph: WorkflowGraph, run_input: dict[str, Any], show_output: bool) -> bool:
    """Drive the run to completion and print results. Returns True on success."""
    assembled = build_inprocess_engine()
    execution = await assembled.engine.start_execution(
        org_id=uuid4(),
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        graph=graph,
        run_input=run_input,
    )
    await assembled.engine.run_to_completion(graph)

    final = await assembled.store.get_execution(execution.id)
    states = sorted(await assembled.store.get_node_states(execution.id), key=lambda s: s.node_id)

    for state in states:
        glyph = _STATUS_GLYPH.get(state.status, "?")
        line = f"  {glyph} {state.node_id:<16} {state.status.value}"
        if state.error is not None:
            line += f"  [{state.error.error_class}: {state.error.message}]"
        typer.echo(line)
        if show_output and state.output:
            typer.echo(f"      → {json.dumps(state.output, default=str)}")

    if final is None:  # the run we just created must exist — defensive
        typer.secho("internal error: execution vanished", fg=typer.colors.RED, err=True)
        return False
    succeeded = final.status is ExecutionStatus.SUCCEEDED
    color = typer.colors.GREEN if succeeded else typer.colors.RED
    typer.secho(f"\nRun {final.status.value} — cost {final.total_cost_cents}¢", fg=color, bold=True)
    return succeeded


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        typer.secho(
            f"{path}: expected a YAML mapping at the top level", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    return data


def _parse_input(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        typer.secho(f"--input is not valid JSON: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    if not isinstance(parsed, dict):
        typer.secho("--input must be a JSON object", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    return parsed


# ── Credential vault (WS-2 / SECURITY.md §5) ──────────────────────────────────
# A sub-app so the surface reads as `axiom creds add|list|rotate|delete`. Every
# command goes through the real (database-backed) vault and NEVER prints a secret:
# the CLI is bound by the same "use ≠ read" rule as the API.

creds_app = typer.Typer(name="creds", help="Manage provider credentials (never prints secrets).")
app.add_typer(creds_app)


def _vault() -> CredentialVault:
    """Build the database-backed vault for CLI use (quiet logging)."""
    configure_logging(level="WARNING", json_output=False)
    return build_vault(get_session_factory())


def _print_credential_row(view: Any) -> None:
    """One masked credential line — id, provider, label, last4. Never the secret."""
    tail = f"…{view.last4}" if view.last4 else "(short)"
    typer.echo(f"  {view.id!s}  {view.provider:<14} {tail:<8} {view.label}")


@creds_app.command("add")
def creds_add(
    provider: str = typer.Argument(..., help="Provider name, e.g. 'apollo' or 'openai'."),
    label: str = typer.Option(..., "--label", "-l", help="A human label to tell keys apart."),
) -> None:
    """Bind a new credential. Prompts for the secret with hidden input.

    The secret is read interactively (never passed as an argument, so it can't
    leak into shell history or the process list), encrypted, and stored. Only the
    masked result is printed back.
    """
    secret = typer.prompt(f"Secret for {provider}", hide_input=True)
    if not secret:
        typer.secho("empty secret — aborting", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    async def _run() -> Any:
        return await _vault().bind(org_id=DEV_ORG_ID, provider=provider, label=label, secret=secret)

    view = asyncio.run(_run())
    typer.secho(f"Bound credential {view.id} ({provider}).", fg=typer.colors.GREEN)
    _print_credential_row(view)


@creds_app.command("list")
def creds_list() -> None:
    """List stored credentials as masked rows (label + last4, never the secret)."""
    views = asyncio.run(_vault().list(org_id=DEV_ORG_ID))
    if not views:
        typer.echo("No credentials. Add one with `axiom creds add <provider> -l <label>`.")
        return
    typer.echo(f"{len(views)} credential(s):")
    for view in views:
        _print_credential_row(view)


@creds_app.command("rotate")
def creds_rotate(
    credential_id: str = typer.Argument(..., help="The credential id to rotate."),
) -> None:
    """Replace a credential's secret in place. Prompts for the new secret."""
    try:
        cred_uuid = UUID(credential_id)
    except ValueError as exc:
        typer.secho("credential id must be a UUID", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    secret = typer.prompt("New secret", hide_input=True)

    async def _run() -> Any:
        return await _vault().rotate(org_id=DEV_ORG_ID, credential_id=cred_uuid, secret=secret)

    try:
        view = asyncio.run(_run())
    except CredentialNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(f"Rotated credential {view.id}.", fg=typer.colors.GREEN)
    _print_credential_row(view)


@creds_app.command("delete")
def creds_delete(
    credential_id: str = typer.Argument(..., help="The credential id to delete."),
) -> None:
    """Delete a credential."""
    try:
        cred_uuid = UUID(credential_id)
    except ValueError as exc:
        typer.secho("credential id must be a UUID", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    async def _run() -> None:
        await _vault().delete(org_id=DEV_ORG_ID, credential_id=cred_uuid)

    try:
        asyncio.run(_run())
    except CredentialNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(f"Deleted credential {credential_id}.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
