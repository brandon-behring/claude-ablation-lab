"""T3 anchor grader — verbatim-substring scoring + parse robustness."""

from __future__ import annotations

import pytest

from claude_ablation_lab.graders.anchor import AnchorGrader

SRC = (
    "The bootstrap is a resampling method. Each resample has the same size. "
    "The percentile interval takes the 2.5th and 97.5th percentiles."
)


@pytest.mark.golden
def test_all_substrings_score_one() -> None:
    out = '{"claims":[{"claim":"a","quote":"resampling method"},{"claim":"b","quote":"same size"}]}'
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC})
    assert score.value == 1.0
    assert score.status == "ok"
    assert score.subscores == {"n_quotes": 2.0, "n_verbatim": 2.0}


@pytest.mark.golden
def test_partial_substrings_give_fraction() -> None:
    out = (
        '{"claims":[{"claim":"a","quote":"resampling method"},{"claim":"b","quote":"NOT PRESENT"}]}'
    )
    score = AnchorGrader().grade(output=out, gold={"source_text": SRC})
    assert score.value == 0.5
    assert score.details["misses"] == ["NOT PRESENT"]


@pytest.mark.golden
def test_bare_list_and_preamble_tolerated() -> None:
    out = 'Sure, here is the JSON:\n[{"claim":"a","quote":"bootstrap"}]\nDone.'
    assert AnchorGrader().grade(output=out, gold={"source_text": SRC}).value == 1.0


@pytest.mark.unit
def test_garbage_is_unparseable() -> None:
    assert AnchorGrader().grade(output="no json here", gold={"source_text": SRC}).status == (
        "unparseable"
    )


@pytest.mark.unit
def test_no_quotes_is_unparseable() -> None:
    out = '{"claims":[{"claim":"missing its quote"}]}'
    assert AnchorGrader().grade(output=out, gold={"source_text": SRC}).status == "unparseable"


@pytest.mark.unit
def test_empty_source_is_grader_error() -> None:
    out = '{"claims":[{"claim":"a","quote":"bootstrap"}]}'
    assert AnchorGrader().grade(output=out, gold={}).status == "grader_error"


@pytest.mark.golden
def test_whitespace_reflow_is_tolerated() -> None:
    # The source is hard-wrapped mid-sentence; a faithful quote reflows it.
    source = "samples,\n    with replacement,\n    from the observed data"
    out = '{"claims":[{"claim":"a","quote":"samples, with replacement, from the observed data"}]}'
    assert AnchorGrader().grade(output=out, gold={"source_text": source}).value == 1.0
