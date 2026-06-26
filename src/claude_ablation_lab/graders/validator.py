"""T2 — research-plan generation grader.

Scores a produced ``research_plan.md`` by running ``research_toolkit``'s
standalone ``validators/research_plan.py`` as a subprocess and reading its
result (0 errors → ``1.0``; partial credit by error count). The validator is a
separate repo run out-of-process (zero dependency coupling); we never import it.

Verified validator contract (``research_toolkit/validators/research_plan.py``):
output goes to **stderr**; exit ``0`` pass / ``1`` schema failure / ``2`` usage
error; the failure summary line is ``VALIDATION FAILED: <n> error(s) in <path>``
and each error is a ``  - <message>`` line.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ValidatorGrader", "DEFAULT_TOOLKIT_ROOT", "ERROR_CAP"]

DEFAULT_TOOLKIT_ROOT = Path(os.path.expanduser("~/Claude/research_toolkit"))
_VALIDATOR_RELPATH = "validators/research_plan.py"
_ERROR_COUNT_RE = re.compile(r"VALIDATION FAILED:\s*(\d+)\s+error")
_ERROR_LINE_RE = re.compile(r"^\s*-\s+(.*)$")
# The validator has ~5 distinct structural checks (H1 + 3 required sections +
# bullet-count bounds); the partial-credit slope is normalised against this.
ERROR_CAP = 5
_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class ValidatorGrader:
    """Subprocess the research_plan validator; score by error count."""

    version: str = "t2-research-plan-v1"
    toolkit_root: Path = field(default=DEFAULT_TOOLKIT_ROOT)

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        """Validate ``output`` (the produced ``research_plan.md`` content)."""
        root = Path(gold.get("toolkit_root", self.toolkit_root)).expanduser()
        validator = root / _VALIDATOR_RELPATH
        if not validator.is_file():
            return Score(
                0.0,
                status="grader_error",
                details={"reason": f"validator not found: {validator}"},
            )

        try:
            proc = _run_validator(validator, root, output)
        except (OSError, subprocess.SubprocessError) as exc:
            return Score(0.0, status="grader_error", details={"reason": str(exc)})

        if proc.returncode == 0:
            return Score(1.0, subscores={"errors": 0.0}, details={"returncode": 0})

        # Partial credit ONLY for a genuine schema failure: returncode 1 *and* the
        # expected "VALIDATION FAILED" summary. A crash/traceback (exit 1 with no
        # summary), a usage error (exit 2), or any other code is a grader failure,
        # not low plan quality — never silently convert it into a ~0.8 score.
        n_errors = _count_errors(proc.stderr)
        if proc.returncode == 1 and n_errors is not None:
            return Score(
                value=max(0.0, 1.0 - n_errors / ERROR_CAP),
                subscores={"errors": float(n_errors)},
                details={
                    "returncode": 1,
                    "errors": _error_lines(proc.stderr),
                    "stderr": proc.stderr[:1000],
                },
            )
        return Score(
            0.0,
            status="grader_error",
            details={"returncode": proc.returncode, "stderr": proc.stderr[:1000]},
        )


def _run_validator(validator: Path, root: Path, content: str) -> subprocess.CompletedProcess[str]:
    """Write ``content`` to a temp file and run the validator over it."""
    with tempfile.TemporaryDirectory() as tmp:
        plan = Path(tmp) / "research_plan.md"
        plan.write_text(content, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(validator), str(plan)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )


def _count_errors(stderr: str) -> int | None:
    """Parse the ``VALIDATION FAILED: <n> error(s)`` count, or ``None`` if absent.

    Absence means the validator never emitted its schema-failure summary (e.g. it
    crashed) — the caller treats that as a grader error, not as zero errors.
    """
    match = _ERROR_COUNT_RE.search(stderr)
    return int(match.group(1)) if match else None


def _error_lines(stderr: str) -> list[str]:
    """Extract the individual ``  - <message>`` error lines."""
    return [m.group(1) for m in (_ERROR_LINE_RE.match(ln) for ln in stderr.splitlines()) if m]
