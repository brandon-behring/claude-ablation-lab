"""Adversarial battery for the set exact-match grader — a smooth fraction over N ordered
answers, robust to verbose prose. One test per attack."""

from __future__ import annotations

import pytest

from claude_ablation_lab.graders.exact_match_set import ExactMatchSetGrader

G = ExactMatchSetGrader()
GOLD = {"expected": ["a = 1", "b = 2", "c = 3", "d = 4"]}  # N = 4


def _ans(lines: list[str]) -> str:
    return "\n".join(f"ANSWER {i}: {line}" for i, line in enumerate(lines, start=1))


def v(output: str, gold: dict = GOLD):  # type: ignore[type-arg]
    return G.grade(output=output, gold=gold)


@pytest.mark.unit
def test_all_correct_scores_one() -> None:
    assert v("reasoning first...\n" + _ans(["a = 1", "b = 2", "c = 3", "d = 4"])).value == 1.0


@pytest.mark.unit
def test_half_correct_scores_half() -> None:
    assert v(_ans(["a = 1", "b = 2", "wrong", "wrong"])).value == pytest.approx(0.5)


@pytest.mark.unit
def test_whitespace_insensitive_per_answer() -> None:
    assert v(_ans(["a=1", "b  =  2", "c=3", "d = 4"])).value == 1.0


@pytest.mark.unit
def test_json_answers_object_and_equals_delimiter() -> None:
    assert v('{"answers": {"1":"a = 1","2":"b = 2","3":"c = 3","4":"d = 4"}}').value == 1.0
    assert v("ANSWER 1 = a = 1\nANSWER 2 = b = 2\nANSWER 3 = c = 3\nANSWER 4 = d = 4").value == 1.0


@pytest.mark.unit
def test_missing_positions_only_lose_those_items() -> None:
    assert v("ANSWER 1: a = 1\nANSWER 3: c = 3").value == pytest.approx(0.5)  # 2 of 4


@pytest.mark.unit
def test_duplicate_answer_line_last_wins() -> None:
    out = "ANSWER 1: WRONG\nANSWER 1: a = 1\nANSWER 2: b = 2\nANSWER 3: c = 3\nANSWER 4: d = 4"
    assert v(out).value == 1.0


@pytest.mark.unit
def test_spurious_answers_object_does_not_shadow_the_real_one() -> None:
    out = '{"answers":{"1":"x","2":"y"}} ... final:\n{"answers":{"1":"a = 1","2":"b = 2","3":"c = 3","4":"d = 4"}}'
    assert v(out).value == 1.0


@pytest.mark.unit
def test_extra_answers_beyond_n_are_ignored() -> None:
    assert v(_ans(["a = 1", "b = 2", "c = 3", "d = 4"]) + "\nANSWER 5: bonus").value == 1.0


@pytest.mark.unit
def test_empty_output_is_unparseable() -> None:
    assert v("").status == "unparseable"


@pytest.mark.unit
def test_no_answer_lines_is_unparseable() -> None:
    assert v("I reasoned about it but forgot to format anything.").status == "unparseable"


@pytest.mark.unit
def test_non_integer_answer_key_is_skipped() -> None:
    assert v('{"answers": {"one": "a = 1"}}').status == "unparseable"


@pytest.mark.unit
def test_missing_or_empty_gold_is_grader_error() -> None:
    assert G.grade(output=_ans(["a = 1"]), gold={}).status == "grader_error"
    assert G.grade(output=_ans(["a = 1"]), gold={"expected": []}).status == "grader_error"


@pytest.mark.unit
def test_subscores_report_found_and_n() -> None:
    s = v(_ans(["a = 1", "b = 2", "wrong", "wrong"]))
    assert s.subscores["n"] == 4.0 and s.subscores["found"] == 2.0


@pytest.mark.unit
def test_numeric_mode_matches_number_forms_and_scores_the_fraction() -> None:
    gold = {"expected": ["42", "100"], "numeric": True}
    assert G.grade(output="ANSWER 1: 42\nANSWER 2: 100", gold=gold).value == 1.0
    # commas / "= " / trailing prose all parse to the number; one wrong -> 0.5
    assert G.grade(output="ANSWER 1: = 42.0\nANSWER 2: 99", gold=gold).value == pytest.approx(0.5)
    thou = {"expected": ["1000", "100"], "numeric": True}
    assert G.grade(output="ANSWER 1: 1,000\nANSWER 2: 100", gold=thou).value == 1.0


@pytest.mark.unit
def test_numeric_mode_non_numeric_answer_misses() -> None:
    gold = {"expected": ["42"], "numeric": True}
    assert G.grade(output="ANSWER 1: forty-two", gold=gold).value == 0.0


@pytest.mark.unit
def test_backtick_wrapped_answers_still_match() -> None:
    # The confound the v2 fix closes: a model that wraps each answer in markdown code
    # spans found the bugs and must score full, not 0.
    out = "\n".join(
        f"ANSWER {i}: `{line}`" for i, line in enumerate(["a = 1", "b = 2", "c = 3", "d = 4"], 1)
    )
    assert v(out).value == 1.0


@pytest.mark.unit
def test_version_is_stable() -> None:
    assert G.version == "exact-match-set-v2"
