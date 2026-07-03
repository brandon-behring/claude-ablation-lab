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


@pytest.mark.unit
def test_compare_renders_a_real_verdict(tmp_path) -> None:
    # The headline table (_print_compare) — 6 same-sign pairs → exact p = 0.031 → "yes".
    led = tmp_path / "ledger.jsonl"
    configs = [(m, e) for m in ("haiku", "sonnet", "opus") for e in ("low", "high")]
    for i, (model, effort) in enumerate(configs):
        _ledger_row(
            led, run_id=f"a{i}", task_id="t4", variant="d@a", model=model, effort=effort, value=0.2
        )
        _ledger_row(
            led, run_id=f"b{i}", task_id="t4", variant="d@b", model=model, effort=effort, value=0.9
        )
    result = cli.invoke(app, ["compare", str(led), "--a", "d@a", "--b", "d@b"])
    assert result.exit_code == 0
    assert "yes" in result.stdout and "0.031" in result.stdout


@pytest.mark.unit
def test_advise_renders_downgrade(tmp_path) -> None:
    # opus/high and haiku/high tie on quality; haiku far cheaper → a downgrade row.
    led = tmp_path / "ledger.jsonl"
    _ledger_row(led, run_id="o", model="opus", effort="high", value=1.0, cost_usd=0.08)
    _ledger_row(led, run_id="h", model="haiku", effort="high", value=1.0, cost_usd=0.005)
    result = cli.invoke(app, ["advise", str(led), "--reflex", "opus/high"])
    assert result.exit_code == 0
    assert "advise" in result.stdout  # the table title rendered (not the empty-ledger path)
    assert "overpay" in result.stdout  # the savings footnote


@pytest.mark.unit
def test_advise_bad_reflex_exits_2(tmp_path) -> None:
    led = tmp_path / "ledger.jsonl"
    _ledger_row(led, run_id="o", model="opus", effort="high", value=1.0, cost_usd=0.08)
    result = cli.invoke(app, ["advise", str(led), "--reflex", "opusmax"])
    assert result.exit_code == 2
    assert "model/effort" in result.stdout


@pytest.mark.unit
def test_advise_empty_ledger_message(tmp_path) -> None:
    result = cli.invoke(app, ["advise", str(tmp_path / "none.jsonl")])
    assert result.exit_code == 0
    assert "no graded rows" in result.stdout


@pytest.mark.unit
def test_advise_total_excludes_vacuous_rows(tmp_path) -> None:
    # A real downgrade ($0.07 saved) plus an all-zero task (would add $0.04) — the vacuous
    # row must render "n/a" and NOT inflate the headline Σ (the shipped-data bug the review
    # caught: a control variant banking 37% of the total).
    led = tmp_path / "ledger.jsonl"
    _ledger_row(
        led, run_id="a1", task_id="real", model="opus", effort="high", value=1.0, cost_usd=0.08
    )
    _ledger_row(
        led, run_id="a2", task_id="real", model="haiku", effort="high", value=1.0, cost_usd=0.01
    )
    _ledger_row(
        led, run_id="b1", task_id="dead", model="opus", effort="high", value=0.0, cost_usd=0.05
    )
    _ledger_row(
        led, run_id="b2", task_id="dead", model="haiku", effort="high", value=0.0, cost_usd=0.01
    )
    result = cli.invoke(app, ["advise", str(led), "--reflex", "opus/high"])
    assert result.exit_code == 0
    assert "0.0700" in result.stdout  # only the real task's saving; the dead task is excluded
    assert "n/a" in result.stdout  # the dead task is shown but flagged, not silently dropped


def _estimate(status: str) -> object:
    """A canned Estimate; ``estimate`` imports estimate_sweep at call time so we stub it."""
    from claude_ablation_lab import orchestrate

    return orchestrate.Estimate(
        n_cells=4,
        calibration_label="t3_verbatim_anchor/haiku/low",
        calibration_status=status,
        cell_cost_usd=0.01,
        cell_latency_s=5.0,
        cell_turns=1,
        cell_input_tokens=100,
        cell_output_tokens=50,
        projected_cost_usd=0.04,
        projected_wall_clock_s=20.0,
        projected_turns=4,
        projected_input_tokens=400,
        projected_output_tokens=200,
    )


@pytest.mark.unit
def test_estimate_renders_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_ablation_lab import orchestrate
    from claude_ablation_lab.runner import CATALOG_VERIFIED_CLAUDE_VERSION, ClaudeCodeRunner

    captured: dict[str, object] = {}

    def _stub(tasks, grid, *, runner, **kw):  # capture the seam — a swallowing lambda
        captured["task_ids"] = [t.id for t in tasks]  # let a transposed argument pass
        captured["models"] = grid.models
        return _estimate("ok")

    monkeypatch.setattr(orchestrate, "estimate_sweep", _stub)
    # CI has no `claude` binary at all (claude_version() -> None) — estimate now
    # shares run's version gate (D6), so this must not depend on what's installed
    # on the machine running the test.
    monkeypatch.setattr(
        "claude_ablation_lab.provenance.claude_version", lambda: CATALOG_VERIFIED_CLAUDE_VERSION
    )
    # Belt-and-braces: if a refactor ever makes the call-time import miss the patch,
    # fail loudly instead of silently invoking the real `claude` binary from pytest.
    monkeypatch.setattr(
        ClaudeCodeRunner,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("live claude call escaped")),
    )
    result = cli.invoke(
        app,
        [
            "estimate",
            str(REPO / "tasks"),
            str(REPO / "grids" / "smoke.yaml"),
            "--task",
            "t3_verbatim_anchor",
        ],
    )
    assert result.exit_code == 0
    # _print_estimate rendered the per-cell → projected table
    assert "input tokens" in result.stdout and "wall-clock" in result.stdout
    assert captured["task_ids"] == ["t3_verbatim_anchor"]  # the CLI→orchestrate seam
    assert captured["models"] == ("haiku", "sonnet")


@pytest.mark.unit
def test_run_version_gate_blocks_on_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("claude_ablation_lab.provenance.claude_version", lambda: "9.9.9")
    result = cli.invoke(
        app,
        [
            "run",
            str(REPO / "tasks"),
            str(REPO / "grids" / "smoke.yaml"),
            "--task",
            "t3_verbatim_anchor",
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
        ],
    )
    assert result.exit_code == 2
    assert "9.9.9" in result.stdout and "--allow-unverified-tools" in result.stdout


@pytest.mark.unit
def test_run_version_gate_bypassed_with_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_ablation_lab import orchestrate
    from claude_ablation_lab.runner import ClaudeCodeRunner

    monkeypatch.setattr("claude_ablation_lab.provenance.claude_version", lambda: "9.9.9")
    monkeypatch.setattr(
        orchestrate,
        "run_sweep",
        lambda *a, **k: orchestrate.SweepSummary(total=0, ran=0, regraded=0, skipped=0, failed=0),
    )
    monkeypatch.setattr(
        ClaudeCodeRunner,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("live claude call escaped")),
    )
    result = cli.invoke(
        app,
        [
            "run",
            str(REPO / "tasks"),
            str(REPO / "grids" / "smoke.yaml"),
            "--task",
            "t3_verbatim_anchor",
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--allow-unverified-tools",
        ],
    )
    assert result.exit_code == 0


@pytest.mark.unit
def test_run_agent_task_with_declared_tools_prints_info_not_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_ablation_lab import orchestrate
    from claude_ablation_lab.runner import CATALOG_VERIFIED_CLAUDE_VERSION, ClaudeCodeRunner

    monkeypatch.setattr(
        "claude_ablation_lab.provenance.claude_version", lambda: CATALOG_VERIFIED_CLAUDE_VERSION
    )
    monkeypatch.setattr(
        orchestrate,
        "run_sweep",
        lambda *a, **k: orchestrate.SweepSummary(total=0, ran=0, regraded=0, skipped=0, failed=0),
    )
    monkeypatch.setattr(
        ClaudeCodeRunner,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("live claude call escaped")),
    )
    # t2_research_plan.yaml declares tools: [Read, Write, Bash] (D6).
    result = cli.invoke(
        app,
        [
            "run",
            str(REPO / "tasks" / "t2_research_plan.yaml"),
            str(REPO / "grids" / "v1.yaml"),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
        ],
    )
    assert result.exit_code == 0
    assert "tools relaxed" in result.stdout
    assert "Bash" in result.stdout
    assert "score ~0" not in result.stdout  # the no-tools warning must NOT fire


@pytest.mark.unit
def test_run_agent_task_without_declared_tools_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_ablation_lab import orchestrate
    from claude_ablation_lab.runner import CATALOG_VERIFIED_CLAUDE_VERSION, ClaudeCodeRunner

    monkeypatch.setattr(
        "claude_ablation_lab.provenance.claude_version", lambda: CATALOG_VERIFIED_CLAUDE_VERSION
    )
    monkeypatch.setattr(
        orchestrate,
        "run_sweep",
        lambda *a, **k: orchestrate.SweepSummary(total=0, ran=0, regraded=0, skipped=0, failed=0),
    )
    monkeypatch.setattr(
        ClaudeCodeRunner,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("live claude call escaped")),
    )
    task_yaml = tmp_path / "t2_no_tools.yaml"
    task_yaml.write_text(
        "id: t2_research_plan\ndomain: r\ngrader: validator\nmode: agent\n"
        "infra_repo: ~/Claude/research_toolkit\nprompt: '/research-plan x'\n",
        encoding="utf-8",
    )
    result = cli.invoke(
        app,
        [
            "run",
            str(task_yaml),
            str(REPO / "grids" / "v1.yaml"),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
        ],
    )
    assert result.exit_code == 0
    assert "score ~0" in result.stdout


@pytest.mark.unit
def test_estimate_bad_calibration_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_ablation_lab.runner import CATALOG_VERIFIED_CLAUDE_VERSION

    monkeypatch.setattr(
        "claude_ablation_lab.provenance.claude_version", lambda: CATALOG_VERIFIED_CLAUDE_VERSION
    )
    monkeypatch.setattr(
        "claude_ablation_lab.orchestrate.estimate_sweep",
        lambda *a, **k: _estimate("halted"),
    )
    result = cli.invoke(
        app,
        [
            "estimate",
            str(REPO / "tasks"),
            str(REPO / "grids" / "smoke.yaml"),
            "--task",
            "t3_verbatim_anchor",
        ],
    )
    assert result.exit_code == 2
    assert "unreliable" in result.stdout
