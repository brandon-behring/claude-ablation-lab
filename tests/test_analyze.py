"""Phase 4 analysis: report aggregation (dedupe/Pareto/leakage) + compare bootstrap."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from claude_ablation_lab.analyze import ReportCell, compare, cost_advisor, report
from claude_ablation_lab.ledger import LedgerRow, append_row

# eval_toolkit backs only the bootstrap CIs (report cells with >=3 epochs, compare
# with >=2 configs). Those tests guard individually with importorskip below, so the
# rest of the report/compare logic stays covered even without the optional dep.


def _row(
    led,
    *,
    rid,
    task="t1",
    model="haiku",
    effort="low",
    variant="none",
    epoch=0,
    gv="v1",
    value=0.8,
    cost=0.01,
    lat=1.0,
    ts="2026-01-01",
    sub=None,
    spec="S",
    run_status="ok",
    grade_status="ok",
    in_tok=None,
    out_tok=None,
) -> None:
    append_row(
        led,
        LedgerRow(
            task_id=task,
            model=model,
            effort=effort,
            variant=variant,
            epoch=epoch,
            grader_version=gv,
            run_id=rid,
            run_status=run_status,
            cost_usd=cost,
            latency_s=lat,
            returncode=0,
            model_resolved=model,
            num_turns=1,
            session_id="s",
            grade_status=grade_status,
            value=value,
            spec_sha=spec,
            subscores=sub or {},
            details={},
            output_path=None,
            ts=ts,
            input_tokens=in_tok,
            output_tokens=out_tok,
        ),
    )


@pytest.mark.unit
def test_report_empty_ledger_returns_empty(tmp_path) -> None:
    assert report(tmp_path / "missing.jsonl") == []


@pytest.mark.unit
def test_report_means_over_epochs(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="r1", epoch=0, value=0.8, cost=0.01)
    _row(led, rid="r2", epoch=1, value=1.0, cost=0.03)
    [cell] = report(led)
    assert cell.n_epochs == 2
    assert cell.mean_value == pytest.approx(0.9)
    assert cell.mean_cost == pytest.approx(0.02)


@pytest.mark.unit
def test_report_dedupes_to_latest_grade_per_run(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    # Same run_id, re-graded later (higher value) → only the latest grade counts.
    _row(led, rid="r1", gv="v1", value=0.5, ts="2026-01-01")
    _row(led, rid="r1", gv="v2", value=0.9, ts="2026-02-01")
    [cell] = report(led)
    assert cell.n_epochs == 1  # one run, not two
    assert cell.mean_value == pytest.approx(0.9)  # latest grade wins


@pytest.mark.unit
def test_report_marks_pareto_frontier(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    # Dominant: higher quality AND cheaper. Dominated: lower quality, pricier.
    _row(led, rid="a", model="opus", effort="low", value=0.95, cost=0.02)
    _row(led, rid="b", model="haiku", effort="low", value=0.70, cost=0.05)
    cells = {c.model: c for c in report(led)}
    assert cells["opus"].pareto is True
    assert cells["haiku"].pareto is False


@pytest.mark.unit
def test_report_token_stats_partial_coverage(tmp_path) -> None:
    # Mixed-era ledger: one epoch measured tokens, one predates them. Token stats
    # cover only the measured subset and say so via n_token_epochs.
    led = tmp_path / "l.jsonl"
    _row(led, rid="new", epoch=0, in_tok=100, out_tok=800)
    _row(led, rid="old", epoch=1)
    [cell] = report(led)
    assert cell.n_epochs == 2
    assert cell.n_token_epochs == 1
    assert cell.mean_input_tokens == pytest.approx(100.0)
    assert cell.mean_output_tokens == pytest.approx(800.0)


@pytest.mark.unit
def test_report_no_token_rows_reads_none_not_zero(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="r0")  # a pre-token row: unmeasured must stay None, never 0
    [cell] = report(led)
    assert cell.mean_output_tokens is None
    assert cell.mean_input_tokens is None
    assert cell.n_token_epochs == 0


@pytest.mark.unit
def test_report_cost_latency_intervals_share_the_epoch_gate(tmp_path) -> None:
    pytest.importorskip("eval_toolkit")
    led = tmp_path / "l.jsonl"
    for i, (c, lt) in enumerate([(0.01, 1.0), (0.02, 2.0), (0.03, 3.0)]):
        _row(led, rid=f"e{i}", epoch=i, cost=c, lat=lt)
    [cell] = report(led)
    # Same estimator and gate as the quality interval: present at 3 epochs, bounded
    # by the observed epoch range (at n=3 it degenerates toward min–max).
    assert cell.cost_ci_low is not None and cell.cost_ci_high is not None
    assert 0.01 <= cell.cost_ci_low <= cell.cost_ci_high <= 0.03
    assert cell.latency_ci_low is not None and cell.latency_ci_high is not None
    assert 1.0 <= cell.latency_ci_low <= cell.latency_ci_high <= 3.0


@pytest.mark.unit
def test_report_no_cost_interval_below_epoch_gate(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="r0", epoch=0)
    _row(led, rid="r1", epoch=1)
    [cell] = report(led)
    assert cell.cost_ci_low is None and cell.latency_ci_low is None


@pytest.mark.unit
def test_report_latency_frontier_differs_from_cost_frontier(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    # Cheap-but-slow vs pricey-but-fast at equal quality: each owns one axis, which
    # is the whole point of a selectable frontier (subscription cost ≈ wall-clock).
    _row(led, rid="a", model="haiku", value=1.0, cost=0.01, lat=30.0)
    _row(led, rid="b", model="sonnet", value=1.0, cost=0.05, lat=5.0)
    cost_front = {c.model for c in report(led, x_axis="cost") if c.pareto}
    lat_front = {c.model for c in report(led, x_axis="latency") if c.pareto}
    assert cost_front == {"haiku"}
    assert lat_front == {"sonnet"}


@pytest.mark.unit
def test_report_token_axis_unmeasured_cell_never_pareto_never_dominates(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="a", model="haiku", value=0.5, out_tok=500)
    _row(led, rid="b", model="opus", value=1.0)  # higher quality but unmeasured tokens
    cells = {c.model: c for c in report(led, x_axis="tokens")}
    # The unmeasured cell sits off the token frontier (unknown ≠ free) — and it must
    # not dominate the measured one despite its higher quality.
    assert cells["opus"].pareto is False
    assert cells["haiku"].pareto is True


@pytest.mark.unit
def test_report_token_axis_partial_coverage_never_pareto_never_dominates(tmp_path) -> None:
    # Mixed-era cell: 2 epochs, only 1 measured tokens. Its token mean is a partial
    # denominator — letting it compete would let a partially-unknown cost read as
    # measured (PR-wide review, F1). It must sit off the token frontier AND not
    # dominate, while staying visible for display (mean + n_token_epochs).
    led = tmp_path / "l.jsonl"
    _row(led, rid="m0", model="haiku", epoch=0, value=1.0, out_tok=100)
    _row(led, rid="m1", model="haiku", epoch=1, value=1.0)  # pre-token epoch
    _row(led, rid="f0", model="sonnet", epoch=0, value=0.9, out_tok=900)
    cells = {c.model: c for c in report(led, x_axis="tokens")}
    assert cells["haiku"].n_token_epochs == 1 and cells["haiku"].n_epochs == 2
    assert cells["haiku"].mean_output_tokens == pytest.approx(100.0)  # display survives
    assert cells["haiku"].pareto is False
    # sonnet is fully measured (1/1) — it must win despite haiku's cheaper partial mean.
    assert cells["sonnet"].pareto is True


@pytest.mark.unit
def test_bootstrap_missing_warns_once_not_per_call(monkeypatch, caplog) -> None:
    import sys

    from claude_ablation_lab.analyze import _bootstrap_fn, _epoch_interval

    # Force the ImportError even when eval_toolkit is installed (None in sys.modules
    # makes `from eval_toolkit.bootstrap import ...` raise), and clear the cache on
    # both sides so this test neither sees nor leaves a poisoned loader.
    _bootstrap_fn.cache_clear()
    monkeypatch.setitem(sys.modules, "eval_toolkit.bootstrap", None)
    try:
        with caplog.at_level(logging.WARNING, logger="claude_ablation_lab.analyze"):
            assert _epoch_interval(np.array([1.0, 2.0, 3.0])) == (None, None)
            assert _epoch_interval(np.array([4.0, 5.0, 6.0])) == (None, None)
        warnings = [r for r in caplog.records if "eval_toolkit missing" in r.message]
        assert len(warnings) == 1  # once per process, not once per axis per cell
    finally:
        _bootstrap_fn.cache_clear()


@pytest.mark.unit
def test_plot_axis_spec_mirrors_analyze_x_axes() -> None:
    # Drift canary: the plot's per-axis metadata and the frontier's axis registry
    # must stay in sync — adding an axis to one module without the other fails here.
    from claude_ablation_lab import plot
    from claude_ablation_lab.analyze import X_AXES

    assert set(plot._X_AXIS_SPEC) == set(X_AXES)


@pytest.mark.unit
def test_report_rejects_unknown_x_axis(tmp_path) -> None:
    with pytest.raises(ValueError, match="x_axis"):
        report(tmp_path / "missing.jsonl", x_axis="bogus")


@pytest.mark.unit
def test_report_flags_label_leakage(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="leaky", value=0.95, sub={"shuffled_auroc": 0.85})
    _row(led, rid="clean", model="opus", value=0.9, sub={"shuffled_auroc": 0.50})
    cells = {c.model: c for c in report(led)}
    assert cells["haiku"].leakage is True  # shuffled control far from 0.5 → suspect
    assert cells["opus"].leakage is False


@pytest.mark.unit
def test_report_leakage_uses_worst_epoch_not_mean(tmp_path) -> None:
    pytest.importorskip("eval_toolkit")  # 3 epochs → across-epoch bootstrap CI
    led = tmp_path / "l.jsonl"
    # One leaky epoch (0.85) among clean ones: mean dev 0.117 < band, MAX dev 0.35 > band.
    for i, s in enumerate([0.85, 0.50, 0.50]):
        _row(led, rid=f"e{i}", epoch=i, sub={"shuffled_auroc": s})
    [cell] = report(led)
    assert cell.leakage is True  # the worst epoch fires the gate, not the average


@pytest.mark.unit
def test_report_across_epoch_ci_only_at_three_plus_epochs(tmp_path) -> None:
    pytest.importorskip("eval_toolkit")  # 3 epochs → across-epoch bootstrap CI
    led = tmp_path / "l.jsonl"
    _row(led, rid="a", epoch=0, value=0.6)
    _row(led, rid="b", epoch=1, value=0.9)
    assert report(led)[0].ci_low is None  # 2 epochs → no across-epoch CI
    _row(led, rid="c", epoch=2, value=0.75)
    [cell] = report(led)
    assert cell.ci_low is not None and cell.ci_high is not None  # 3 epochs → CI of the mean


@pytest.mark.unit
def test_report_excludes_run_whose_latest_grade_failed(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    # Same run_id: an older ok grade, then a newer grader_error (a re-grade that broke).
    _row(led, rid="r1", gv="v1", value=0.8, ts="2026-01-01", grade_status="ok")
    _row(led, rid="r1", gv="v2", value=0.0, ts="2026-02-01", grade_status="grader_error")
    # The stale ok score must NOT survive — the run drops out entirely.
    assert report(led) == []


@pytest.mark.unit
def test_report_ignores_failed_and_ungraded_rows(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="ok", value=0.8)
    _row(led, rid="infra", run_status="infra_error", value=0.0)
    _row(led, rid="ge", grade_status="grader_error", value=0.0)
    [cell] = report(led)
    assert cell.n_epochs == 1 and cell.mean_value == pytest.approx(0.8)


@pytest.mark.unit
def test_report_counts_unparseable_as_honest_zero(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    # An unparseable grade is a MODEL quality failure (value 0.0) — excluding it
    # would inflate the surviving mean (+0.667 publishing as +1.000 in the audit's
    # demonstration). It must be included and surfaced.
    _row(led, rid="good", epoch=0, value=0.8)
    _row(led, rid="junk", epoch=1, value=0.0, grade_status="unparseable")
    [cell] = report(led)
    assert cell.n_epochs == 2
    assert cell.mean_value == pytest.approx(0.4)
    assert cell.n_unparseable == 1


@pytest.mark.unit
def test_report_flags_mixed_grader_versions(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    # A partial re-grade must not silently mix metric definitions within a cell.
    _row(led, rid="e0", epoch=0, gv="t3-anchor-v1", value=0.8)
    _row(led, rid="e1", epoch=1, gv="t3-anchor-v2", value=0.9)
    [cell] = report(led)
    assert cell.n_grader_versions == 2


_SIX_CONFIGS = [
    ("haiku", "low"),
    ("haiku", "high"),
    ("sonnet", "low"),
    ("sonnet", "high"),
    ("opus", "low"),
    ("opus", "high"),
]


@pytest.mark.unit
def test_compare_six_same_sign_pairs_are_real(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    va, vb = "repo@a", "repo@b"
    # B beats A across all 6 configs → exact sign-flip p = 2/2^6 = 0.03125 → real.
    for cfg, (model, effort) in enumerate(_SIX_CONFIGS):
        _row(led, rid=f"a{cfg}", task="t2", variant=va, model=model, effort=effort, value=0.50)
        _row(led, rid=f"b{cfg}", task="t2", variant=vb, model=model, effort=effort, value=0.80)
    [row] = compare(led, va, vb)
    assert row.task_id == "t2" and row.n_pairs == 6 and row.n_nonzero == 6
    assert row.delta == pytest.approx(0.30)
    assert row.p_value == pytest.approx(2 / 64)
    assert row.real is True


@pytest.mark.unit
def test_compare_four_pairs_can_never_be_real(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    va, vb = "repo@a", "repo@b"
    # 4/4 same-sign — the old bootstrap rule called this "real" by construction
    # (measured Type-I ≈ 21%); the exact test says p = 2/2^4 = 0.125, honestly not.
    for cfg, (model, effort) in enumerate(_SIX_CONFIGS[:4]):
        _row(led, rid=f"a{cfg}", task="t2", variant=va, model=model, effort=effort, value=0.5)
        _row(led, rid=f"b{cfg}", task="t2", variant=vb, model=model, effort=effort, value=0.8)
    [row] = compare(led, va, vb)
    assert row.n_pairs == 4 and row.p_value == pytest.approx(0.125)
    assert row.real is False
    assert "cannot reach" in row.note


@pytest.mark.unit
def test_compare_zero_diffs_carry_no_evidence(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    va, vb = "repo@a", "repo@b"
    # 6 configs but two are exact ties → n_nonzero = 4 → min p = 0.125 → not real,
    # and the note names the effective sample size (codex-consulted convention).
    for cfg, (model, effort) in enumerate(_SIX_CONFIGS):
        tied = cfg < 2
        _row(led, rid=f"a{cfg}", task="t2", variant=va, model=model, effort=effort, value=0.5)
        _row(
            led,
            rid=f"b{cfg}",
            task="t2",
            variant=vb,
            model=model,
            effort=effort,
            value=0.5 if tied else 0.8,
        )
    [row] = compare(led, va, vb)
    assert row.n_pairs == 6 and row.n_nonzero == 4
    assert row.real is False and "n_nonzero=4" in row.note


@pytest.mark.unit
def test_compare_all_ties_report_no_evidence(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    va, vb = "repo@a", "repo@b"
    for cfg, (model, effort) in enumerate(_SIX_CONFIGS[:4]):
        _row(led, rid=f"a{cfg}", task="t2", variant=va, model=model, effort=effort, value=0.7)
        _row(led, rid=f"b{cfg}", task="t2", variant=vb, model=model, effort=effort, value=0.7)
    [row] = compare(led, va, vb)
    assert row.p_value is None and row.real is False
    assert "no directional evidence" in row.note


@pytest.mark.unit
def test_compare_single_config_has_no_ci(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    va, vb = "repo@a", "repo@b"
    _row(led, rid="a0", task="t2", variant=va, value=0.5)
    _row(led, rid="b0", task="t2", variant=vb, value=0.9)
    [row] = compare(led, va, vb)
    assert row.n_pairs == 1 and row.ci_low is None and row.real is False


@pytest.mark.unit
def test_compare_no_common_task_returns_empty(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="a0", task="t2", variant="repo@a", value=0.5)  # only under A
    assert compare(led, "repo@a", "repo@b") == []


# --- cost_advisor (Phase 1: where the reflex config overpays) -----------------


def _cell(
    *,
    task: str = "t1",
    model: str = "haiku",
    effort: str = "low",
    variant: str = "none",
    value: float = 1.0,
    cost: float = 0.01,
    lat: float = 1.0,
    n: int = 3,
    leakage: bool = False,
    n_spec: int = 1,
    n_grader_versions: int = 1,
    n_unparseable: int = 0,
) -> ReportCell:
    """A ReportCell with only the fields cost_advisor reads set (rest defaulted)."""
    return ReportCell(
        task_id=task,
        model=model,
        effort=effort,
        variant=variant,
        n_epochs=n,
        n_spec=n_spec,
        mean_value=value,
        sd_value=None,
        mean_cost=cost,
        mean_latency=lat,
        ci_low=None,
        ci_high=None,
        shuffled_auroc=None,
        leakage=leakage,
        n_unparseable=n_unparseable,
        n_grader_versions=n_grader_versions,
    )


@pytest.mark.unit
def test_advise_recommends_cheapest_on_quality_tie() -> None:
    cells = [
        _cell(model="opus", effort="high", value=1.0, cost=0.080, lat=12.0),
        _cell(model="sonnet", effort="high", value=1.0, cost=0.050, lat=10.0),
        _cell(model="haiku", effort="high", value=1.0, cost=0.005, lat=8.0),
    ]
    [row] = cost_advisor(cells, reflex="opus/high", margin=0.02)
    assert (row.rec_model, row.rec_effort) == ("haiku", "high")
    assert row.reflex_fallback is False  # opus/high was present exactly
    assert row.quality_delta == pytest.approx(0.0)
    assert row.cost_saving == pytest.approx(0.075)
    assert row.cost_multiple == pytest.approx(0.080 / 0.005)
    assert row.latency_saving == pytest.approx(4.0)
    assert "same quality" in row.note and "cheaper" in row.note


@pytest.mark.unit
def test_advise_reflex_fallback_ranks_xhigh_above_high() -> None:
    # An absent reflex (opus/max) falls back to the model's HIGHEST effort that ran —
    # which must be xhigh, not high, on a Claude-5-era grid (effort-order fix).
    cells = [
        _cell(model="opus", effort="high", value=1.0, cost=0.08),
        _cell(model="opus", effort="xhigh", value=1.0, cost=0.12),
        _cell(model="haiku", effort="low", value=1.0, cost=0.01),
    ]
    [row] = cost_advisor(cells, reflex="opus/max", margin=0.02)
    assert (row.reflex_model, row.reflex_effort) == ("opus", "xhigh")
    assert row.reflex_fallback is True


@pytest.mark.unit
def test_advise_respects_margin_boundary() -> None:
    # haiku is 0.01 below reflex (inside δ=0.02); sonnet is 0.10 below (outside) yet cheaper.
    cells = [
        _cell(model="opus", effort="low", value=1.00, cost=0.090),
        _cell(model="haiku", effort="high", value=0.99, cost=0.010),
        _cell(model="sonnet", effort="low", value=0.90, cost=0.005),
    ]
    [tight] = cost_advisor(cells, reflex="opus/low", margin=0.02)
    assert (tight.rec_model, tight.rec_effort) == ("haiku", "high")  # sonnet excluded by margin
    assert tight.quality_delta == pytest.approx(-0.01)
    assert "quality drop" in tight.note
    # Widen the tolerance and the cheaper-but-worse sonnet becomes admissible.
    [loose] = cost_advisor(cells, reflex="opus/low", margin=0.20)
    assert (loose.rec_model, loose.rec_effort) == ("sonnet", "low")


@pytest.mark.unit
def test_advise_reflex_falls_back_to_highest_effort_of_model() -> None:
    # No opus/max in the ledger → measure vs the highest opus effort that ran (high).
    cells = [
        _cell(model="opus", effort="low", value=1.0, cost=0.07),
        _cell(model="opus", effort="high", value=1.0, cost=0.09),
        _cell(model="haiku", effort="high", value=1.0, cost=0.006),
    ]
    [row] = cost_advisor(cells, reflex="opus/max", margin=0.02)
    assert (row.reflex_model, row.reflex_effort) == ("opus", "high")
    assert row.reflex_fallback is True
    assert "cheaper" in row.note  # the fallback surfaces via reflex_fallback + a '*', not the note
    assert (row.rec_model, row.rec_effort) == ("haiku", "high")


@pytest.mark.unit
def test_advise_reflex_falls_back_to_priciest_when_model_absent() -> None:
    cells = [
        _cell(model="haiku", effort="high", value=1.0, cost=0.006),
        _cell(model="sonnet", effort="high", value=1.0, cost=0.050),
    ]
    [row] = cost_advisor(cells, reflex="opus/max", margin=0.02)
    assert (row.reflex_model, row.reflex_effort) == ("sonnet", "high")  # priciest that ran
    assert row.reflex_fallback is True


@pytest.mark.unit
def test_advise_already_optimal_when_reflex_is_cheapest() -> None:
    cells = [
        _cell(model="opus", effort="high", value=1.0, cost=0.01),  # reflex AND cheapest
        _cell(model="haiku", effort="high", value=1.0, cost=0.02),
    ]
    [row] = cost_advisor(cells, reflex="opus/high", margin=0.02)
    assert (row.rec_model, row.rec_effort) == ("opus", "high")
    assert row.cost_saving == pytest.approx(0.0)
    assert "already the cheapest" in row.note


@pytest.mark.unit
def test_advise_single_config_notes_it() -> None:
    [row] = cost_advisor([_cell(model="opus", effort="high")], reflex="opus/high")
    assert (row.rec_model, row.rec_effort) == ("opus", "high")
    assert row.cost_saving == pytest.approx(0.0) and "only one config" in row.note


@pytest.mark.unit
def test_advise_free_recommendation_has_no_multiple() -> None:
    cells = [
        _cell(model="opus", effort="high", value=1.0, cost=0.08),
        _cell(model="haiku", effort="low", value=1.0, cost=0.0),
    ]
    [row] = cost_advisor(cells, reflex="opus/high")
    assert row.rec_cost == pytest.approx(0.0)
    assert row.cost_multiple is None  # no division by zero


@pytest.mark.unit
def test_advise_orders_by_dollar_saving_descending() -> None:
    cells = [
        _cell(task="t_small", model="opus", effort="high", cost=0.02),
        _cell(task="t_small", model="haiku", effort="low", cost=0.01),
        _cell(task="t_big", model="opus", effort="high", cost=0.10),
        _cell(task="t_big", model="haiku", effort="low", cost=0.01),
    ]
    advice = cost_advisor(cells, reflex="opus/high")
    assert [a.task_id for a in advice] == ["t_big", "t_small"]  # biggest overpay first


@pytest.mark.unit
def test_advise_groups_by_variant() -> None:
    cells = [
        _cell(task="t4", variant="repo@with", model="opus", effort="high", value=1.0, cost=0.10),
        _cell(task="t4", variant="repo@with", model="haiku", effort="low", value=1.0, cost=0.01),
        _cell(task="t4", variant="repo@without", model="opus", effort="high", value=1.0, cost=0.09),
        _cell(task="t4", variant="repo@without", model="haiku", effort="low", value=1.0, cost=0.01),
    ]
    advice = cost_advisor(cells, reflex="opus/high")
    assert {a.variant for a in advice} == {"repo@with", "repo@without"}  # one row per variant


@pytest.mark.unit
def test_advise_empty_cells_is_empty() -> None:
    assert cost_advisor([]) == []


@pytest.mark.unit
def test_advise_rejects_malformed_reflex_and_margin() -> None:
    with pytest.raises(ValueError, match="model/effort"):
        cost_advisor([_cell()], reflex="opusmax")
    with pytest.raises(ValueError, match="margin"):
        cost_advisor([_cell()], reflex="opus/high", margin=1.5)


@pytest.mark.unit
def test_advise_rejects_more_malformed_reflex() -> None:
    # split('/', 1) let 'opus/', '/high', and 'opus/high/typo' slip into the fallback
    # path against the wrong reflex; require exactly two non-empty parts.
    for bad in ("opus/", "/high", "opus/high/typo", "", "/"):
        with pytest.raises(ValueError, match="model/effort"):
            cost_advisor([_cell()], reflex=bad)


@pytest.mark.unit
def test_advise_floors_at_best_not_reflex() -> None:
    # Inverted gradient: the expensive reflex FAILS (0.0) while a mid config works (1.0),
    # and the very cheapest also fails. Flooring at the reflex would recommend the cheapest
    # FAILING config; flooring at the best recommends the one that actually works.
    cells = [
        _cell(model="opus", effort="high", value=0.0, cost=0.10),
        _cell(model="sonnet", effort="high", value=1.0, cost=0.05),
        _cell(model="haiku", effort="high", value=0.0, cost=0.005),
    ]
    [row] = cost_advisor(cells, reflex="opus/high", margin=0.02)
    assert (row.rec_model, row.rec_effort) == ("sonnet", "high")  # NOT haiku(0.0)
    assert row.best_value == pytest.approx(1.0)
    assert row.quality_delta == pytest.approx(1.0)  # the reflex was itself suboptimal
    assert row.vacuous is False
    assert "quality vs reflex" in row.note and "cheaper" in row.note


@pytest.mark.unit
def test_advise_marks_vacuous_when_nothing_works() -> None:
    # Every config fails (~0): no recommendation is meaningful → vacuous, kept out of Σ.
    cells = [
        _cell(model="opus", effort="high", value=0.0, cost=0.10),
        _cell(model="haiku", effort="low", value=0.0, cost=0.01),
    ]
    [row] = cost_advisor(cells, reflex="opus/high", margin=0.02)
    assert row.vacuous is True
    assert row.best_value == pytest.approx(0.0)
    assert "n/a" in row.note


@pytest.mark.unit
def test_advise_flags_suspect_cell() -> None:
    # A leaky (or mixed-spec / mixed-grader / unparseable) cell must not read as a clean
    # downgrade — the row is flagged suspect and the note says so.
    cells = [
        _cell(model="opus", effort="high", value=1.0, cost=0.08),
        _cell(model="haiku", effort="high", value=1.0, cost=0.005, leakage=True),
    ]
    [row] = cost_advisor(cells, reflex="opus/high", margin=0.02)
    assert (row.rec_model, row.rec_effort) == ("haiku", "high")  # the leaky cell is the rec
    assert row.suspect is True and "suspect" in row.note


@pytest.mark.unit
def test_advise_note_says_equal_cost_not_cheaper_on_a_cost_tie() -> None:
    # Two configs tie on cost AND quality; the tie-break picks the non-reflex one (lower
    # latency), so cost_saving is exactly 0 — the note must say "equal cost", not "cheaper".
    cells = [
        _cell(model="opus", effort="high", value=1.0, cost=0.05, lat=10.0),
        _cell(model="haiku", effort="high", value=1.0, cost=0.05, lat=8.0),
    ]
    [row] = cost_advisor(cells, reflex="opus/high", margin=0.02)
    assert (row.rec_model, row.rec_effort) == ("haiku", "high")
    assert row.cost_saving == pytest.approx(0.0)
    assert "equal cost" in row.note and "cheaper" not in row.note


@pytest.mark.unit
def test_advise_surfaces_absolute_quality_and_epochs() -> None:
    # The table shows the recommendation's ABSOLUTE quality (not just Δ vs reflex), so a
    # near-failing recommendation can't hide behind a 0.000 delta.
    cells = [
        _cell(model="opus", effort="high", value=0.90, cost=0.08, n=4),
        _cell(model="haiku", effort="high", value=0.90, cost=0.005, n=4),
    ]
    [row] = cost_advisor(cells, reflex="opus/high", margin=0.02)
    assert row.rec_value == pytest.approx(0.90)
    assert row.n_epochs == 4


@pytest.mark.unit
def test_report_tolerates_ledger_rows_missing_token_keys_entirely(tmp_path) -> None:
    # Every pre-2026-07-06 ledger row lacks the token KEYS (not null values — the
    # LedgerRow writer always emits them, so this must be raw JSON): the explicit
    # read_json schema must surface them as NULL, never error (adversarial
    # re-review: this path was previously covered only by a manual smoke).
    import json as _json

    led = tmp_path / "old.jsonl"
    row = {
        "task_id": "t1",
        "model": "haiku",
        "effort": "low",
        "variant": "none",
        "epoch": 0,
        "grader_version": "v1",
        "run_id": "r0",
        "run_status": "ok",
        "grade_status": "ok",
        "value": 0.8,
        "cost_usd": 0.01,
        "latency_s": 1.0,
        "spec_sha": "S",
        "subscores": "{}",
        "ts": "2026-01-01",
    }
    led.write_text(_json.dumps(row) + "\n", encoding="utf-8")
    [cell] = report(led)
    assert cell.mean_value == pytest.approx(0.8)
    assert cell.mean_cost == pytest.approx(0.01)
    assert cell.mean_output_tokens is None
    assert cell.mean_input_tokens is None
    assert cell.n_token_epochs == 0


@pytest.mark.unit
def test_report_nan_cost_cell_is_never_pareto() -> None:
    # A NaN x compares false against everything, so without the _x_value guard a
    # null-cost cell (only reachable via a hand-edited ledger) would dodge every
    # domination test and sit spuriously ON the frontier.
    from dataclasses import replace as _replace

    from claude_ablation_lab.analyze import _mark_pareto

    nan_cell = _replace(_cell(model="haiku", effort="low", value=1.0), mean_cost=float("nan"))
    real_cell = _cell(model="sonnet", effort="low", value=0.5, cost=0.05)
    marked = {c.model: c for c in _mark_pareto([nan_cell, real_cell])}
    assert marked["haiku"].pareto is False  # unmeasured x never wins a frontier
    assert marked["sonnet"].pareto is True
