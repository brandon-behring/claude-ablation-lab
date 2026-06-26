"""CLI wiring: dry-run expansion, suite filtering, regrade on an empty ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_ablation_lab.cli.main import app
from claude_ablation_lab.ledger import LedgerRow, append_row

cli = CliRunner()
REPO = Path(__file__).resolve().parents[1]


def _ledger_row(led: Path, **over: object) -> None:
    base: dict[str, object] = {
        "task_id": "t1",
        "model": "haiku",
        "effort": "low",
        "variant": "none",
        "epoch": 0,
        "grader_version": "v1",
        "run_id": "r0",
        "run_status": "ok",
        "cost_usd": 0.01,
        "latency_s": 1.0,
        "returncode": 0,
        "model_resolved": "m",
        "num_turns": 1,
        "session_id": "s",
        "grade_status": "ok",
        "value": 0.8,
        "spec_sha": "S",
        "ts": "2026-01-01",
    }
    base.update(over)
    append_row(led, LedgerRow(**base))  # type: ignore[arg-type]


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


@pytest.mark.unit
def test_report_renders_table(tmp_path) -> None:
    led = tmp_path / "ledger.jsonl"
    _ledger_row(led, run_id="r1", value=0.9, model="opus")
    result = cli.invoke(app, ["report", str(led)])
    assert result.exit_code == 0
    assert "report" in result.stdout and "opus" in result.stdout


@pytest.mark.unit
def test_report_empty_ledger_message(tmp_path) -> None:
    result = cli.invoke(app, ["report", str(tmp_path / "none.jsonl")])
    assert result.exit_code == 0
    assert "no graded rows" in result.stdout


@pytest.mark.unit
def test_compare_renders_or_reports_no_overlap(tmp_path) -> None:
    led = tmp_path / "ledger.jsonl"
    _ledger_row(led, run_id="x", task_id="t2", variant="repo@a", value=0.5)
    result = cli.invoke(app, ["compare", str(led), "--a", "repo@a", "--b", "repo@b"])
    assert result.exit_code == 0
    assert "no task ran under both" in result.stdout  # only A present
