"""Pressure-test fixture invariants (t7, multi-bug) — every gold buggy line is actually
in its function (satisfiable), the score is a smooth fraction (full -> 1.0, partial ->
k/N), and the source renders into the prompt. Guards against silent fixture drift."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_ablation_lab.graders.exact_match_set import ExactMatchSetGrader
from claude_ablation_lab.task import load_task

REPO = Path(__file__).resolve().parents[1]
T7 = load_task(REPO / "tasks" / "t7_find_bug.yaml")
GOLD = list(T7.gold["expected"])
G = ExactMatchSetGrader()


def _squash(text: str) -> str:
    return "".join(str(text).split())


def _answers(lines: list[str]) -> str:
    return "\n".join(f"ANSWER {i}: {line}" for i, line in enumerate(lines, start=1))


@pytest.mark.unit
def test_every_gold_line_appears_verbatim_in_its_function() -> None:
    source = _squash(str(T7.params["source_text"]))
    for line in GOLD:
        assert _squash(line) in source, line  # else that item is unsatisfiable


@pytest.mark.unit
def test_multi_item_enough_for_a_smooth_score() -> None:
    # v1 was a single binary bug (0/1, 0.2-quantum at n=5). Multiple items give a smooth
    # k/N score that discriminates — the reason this task was hardened.
    assert len(GOLD) >= 6


@pytest.mark.unit
def test_a_full_answer_scores_one_and_a_partial_scores_the_fraction() -> None:
    assert G.grade(output="reasoning...\n" + _answers(GOLD), gold=T7.gold).value == 1.0
    half = GOLD[:3] + ["not the bug", "not the bug", "not the bug"]
    assert G.grade(output=_answers(half), gold=T7.gold).value == pytest.approx(
        len(GOLD[:3]) / len(GOLD)
    )


@pytest.mark.unit
def test_source_text_is_rendered_into_the_prompt() -> None:
    assert "{source_text}" not in T7.prompt and "quantile" in T7.prompt
