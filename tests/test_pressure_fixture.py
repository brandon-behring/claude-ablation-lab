"""Pressure-test fixture invariants — the gold answer is actually a line of the
function (satisfiable / not a typo), the grader scores it 1.0 while a real-but-wrong
decoy line scores 0.0, and the source is rendered into the prompt. Guards against
silent fixture drift (an edited function or a mistyped gold line)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_ablation_lab.graders.exact_match import ExactMatchGrader
from claude_ablation_lab.task import load_task

REPO = Path(__file__).resolve().parents[1]
T7 = load_task(REPO / "tasks" / "t7_find_bug.yaml")


def _squash(text: str) -> str:
    return "".join(str(text).split())


@pytest.mark.unit
def test_gold_buggy_line_appears_verbatim_in_the_function() -> None:
    source = _squash(str(T7.params["source_text"]))
    for line in T7.gold["expected"]:
        assert _squash(line) in source, line  # else the answer key is unsatisfiable


@pytest.mark.unit
def test_gold_scores_one_and_a_real_decoy_line_scores_zero() -> None:
    grader = ExactMatchGrader()
    assert grader.grade(output='{"answer": "rank = q * n"}', gold=T7.gold).value == 1.0
    # `lo = int(rank)` is a genuine line of the function but NOT the bug.
    assert grader.grade(output='{"answer": "lo = int(rank)"}', gold=T7.gold).value == 0.0


@pytest.mark.unit
def test_source_text_is_rendered_into_the_prompt() -> None:
    assert "{source_text}" not in T7.prompt and "quantile" in T7.prompt
