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
from typing import Any

__all__ = ["lenient_json", "parse_verdict"]


_DECODER = json.JSONDecoder()


def lenient_json(text: str) -> Any | None:
    """Best-effort parse of the JSON value embedded in ``text``.

    Tries the whole string first, then ``raw_decode`` from each ``{``/``[`` start
    (earliest first, so a top-level array ``[{…}]`` wins over the inner object it
    contains). ``raw_decode`` stops at the end of the first valid value, so
    trailing chatter (``{"a": 1} }``) no longer poisons an otherwise-valid object
    — the failure mode of a first-open…last-close slice. Returns the parsed value
    (dict/list/scalar) or ``None`` if nothing parses.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    for start in sorted(i for i, ch in enumerate(text) if ch in "{["):
        try:
            value, _end = _DECODER.raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            continue
        return value
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
