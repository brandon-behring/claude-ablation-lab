"""Books-validate grader — score a corrected MDX chapter against the fixture checklist.

Runs the fixture's grader-only ``check.py`` (never shipped to the model) over the returned/edited
chapter and reads its checklist score. It follows :mod:`claude_ablation_lab.graders.validator`'s
subprocess *shape*, but deliberately does NOT copy its two footguns:

* **exit-0 is not trusted blindly.** ``1.0`` requires the literal ``CHECK PASSED`` summary — a
  ``check.py`` that exits 0 on a bug path cannot silently score every config perfect.
* **the summary is parsed from the FINAL line only, ``^``-anchored, and cross-checked against the
  exit code.** A submission whose tag value is literally ``CHECK PASSED`` cannot inject a verdict
  (``validator.py`` used ``.search()`` anywhere in stderr — the exact hole).

Degenerate, model-controlled outputs (oversize, NUL/binary, unparseable summary from valid input)
score a deterministic ``Score(0.0, status="ok")`` — NOT ``grader_error``, which would *exclude* the
row from quality means (``analyze._LATEST_OK``) and let junk beat an honest 0.0 by vanishing.
``grader_error`` is reserved for genuine grader faults (missing fixture, subprocess crash,
summary/exit-code disagreement).

The grader ``version`` embeds a hash of the *grade-time* rubric (``expected.json`` + ``check.py``):
editing the rubric bumps the version, so stored outputs re-grade for free (the run/grade decoupling)
rather than silently mixing two rubrics in one comparison.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["BooksValidateGrader", "DEFAULT_FIXTURE_ROOT", "extract_chapter"]

#: The packaged fixture (examples/books-validate/), resolved from this module's location.
DEFAULT_FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "examples" / "books-validate"
_RUBRIC_FILES = ("expected.json", "check.py")
_MAX_OUTPUT_BYTES = 1_000_000
_TIMEOUT_S = 60.0
_PASS_RE = re.compile(r"^CHECK PASSED: (\d+)/(\d+) points\s*$")
_FAIL_RE = re.compile(r"^CHECK FAILED: ([\d.]+)/(\d+) points, (\d+) items below full\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def _rubric_version(root: Path) -> str:
    """``books-validate-v1`` plus an 8-hex hash of the rubric files (stable if they are absent).

    Deliberately NOT cached: a review found an ``lru_cache`` here returns a stale version if the
    rubric is edited within a process, defeating the re-grade trigger. The read+hash of two small
    files per call is negligible next to the subprocess it gates.
    """
    h = hashlib.sha256()
    present = False
    for name in _RUBRIC_FILES:
        p = root / name
        if p.is_file():
            present = True
            h.update(p.read_bytes())
    return f"books-validate-v1+fx{h.hexdigest()[:8]}" if present else "books-validate-v1"


@dataclass(frozen=True, slots=True)
class BooksValidateGrader:
    """Subprocess the fixture ``check.py``; score by its checklist points/N."""

    fixture_root: Path = field(default=DEFAULT_FIXTURE_ROOT)

    @property
    def version(self) -> str:
        return _rubric_version(self.fixture_root)

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        root = Path(gold.get("fixture_root", self.fixture_root)).expanduser()
        checker = root / "check.py"
        if not checker.is_file():
            return Score(0.0, status="grader_error", details={"reason": f"no checker: {checker}"})

        # Deterministic model-behaviour failures — a real 0.0, not a grader fault (so they stay in
        # the epoch mean instead of vanishing as grader_error and beating an honest 0.0).
        if len(output) > _MAX_OUTPUT_BYTES:
            return Score(0.0, status="ok", details={"reason": "oversize", "len": len(output)})
        if "\x00" in output:
            return Score(0.0, status="ok", details={"reason": "binary/NUL in output"})

        fence_anchor = _fence_anchor(root)
        chapter = extract_chapter(output, fence_anchor)

        try:
            proc = _run_checker(checker, root, chapter)
        except (OSError, subprocess.SubprocessError) as exc:
            return Score(0.0, status="grader_error", details={"reason": repr(exc)})

        return _score_from_summary(proc.returncode, proc.stdout)


def _run_checker(checker: Path, root: Path, chapter: str) -> subprocess.CompletedProcess[str]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sub = Path(tmp) / "chapter.mdx"
        sub.write_text(chapter, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(checker), str(sub), "--fixture", str(root)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )


def _score_from_summary(returncode: int, stdout: str) -> Score:
    """Parse ONLY the final non-empty stdout line; cross-check its count against the exit code."""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return Score(0.0, status="grader_error", details={"reason": "no checker output"})
    summary = lines[-1]

    passed = _PASS_RE.match(summary)
    if passed:
        pts, n = int(passed.group(1)), int(passed.group(2))
        if returncode != 0 or pts != n:
            return Score(
                0.0,
                status="grader_error",
                details={"reason": "PASS/exit disagree", "summary": summary, "rc": returncode},
            )
        return Score(
            1.0, subscores={"points": float(n), "n": float(n)}, details={"summary": summary}
        )

    failed = _FAIL_RE.match(summary)
    if failed:
        points, total, below = float(failed.group(1)), int(failed.group(2)), int(failed.group(3))
        if returncode != below:
            return Score(
                0.0,
                status="grader_error",
                details={"reason": "count/exit disagree", "summary": summary, "rc": returncode},
            )
        value = max(0.0, min(1.0, points / total)) if total else 0.0
        return Score(
            value,
            subscores={"points": points, "n": float(total), "below": float(below)},
            details={"summary": summary},
        )

    return Score(
        0.0,
        status="grader_error",
        details={"reason": "unparseable summary", "summary": summary[:200]},
    )


def _fence_anchor(root: Path) -> str | None:
    import json

    try:
        spec = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    anchor = spec.get("fence_anchor")
    return str(anchor) if anchor else None


def extract_chapter(output: str, fence_anchor: str | None) -> str:
    """Recover the chapter MDX from a model reply that may wrap it in a code fence.

    Strategy (nesting-safe, line-based — a submission is graded on the corrected chapter, not the
    model's chatter around it): collect fenced blocks; if any contains ``fence_anchor``, grade the
    largest such block (handles prose-then-code and double-printed replies); else if the whole
    payload is a single outer fence, strip just the fence lines; else grade the payload as-is.
    """
    blocks = _fenced_blocks(output)
    if fence_anchor:
        anchored = [b for b in blocks if fence_anchor in b]
        if anchored:
            return max(anchored, key=len)
    lines = output.splitlines()
    nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
    if (
        len(nonempty) >= 2
        and _FENCE_RE.match(lines[nonempty[0]])
        and lines[nonempty[-1]].strip() == "```"
    ):
        return "\n".join(lines[nonempty[0] + 1 : nonempty[-1]])
    return output


def _fenced_blocks(output: str) -> list[str]:
    """Return the content of each ``` … ``` block (outermost pairing, in order)."""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in output.splitlines():
        if _FENCE_RE.match(line):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
        elif current is not None:
            current.append(line)
    return blocks
