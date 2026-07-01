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

Matching is **whitespace-normalised** by default (runs of whitespace collapsed to
single spaces on both sides): a model that copies the content faithfully but reflows
line-wrapping is not penalised, while fabricated/altered content still fails. The
``strict=True`` variant (grader ``anchor_strict``) instead requires a character-exact
substring — a stricter faithfulness bar whose gap from the lenient score is itself a
signal. (Plain substring, not SHA256 — hashing buys nothing at paragraph scale.)

Anti-gaming floor (v2): a quote counts only if it is at least :data:`MIN_QUOTE_WORDS`
words, and only *distinct* verbatim quotes count — otherwise ``"the"`` repeated, or a
short phrase leaked by the task prompt itself, scores a perfect 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score
from claude_ablation_lab.graders._parse import lenient_json

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["AnchorGrader", "MIN_QUOTE_WORDS"]

_CLAIM_LIST_KEYS = ("claims", "anchors", "extractions")
_DEFAULT_EXPECTED_CLAIMS = 5
#: Gaming floor (2026-07-01 methodology audit): without it, `"the"×3` — or a 2-word
#: phrase the task prompt itself leaks, like "Project Vega" — scores 1.0. A quote
#: counts only at >= this many words, and only DISTINCT verbatim quotes count.
MIN_QUOTE_WORDS = 3


@dataclass(frozen=True, slots=True)
class AnchorGrader:
    """Fraction of expected claims whose quote is a substring of the source.

    ``strict=False`` (default) matches whitespace-normalised (reflow-tolerant);
    ``strict=True`` requires a **character-exact** substring — a stricter faithfulness
    bar. The two carry different ``version`` strings, so a ledger can hold both and
    ``ablation regrade`` can add the strict score to stored T3 runs for free.
    """

    strict: bool = False
    expected_claims: int = _DEFAULT_EXPECTED_CLAIMS

    @property
    def version(self) -> str:
        # v2: the >= MIN_QUOTE_WORDS floor + distinct-quote counting (audit fix); a
        # behavior change is a version bump so stored rows re-grade under a new key.
        return "t3-anchor-strict-v2" if self.strict else "t3-anchor-v2"

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        """Score ``output`` against ``gold["source_text"]`` (char-exact if ``strict``)."""
        source = self._prep(str(gold.get("source_text", "")))
        if not source:
            return Score(0.0, status="grader_error", details={"reason": "empty source_text"})

        claims = _parse_claims(output)
        if claims is None:  # no parseable claim structure at all
            return Score(0.0, status="unparseable", details={"raw": output[:500]})

        expected = int(gold.get("expected_claims", self.expected_claims))
        # Quotes are edge-trimmed in both modes: incidental leading/trailing whitespace is
        # not a faithfulness signal. ``strict`` still requires the trimmed quote to be a
        # character-exact substring, so it catches internal reflow — only the edges are lenient.
        quotes = [str(claim.get("quote", "")).strip() for claim in claims]
        seen: set[str] = set()
        misses: list[str] = []
        duplicates = 0
        for quote in quotes:
            prepped = self._prep(quote)
            hit = bool(quote) and len(quote.split()) >= MIN_QUOTE_WORDS and prepped in source
            if not hit:
                misses.append(quote)  # absent, too short, or empty — all score nothing
            elif prepped in seen:
                duplicates += 1  # repeating one verbatim quote earns nothing extra
            else:
                seen.add(prepped)

        denominator = max(expected, len(claims))
        value = len(seen) / denominator if denominator else 0.0
        details: dict[str, Any] = {"misses": misses, "shortfall": max(0, expected - len(claims))}
        if duplicates:
            details["duplicate_quotes"] = duplicates
        return Score(
            value=value,
            subscores={
                "n_claims": float(len(claims)),
                "n_verbatim": float(len(seen)),
                "expected": float(expected),
            },
            details=details,
        )

    def _prep(self, text: str) -> str:
        """Char-exact in strict mode; whitespace-collapsed (reflow-tolerant) otherwise."""
        return text if self.strict else _normalize(text)


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
