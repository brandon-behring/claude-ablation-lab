"""evaluate_controls — the numeric gate over canned judge rows, plus the fixture
loader against the committed examples/judge-controls/ tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_ablation_lab.judge_ledger import JudgeRow
from claude_ablation_lab.judge_orchestrate import (
    SAME_OUTPUT_FIXTURES,
    evaluate_controls,
    load_control_pairs,
)

_VERSION = "pj-v1+vp-v1/codex:gpt-5.5:medium"
_FIXTURES = Path(__file__).parent.parent / "examples" / "judge-controls"


def _row(
    control: str,
    epoch: int,
    order: str,
    verdict: str | None,
    *,
    status: str = "ok",
    judge_id: str = "codex",
    version: str = _VERSION,
    configs: tuple[str, str] | None = None,
) -> JudgeRow:
    defaults = {
        "same_output": ("control/left", "control/right"),
        "verbosity": ("control/concise", "control/padded"),
        "positive": ("control/degraded", "control/good"),
    }
    config_a, config_b = configs or defaults[control]
    return JudgeRow(
        task_id=f"control_{control}",
        epoch=epoch,
        config_a=config_a,
        config_b=config_b,
        order=order,
        judge_id=judge_id,
        judge_version=version,
        spec_sha="s" * 16,
        output_sha_a="a" * 16,
        output_sha_b="b" * 16,
        control=control,
        status=status,
        verdict=verdict if status == "ok" else None,
    )


def _passing_rows(judge_id: str = "codex", version: str = _VERSION) -> list[JudgeRow]:
    """A fully clean control run: all ties on same-output, concise/good wins."""
    rows: list[JudgeRow] = []
    for e in range(SAME_OUTPUT_FIXTURES):
        for order in ("ab", "ba"):
            rows.append(_row("same_output", e, order, "tie", judge_id=judge_id, version=version))
    for e in range(6):
        for order in ("ab", "ba"):
            # verbosity: concise (config_a) wins both orders -> padded never wins
            rows.append(_row("verbosity", e, order, "a", judge_id=judge_id, version=version))
            # positive: good (config_b) wins both orders
            rows.append(_row("positive", e, order, "b", judge_id=judge_id, version=version))
    return rows


@pytest.mark.unit
def test_clean_run_passes_every_control() -> None:
    report = evaluate_controls(_passing_rows(), {"codex": _VERSION})
    outcomes = {o.name: o for o in report.per_judge["codex"]}
    assert set(outcomes) == {"same_output", "verbosity", "positive", "call_health"}
    assert report.passed
    assert all(o.passed for o in outcomes.values())


@pytest.mark.unit
def test_verbosity_gate_fails_when_padded_wins_two_pairs() -> None:
    rows = [r for r in _passing_rows() if r.control != "verbosity"]
    for e in range(6):
        winner = "b" if e < 2 else "a"  # padded (config_b) wins pairs 0 and 1
        for order in ("ab", "ba"):
            rows.append(_row("verbosity", e, order, winner))
    report = evaluate_controls(rows, {"codex": _VERSION})
    outcomes = {o.name: o for o in report.per_judge["codex"]}
    assert not outcomes["verbosity"].passed
    assert "2/6" in outcomes["verbosity"].detail
    assert not report.passed
    # One padded win stays within the gate (<= 1 of 6).
    rows2 = [r for r in _passing_rows() if r.control != "verbosity"]
    for e in range(6):
        winner = "b" if e == 0 else "a"
        for order in ("ab", "ba"):
            rows2.append(_row("verbosity", e, order, winner))
    assert evaluate_controls(rows2, {"codex": _VERSION}).passed


@pytest.mark.unit
def test_positive_gate_fails_if_degraded_ever_wins() -> None:
    rows = [r for r in _passing_rows() if r.control != "positive"]
    for e in range(6):
        winner = "a" if e == 0 else "b"  # degraded (config_a) wins one pair
        for order in ("ab", "ba"):
            rows.append(_row("positive", e, order, winner))
    report = evaluate_controls(rows, {"codex": _VERSION})
    outcomes = {o.name: o for o in report.per_judge["codex"]}
    assert not outcomes["positive"].passed


@pytest.mark.unit
def test_positive_gate_tolerates_one_tie_but_not_two() -> None:
    def rows_with_ties(n_ties: int) -> list[JudgeRow]:
        rows = [r for r in _passing_rows() if r.control != "positive"]
        for e in range(6):
            winner = "tie" if e < n_ties else "b"
            for order in ("ab", "ba"):
                rows.append(_row("positive", e, order, winner))
        return rows

    assert evaluate_controls(rows_with_ties(1), {"codex": _VERSION}).passed
    assert not evaluate_controls(rows_with_ties(2), {"codex": _VERSION}).passed


@pytest.mark.unit
def test_same_output_gate_catches_consistent_side_preference() -> None:
    rows = [r for r in _passing_rows() if r.control != "same_output"]
    for e in range(SAME_OUTPUT_FIXTURES):
        # fixture 0: BOTH calls prefer side "a" of identical texts -> position bias
        verdict = "a" if e == 0 else "tie"
        for order in ("ab", "ba"):
            rows.append(_row("same_output", e, order, verdict))
    report = evaluate_controls(rows, {"codex": _VERSION})
    outcomes = {o.name: o for o in report.per_judge["codex"]}
    assert not outcomes["same_output"].passed
    assert "1 consistent side preference" in outcomes["same_output"].detail


@pytest.mark.unit
def test_health_gate_reads_latest_row_not_history() -> None:
    # Every call failed once then succeeded on retry: health must read the
    # superseding ok rows, not the historical failures (plan-review finding).
    rows: list[JudgeRow] = []
    for good in _passing_rows():
        rows.append(_row(good.control, good.epoch, good.order, None, status="timeout"))
        rows.append(good)
    report = evaluate_controls(rows, {"codex": _VERSION})
    outcomes = {o.name: o for o in report.per_judge["codex"]}
    assert outcomes["call_health"].passed
    assert report.passed


@pytest.mark.unit
def test_stale_judge_version_means_no_controls() -> None:
    report = evaluate_controls(_passing_rows(version="pj-v0+vp-v1/old"), {"codex": _VERSION})
    outcomes = report.per_judge["codex"]
    assert len(outcomes) == 1
    assert not outcomes[0].passed
    assert "no control rows" in outcomes[0].detail


@pytest.mark.unit
def test_every_judge_must_pass_independently() -> None:
    rows = _passing_rows("codex") + _passing_rows("gemini", version="pj-v1+vp-v1/gemini:x")
    both = evaluate_controls(rows, {"codex": _VERSION, "gemini": "pj-v1+vp-v1/gemini:x"})
    assert both.passed
    one_missing = evaluate_controls(rows, {"codex": _VERSION, "gemini": "pj-v2+vp-v1/gemini:x"})
    assert not one_missing.passed


# --- the committed fixture tree ---------------------------------------------------


@pytest.mark.unit
def test_committed_fixtures_load_with_expected_shape() -> None:
    specs = load_control_pairs(_FIXTURES)
    by_control: dict[str, int] = {}
    for s in specs:
        by_control[s.control] = by_control.get(s.control, 0) + 1
    assert by_control == {"positive": 6, "verbosity": 6, "same_output": SAME_OUTPUT_FIXTURES}
    for s in specs:
        assert s.assignment.strip()
        assert s.output_a.strip() and s.output_b.strip()
        assert s.config_a < s.config_b  # canonical ordering
        if s.control == "same_output":
            assert s.output_a == s.output_b
        if s.control == "verbosity":
            # padded (config_b) carries the same content inflated ~2x.
            assert 1.8 <= len(s.output_b) / len(s.output_a) <= 2.6
        if s.control == "positive":
            # degraded (config_a) is length-MATCHED: quality, not length, differs.
            assert 0.8 <= len(s.output_a) / len(s.output_b) <= 1.3


@pytest.mark.unit
def test_missing_fixture_root_refuses(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="controls fixtures not found"):
        load_control_pairs(tmp_path / "nope")
