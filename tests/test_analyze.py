"""Phase 4 analysis: report aggregation (dedupe/Pareto/leakage) + compare bootstrap."""

from __future__ import annotations

import pytest

from claude_ablation_lab.analyze import compare, report
from claude_ablation_lab.ledger import LedgerRow, append_row


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
    _row(led, rid="leaky", value=0.95, sub={"shuffled_auroc": 0.85, "ci_low": 0.9, "ci_high": 1.0})
    _row(led, rid="clean", model="opus", value=0.9, sub={"shuffled_auroc": 0.50})
    cells = {c.model: c for c in report(led)}
    assert cells["haiku"].leakage is True  # shuffled control far from 0.5 → suspect
    assert cells["opus"].leakage is False
    assert cells["haiku"].ci_low == pytest.approx(0.9)  # within-cell CI surfaced


@pytest.mark.unit
def test_report_ignores_failed_and_ungraded_rows(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="ok", value=0.8)
    _row(led, rid="infra", run_status="infra_error", value=0.0)
    _row(led, rid="ge", grade_status="grader_error", value=0.0)
    [cell] = report(led)
    assert cell.n_epochs == 1 and cell.mean_value == pytest.approx(0.8)


@pytest.mark.unit
def test_compare_detects_a_real_delta(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    va, vb = "repo@a", "repo@b"
    # B consistently beats A across both configs → delta>0, CI should exclude 0.
    for cfg, (model, effort) in enumerate([("haiku", "low"), ("sonnet", "high")]):
        _row(led, rid=f"a{cfg}", task="t2", variant=va, model=model, effort=effort, value=0.50)
        _row(led, rid=f"b{cfg}", task="t2", variant=vb, model=model, effort=effort, value=0.80)
    [row] = compare(led, va, vb)
    assert row.task_id == "t2" and row.n_pairs == 2
    assert row.delta == pytest.approx(0.30)
    assert row.ci_low is not None and row.real is True  # CI excludes 0


@pytest.mark.unit
def test_compare_single_config_has_no_ci(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    va, vb = "repo@a", "repo@b"
    _row(led, rid="a0", task="t2", variant=va, value=0.5)
    _row(led, rid="b0", task="t2", variant=vb, value=0.9)
    [row] = compare(led, va, vb)
    assert row.n_pairs == 1 and row.ci_low is None and row.real is False
    assert "no CI" in row.note


@pytest.mark.unit
def test_compare_no_common_task_returns_empty(tmp_path) -> None:
    led = tmp_path / "l.jsonl"
    _row(led, rid="a0", task="t2", variant="repo@a", value=0.5)  # only under A
    assert compare(led, "repo@a", "repo@b") == []
