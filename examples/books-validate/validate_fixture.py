#!/usr/bin/env python3
"""Fidelity validator for the books-validate fixture. STDLIB-ONLY, standalone.

This is the AGENT-VISIBLE tool — a faithful, self-contained subset of book-scaffold's
``validate.mjs`` (verified against ``~/book-scaffold-astro/package/scripts/validate.mjs``).
It reports *structural* violations only and knows NO answer key:

  * <XRef id="…">   id must exist in labels.json
  * <Cite key="…">  key must exist in references.json
  * <CodeRef path=… line={N} lineEnd={M}>  path in files.json and line/lineEnd within its length
  * <BookLink …>    must carry both book= and to=

Exactly like the real tool, it CANNOT see semantic errors — a valid-but-wrong XRef id, or a
prose citation missing its <Cite> tag, passes here. Closing that gap is what separates a model
that merely makes the validator green from one that understands the chapter. Run it as you edit:

    python3 validate_fixture.py chapter.mdx

Prints one line per violation, then a summary; exit code = number of violations (0 = clean),
mirroring validate.mjs's ``process.exit(errors.length)``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Ported verbatim from validate.mjs (order-sensitive on purpose — fidelity is the point).
RE_CITE = re.compile(r"<Cite[^>]+key=[\"']([^\"']+)[\"']")
RE_XREF = re.compile(r"<XRef[^>]+id=[\"']([^\"']+)[\"']")
RE_CODEREF = re.compile(
    r"<CodeRef[^>]+path=[\"']([^\"']+)[\"'](?:[^>]*line=\{(\d+)\})?(?:[^>]*lineEnd=\{(\d+)\})?"
)
RE_BOOKLINK = re.compile(r"<BookLink\b([^>]*)>")


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


# A tag inside a comment or a fenced code block is inert; the grader (check.py) ignores it, so this
# validator does too — otherwise a commented-out tag would read as "clean" here yet score 0 there.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE_BLOCK_RE = re.compile(r"^[ \t]*```.*?^[ \t]*```", re.DOTALL | re.MULTILINE)


def main() -> int:
    fx = Path(__file__).resolve().parent
    labels = json.loads((fx / "labels.json").read_text(encoding="utf-8"))
    refs = json.loads((fx / "references.json").read_text(encoding="utf-8"))
    files = json.loads((fx / "files.json").read_text(encoding="utf-8"))

    if len(sys.argv) != 2:
        print("usage: validate_fixture.py <chapter.mdx>", file=sys.stderr)
        return 2
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    text = _FENCE_BLOCK_RE.sub("", _COMMENT_RE.sub("", text))

    violations: list[str] = []

    def fail(idx: int, msg: str) -> None:
        violations.append(f"chapter.mdx:{_line_of(text, idx)}: {msg}")

    for m in RE_CITE.finditer(text):
        if m.group(1) not in refs:
            fail(m.start(), f'Unknown bibkey "{m.group(1)}" — not in references.json')
    for m in RE_XREF.finditer(text):
        if m.group(1) not in labels:
            fail(m.start(), f'Unknown XRef id "{m.group(1)}" — not in labels.json')
    for m in RE_CODEREF.finditer(text):
        path, lo, hi = m.group(1), m.group(2), m.group(3)
        if path not in files:
            fail(m.start(), f'CodeRef path "{path}" not in files.json')
            continue
        n = files[path]
        for bound in (lo, hi):
            if bound is not None and int(bound) > n:
                fail(m.start(), f'CodeRef line {lo}-{hi} exceeds file length ({n}) in "{path}"')
                break
    for m in RE_BOOKLINK.finditer(text):
        attrs = m.group(1)
        if not re.search(r'\bbook=["\']', attrs) or not re.search(r'\bto=["\']', attrs):
            fail(m.start(), "<BookLink> requires both book=\"…\" and to=\"…\".")

    for v in violations:
        print(f"✗ {v}")
    if violations:
        print(f"\nVALIDATION FAILED: {len(violations)} error(s)")
    else:
        print("✓ validate: no errors")
    return len(violations)


if __name__ == "__main__":
    sys.exit(main())
