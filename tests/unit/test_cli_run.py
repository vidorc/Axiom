"""Unit tests for the `axiom run` CLI command.

Drives the command through Typer's CliRunner against the example workflow and
ad-hoc YAML written to a tmp_path. Asserts on exit codes (so the command composes
in scripts) and on the rendered result. No services — `axiom run` executes
in-process against the built-in nodes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from axiom.cli.main import app

pytestmark = pytest.mark.unit

runner = CliRunner()

# Repo-root example workflow (tests/unit/ → ../../examples/...).
_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "workflows" / "hello.yaml"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "wf.yaml"
    path.write_text(text)
    return path


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()  # prints a version string


def test_run_example_workflow_succeeds() -> None:
    result = runner.invoke(app, ["run", str(_EXAMPLE), "--input", '{"email": "ada@example.com"}'])
    assert result.exit_code == 0, result.stdout
    assert "Run succeeded" in result.stdout
    # The fan-in merged both branches onto finalize's input/output.
    assert "stage" in result.stdout
    assert "source" in result.stdout


def test_run_linear_chain_passes_data(tmp_path: Path) -> None:
    wf = _write(
        tmp_path,
        """
name: chain
graph:
  nodes:
    - id: a
      node_ref: axiom/echo
    - id: b
      node_ref: axiom/set-fields
      config:
        fields:
          tagged: true
  edges:
    - { from: a, to: b }
""",
    )
    result = runner.invoke(app, ["run", str(wf), "--input", '{"x": 1}'])
    assert result.exit_code == 0, result.stdout
    assert "Run succeeded" in result.stdout
    assert "tagged" in result.stdout  # b's output carried the static field


def test_run_invalid_graph_exits_2(tmp_path: Path) -> None:
    # A cycle must be rejected at load time with a clear message and exit 2.
    wf = _write(
        tmp_path,
        """
name: cyclic
graph:
  nodes:
    - { id: a, node_ref: axiom/echo }
    - { id: b, node_ref: axiom/echo }
  edges:
    - { from: a, to: b }
    - { from: b, to: a }
""",
    )
    result = runner.invoke(app, ["run", str(wf)])
    assert result.exit_code == 2
    assert "cycle" in result.stdout.lower() or "cycle" in str(result.output).lower()


def test_run_failing_node_exits_1(tmp_path: Path) -> None:
    # set-fields with a non-object `fields` fails validation → run fails → exit 1.
    wf = _write(
        tmp_path,
        """
name: bad
graph:
  nodes:
    - id: a
      node_ref: axiom/set-fields
      config:
        fields: "not-an-object"
  edges: []
""",
    )
    result = runner.invoke(app, ["run", str(wf)])
    assert result.exit_code == 1
    assert "failed" in result.stdout.lower()


def test_run_bad_input_json_exits_2(tmp_path: Path) -> None:
    wf = _write(
        tmp_path,
        """
name: ok
graph:
  nodes:
    - { id: a, node_ref: axiom/echo }
  edges: []
""",
    )
    result = runner.invoke(app, ["run", str(wf), "--input", "{not json}"])
    # Exit 2 is the contract; the explanatory message is written to stderr
    # (where errors belong), so we assert on the code, not stdout.
    assert result.exit_code == 2


def test_run_unknown_node_ref_exits_1(tmp_path: Path) -> None:
    wf = _write(
        tmp_path,
        """
name: missing-node
graph:
  nodes:
    - { id: a, node_ref: axiom/nope }
  edges: []
""",
    )
    result = runner.invoke(app, ["run", str(wf)])
    assert result.exit_code == 1
    assert "internal" in result.stdout.lower()
