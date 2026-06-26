"""Turn a static :class:`~claude_ablation_lab.task.Task` into a runnable cell.

A :class:`Prepared` bundles everything the orchestrator needs to *run* a cell and
then *grade* it: the live ``prompt``, the ``gold`` the grader expects, an optional
``json_schema`` (structured output), an optional ``artifact`` (a file the agentic
run produces, captured as the gradeable output instead of stdout), and an optional
``permission_mode`` (agentic tasks need a non-interactive mode).

Preparation dispatches on ``task.grader`` — in v1 the grader and the task type are
1:1 (``classification``→T1, ``validator``→T2, ``anchor``→T3). T1 is built *live*
and *deterministically* (seeded subsample of the holdout parquet) so a later
re-grade reproduces the identical gold without storing it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_ablation_lab.task import Task

__all__ = ["Prepared", "prepare_task", "DEFAULT_ARTIFACT", "DEFAULT_AGENT_PERMISSION_MODE"]

#: Where an agentic task is expected to write its artifact (overridable per task).
DEFAULT_ARTIFACT = "research_plan.md"
#: Non-interactive permission mode for agentic tasks (so file writes don't block).
DEFAULT_AGENT_PERMISSION_MODE = "acceptEdits"


@dataclass(frozen=True, slots=True)
class Prepared:
    """A task made concrete for one run+grade cycle."""

    prompt: str
    gold: Mapping[str, Any] = field(default_factory=dict)
    json_schema: dict[str, Any] | None = None
    artifact: str | None = None
    permission_mode: str | None = None


def _prepare_classification(task: Task) -> Prepared:
    """T1: seeded balanced subsample → batched prompt + verdict schema + idx→label gold."""
    from claude_ablation_lab import t1_dataset

    params = task.params
    raw_path = params.get("gold_parquet")
    path = Path(os.path.expanduser(str(raw_path))) if raw_path else t1_dataset.DEFAULT_HOLDOUT_PATH
    frame = t1_dataset.subsample(
        t1_dataset.load_holdout(path),
        n=int(params.get("subsample_n", 60)),
        seed=int(params.get("seed", 42)),
    )
    return Prepared(
        prompt=t1_dataset.build_prompt(frame),
        gold={"labels": t1_dataset.build_gold(frame)},
        json_schema=t1_dataset.VERDICT_JSON_SCHEMA,
    )


def _prepare_validator(task: Task) -> Prepared:
    """T2: static ``/research-plan`` prompt; grade the captured ``research_plan.md``."""
    return Prepared(
        prompt=task.prompt,
        gold=task.gold,
        artifact=str(task.params.get("artifact", DEFAULT_ARTIFACT)),
        permission_mode=str(task.params.get("permission_mode", DEFAULT_AGENT_PERMISSION_MODE)),
    )


def _prepare_anchor(task: Task) -> Prepared:
    """T3: fully static — the loaded task already carries prompt + gold."""
    return Prepared(prompt=task.prompt, gold=task.gold)


_PREPARERS = {
    "classification": _prepare_classification,
    "validator": _prepare_validator,
    "anchor": _prepare_anchor,
}


def prepare_task(task: Task) -> Prepared:
    """Build the :class:`Prepared` cell for ``task`` (dispatch on its grader)."""
    try:
        preparer = _PREPARERS[task.grader]
    except KeyError:
        raise ValueError(
            f"no preparer for grader {task.grader!r} (known: {', '.join(_PREPARERS)})"
        ) from None
    return preparer(task)
