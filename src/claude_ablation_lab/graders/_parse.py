"""Shared, dependency-light parsing helpers for graders.

``lenient_json`` tolerates the preamble/trailing chatter a model may wrap around
its JSON (the grading analog of :func:`claude_ablation_lab.runner.extract_json`,
but it also recovers a top-level *array*, which the dict-only runner helper does
not).

``parse_verdict`` is adapted verbatim from ``prompt_injection_detector``'s
``src/pid/judge.py`` (``_parse_verdict``, the proven robust one-word parse). It
is copied — not imported — because that repo is a separate project, not a
dependency of this harness (upstream-friction discipline).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

__all__ = ["lenient_json", "parse_verdict"]


def _slice(text: str, open_ch: str, close_ch: str) -> str | None:
    """Return the first-``open_ch``…last-``close_ch`` slice, or ``None``."""
    start, end = text.find(open_ch), text.rfind(close_ch)
    return text[start : end + 1] if start != -1 and end > start else None


def _candidates(text: str) -> Iterator[str]:
    """Yield the whole text, then bracket slices, outermost structure first.

    The outermost JSON is whichever bracket *opens* first — so a top-level array
    ``[{…}]`` is recovered as the list, not the inner object it contains.
    """
    yield text
    obj, arr = _slice(text, "{", "}"), _slice(text, "[", "]")
    first_obj, first_arr = text.find("{"), text.find("[")
    array_outermost = first_arr != -1 and (first_obj == -1 or first_arr < first_obj)
    for candidate in (arr, obj) if array_outermost else (obj, arr):
        if candidate is not None:
            yield candidate


def lenient_json(text: str) -> Any | None:
    """Best-effort parse of the JSON value embedded in ``text``.

    Tries the whole string, then a first-``{``…last-``}`` object slice, then a
    first-``[``…last-``]`` array slice. Returns the parsed value (dict or list)
    or ``None`` if nothing parses.
    """
    for candidate in _candidates(text):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def parse_verdict(raw: str) -> tuple[int, bool]:
    """Map a free-text injection verdict to ``(label, parse_failed)``.

    ``label`` is ``1`` for *injection*, ``0`` for *safe*. ``parse_failed`` is
    ``True`` when the response was ambiguous (defaults to ``0``). Adapted from
    ``prompt_injection_detector`` ``src/pid/judge.py:_parse_verdict``.
    """
    if not isinstance(raw, str):
        return 0, True
    lower = raw.strip().lower()
    if lower.startswith("injection"):
        return 1, False
    if lower.startswith("safe"):
        return 0, False
    if "injection" in lower and "safe" not in lower:
        return 1, False
    if "safe" in lower and "injection" not in lower:
        return 0, False
    return 0, True
