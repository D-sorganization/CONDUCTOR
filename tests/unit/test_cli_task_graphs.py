"""Tests for the `maxwell-daemon task-graph ...` subcommand group."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maxwell_daemon.cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_create_prints_valid_standard_graph_json(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        [
            "task-graph",
            "create",
            "wi-1",
            "--title",
            "Ship graph CLI",
            "--criterion",
            "Plan exists",
            "--criterion",
            "Tests pass",
            "--criterion",
            "Review passes",
            "--graph-id",
            "graph-1",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["id"] == "graph-1"
    assert payload["work_item_id"] == "wi-1"
    assert payload["template"] == "standard-delivery"
    assert [node["id"] for node in payload["nodes"]] == [
        "planner",
        "implementer",
        "qa",
        "reviewer",
    ]


def test_create_selects_security_template_from_label(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        [
            "task-graph",
            "create",
            "wi-sec",
            "--label",
            "security",
            "--graph-id",
            "graph-sec",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["template"] == "security-sensitive-delivery"
    assert "security" in {node["id"] for node in payload["nodes"]}


def test_create_rejects_unknown_risk(runner: CliRunner) -> None:
    result = runner.invoke(app, ["task-graph", "create", "wi-1", "--risk", "extreme"])

    assert result.exit_code == 2
    assert "risk must be one of" in result.stdout


def test_create_writes_json_file_and_inspect_renders_nodes(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    target = tmp_path / "graph.json"
    create = runner.invoke(
        app,
        [
            "task-graph",
            "create",
            "wi-1",
            "--template",
            "micro-delivery",
            "--graph-id",
            "graph-1",
            "--output",
            str(target),
        ],
    )
    assert create.exit_code == 0
    assert target.is_file()

    inspect = runner.invoke(app, ["task-graph", "inspect", str(target)])

    assert inspect.exit_code == 0
    assert "implement-qa" in inspect.stdout
    assert "implementer" in inspect.stdout


def test_inspect_json_normalizes_saved_graph(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "graph.json"
    runner.invoke(
        app,
        [
            "task-graph",
            "create",
            "wi-1",
            "--template",
            "micro-delivery",
            "--graph-id",
            "graph-1",
            "--output",
            str(target),
        ],
    )

    inspect = runner.invoke(app, ["task-graph", "inspect", str(target), "--json"])

    assert inspect.exit_code == 0
    payload = json.loads(inspect.stdout)
    assert payload["id"] == "graph-1"
    assert payload["nodes"][0]["output_artifact_kind"] == "handoff"
