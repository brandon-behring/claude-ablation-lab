"""T3 — verbatim-anchor claim-extraction grader.

Given a fixed source text, the model is asked to extract claims, each with an
*exact-substring* quote from the source. This grader scores the fraction of
quotes that are verbatim substrings — a zero-judge faithfulness / hallucination
signal that varies by model × effort.

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


@dataclass(frozen=True, slots=True)
class AnchorGrader:
    """Fraction of extracted quotes that are whitespace-normalised substrings."""

    version: str = "t3-anchor-v1"

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        """Score ``output`` against ``gold["source_text"]`` (whitespace-normalised)."""
        source = _normalize(str(gold.get("source_text", "")))
        if not source:
            return Score(0.0, status="grader_error", details={"reason": "empty source_text"})

        claims = _parse_claims(output)
        if claims is None:
            return Score(0.0, status="unparseable", details={"raw": output[:500]})

        quotes = [str(c.get("quote", "")).strip() for c in claims]
        quotes = [q for q in quotes if q]
        if not quotes:
            return Score(0.0, status="unparseable", details={"reason": "no non-empty quotes"})

        verbatim = [q for q in quotes if _normalize(q) in source]
        misses = [q for q in quotes if _normalize(q) not in source]
        return Score(
            value=len(verbatim) / len(quotes),
            subscores={"n_quotes": float(len(quotes)), "n_verbatim": float(len(verbatim))},
            details={"misses": misses},
        )


def _normalize(text: str) -> str:
    """Collapse all runs of whitespace to single spaces (reflow-insensitive match)."""
    return " ".join(text.split())


def _parse_claims(output: str) -> list[dict[str, Any]] | None:
    """Recover a list of claim objects from the model output, or ``None``."""
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
