"""plot.py figure builders (headless structure asserts) + the `ablation plot` CLI.

Agg is forced in plot.py, so these run without a display. We assert *structure* — one
error-bar container per cell, one line per model, the forest's zero line, files written
— never pixels. matplotlib is the optional ``plot`` extra, so the module is guarded.
"""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")  # the `plot` extra; skip locally if not installed

from claude_ablation_lab import plot  # noqa: E402
from claude_ablation_lab.analyze import CompareRow, ReportCell  # noqa: E402


def _cell(
    model: str = "haiku",
    effort: str = "low",
    *,
    value: float = 0.9,
    cost: float = 0.02,
    ci: tuple[float, float] | None = None,
    pareto: bool = False,
    leakage: bool = False,
    task: str = "t3",
) -> ReportCell:
    lo, hi = ci if ci else (None, None)
    return ReportCell(
        task_id=task,
        model=model,
        effort=effort,
        variant="none",
        n_epochs=3,
        n_spec=1,
        mean_value=value,
        sd_value=0.05,
        mean_cost=cost,
        mean_latency=5.0,
        ci_low=lo,
        ci_high=hi,
        shuffled_auroc=None,
        pareto=pareto,
        leakage=leakage,
    )


@pytest.mark.unit
def test_pareto_scatter_one_container_per_cell() -> None:
    cells = [_cell("haiku", "low", pareto=True), _cell("sonnet", "high", leakage=True)]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    assert len(ax.containers) == 2  # one errorbar container per cell


@pytest.mark.unit
def test_pareto_scatter_empty_task_is_safe() -> None:
    fig = plot.pareto_scatter([_cell(task="t3")], task="nope")
    assert "no cells" in fig.axes[0].get_title()


@pytest.mark.unit
def test_effort_curves_one_line_per_model() -> None:
    cells = [_cell("haiku", "low"), _cell("haiku", "high"), _cell("opus", "low")]
    ax = plot.effort_curves(cells, task="t3").axes[0]
    assert {ln.get_label() for ln in ax.get_lines()} == {"haiku", "opus"}


@pytest.mark.unit
def test_ab_forest_has_zero_line_and_row_per_task() -> None:
    rows = [
        CompareRow("t3", 4, 0.5, 0.8, 0.3, 0.1, 0.5, True, ""),
        CompareRow("t2", 4, 0.5, 0.5, 0.0, -0.2, 0.2, False, ""),
    ]
    ax = plot.ab_forest(rows, a="A", b="B").axes[0]
    assert len(ax.get_yticklabels()) == 2  # one row per task
    assert [ln for ln in ax.get_lines() if list(ln.get_xdata()) == [0, 0]]  # the no-effect line


@pytest.mark.unit
def test_render_all_writes_nonempty_files(tmp_path) -> None:
    cells = [_cell("haiku", "low", task="t3"), _cell("opus", "high", task="t1")]
    rows = [CompareRow("t3", 4, 0.5, 0.8, 0.3, 0.1, 0.5, True, "")]
    written = plot.render_all(cells, rows, tmp_path, fmt="png", a="A", b="B")
    assert len(written) == 5  # 2 tasks × (pareto + effort) + 1 forest
    assert all(p.exists() and p.stat().st_size > 0 for p in written)


@pytest.mark.unit
def test_plot_cli_writes_figures(tmp_path) -> None:
    from typer.testing import CliRunner

    from claude_ablation_lab.cli.main import app
    from claude_ablation_lab.ledger import LedgerRow, append_row

    led = tmp_path / "l.jsonl"
    for i, (model, val) in enumerate([("haiku", 0.9), ("opus", 0.8)]):
        append_row(
            led,
            LedgerRow(
                task_id="t3",
                model=model,
                effort="low",
                variant="none",
                epoch=0,
                grader_version="v1",
                run_id=f"r{i}",
                run_status="ok",
                cost_usd=0.01,
                latency_s=1.0,
                returncode=0,
                model_resolved=model,
                num_turns=1,
                session_id="s",
                grade_status="ok",
                value=val,
                spec_sha="S",
                subscores={},
                details={},
                output_path=None,
                ts="2026-01-01",
            ),
        )
    out = tmp_path / "plots"
    result = CliRunner().invoke(app, ["plot", str(led), "--out", str(out)])
    assert result.exit_code == 0
    assert list(out.glob("*.png"))  # at least one figure written
