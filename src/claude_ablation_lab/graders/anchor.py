"""T3 — verbatim-anchor claim-extraction grader.

Given a fixed source text, the model is asked to extract a fixed number of claims
(``expected_claims``, default 5), each with an *exact-substring* quote from the
source. This grader scores ``n_verbatim / max(expected_claims, n_claims)`` — a
zero-judge faithfulness / hallucination signal that varies by model × effort.

Denominator design (why ``max(expected, n)``): scoring only the quotes the model
*emitted* would let it game the metric by returning one perfect quote and
omitting the rest (1/1 = 1.0). Dividing by the expected count penalises
under-production (1 of 5 → 0.2), while ``max(…, n)`` avoids rewarding
over-production. Empty/missing quotes count as misses (they are not verbatim);
a valid-but-empty claim list scores an honest 0.0, and only output with no
parseable claim structure is ``unparseable``.

Matching is **whitespace-normalised** (runs of whitespace collapsed to single
spaces on both sides): a model that copies the content faithfully but reflows
line-wrapping is not penalised, while fabricated/altered content still fails.
Character-strict SHA256 anchoring is a backlog upgrade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score
from claude_ablation_lab.graders._parse import lenient_json

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["AnchorGrader"]

_CLAIM_LIST_KEYS = ("claims", "anchors", "extractions")
_DEFAULT_EXPECTED_CLAIMS = 5


@dataclass(frozen=True, slots=True)
class AnchorGrader:
    """Fraction of expected claims whose quote is a whitespace-normalised substring."""

    version: str = "t3-anchor-v1"
    expected_claims: int = _DEFAULT_EXPECTED_CLAIMS

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        """Score ``output`` against ``gold["source_text"]`` (whitespace-normalised)."""
        source = _normalize(str(gold.get("source_text", "")))
        if not source:
            return Score(0.0, status="grader_error", details={"reason": "empty source_text"})

        claims = _parse_claims(output)
        if claims is None:  # no parseable claim structure at all
            return Score(0.0, status="unparseable", details={"raw": output[:500]})

        expected = int(gold.get("expected_claims", self.expected_claims))
        quotes = [str(claim.get("quote", "")).strip() for claim in claims]
        verbatim = [q for q in quotes if q and _normalize(q) in source]
        misses = [q for q in quotes if not (q and _normalize(q) in source)]

        denominator = max(expected, len(claims))
        value = len(verbatim) / denominator if denominator else 0.0
        return Score(
            value=value,
            subscores={
                "n_claims": float(len(claims)),
                "n_verbatim": float(len(verbatim)),
                "expected": float(expected),
            },
            details={"misses": misses, "shortfall": max(0, expected - len(claims))},
        )


def _normalize(text: str) -> str:
    """Collapse all runs of whitespace to single spaces (reflow-insensitive match)."""
    return " ".join(text.split())


def _parse_claims(output: str) -> list[dict[str, Any]] | None:
    """Recover a list of claim objects from the model output.

    Returns ``None`` only when no claim *list* is present (unparseable); a
    valid-but-empty list returns ``[]`` (→ an honest 0.0, not a drop).
    """
    data = lenient_json(output)
    if data is None:
        return None
    if isinstance(data, dict):
        for key in _CLAIM_LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
        else:
            return None
    if not isinstance(data, list):
        return None
    return [item for item in data if isinstance(item, dict)]
