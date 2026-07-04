"""Exact-match grader — the model's final answer must equal a gold answer.

Where :class:`~claude_ablation_lab.graders.anchor.AnchorGrader` scores *faithfulness*
(is the quoted span actually FROM the source?), this grader scores *correctness* (is
the answer the RIGHT one?). It backs the single-answer reasoning probes in the
pressure-test suite — debugging (the one buggy line), math/logic (the numeric
answer), long-context retrieval (the one correct fact) — each of which has exactly
one right answer that either matches or does not, so the score is a clean ``{0, 1}``.

Gold contract::

    gold:
      expected: ["<answer>", ...]   # one or more acceptable answers; ANY match -> 1.0
      numeric: false                # optional; true -> compare parsed numbers with tolerance
      rel_tol: 1e-9                 # optional numeric tolerances (math.isclose)
      abs_tol: 0.0

The answer is read from JSON (``{"answer": ...}`` / ``result`` / ``value`` / ``line``),
falling back to the whole stripped output when the model emits the bare answer.
String comparison is **whitespace-insensitive** (all whitespace removed) so that
re-typing a code line with different operator spacing or indentation is not
penalised — only the tokens matter, which is the honest bar for "did you name the
right line". It is equality, not containment: dumping the whole function does NOT
match (that would let a model spray every line and score 1.0). Numeric mode parses
the first number out of the answer and compares with :func:`math.isclose`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score
from claude_ablation_lab.graders._parse import lenient_json

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ExactMatchGrader"]

#: Keys under which a model may return its answer (first present wins).
_ANSWER_KEYS = ("answer", "result", "value", "line", "label", "final")
#: First signed int/float (optional thousands commas, optional exponent) in a string.
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


@dataclass(frozen=True, slots=True)
class ExactMatchGrader:
    """1.0 iff the model's answer matches a gold answer; 0.0 otherwise."""

    @property
    def version(self) -> str:
        return "exact-match-v1"

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        expected = gold.get("expected")
        if isinstance(expected, str):
            expected = [expected]
        if not expected or not isinstance(expected, (list, tuple)):
            return Score(
                0.0, status="grader_error", details={"reason": "gold.expected missing or empty"}
            )

        answer = _extract_answer(output)
        if answer is None:
            return Score(0.0, status="unparseable", details={"raw": output[:500]})

        if bool(gold.get("numeric", False)):
            hit = _numeric_match(answer, expected, gold)
        else:
            got = _squash(answer)
            hit = any(_squash(str(e)) == got for e in expected)

        return Score(value=1.0 if hit else 0.0, details={"answer": answer[:200], "matched": hit})


def _squash(text: str) -> str:
    """Remove ALL whitespace — token-level equality for code lines / short answers."""
    return "".join(str(text).split())


def _extract_answer(output: str) -> str | None:
    """Recover the model's answer: a JSON answer field, a bare scalar, else stripped text."""
    data = lenient_json(output)
    if isinstance(data, dict):
        for key in _ANSWER_KEYS:
            if data.get(key) is not None:
                return str(data[key])
        return None  # a JSON object with no answer key is a format miss, not an answer
    if isinstance(data, (str, int, float)):
        return str(data)
    # No usable JSON object: fall back to the whole stripped output (works when the
    # model emits just the answer). A blank output is unparseable.
    return output.strip() or None


def _numeric_match(answer: str, expected: Any, gold: Mapping[str, Any]) -> bool:
    """True if the first number in ``answer`` is close to any expected number."""
    got = _first_number(answer)
    if got is None:
        return False
    rel_tol = float(gold.get("rel_tol", 1e-9))
    abs_tol = float(gold.get("abs_tol", 0.0))
    for e in expected:
        want = _first_number(str(e))
        if want is not None and math.isclose(got, want, rel_tol=rel_tol, abs_tol=abs_tol):
            return True
    return False


def _first_number(text: str) -> float | None:
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None
