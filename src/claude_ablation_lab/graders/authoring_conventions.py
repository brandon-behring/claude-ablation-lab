"""Authoring-conventions grader — a SECONDARY, deterministic conventions check for the
t9 open-ended authoring tasks. It is **not** the phase's quality instrument: quality is
measured by the pairwise LLM judge (see :mod:`claude_ablation_lab.judge`). This grader
exists so contestant ledger rows carry an honest, cheap ``value`` (did the output even
speak the house style?) and so empty/degenerate outputs surface as ``unparseable``
before any judge call is spent on them.

Checks are per corpus family (``gold.family``):

- ``latex_guide`` — the interview-prep / guides-manning LaTeX voice: ``\\los{...}``
  learning outcomes (>= ``gold.min_los``), ``\\companytags``, a margin/interview
  apparatus, a biblatex citation, LaTeX sectioning, and no markdown fences.
- ``astro_book`` — the book-scaffold MDX voice: an MDX heading, every component named
  in ``gold.required_components`` (task-specific, e.g. ``<NoteBox``), and no LaTeX
  environments.

``value`` = fraction of checks passed; each check is a 0/1 subscore so a miss is
inspectable. A family this module does not know is a ``grader_error`` (a spec bug,
never a silent 0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.grade import Score

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["AuthoringConventionsGrader", "FAMILIES"]

FAMILIES = ("latex_guide", "astro_book")

#: ``\los{ID}{bloom}{statement}`` learning-outcome macro (count is a subscore).
_LOS_RE = re.compile(r"\\los\{")
#: Any biblatex citation form used across the corpora.
_CITE_RE = re.compile(r"\\(?:text|paren|auto|foot)?cite[st]?\{")
#: LaTeX sectioning at any level (a "section" with no heading is not a section).
_SECTION_RE = re.compile(r"\\(?:chapter|section|subsection|subsubsection)\{")
#: An MDX/markdown heading line.
_MDX_HEADING_RE = re.compile(r"(?m)^#{1,4} \S")
#: A LaTeX environment — wrong voice inside an MDX chapter.
_LATEX_ENV_RE = re.compile(r"\\begin\{")


@dataclass(frozen=True, slots=True)
class AuthoringConventionsGrader:
    """Fraction of family conventions the output satisfies (0..1). Secondary metric."""

    @property
    def version(self) -> str:
        return "authoring-conv-v1"

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score:
        if not output.strip():
            return Score(0.0, status="unparseable", details={"reason": "empty output"})

        family = gold.get("family")
        if family == "latex_guide":
            checks = _latex_guide_checks(output, gold)
        elif family == "astro_book":
            checks = _astro_book_checks(output, gold)
        else:
            return Score(
                0.0,
                status="grader_error",
                details={"reason": f"unknown corpus family: {family!r} (known: {FAMILIES})"},
            )

        passed = sum(checks.values())
        return Score(
            value=passed / len(checks),
            subscores={name: float(ok) for name, ok in checks.items()},
            details={"missed": sorted(name for name, ok in checks.items() if not ok)},
        )


def _latex_guide_checks(output: str, gold: Mapping[str, Any]) -> dict[str, bool]:
    """The interview-prep / guide-fleet LaTeX conventions."""
    min_los = int(gold.get("min_los", 1))
    return {
        "los": len(_LOS_RE.findall(output)) >= min_los,
        "companytags": "\\companytags" in output,
        "margin_or_interview": ("\\marginnote" in output or "\\begin{interviewcontext}" in output),
        "citation": bool(_CITE_RE.search(output)),
        "sectioning": bool(_SECTION_RE.search(output)),
        "no_md_fences": "```" not in output,
    }


def _astro_book_checks(output: str, gold: Mapping[str, Any]) -> dict[str, bool]:
    """The book-scaffold MDX conventions; components are task-specific via gold."""
    components = gold.get("required_components", ())
    checks = {
        "mdx_heading": bool(_MDX_HEADING_RE.search(output)),
        "no_latex_env": not _LATEX_ENV_RE.search(output),
    }
    for component in components:
        checks[f"component:{component}"] = str(component) in output
    return checks
