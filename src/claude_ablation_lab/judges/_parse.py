"""Verdict extraction from raw judge-CLI output — the anti-narration scanner.

Both CLIs are instructed to output a single JSON object, but print-mode CLIs
narrate: a preamble, a ```json fence, sometimes an unrelated JSON fragment before
the real answer. Taking the *first* JSON-ish thing would drop real verdicts, and
regex-matching ``"winner"`` would accept garbage. So: scan EVERY balanced
top-level ``{...}`` span (quote/escape-aware), ``json.loads`` each, and accept the
first object that schema-matches ``{"winner": A|B|tie}``. Nothing matches →
``None`` — the caller records ``unparsed``, which is a first-class reported
status, never silently treated as clean.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from claude_ablation_lab.judge import RawVerdict

__all__ = ["PARSER_VERSION", "extract_verdict"]

#: Part of every judge ``version`` — a parser change re-judges stored outputs.
PARSER_VERSION = "vp-v1"

_WINNERS: dict[str, str] = {"a": "A", "b": "B", "tie": "tie"}


def extract_verdict(text: str) -> tuple[RawVerdict, str] | None:
    """``(winner, reason)`` from the first schema-matching JSON object, else ``None``."""
    for span in _json_spans(text):
        try:
            obj = json.loads(span)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        winner = obj.get("winner")
        if not isinstance(winner, str):
            continue
        normalized = _WINNERS.get(winner.strip().lower())
        if normalized is None:
            continue
        reason = obj.get("reason", "")
        reason_text = reason.strip()[:500] if isinstance(reason, str) else ""
        verdict: RawVerdict = normalized  # type: ignore[assignment]
        return verdict, reason_text
    return None


def _json_spans(text: str) -> Iterator[str]:
    """Yield every balanced top-level ``{...}`` span (string- and escape-aware)."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            if depth > 0:
                in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                yield text[start : i + 1]
