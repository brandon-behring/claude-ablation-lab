#!/usr/bin/env python3
"""Grader-side checker for the books-validate fixture. STDLIB-ONLY, standalone.

NOT shipped to the model: it reads ``expected.json`` (the answer key) and scores a
submitted ``chapter.mdx`` against the 15-item ladder. The agent-visible fidelity
validator is ``validate_fixture.py`` (violations only, no answer key) — keep the two
separate.

Scoring (see ``expected.json``):
  xref     -> {0, 0.5, 1}  correct id = 1; valid-but-wrong id within `family` = 0.5; else 0
  cite     -> binary       a <Cite> with `expected_key` (present in references.json) near the anchor
  coderef  -> binary       a <CodeRef> near the anchor with a known path and in-range line/lineEnd
  booklink -> binary       a <BookLink> near the anchor carrying both book= and to=
  census   -> binary       total openings of a tag kind <= max (EXCESS-ONLY: an omission is charged
                           once by its own location item, never twice)

Each item is located by a UNIQUE prose `anchor` (case-insensitive); the graded tag is the first of
its kind within `window` chars after the anchor. Locating by stable prose (not the mutable tag value)
means an honest reformat — quote style, attribute order, whitespace — never flips an item, and a
model that rewrites the anchoring prose fairly fails that item (the conventions forbid it).

Output: one line per item (submitted values repr-sanitized + truncated, so a crafted value cannot
inject a fake summary), then a FINAL summary line:
    CHECK PASSED: <N>/<N> points
    CHECK FAILED: <points>/<N> points, <f> items below full
Exit code = f (items scoring below 1.0). The grader parses only the final line and cross-checks it
against the exit code.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# name="v" | name='v' | name={v}  — word-anchored name, both quote styles, JSX braces.
_ATTR = re.compile(r"\b(\w+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{([^}]*)\})")
# Inert content that must NOT be scored: a tag inside an HTML/MDX comment or a fenced code block
# does not render, so counting it would let a submission that merely comments-out or code-quotes
# the tags score as if it fixed them (a confirmed 15/15 exploit).
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE_BLOCK_RE = re.compile(r"^[ \t]*```.*?^[ \t]*```", re.DOTALL | re.MULTILINE)


def _strip_noise(text: str) -> str:
    """Drop comments and fenced code blocks so only tags that actually render are scored."""
    return _FENCE_BLOCK_RE.sub("", _COMMENT_RE.sub("", text))


def _open_tag(kind: str) -> re.Pattern[str]:
    # An opening tag of `kind`: self-closing (<XRef .../>) or with children (<BookLink ...>).
    return re.compile(r"<" + re.escape(kind) + r"\b([^>]*?)/?>")


def _attrs(blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR.finditer(blob):
        # Strip surrounding quotes so a brace-wrapped string (valid MDX: id={"thm-clt"}) normalizes
        # to thm-clt — otherwise a correct value would be mis-scored 0 for its delimiter style.
        out[m.group(1)] = next(g for g in m.groups()[1:] if g is not None).strip("\"'")
    return out


def _tags(kind: str, text: str) -> list[tuple[int, dict[str, str]]]:
    """All openings of `kind` as (start_index, attr_dict), in document order."""
    return [(m.start(), _attrs(m.group(1))) for m in _open_tag(kind).finditer(text)]


_KIND_TAG = {"xref": "XRef", "cite": "Cite", "coderef": "CodeRef", "booklink": "BookLink"}


def _near(text_lower: str, text: str, anchor: str, kind: str, window: int) -> dict[str, str] | None:
    """First tag of `kind` within `window` chars after the anchor; None if absent OR duplicated.

    A unique anchor is a fixture invariant. A DUPLICATED anchor means the submission added prose
    (a preamble/echo carrying a correct tag) to farm the item without fixing the body — reject it,
    so the exploit scores 0 rather than the planted tag.
    """
    first = text_lower.find(anchor.lower())
    if first < 0 or text_lower.find(anchor.lower(), first + 1) != -1:
        return None
    a_end = first + len(anchor)
    for start, attrs in _tags(_KIND_TAG[kind], text):
        if a_end <= start <= a_end + window:
            return attrs
    return None


def _trunc(v: str | None, n: int = 60) -> str:
    return repr(v if v is None or len(v) <= n else v[:n] + "…")


def _score_item(item, text, text_lower, window, labels, refs, files):
    kind = item["kind"]
    if kind == "census":
        # Excess-only anti-spray, with a floor so an EMPTY/wiped doc cannot farm census
        # credit for free (count 0 <= max). A single omission keeps count >= min, so it is
        # charged once by its own location item, never twice; only a total wipe fails census.
        count = len(_tags(_KIND_TAG[item["tag"]], text))
        lo, hi = item.get("min", 1), item["max"]
        ok = lo <= count <= hi
        return (1.0 if ok else 0.0), f"count={count} range=[{lo},{hi}]"

    attrs = _near(text_lower, text, item["anchor"], kind, window)
    if attrs is None:
        return 0.0, "no tag found at anchor"

    if kind == "xref":
        got = attrs.get("id")
        if got == item["expected_id"]:
            return 1.0, f"id={_trunc(got)}"
        if got in item.get("family", []) and got in labels:
            return 0.5, f"id={_trunc(got)} valid-but-wrong"
        return 0.0, f"id={_trunc(got)}"
    if kind == "cite":
        got = attrs.get("key")
        ok = got == item["expected_key"] and got in refs
        return (1.0 if ok else 0.0), f"key={_trunc(got)}"
    if kind == "coderef":
        path = attrs.get("path")
        want = item.get("expected_path")  # pin the file, else repointing to another valid file = 1.0
        ok = (
            path in files
            and (want is None or path == want)
            and _line_ok(attrs.get("line"), attrs.get("lineEnd"), files.get(path))
        )
        return (1.0 if ok else 0.0), f"path={_trunc(path)} line={_trunc(attrs.get('line'))}"
    if kind == "booklink":
        ok = bool(attrs.get("book")) and bool(attrs.get("to"))
        return (1.0 if ok else 0.0), f"book={_trunc(attrs.get('book'))} to={_trunc(attrs.get('to'))}"
    return 0.0, f"unknown kind {kind!r}"


def _line_ok(line: str | None, line_end: str | None, n: int | None) -> bool:
    if n is None:
        return False
    lo = _as_int(line)
    if lo is None:  # a CodeRef must cite a starting line — deleting it is an evasion, not a fix
        return False
    if not (1 <= lo <= n):
        return False
    hi = _as_int(line_end)
    if hi is not None and not (1 <= hi <= n):
        return False
    if hi is not None and lo > hi:
        return False
    return True


def _as_int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return -1  # a non-numeric line=... is present-but-invalid, not absent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("submission", type=Path)
    ap.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()

    fx = args.fixture
    spec = json.loads((fx / "expected.json").read_text(encoding="utf-8"))
    labels = json.loads((fx / "labels.json").read_text(encoding="utf-8"))
    refs = json.loads((fx / "references.json").read_text(encoding="utf-8"))
    files = json.loads((fx / "files.json").read_text(encoding="utf-8"))
    window = int(spec.get("window", 200))
    items = spec["items"]

    try:
        raw = args.submission.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raw = ""
    text = _strip_noise(raw)  # inert tags (in comments / code fences) must not be scored
    text_lower = text.lower()

    points = 0.0
    below = 0
    for item in items:
        score, reason = _score_item(item, text, text_lower, window, labels, refs, files)
        points += score
        if score < 1.0:
            below += 1
        print(f"item {item['id']} [{item['rung']}]: {score}  ({reason})")

    n = len(items)
    points = round(points, 4)
    if below == 0:
        print(f"CHECK PASSED: {n}/{n} points")
    else:
        print(f"CHECK FAILED: {points}/{n} points, {below} items below full")
    return below


if __name__ == "__main__":
    sys.exit(main())
