"""Grader protocol, Score, and the run/grade decoupling seam.

Graders are pure, cheap, re-runnable passes over a *stored* ``RunResult.output``
— they never call Claude. Each grader carries a ``version`` string that becomes
the ledger's ``grader_version`` key (Phase 3), so fixing a buggy grader re-scores
stored transcripts without re-running the model.

``GradeStatus`` separates a *legitimately-low score* (``ok`` with a low ``value``)
from a grader that *could not run* (``grader_error``) or *could not parse* the
model output (``unparseable``) — the grading analog of the runner's
infra-vs-model status split (see :mod:`claude_ablation_lab.runner`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from claude_ablation_lab.runner import RunResult

__all__ = ["GradeStatus", "Score", "Grader", "grade_run"]

GradeStatus = Literal["ok", "unparseable", "grader_error"]


@dataclass(frozen=True, slots=True)
class Score:
    """The outcome of grading one stored run.

    Parameters
    ----------
    value:
        Primary quality in ``[0, 1]`` (higher is better). ``0.0`` when
        ``status`` is not ``ok``.
    subscores:
        Named secondary metrics (e.g. ``f1``, ``ci_low``) for analysis.
    details:
        Free-form diagnostics (parse misses, validator stderr, …) — never
        aggregated, always inspectable.
    status:
        ``ok`` (a real score), ``unparseable`` (model output could not be
        parsed), or ``grader_error`` (the grader itself could not run).
    """

    value: float
    subscores: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    status: GradeStatus = "ok"


@runtime_checkable
class Grader(Protocol):
    """A pure scorer over stored model output.

    Implementations expose a ``version`` string (→ ledger ``grader_version``)
    and a single :meth:`grade` method taking the stored ``output`` text and a
    task-specific ``gold`` mapping. ``version`` is declared read-only so that a
    plain attribute *or* a frozen-dataclass field satisfies the protocol.
    """

    @property
    def version(self) -> str: ...

    def grade(self, *, output: str, gold: Mapping[str, Any]) -> Score: ...


def grade_run(grader: Grader, run_result: RunResult, gold: Mapping[str, Any]) -> Score:
    """Grade a stored :class:`~claude_ablation_lab.runner.RunResult`.

    A run whose status is not ``ok`` (infra_error / timeout / rate_limited /
    parse_fail) is short-circuited to ``Score(0.0, status="grader_error")`` so an
    infrastructure failure is never silently counted as a quality-0 *model*
    result — it is excluded from quality aggregation downstream (Phase 4).
    """
    if run_result.status != "ok":
        return Score(value=0.0, status="grader_error", details={"run_status": run_result.status})
    return grader.grade(output=run_result.output, gold=gold)
