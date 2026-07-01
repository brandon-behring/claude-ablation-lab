"""Phase 4 analysis: report aggregation (dedupe/Pareto/leakage) + compare bootstrap."""

from __future__ import annotations

import pytest

from claude_ablation_lab.analyze import compare, report
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
