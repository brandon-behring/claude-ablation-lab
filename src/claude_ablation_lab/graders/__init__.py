"""Grader implementations + a lazy registry.

:func:`get_grader` resolves a task's ``grader`` ref to an instance, importing the
concrete module *on demand*. This keeps ``import claude_ablation_lab.graders``
free of the heavy/optional ``eval_toolkit`` dependency: it is pulled in only when
the classification grader is actually requested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_ablation_lab.grade import Grader

__all__ = ["get_grader", "GRADER_NAMES"]

GRADER_NAMES = ("classification", "validator", "anchor")


def get_grader(name: str) -> Grader:
    """Return a grader instance by name (``classification`` / ``validator`` / ``anchor``)."""
    if name == "anchor":
        from claude_ablation_lab.graders.anchor import AnchorGrader

        return AnchorGrader()
    if name == "validator":
        from claude_ablation_lab.graders.validator import ValidatorGrader

        return ValidatorGrader()
    if name == "classification":
        from claude_ablation_lab.graders.classification import ClassificationGrader

        return ClassificationGrader()
    raise ValueError(f"unknown grader: {name!r} (known: {', '.join(GRADER_NAMES)})")
