"""Set exact-match grader — the fraction of a fixed, ordered list of expected answers
the model got right. Backs the MULTI-ITEM reasoning probes (find-the-bug over N
functions): a smooth ``k/N`` score per cell, which discriminates far better at low
epoch counts than a single binary answer (the t7-v1 lesson — one bug saturated and its
0/1 score was too coarse and too fragile to grade).

Each sub-problem is numbered ``1..N``. The model answers per number; the score is
``(# positions whose answer matches the expected answer) / N``. Extraction is robust to
verbose prose (the other t7-v1 lesson): per-number ``ANSWER k: <x>`` lines (last per k
wins), or a JSON ``{"answers": {"1": ...}}`` object. Comparison is whitespace-insensitive
(shared ``_squash`` with :mod:`exact_match`), so re-typing a code line with different
spacing/indentation is not penalised.

gold:
  expected: ["<answer 1>", "<answer 2>", ...]   # ordered; position k <-> ANSWER k
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score
from claude_ablation_lab.graders.exact_match import _squash

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ExactMatchSetGrader"]

_DECODER = json.JSONDecoder()
#: ``ANSWER 3: <x>`` / ``answer #3 = <x>`` (case-insensitive), one per line; last k wins.
_ANSWER_N_RE = re.compile(r"(?im)^[ \t>*\-]*answer[ \t]*#?[ \t]*(\d+)[ \t]*[:=][ \t]*(.+?)[ \t]*$")


@dataclass(frozen=True, slots=True)
class ExactMatchSetGrader:
    """Fraction of the ordered gold answers the model matched (0..1)."""

    @property
    def version(self) -> str:
        # v2: shared _squash now strips surrounding backticks/quotes — v1 scored a
        # markdown-wrapped answer (`` `line` ``) as 0, biasing by formatting style.
        return "exact-match-set-v2"

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        expected = gold.get("expected")
        if not expected or not isinstance(expected, (list, tuple)):
            return Score(
                0.0, status="grader_error", details={"reason": "gold.expected missing or empty"}
            )

        got = _extract_answers(output)
        if not got:
            return Score(0.0, status="unparseable", details={"raw": output[:500]})

        n = len(expected)
        hits = sum(
            1
            for i, exp in enumerate(expected, start=1)
            if i in got and _squash(got[i]) == _squash(str(exp))
        )
        return Score(
            value=hits / n,
            subscores={"n": float(n), "found": float(hits)},
            details={"answered": len(got)},
        )


def _extract_answers(output: str) -> dict[int, str]:
    """Recover ``{position: answer}`` from ``ANSWER k:`` lines or a JSON answers object."""
    got: dict[int, str] = {}
    for match in _ANSWER_N_RE.finditer(output):
        got[int(match.group(1))] = match.group(2).strip()  # last occurrence per k wins
    if got:
        return got

    # Fallback: the LAST {"answers": {"1": ...}} object (a spurious earlier one loses).
    answers: dict[str, Any] | None = None
    for start in (i for i, ch in enumerate(output) if ch in "{["):
        try:
            value, _end = _DECODER.raw_decode(output, start)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("answers"), dict):
            answers = value["answers"]
    if answers:
        for key, val in answers.items():
            try:
                got[int(key)] = str(val)
            except (ValueError, TypeError):
                continue
    return got
