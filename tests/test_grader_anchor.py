"""T3 anchor grader — expected-count denominator, whitespace match, parse robustness."""

from __future__ import annotations

import pytest

from claude_ablation_lab.graders.anchor import AnchorGrader

SRC = (
    "The bootstrap is a resampling method. Each resample has the same size. "
    "The percentile interval takes the 2.5th and 97.5th percentiles."
)


@pytest.mark.golden
def test_all_expected_quotes_verbatim_score_one() -> None:
    out = '{"claims":[{"claim":"a","quote":"a resampling method"},{"claim":"b","quote":"the same size"}]}'
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC, "expected_claims": 2})
    assert score.value == 1.0
    assert score.status == "ok"
    assert score.subscores["n_verbatim"] == 2.0


@pytest.mark.golden
def test_partial_substrings_give_fraction() -> None:
    out = '{"claims":[{"claim":"a","quote":"a resampling method"},{"claim":"b","quote":"NOT PRESENT"}]}'
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC, "expected_claims": 2})
    assert score.value == 0.5
    assert score.details["misses"] == ["NOT PRESENT"]


@pytest.mark.golden
def test_underproduction_penalised_against_expected() -> None:
    # One valid quote but the task expects 5 → 1/5, not a gamed 1/1.
    out = '{"claims":[{"claim":"a","quote":"a resampling method"}]}'
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC})  # default expected=5
    assert score.value == pytest.approx(0.2)
    assert score.subscores["expected"] == 5.0
    assert score.details["shortfall"] == 4


@pytest.mark.golden
def test_overproduction_capped_at_one() -> None:
    quotes = [
        "a resampling method",
        "the same size",
        "The bootstrap is",
        "The percentile interval",
        "the 2.5th and",
        "and 97.5th percentiles",
    ]
    items = ",".join(f'{{"claim":"c{i}","quote":"{q}"}}' for i, q in enumerate(quotes))
    score = AnchorGrader().grade(
        output=f'{{"claims":[{items}]}}', gold={"source_text": SRC, "expected_claims": 5}
    )
    assert score.value == 1.0  # 6 verbatim / max(5, 6) = 1.0, never > 1


@pytest.mark.golden
def test_whitespace_reflow_is_tolerated() -> None:
    # The source is hard-wrapped mid-sentence; a faithful quote reflows it.
    source = "samples,\n    with replacement,\n    from the observed data"
    out = '{"claims":[{"claim":"a","quote":"samples, with replacement, from the observed data"}]}'
    score = AnchorGrader().grade(output=out, gold={"source_text": source, "expected_claims": 1})
    assert score.value == 1.0


@pytest.mark.golden
def test_bare_list_and_preamble_tolerated() -> None:
    out = 'Sure, here is the JSON:\n[{"claim":"a","quote":"The bootstrap is"}]\nDone.'
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC, "expected_claims": 1})
    assert score.value == 1.0


@pytest.mark.unit
def test_empty_quotes_count_as_misses_not_perfect() -> None:
    # Empty quotes must NOT match (`"" in source` is True) — they are misses.
    out = '{"claims":[{"claim":"a","quote":"the same size"},{"claim":"b","quote":""}]}'
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC, "expected_claims": 2})
    assert score.value == 0.5
    assert score.subscores["n_verbatim"] == 1.0


@pytest.mark.unit
def test_valid_empty_claim_list_scores_zero_ok() -> None:
    score = AnchorGrader().grade(output='{"claims":[]}', gold={"source_text": SRC})
    assert score.value == 0.0
    assert score.status == "ok"  # a refusal scores 0, it is not dropped from aggregation


@pytest.mark.unit
def test_no_claim_structure_is_unparseable() -> None:
    assert (
        AnchorGrader().grade(output="no json here", gold={"source_text": SRC}).status
        == "unparseable"
    )
    assert (
        AnchorGrader().grade(output='{"foo": 1}', gold={"source_text": SRC}).status == "unparseable"
    )


@pytest.mark.unit
def test_empty_source_is_grader_error() -> None:
    out = '{"claims":[{"claim":"a","quote":"The bootstrap is"}]}'
    assert AnchorGrader().grade(output=out, gold={}).status == "grader_error"


@pytest.mark.unit
def test_strict_version_differs_from_lenient() -> None:
    assert AnchorGrader().version == "t3-anchor-v2"
    assert AnchorGrader(strict=True).version == "t3-anchor-strict-v2"


@pytest.mark.golden
def test_strict_rejects_reflow_that_lenient_accepts() -> None:
    # The discriminator: the reflowed quote lenient scores 1.0, strict must score 0.0.
    source = "samples,\n    with replacement,\n    from the observed data"
    out = '{"claims":[{"claim":"a","quote":"samples, with replacement, from the observed data"}]}'
    gold = {"source_text": source, "expected_claims": 1}
    assert AnchorGrader().grade(output=out, gold=gold).value == 1.0
    assert AnchorGrader(strict=True).grade(output=out, gold=gold).value == 0.0


@pytest.mark.golden
def test_strict_accepts_character_exact_quote() -> None:
    # A byte-for-byte substring still scores 1.0 under strict (it is not just "reject all").
    out = '{"claims":[{"claim":"a","quote":"a resampling method"}]}'
    score = AnchorGrader(strict=True).grade(
        output=out, gold={"source_text": SRC, "expected_claims": 1}
    )
    assert score.value == 1.0 and score.status == "ok"


@pytest.mark.unit
def test_registry_resolves_anchor_strict() -> None:
    from claude_ablation_lab.graders import get_grader

    lenient, strict = get_grader("anchor"), get_grader("anchor_strict")
    assert isinstance(lenient, AnchorGrader) and not lenient.strict
    assert isinstance(strict, AnchorGrader) and strict.strict
    assert strict.version == "t3-anchor-strict-v2"


@pytest.mark.unit
def test_strict_trims_incidental_edge_whitespace_by_design() -> None:
    # By design: strict is char-exact on the *trimmed* quote — incidental leading/trailing
    # whitespace is not a faithfulness failure (internal reflow still is, tested above).
    out = '{"claims":[{"claim":"a","quote":"   a resampling method   "}]}'
    score = AnchorGrader(strict=True).grade(
        output=out, gold={"source_text": SRC, "expected_claims": 1}
    )
    assert score.value == 1.0


@pytest.mark.golden
def test_short_quotes_never_score() -> None:
    # The v2 anti-gaming floor: `"the"×3` — or a 2-word phrase the task prompt itself
    # leaks (the audit's '"Project Vega"×3 scores 1.0' demonstration) — must score 0.
    out = (
        '{"claims":[{"claim":"a","quote":"The bootstrap"},'
        '{"claim":"b","quote":"the"},{"claim":"c","quote":"same size"}]}'
    )
    gold = {"source_text": SRC, "expected_claims": 3}
    assert AnchorGrader().grade(output=out, gold=gold).value == 0.0
    assert AnchorGrader(strict=True).grade(output=out, gold=gold).value == 0.0


@pytest.mark.unit
def test_duplicate_quotes_count_once() -> None:
    # Repeating one verbatim quote must not multiply the score: 1 distinct / max(3, 3).
    out = (
        '{"claims":[{"claim":"a","quote":"a resampling method"},'
        '{"claim":"b","quote":"a resampling method"},'
        '{"claim":"c","quote":"a resampling method"}]}'
    )
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC, "expected_claims": 3})
    assert score.value == pytest.approx(1 / 3)
    assert score.subscores["n_verbatim"] == 1.0
    assert score.details["duplicate_quotes"] == 2
