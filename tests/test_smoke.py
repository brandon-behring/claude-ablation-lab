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
def test_cli_exposes_exactly_the_expected_commands() -> None:
    # Loud set equality derived from the app itself — an `in`-membership list rotted
    # silently once already (`plot` shipped without this test noticing).
    registered = {
        cmd.name or cmd.callback.__name__  # type: ignore[union-attr]
        for cmd in app.registered_commands
    }
    assert registered == {
        "version",
        "run",
        "regrade",
        "estimate",
        "report",
        "compare",
        "advise",
        "plot",
        "judge",
        "judge-report",
        "judge-spotcheck",
    }
