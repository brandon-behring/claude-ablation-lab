"""Adversarial battery for the exact-match grader — it scores CORRECTNESS (the right
answer), not faithfulness (a real substring). One test per attack: right / wrong /
spray / format-miss / numeric / edge. The load-bearing property (vs the anchor grader)
is that a real-but-wrong line scores 0.0."""

from __future__ import annotations

import pytest

from claude_ablation_lab.graders.exact_match import ExactMatchGrader

G = ExactMatchGrader()
CODE_GOLD = {"expected": ["rank = q * n"]}
NUM_GOLD = {"expected": ["42"], "numeric": True}


def val(output: str, gold: dict = CODE_GOLD):  # type: ignore[type-arg]
    return G.grade(output=output, gold=gold)


@pytest.mark.unit
def test_correct_json_answer_scores_one() -> None:
    assert val('{"answer": "rank = q * n", "why": "off by one"}').value == 1.0


@pytest.mark.unit
def test_whitespace_insensitive_operator_spacing_and_indent() -> None:
    # Re-typing the line with different operator spacing / indentation must still
    # match — tokens are what matter, not formatting.
    assert val('{"answer": "    rank = q*n"}').value == 1.0


@pytest.mark.unit
def test_bare_line_without_json_scores_one() -> None:
    assert val("rank = q * n").value == 1.0


@pytest.mark.unit
def test_bare_json_scalar_is_read() -> None:
    assert val('"rank = q * n"').value == 1.0  # a JSON string scalar
    assert G.grade(output="42", gold=NUM_GOLD).value == 1.0  # a JSON number scalar


@pytest.mark.unit
def test_a_real_but_wrong_line_scores_zero() -> None:
    # THE property that separates exact_match from anchor: a genuine line of the
    # source that is not the bug must score 0.0, not 1.0.
    s = val('{"answer": "hi = min(lo + 1, n - 1)"}')
    assert s.value == 0.0 and s.status == "ok"


@pytest.mark.unit
def test_spraying_the_whole_function_does_not_match() -> None:
    # Equality, not containment: dumping every line (which contains the buggy one)
    # must NOT score 1.0, else a model games it by quoting everything.
    assert val('{"answer": "def quantile(sorted_values, q): rank = q * n return x"}').value == 0.0


@pytest.mark.unit
def test_empty_output_is_unparseable() -> None:
    assert val("").status == "unparseable"


@pytest.mark.unit
def test_json_object_without_answer_key_falls_back_and_scores_zero() -> None:
    # A dict with no answer field is not an answer; robust extraction falls back to the
    # whole output, which does not match -> a scored-0 wrong answer (included in the
    # mean), not an excluded unparseable.
    s = val('{"why": "something", "note": "x"}')
    assert s.value == 0.0 and s.status == "ok"


@pytest.mark.unit
def test_spurious_array_in_prose_does_not_shadow_the_answer() -> None:
    # The v1 bug it fixes: a JSON array like [10, 20, 30] in the reasoning was parsed
    # first and shadowed the real answer object, mis-scoring a correct verbose response
    # as 0 (a bias against exactly the max-effort configs under test).
    out = 'For [10, 20, 30] the median is 20.\n```json\n{"answer": "rank = q * n"}\n```'
    assert val(out).value == 1.0


@pytest.mark.unit
def test_last_answer_object_wins_over_an_earlier_example() -> None:
    out = '{"answer": "lo = int(rank)"} ... actually no:\n{"answer": "rank = q * n"}'
    assert val(out).value == 1.0


@pytest.mark.unit
def test_a_single_code_fence_is_the_answer() -> None:
    assert val("The buggy line is:\n```python\nrank = q * n\n```").value == 1.0


@pytest.mark.unit
def test_answer_delimiter_line_wins() -> None:
    assert val("Reasoning about type-7 interpolation...\nANSWER: rank = q * n").value == 1.0


@pytest.mark.unit
def test_prose_without_the_line_scores_zero() -> None:
    assert val("The bug is in how the rank is computed.").value == 0.0


@pytest.mark.unit
def test_alternate_answer_keys_are_read() -> None:
    for key in ("result", "value", "line", "final"):
        assert val(f'{{"{key}": "rank = q * n"}}').value == 1.0


@pytest.mark.unit
def test_multiple_acceptable_answers_any_match() -> None:
    gold = {"expected": ["rank = q * n", "foo = bar"]}
    assert val('{"answer": "foo=bar"}', gold).value == 1.0


@pytest.mark.unit
def test_string_expected_is_coerced_to_a_singleton() -> None:
    assert G.grade(output='{"answer":"a b c"}', gold={"expected": "a b c"}).value == 1.0


@pytest.mark.unit
def test_missing_or_empty_gold_expected_is_grader_error() -> None:
    assert G.grade(output='{"answer":"x"}', gold={}).status == "grader_error"
    assert G.grade(output='{"answer":"x"}', gold={"expected": []}).status == "grader_error"


# --- numeric mode ---


@pytest.mark.unit
def test_numeric_exact_and_float_and_embedded_forms_match() -> None:
    assert G.grade(output='{"answer": "42"}', gold=NUM_GOLD).value == 1.0
    assert G.grade(output='{"answer": "42.0"}', gold=NUM_GOLD).value == 1.0
    assert G.grade(output="The answer is 42.", gold=NUM_GOLD).value == 1.0


@pytest.mark.unit
def test_numeric_wrong_number_scores_zero() -> None:
    assert G.grade(output='{"answer": "43"}', gold=NUM_GOLD).value == 0.0


@pytest.mark.unit
def test_numeric_tolerance_and_commas() -> None:
    tol = {"expected": ["3.14159"], "numeric": True, "abs_tol": 1e-2}
    assert G.grade(output='{"answer": "3.14"}', gold=tol).value == 1.0
    thou = {"expected": ["1000"], "numeric": True}
    assert G.grade(output='{"answer": "1,000"}', gold=thou).value == 1.0


@pytest.mark.unit
def test_numeric_non_numeric_answer_scores_zero() -> None:
    assert G.grade(output='{"answer": "not a number"}', gold=NUM_GOLD).value == 0.0


@pytest.mark.unit
def test_markdown_or_quote_wrapped_answer_matches() -> None:
    # A correct line wrapped in a markdown code span or quotes must not score 0.
    assert val('{"answer": "`rank = q * n`"}').value == 1.0
    assert val("ANSWER: `rank = q * n`").value == 1.0


@pytest.mark.unit
def test_version_is_stable() -> None:
    assert G.version == "exact-match-v3"
