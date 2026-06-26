"""CLI wiring: dry-run expansion, suite filtering, regrade on an empty ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_ablation_lab.cli.main import app

cli = CliRunner()
REPO = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_run_dry_run_expands_without_calling_claude() -> None:
    result = cli.invoke(
        app,
        [
            "run",
            str(REPO / "tasks"),
            str(REPO / "grids" / "smoke.yaml"),
            "--task",
            "t3_verbatim_anchor",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "4 cells" in result.stdout  # 2 models × 2 efforts × 1 epoch


@pytest.mark.unit
def test_run_unknown_task_fails_cleanly() -> None:
    result = cli.invoke(
        app,
        ["run", str(REPO / "tasks"), str(REPO / "grids" / "smoke.yaml"), "--task", "nope"],
    )
    assert result.exit_code == 1
    assert "no tasks selected" in result.stdout


@pytest.mark.unit
def test_regrade_on_empty_ledger_is_noop(tmp_path) -> None:
    result = cli.invoke(
        app,
        [
            "regrade",
            str(REPO / "tasks" / "t3_verbatim_anchor.yaml"),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
        ],
    )
    assert result.exit_code == 0
    assert "sweep summary" in result.stdout
