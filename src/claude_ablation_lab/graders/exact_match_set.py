"""Set exact-match grader — the fraction of a fixed, ordered list of expected answers
the model got right. Backs the MULTI-ITEM reasoning probes (find-the-bug over N
functions; hard-math over N problems): a smooth ``k/N`` score per cell, which
discriminates far better at low epoch counts than a single binary answer (the t7-v1
lesson — one bug saturated and its 0/1 score was too coarse and too fragile to grade).

Each sub-problem is numbered ``1..N``; the model answers per number via ``ANSWER k: <x>``
lines (last per k wins) or a JSON ``{"answers": {"1": ...}}`` object.

Two modes:

- **string** (default; t7 find-the-bug): score = fraction of positions whose answer
  ``_squash``-equals the gold (whitespace/backtick/quote-insensitive).
- **numeric** (``gold.numeric: true``; t8 hard-math): **STRICT** — each answered position
  must be a *bare integer* (digits, optional leading sign, valid thousands groups only).
  Anything else — an equation, prose, a fraction, markdown, a stray number — makes the
  **whole cell ``unparseable``** (excluded from the mean, *never* a silently-biased 0).
  This is deliberate: three separate "lenient extraction" confounds (a spurious JSON array
  shadowing the answer; backtick wrapping; first-number-on-the-line) each scored *correct*
  verbose answers as 0 and biased the A/B against high-effort models. Strict parsing removes
  that biased *zero* — non-compliance surfaces as a visible ``unparseable`` count rather than a
  corrupted score — but trades it for a *missing-data* bias if the unparseable rate is nonzero
  and correlates with model/effort (a config that admits failure is dropped; one that guesses is
  scored). So it is safe only when unparseables are ~0: treat a nonzero rate as an invalid-run /
  gating signal, not neutral missing data. The task prompt demands a bare answer to keep it ~0.

gold:
  expected: ["<answer 1>", "<answer 2>", ...]   # ordered; position k <-> ANSWER k
  numeric: false                                # true -> strict bare-integer match per position
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
#: Surrounding markdown / quote / LaTeX wrappers stripped before the bare-integer check.
_WRAP_CHARS = " \t\r\n*_`'\"$"
#: A BARE integer: plain digits, or valid thousands groups (``7,334`` ok, ``2,4`` not).
_BARE_INT_RE = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)$")


@dataclass(frozen=True, slots=True)
class ExactMatchSetGrader:
    """Fraction of the ordered gold answers the model matched (0..1)."""

    @property
    def version(self) -> str:
        # v4: shared _squash now also drops inline comments / trailing periods (an annotated
        # correct code line was scored 0 in string mode). v3: numeric mode STRICT (bare integer
        # per position, else the cell is unparseable). v2: _squash strips backticks/quotes.
        return "exact-match-set-v4"

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

        if bool(gold.get("numeric", False)):
            # A numeric gold must itself be bare integers — else a typo'd gold is silently
            # unhittable for every config (a fixture bug: surface it, don't deflate scores).
            gold_ints = [_bare_int(str(e)) for e in expected]
            if any(g is None for g in gold_ints):
                return Score(
                    0.0,
                    status="grader_error",
                    details={"reason": "numeric gold must be bare integers"},
                )
            # STRICT: every ANSWERED position (1..N) must be a bare integer, else the whole
            # cell is unparseable. Absent positions are misses (they count in N).
            parsed: dict[int, int] = {}
            for k, raw in got.items():
                if not 1 <= k <= n:
                    continue  # an extra ANSWER beyond N is ignored, not a fault
                value = _bare_int(raw)
                if value is None:
                    return Score(
                        0.0,
                        status="unparseable",
                        details={"reason": f"position {k} is not a bare integer", "raw": raw[:100]},
                    )
                parsed[k] = value
            hits = sum(
                1 for i, g in enumerate(gold_ints, start=1) if i in parsed and g == parsed[i]
            )
        else:
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


def _bare_int(text: str) -> int | None:
    """The int value iff ``text`` — after stripping surrounding markdown/quote/LaTeX wrappers —
    is a BARE integer (plain digits or valid thousands groups). Otherwise ``None``."""
    stripped = str(text).strip(_WRAP_CHARS)
    if not _BARE_INT_RE.match(stripped):
        return None
    return int(stripped.replace(",", ""))


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
