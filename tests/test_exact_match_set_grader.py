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


NUM = {"expected": ["42", "100"], "numeric": True}


@pytest.mark.unit
def test_numeric_strict_bare_integers_score() -> None:
    assert G.grade(output="ANSWER 1: 42\nANSWER 2: 100", gold=NUM).value == 1.0
    # valid thousands grouping is still a bare integer
    thou = {"expected": ["1000", "100"], "numeric": True}
    assert G.grade(output="ANSWER 1: 1,000\nANSWER 2: 100", gold=thou).value == 1.0
    # markdown / quote / LaTeX WRAPPERS are stripped, still bare
    assert G.grade(output="ANSWER 1: `42`\nANSWER 2: $100$", gold=NUM).value == 1.0
    # JSON answers object with bare ints
    assert G.grade(output='{"answers": {"1": 42, "2": 100}}', gold=NUM).value == 1.0
    # bare but WRONG -> a correct low score, NOT unparseable
    assert G.grade(output="ANSWER 1: 42\nANSWER 2: 99", gold=NUM).value == pytest.approx(0.5)


@pytest.mark.unit
def test_numeric_strict_absent_position_is_a_miss_not_unparseable() -> None:
    s = G.grade(output="ANSWER 1: 42", gold=NUM)  # position 2 absent
    assert s.status == "ok" and s.value == pytest.approx(0.5)


@pytest.mark.unit
def test_numeric_strict_non_bare_answer_makes_the_cell_unparseable() -> None:
    # The three confounds the strict rule closes: an equation / restated-problem / fraction
    # answer that the OLD first-number match scored as a correct 0 (biasing against verbose
    # high-effort configs) is now EXCLUDED (unparseable), never a silent, effort-biased 0.
    for ans in ("42 = 6*7", "problem 1 -> 42", "84/2 so 42", "42 (by the formula)", "forty-two"):
        assert (
            G.grade(output=f"ANSWER 1: {ans}\nANSWER 2: 100", gold=NUM).status == "unparseable"
        ), ans


@pytest.mark.unit
def test_numeric_strict_false_positive_is_unparseable() -> None:
    # "42? no, final 41" would FALSE-HIT gold 42 under first-number match; strict excludes it.
    assert (
        G.grade(output="ANSWER 1: 42? no, final 41\nANSWER 2: 100", gold=NUM).status
        == "unparseable"
    )


@pytest.mark.unit
def test_numeric_strict_comma_merge_is_not_a_bare_integer() -> None:
    # "2,4" (invalid thousands grouping) must NOT parse as 24 — the comma-merge false-hit bug.
    gold = {"expected": ["24", "100"], "numeric": True}
    assert G.grade(output="ANSWER 1: 2,4\nANSWER 2: 100", gold=gold).status == "unparseable"


@pytest.mark.unit
def test_backtick_wrapped_answers_still_match() -> None:
    # The confound the v2 fix closes: a model that wraps each answer in markdown code
    # spans found the bugs and must score full, not 0.
    out = "\n".join(
        f"ANSWER {i}: `{line}`" for i, line in enumerate(["a = 1", "b = 2", "c = 3", "d = 4"], 1)
    )
    assert v(out).value == 1.0


@pytest.mark.unit
def test_string_mode_inline_comment_and_period_are_stripped() -> None:
    # F1 (pre-merge review): an annotated-but-correct code line must not score 0 in string mode.
    assert v(_ans(["a = 1  # first", "b = 2  # second", "c = 3", "d = 4."])).value == 1.0


@pytest.mark.unit
def test_numeric_gold_must_be_bare_integers() -> None:
    # F3: a non-bare numeric gold (typo / decimal) is a fixture bug -> grader_error, not a
    # silently unhittable item that deflates every config's score.
    bad = {"expected": ["3.5"], "numeric": True}
    assert G.grade(output="ANSWER 1: 42", gold=bad).status == "grader_error"


@pytest.mark.unit
def test_version_is_stable() -> None:
    assert G.version == "exact-match-set-v4"
