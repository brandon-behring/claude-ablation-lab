"""judge_analyze: per-contrast summaries from canned judge + contestant rows —
sign-flip over prompts, W/L/T, Holm on exploratory contrasts, cost/length joins."""

from __future__ import annotations

import pytest

from claude_ablation_lab.judge_analyze import DEFAULT_PRIMARY, judge_report
from claude_ablation_lab.judge_ledger import JudgeRow
from claude_ablation_lab.ledger import LedgerRow

_BASE = "claude-fable-5/low"
_VERSIONS = {"codex": "pj-v1+vp-v1/codex:x", "gemini": "pj-v1+vp-v1/gemini:x"}


def _judge_row(
    task_id: str,
    candidate: str,
    verdict_for: str | None,  # config string that wins, "tie", or None (failed call)
    *,
    epoch: int = 0,
    order: str = "ab",
    judge_id: str = "codex",
    chars: tuple[int, int] = (1000, 1000),
) -> JudgeRow:
    config_a, config_b = sorted((candidate, _BASE))
    if verdict_for is None:
        status, verdict = "timeout", None
    elif verdict_for == "tie":
        status, verdict = "ok", "tie"
    else:
        status, verdict = "ok", ("a" if verdict_for == config_a else "b")
    return JudgeRow(
        task_id=task_id,
        epoch=epoch,
        config_a=config_a,
        config_b=config_b,
        order=order,
        judge_id=judge_id,
        judge_version=_VERSIONS[judge_id],
        spec_sha="s" * 16,
        output_sha_a="a" * 16,
        output_sha_b="b" * 16,
        status=status,
        verdict=verdict,
        output_chars_a=chars[0],
        output_chars_b=chars[1],
    )


def _contestant_row(
    task_id: str, config: str, *, cost: float, tokens: int | None = 2000
) -> LedgerRow:
    model, effort = config.split("/")
    return LedgerRow(
        task_id=task_id,
        model=model,
        effort=effort,
        variant="none",
        epoch=0,
        grader_version="authoring-conv-v1",
        run_id=f"r-{task_id}-{config}",
        run_status="ok",
        cost_usd=cost,
        latency_s=100.0 if config != _BASE else 50.0,
        returncode=0,
        model_resolved=model,
        num_turns=1,
        grade_status="ok",
        value=1.0,
        spec_sha="s" * 16,
        output_tokens=tokens,
    )


def _unanimous_rows(candidate: str, winners: list[str]) -> list[JudgeRow]:
    """Both judges, both orders, one epoch: winners[i] wins prompt i outright."""
    rows = []
    for i, winner in enumerate(winners):
        for judge_id in ("codex", "gemini"):
            for order in ("ab", "ba"):
                rows.append(
                    _judge_row(f"t9_p{i:02d}", candidate, winner, order=order, judge_id=judge_id)
                )
    return rows


@pytest.mark.unit
def test_unanimous_sweep_is_real_with_enough_prompts() -> None:
    candidate = DEFAULT_PRIMARY
    winners = [candidate] * 8  # candidate wins all 8 prompts, both judges, both orders
    contestants = [
        _contestant_row(f"t9_p{i:02d}", c, cost=(0.30 if c == candidate else 0.10))
        for i in range(8)
        for c in (candidate, _BASE)
    ]
    [summary] = judge_report(_unanimous_rows(candidate, winners), contestants, baseline=_BASE)
    assert summary.primary and summary.p_adjusted is None
    assert (summary.wins, summary.losses, summary.ties) == (8, 0, 0)
    assert summary.mean_score == 1.0
    assert summary.p_value == pytest.approx(2 / 2**8)
    assert summary.real
    assert summary.cost_multiple == pytest.approx(3.0)
    assert summary.order_disagree_rate == {"codex": 0.0, "gemini": 0.0}
    assert summary.cross_judge_disagree_rate == 0.0
    assert summary.missing_rate == 0.0


@pytest.mark.unit
def test_underpowered_below_min_pairs_is_not_real() -> None:
    candidate = DEFAULT_PRIMARY
    winners = [candidate] * 4 + ["tie"] * 4  # only 4 nonzero prompts
    [summary] = judge_report(
        _unanimous_rows(candidate, winners),
        [
            _contestant_row(f"t9_p{i:02d}", c, cost=0.1)
            for i in range(8)
            for c in (candidate, _BASE)
        ],
        baseline=_BASE,
    )
    assert not summary.real
    assert "underpowered" in summary.note
    assert summary.n_nonzero == 4
    assert (summary.wins, summary.ties) == (4, 4)


@pytest.mark.unit
def test_order_flip_becomes_tie_and_is_counted() -> None:
    candidate = "sonnet/high"
    # codex flips with order on prompt 0 (position bias); gemini prefers candidate.
    rows = [
        # codex prefers candidate in ab-order but baseline in ba-order: a real flip.
        _judge_row("t9_p00", candidate, candidate, order="ab", judge_id="codex"),
        _judge_row("t9_p00", candidate, _BASE, order="ba", judge_id="codex"),
        _judge_row("t9_p00", candidate, candidate, order="ab", judge_id="gemini"),
        _judge_row("t9_p00", candidate, candidate, order="ba", judge_id="gemini"),
    ]
    [summary] = judge_report(
        rows,
        [_contestant_row("t9_p00", c, cost=0.1) for c in (candidate, _BASE)],
        baseline=_BASE,
    )
    assert summary.order_disagree_rate["codex"] == 1.0
    assert summary.order_disagree_rate["gemini"] == 0.0
    # codex debiased -> tie; gemini -> candidate; cross-judge mean = +0.5 half-signal.
    assert summary.mean_score == pytest.approx(0.5)
    assert summary.cross_judge_disagree_rate == 1.0


@pytest.mark.unit
def test_failed_calls_make_missing_pairs_not_zeros() -> None:
    candidate = "sonnet/high"
    rows = []
    for judge_id in ("codex", "gemini"):
        for order in ("ab", "ba"):
            rows.append(_judge_row("t9_p00", candidate, None, order=order, judge_id=judge_id))
    [summary] = judge_report(
        rows,
        [_contestant_row("t9_p00", c, cost=0.1) for c in (candidate, _BASE)],
        baseline=_BASE,
    )
    assert summary.n_scored == 0
    assert summary.mean_score is None
    assert summary.missing_rate == 1.0
    assert "no scored prompts" in summary.note


@pytest.mark.unit
def test_holm_applies_to_exploratory_contrasts_only() -> None:
    # Primary (fable/high) + two exploratory contrasts, all unanimous over 8 prompts.
    contestants = []
    rows = []
    for candidate in (DEFAULT_PRIMARY, "sonnet/high", "opus/high"):
        rows += _unanimous_rows(candidate, [candidate] * 8)
        contestants += [
            _contestant_row(f"t9_p{i:02d}", c, cost=0.1)
            for i in range(8)
            for c in (candidate, _BASE)
        ]
    summaries = judge_report(rows, contestants, baseline=_BASE)
    by_config = {s.config: s for s in summaries}
    assert by_config[DEFAULT_PRIMARY].p_adjusted is None  # predeclared, uncorrected
    raw = 2 / 2**8
    for exploratory in ("sonnet/high", "opus/high"):
        s = by_config[exploratory]
        assert "exploratory" in s.note
        assert s.p_adjusted is not None and s.p_adjusted >= raw
    # m=2 exploratory contrasts at identical raw p -> worst adjusted = 2x raw.
    assert max(s.p_adjusted for s in summaries if s.p_adjusted is not None) == pytest.approx(
        2 * raw
    )


@pytest.mark.unit
def test_length_ratio_is_candidate_over_baseline() -> None:
    candidate = "sonnet/high"
    config_a, _config_b = sorted((candidate, _BASE))
    # candidate output 2000 chars, baseline 1000 -> ratio 2.0 regardless of frame.
    chars = (2000, 1000) if config_a == candidate else (1000, 2000)
    rows = [
        _judge_row("t9_p00", candidate, candidate, order=o, judge_id=j, chars=chars)
        for o in ("ab", "ba")
        for j in ("codex", "gemini")
    ]
    [summary] = judge_report(
        rows,
        [_contestant_row("t9_p00", c, cost=0.1) for c in (candidate, _BASE)],
        baseline=_BASE,
    )
    assert summary.mean_length_ratio == pytest.approx(2.0)


@pytest.mark.unit
def test_token_multiple_none_when_not_measured() -> None:
    candidate = "sonnet/high"
    rows = _unanimous_rows(candidate, [candidate] * 2)
    contestants = [
        _contestant_row(f"t9_p{i:02d}", c, cost=0.1, tokens=None)
        for i in range(2)
        for c in (candidate, _BASE)
    ]
    [summary] = judge_report(rows, contestants, baseline=_BASE)
    assert summary.token_multiple is None  # unknown is never 1.0
    assert summary.cost_multiple == pytest.approx(1.0)
