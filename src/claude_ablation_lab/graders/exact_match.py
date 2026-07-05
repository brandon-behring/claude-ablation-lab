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

**Answer extraction is deliberately robust** (v2), because verbose / max-effort
responses reason in prose and are otherwise scored 0 even when they contain the right
answer — a bias against exactly the configs under test. In priority order the answer
is read from: (1) an explicit ``ANSWER: <x>`` delimiter line (the contract to prefer);
(2) the LAST JSON object carrying an answer key — scanning every ``{...}`` so a
spurious array like ``[10, 20, 30]`` in the reasoning cannot shadow the real answer
object (the v1 bug); (3) a lone ```` ``` ````-fenced code block; (4) the whole stripped
output. Comparison is **whitespace-insensitive** (all whitespace removed) so re-typing
a code line with different spacing/indentation is not penalised. It is equality, not
containment: dumping the whole function does NOT match.

Numeric mode here is the **lenient first-number** match and still carries the effort-bias
confound (``= 1`` in ``3^200 mod 1000 = 1`` parses ``3``); it is currently unused by any task.
For unbiased numeric grading use the **strict bare-integer** mode of
:class:`~claude_ablation_lab.graders.exact_match_set.ExactMatchSetGrader`.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ExactMatchGrader"]

_DECODER = json.JSONDecoder()
#: Keys under which a model may return its answer (first present wins within an object).
_ANSWER_KEYS = ("answer", "result", "value", "line", "label", "final")
#: A first signed int/float (optional thousands commas, optional exponent) in a string.
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")
#: An explicit ``ANSWER: <x>`` / ``answer = <x>`` line (case-insensitive), leading
#: markdown bullet/quote decoration tolerated. The last such line wins.
_ANSWER_LINE_RE = re.compile(r"(?im)^[ \t>*\-]*answer[ \t]*[:=][ \t]*(.+?)[ \t]*$")
#: A fenced code block ```` ```lang\n...``` ````; group 1 is its body.
_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ExactMatchGrader:
    """1.0 iff the model's answer matches a gold answer; 0.0 otherwise."""

    @property
    def version(self) -> str:
        # v4: _squash also drops a trailing inline "# comment" + trailing period (an annotated
        # correct code line was scored 0 — the same effort-bias, string-mode). v3: _squash strips
        # backticks/quotes. v2: robust extraction (delimiter / last-object / fence). Each behavior
        # change bumps the version so stored rows re-grade under a new key.
        return "exact-match-v4"

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
    """Token-level key for equality: drop a trailing inline ``# comment``, strip surrounding
    whitespace + markdown-code-span backticks + quotes + a trailing period, then remove all
    internal whitespace. So ``for i in range(1, n):  # fix``, ``` `x = 1` ```, ``"x=1"`` and
    ``x=1`` all compare equal — a model that annotates, wraps, or reflows a correct code line
    is not scored 0 for formatting (the string-mode analog of the numeric bias fixes)."""
    stripped = re.sub(r"\s+#.*$", "", str(text))  # a trailing inline "# explanation"
    return "".join(stripped.strip(" \t\r\n`'\"").rstrip(".").split())


def _extract_answer(output: str) -> str | None:
    """Best-effort recovery of the model's answer; ``None`` only on blank output."""
    stripped = output.strip()
    if not stripped:
        return None
    # 0. The whole output is itself a bare JSON scalar (e.g. "line" or 42). A clean
    #    JSON *object* is left to the answer-key scan (2) below.
    try:
        whole = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        whole = None
    if isinstance(whole, (str, int, float)):
        return str(whole)

    # 1. An explicit ANSWER: delimiter line (the robust contract) — last one wins.
    delimited = None
    for match in _ANSWER_LINE_RE.finditer(output):
        delimited = match.group(1).strip()
    if delimited:
        return delimited

    # 2. The LAST JSON object carrying an answer key (scan every {/[ start, so a
    #    spurious array/object in the reasoning cannot shadow the real answer). A bare
    #    scalar is a fallback used only if no answer-bearing object is found.
    answer: str | None = None
    scalar: str | None = None
    for start in (i for i, ch in enumerate(output) if ch in "{["):
        try:
            value, _end = _DECODER.raw_decode(output, start)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            for key in _ANSWER_KEYS:
                if value.get(key) is not None:
                    answer = str(value[key])  # keep scanning — the LAST answer wins
                    break
        elif isinstance(value, (str, int, float)) and scalar is None:
            scalar = str(value)
    if answer is not None:
        return answer
    if scalar is not None:
        return scalar

    # 3. A single fenced code block is the answer (multiple fences are ambiguous -> skip).
    fences: list[str] = [str(f).strip() for f in _FENCE_RE.findall(output)]
    if len(fences) == 1:
        return fences[0]

    # 4. The whole stripped output (bare answer) — non-empty by the guard above.
    return stripped


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
