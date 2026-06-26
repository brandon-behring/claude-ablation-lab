"""Phase 0 smoke tests: the package imports, versions agree, the CLI is wired."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import claude_ablation_lab
from claude_ablation_lab.cli.main import app

runner = CliRunner()


@pytest.mark.unit
def test_version_is_exposed() -> None:
    assert claude_ablation_lab.__version__ == "0.1.0"


@pytest.mark.unit
def test_cli_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == claude_ablation_lab.__version__


@pytest.mark.unit
def test_cli_exposes_expected_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("estimate", "run", "report", "compare"):
        assert cmd in result.stdout


@pytest.mark.unit
def test_unimplemented_commands_fail_cleanly() -> None:
    # `estimate` is the remaining Phase-5 stub: exit non-zero cleanly, not crash.
    result = runner.invoke(app, ["estimate", "tasks", "grids/smoke.yaml"])
    assert result.exit_code == 1
