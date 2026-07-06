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
    lat: float = 5.0,
    ci: tuple[float, float] | None = None,
    cost_ci: tuple[float, float] | None = None,
    lat_ci: tuple[float, float] | None = None,
    out_tok: float | None = None,
    n_token_epochs: int | None = None,
    pareto: bool = False,
    leakage: bool = False,
    task: str = "t3",
    variant: str = "none",
) -> ReportCell:
    lo, hi = ci if ci else (None, None)
    clo, chi = cost_ci if cost_ci else (None, None)
    llo, lhi = lat_ci if lat_ci else (None, None)
    return ReportCell(
        task_id=task,
        model=model,
        effort=effort,
        variant=variant,
        n_epochs=3,
        n_spec=1,
        mean_value=value,
        sd_value=0.05,
        mean_cost=cost,
        mean_latency=lat,
        ci_low=lo,
        ci_high=hi,
        shuffled_auroc=None,
        pareto=pareto,
        leakage=leakage,
        cost_ci_low=clo,
        cost_ci_high=chi,
        latency_ci_low=llo,
        latency_ci_high=lhi,
        mean_output_tokens=out_tok,
        # full coverage by default when measured; override for mixed-era cells
        n_token_epochs=(
            n_token_epochs if n_token_epochs is not None else (3 if out_tok is not None else 0)
        ),
    )


@pytest.mark.unit
def test_pareto_scatter_one_container_per_cell() -> None:
    cells = [_cell("haiku", "low", pareto=True), _cell("sonnet", "high", leakage=True)]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    assert len(ax.containers) == 2  # one errorbar container per cell
    # The advertised encodings, not just counts: the pareto cell is filled with its
    # series colour (not white) and the leaky cell adds the ring scatter collection.
    pareto_line = ax.containers[0][0]
    assert pareto_line.get_markerfacecolor() != "white"
    assert len(ax.collections) == 1  # exactly one red leakage ring


@pytest.mark.unit
def test_pareto_scatter_distinguishes_variants() -> None:
    # A multi-variant ledger (the A/B showcase) must not render two variants of one
    # model identically — one colour per (model, variant) series (review consensus).
    cells = [
        _cell("haiku", "low", variant="d@a", value=0.2),
        _cell("haiku", "low", variant="d@b", value=0.9),
    ]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    colors = {line[0].get_markeredgecolor() for line in ax.containers}
    assert len(colors) == 2  # distinct colours per variant series


@pytest.mark.unit
def test_pareto_scatter_renders_ci_bars_and_frontier() -> None:
    # The live showcase always takes the CI + frontier branches (epochs=3); the suite
    # previously never executed them (review HIGH finding).
    cells = [
        _cell("haiku", "low", value=0.7, cost=0.01, ci=(0.6, 0.8), pareto=True),
        _cell("sonnet", "low", value=0.9, cost=0.10, ci=(0.85, 0.95), pareto=True),
    ]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    assert all(len(c) == 3 and c[2] for c in ax.containers)  # each errorbar has barlines
    frontier = [ln for ln in ax.get_lines() if ln.get_linestyle() == "--"]
    assert frontier and list(frontier[0].get_xdata()) == [0.01, 0.10]  # sorted by cost


@pytest.mark.unit
def test_pareto_scatter_frontier_is_a_staircase() -> None:
    # The frontier is the achievable-quality envelope: flat between points, stepping
    # up at each frontier cell — drawn as a steps-post line, not point-to-point.
    cells = [
        _cell("haiku", "low", value=0.7, cost=0.01, pareto=True),
        _cell("sonnet", "low", value=0.9, cost=0.10, pareto=True),
    ]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    frontier = [ln for ln in ax.get_lines() if ln.get_linestyle() == "--"]
    assert frontier and frontier[0].get_drawstyle() == "steps-post"


@pytest.mark.unit
def test_pareto_scatter_latency_axis_positions_and_labels() -> None:
    cells = [
        _cell("haiku", "low", cost=0.01, lat=30.0, lat_ci=(25.0, 35.0)),
        _cell("sonnet", "low", cost=0.05, lat=5.0),
    ]
    ax = plot.pareto_scatter(cells, task="t3", x_axis="latency").axes[0]
    xs = sorted(float(c[0].get_xdata()[0]) for c in ax.containers)
    assert xs == [5.0, 30.0]  # positioned by latency, not cost
    assert "latency" in ax.get_xlabel()
    # The cell with a latency interval gets x error bars (3-tuple with barlines).
    assert any(len(c) == 3 and c[2] for c in ax.containers)


@pytest.mark.unit
def test_pareto_scatter_tokens_axis_drops_unmeasured_cells() -> None:
    cells = [
        _cell("haiku", "low", out_tok=500.0),
        _cell("opus", "low"),  # unmeasured tokens: dropped from the figure, counted
    ]
    ax = plot.pareto_scatter(cells, task="t3", x_axis="tokens").axes[0]
    assert len(ax.containers) == 1
    assert "1 cell(s) lack tokens data" in ax.get_title()


@pytest.mark.unit
def test_pareto_scatter_tokens_axis_all_unmeasured_is_safe() -> None:
    fig = plot.pareto_scatter([_cell("haiku", "low")], task="t3", x_axis="tokens")
    assert "no cells with a measured tokens axis" in fig.axes[0].get_title()


@pytest.mark.unit
def test_pareto_scatter_tokens_axis_drops_partial_coverage_cells() -> None:
    # A mixed-era cell (tokens on 1 of 3 epochs) is off the token frontier by the
    # analyze.x_value coverage gate — the figure must apply the SAME predicate, so
    # the point is dropped and counted rather than plotted at its partial mean
    # (PR-wide review, F1/F2: figure and flag must never disagree).
    cells = [
        _cell("haiku", "low", out_tok=500.0),
        _cell("opus", "low", out_tok=300.0, n_token_epochs=1),  # partial: 1/3 epochs
    ]
    ax = plot.pareto_scatter(cells, task="t3", x_axis="tokens").axes[0]
    assert len(ax.containers) == 1
    assert "1 cell(s) lack tokens data" in ax.get_title()


@pytest.mark.unit
def test_pareto_scatter_nan_x_cell_is_dropped_and_counted() -> None:
    # A NaN mean_cost (NULL-cost ledger row) is un-Pareto'd by analyze; the figure
    # must drop it through the same x_value predicate — previously it was handed to
    # matplotlib as x=NaN and silently vanished without being counted in the title.
    from dataclasses import replace

    cells = [
        _cell("haiku", "low", cost=0.02),
        replace(_cell("opus", "low"), mean_cost=float("nan")),
    ]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    assert len(ax.containers) == 1
    assert "1 cell(s) lack cost data" in ax.get_title()


@pytest.mark.unit
def test_pareto_scatter_clamps_negative_xerr() -> None:
    # A bootstrap endpoint past the mean (only constructible by hand) must degrade to
    # a zero-length bar, not a matplotlib ValueError: ci low 0.03 > mean 0.02 would
    # produce raw xerr −0.01 without the clamp.
    cells = [
        _cell("haiku", "low", cost=0.02, cost_ci=(0.03, 0.05)),
        _cell("sonnet", "low", cost=0.04),
    ]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]  # must not raise
    assert len(ax.containers) == 2


@pytest.mark.unit
def test_pareto_scatter_rejects_unknown_axis() -> None:
    with pytest.raises(ValueError, match="x_axis"):
        plot.pareto_scatter([_cell()], task="t3", x_axis="bogus")


@pytest.mark.unit
def test_pareto_scatter_wide_cost_range_goes_log() -> None:
    cells = [_cell("haiku", "low", cost=0.005), _cell("opus", "max", cost=0.5)]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    assert ax.get_xscale() == "log"  # 100× spread → log axis


@pytest.mark.unit
def test_pareto_scatter_narrow_cost_range_stays_linear() -> None:
    cells = [_cell("haiku", "low", cost=0.02), _cell("sonnet", "low", cost=0.05)]
    ax = plot.pareto_scatter(cells, task="t3").axes[0]
    assert ax.get_xscale() == "linear"


@pytest.mark.unit
def test_effort_curves_render_ci_band() -> None:
    cells = [
        _cell("haiku", "low", value=0.6, ci=(0.5, 0.7)),
        _cell("haiku", "high", value=0.8, ci=(0.7, 0.9)),
    ]
    ax = plot.effort_curves(cells, task="t3").axes[0]
    assert len(ax.get_lines()) == 1
    assert len(ax.collections) == 1  # the fill_between CI band exists


@pytest.mark.unit
def test_pareto_scatter_empty_task_is_safe() -> None:
    fig = plot.pareto_scatter([_cell(task="t3")], task="nope")
    assert "no cells" in fig.axes[0].get_title()


@pytest.mark.unit
def test_effort_curves_one_line_per_model() -> None:
    cells = [_cell("haiku", "low"), _cell("haiku", "high"), _cell("opus", "low")]
    ax = plot.effort_curves(cells, task="t3").axes[0]
    assert len(ax.get_lines()) == 2  # a set of labels would collapse duplicates
    assert {ln.get_label() for ln in ax.get_lines()} == {"haiku", "opus"}


@pytest.mark.unit
def test_effort_curves_do_not_span_variants() -> None:
    # A model run under two variants must be two separate lines, not one mixed trend.
    cells = [
        _cell("haiku", "low", task="t4", variant="repo@a"),
        _cell("haiku", "high", task="t4", variant="repo@a"),
        _cell("haiku", "low", task="t4", variant="repo@b"),
        _cell("haiku", "high", task="t4", variant="repo@b"),
    ]
    ax = plot.effort_curves(cells, task="t4").axes[0]
    assert len(ax.get_lines()) == 2
    assert {ln.get_label() for ln in ax.get_lines()} == {"haiku @ repo@a", "haiku @ repo@b"}


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
def test_render_all_suffixes_non_cost_axis_filenames(tmp_path) -> None:
    # A latency view must never silently overwrite the historical cost figure.
    cells = [_cell("haiku", "low", task="t3")]
    written = plot.render_all(cells, [], tmp_path, x_axis="latency")
    names = {p.name for p in written}
    assert "t3_pareto_latency.png" in names
    assert "t3_pareto.png" not in names


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


@pytest.mark.unit
def test_plot_cli_ab_forest_wiring(tmp_path) -> None:
    # The CLI → compare → forest path (with the --task filter honoured) was untested.
    from typer.testing import CliRunner

    from claude_ablation_lab.cli.main import app
    from claude_ablation_lab.ledger import LedgerRow, append_row

    led = tmp_path / "l.jsonl"
    base: dict[str, object] = {
        "task_id": "t4",
        "effort": "low",
        "epoch": 0,
        "grader_version": "v1",
        "run_status": "ok",
        "cost_usd": 0.01,
        "latency_s": 1.0,
        "returncode": 0,
        "model_resolved": "m",
        "num_turns": 1,
        "session_id": "s",
        "grade_status": "ok",
        "spec_sha": "S",
        "ts": "2026-01-01",
    }
    for i, model in enumerate(("haiku", "sonnet")):
        append_row(led, LedgerRow(**{**base, "model": model, "variant": "d@a", "run_id": f"a{i}", "value": 0.2}))  # type: ignore[arg-type]
        append_row(led, LedgerRow(**{**base, "model": model, "variant": "d@b", "run_id": f"b{i}", "value": 0.9}))  # type: ignore[arg-type]
    out = tmp_path / "plots"
    result = CliRunner().invoke(
        app, ["plot", str(led), "--out", str(out), "--a", "d@a", "--b", "d@b"]
    )
    assert result.exit_code == 0
    assert (out / "compare_forest.png").exists()
    # And the --task filter suppresses the forest (with a loud note) when nothing matches.
    result2 = CliRunner().invoke(
        app,
        ["plot", str(led), "--out", str(out), "--a", "d@a", "--b", "d@b", "--task", "t4"],
    )
    assert result2.exit_code == 0  # t4 matches → forest present; now a non-matching task:
    result3 = CliRunner().invoke(
        app,
        ["plot", str(led), "--out", str(tmp_path / "p3"), "--a", "d@a", "--b", "nope"],
    )
    assert "no A/B forest" in result3.stdout


@pytest.mark.unit
def test_plot_cli_rejects_partial_ab(tmp_path) -> None:
    from typer.testing import CliRunner

    from claude_ablation_lab.cli.main import app

    # --a without --b is a silent no-forest today; must now fail loudly (before reading the ledger).
    result = CliRunner().invoke(app, ["plot", str(tmp_path / "l.jsonl"), "--a", "onlyA"])
    assert result.exit_code == 2 and "must be given together" in result.stdout


@pytest.mark.unit
def test_plot_cli_rejects_bad_format(tmp_path) -> None:
    from typer.testing import CliRunner

    from claude_ablation_lab.cli.main import app

    result = CliRunner().invoke(app, ["plot", str(tmp_path / "l.jsonl"), "--format", "gif"])
    assert result.exit_code == 2 and "unsupported --format" in result.stdout
