"""judge.py aggregation primitives: canonical mapping, order debias, cross-judge score.

Getting canonical_verdict wrong would silently invert half of all verdicts, so
every (raw, order) combination is enumerated.
"""

from __future__ import annotations

import pytest

from claude_ablation_lab.judge import (
    JudgeCall,
    build_judge_prompt,
    canonical_verdict,
    debias,
    pair_score,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "order", "expected"),
    [
        ("A", "ab", "a"),  # config_a shown first, judge picked "A" -> config_a
        ("B", "ab", "b"),
        ("tie", "ab", "tie"),
        ("A", "ba", "b"),  # swapped: judge's "A" is config_b
        ("B", "ba", "a"),
        ("tie", "ba", "tie"),
    ],
)
def test_canonical_verdict_every_combination(raw: str, order: str, expected: str) -> None:
    assert canonical_verdict(raw, order) == expected  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("a", "a", "a"),  # both orders agree
        ("b", "b", "b"),
        ("tie", "tie", "tie"),
        ("a", "b", "tie"),  # order-flip disagreement -> tie (the design rule)
        ("b", "a", "tie"),
        ("a", "tie", "tie"),
        ("tie", "b", "tie"),
        (None, "a", None),  # a missing order -> no verdict, never a guess
        ("a", None, None),
        (None, None, None),
    ],
)
def test_debias_rules(first: str | None, second: str | None, expected: str | None) -> None:
    assert debias(first, second) == expected  # type: ignore[arg-type]


@pytest.mark.unit
def test_pair_score_cross_judge_patterns() -> None:
    assert pair_score(["a", "a"]) == 1.0
    assert pair_score(["b", "b"]) == -1.0
    assert pair_score(["a", "b"]) == 0.0  # full disagreement -> headline tie
    assert pair_score(["a", "tie"]) == 0.5  # the half-signal is kept
    assert pair_score(["tie", "b"]) == -0.5
    assert pair_score(["tie", "tie"]) == 0.0
    assert pair_score(["a", None]) == 1.0  # missing judge excluded, not zeroed
    assert pair_score([None, None]) is None  # no verdict at all -> missing pair


@pytest.mark.unit
def test_judge_prompt_is_blinded_and_json_only() -> None:
    prompt = build_judge_prompt(
        assignment="Write the section on X.", first="OUT-ONE", second="OUT-TWO"
    )
    # Presentation order preserved; both outputs present exactly once.
    assert prompt.index("OUT-ONE") < prompt.index("OUT-TWO")
    assert prompt.count("OUT-ONE") == 1
    # No config identity can leak through the template itself. (Generic English
    # words like "effort" in the constant rubric are fine — blinding means no
    # PER-PAIR distinguishing information, and the template is identical for all.)
    for leak in ("sonnet", "opus", "fable", "haiku", "claude", "cost", "model name"):
        assert leak not in prompt.lower()
    # The instrument's two load-bearing instructions.
    assert "Do NOT reward length" in prompt
    assert '"winner": "A" | "B" | "tie"' in prompt


@pytest.mark.unit
def test_judgecall_defaults_are_not_ok_shaped() -> None:
    call = JudgeCall(status="timeout")
    assert call.verdict is None
    assert call.raw_text == ""
