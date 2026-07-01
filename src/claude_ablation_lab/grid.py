"""Grid spec + cell expansion.

A *grid* is the sweep's cartesian axes — models × efforts × variants × epochs —
plus an ``effort_support`` validity matrix (e.g. ``max`` effort is Opus-only). A
*cell* is one concrete point ``(task, model, effort, variant, epoch)`` the
orchestrator will run.

Expansion is pure (only reads YAML): it drops two kinds of invalid combination,
logging each so a silently-missing cell never masquerades as a covered one:

1. **effort not supported by a model** (``effort_support``).
2. **task / variant incompatibility** — an infra-agnostic task (``infra_repo:
   null``, e.g. T1/T3) is meaningful only under the ``none`` variant (a neutral
   cwd); an infra-sensitive task (``infra_repo`` set, e.g. T2) needs its project
   config and so runs only under a real ``repo@ref`` worktree variant. Crossing
   them would waste quota or measure nothing, so those cells are dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from claude_ablation_lab.task import Task

__all__ = [
    "Cell",
    "Grid",
    "NONE_VARIANT",
    "load_grid",
    "expand_grid",
    "parse_variant",
]

logger = logging.getLogger(__name__)

#: Sentinel variant: run in a neutral cwd with no worktree (infra-agnostic tasks).
NONE_VARIANT = "none"


@dataclass(frozen=True, slots=True)
class Cell:
    """One concrete sweep point. The first five fields are the run-identity tuple."""

    task_id: str
    model: str
    effort: str
    variant: str
    epoch: int


@dataclass(frozen=True, slots=True)
class Grid:
    """The sweep axes plus the (model, effort) validity matrix.

    Parameters
    ----------
    models, efforts, variants:
        The cartesian axes. ``variants`` entries are ``"none"`` (neutral cwd) or
        ``"<repo>@<ref>"`` strings (materialized as worktrees by the orchestrator).
    epochs:
        Repeats per cell (the run-variance axis; not a within-cell CI). ``>= 1``.
    effort_support:
        ``model -> [allowed efforts]``. A model absent from the map supports every
        effort in ``efforts`` (no restriction).
    """

    models: tuple[str, ...]
    efforts: tuple[str, ...]
    variants: tuple[str, ...]
    epochs: int
    effort_support: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def effort_ok(self, model: str, effort: str) -> bool:
        """True iff ``model`` supports ``effort`` (unlisted model → all efforts)."""
        allowed = self.effort_support.get(model)
        return True if allowed is None else effort in allowed


def parse_variant(variant: str) -> tuple[str, str] | None:
    """Split a ``"<repo>@<ref>"`` variant into ``(repo, ref)``; ``None`` for ``none``.

    ``ref`` may itself contain ``@`` (rare), so only the *first* ``@`` splits.
    """
    if variant == NONE_VARIANT:
        return None
    repo, sep, ref = variant.partition("@")
    if not sep or not repo or not ref:
        raise ValueError(f"variant {variant!r} is not 'none' or '<repo>@<ref>'")
    return repo, ref


def load_grid(path: Path | str) -> Grid:
    """Load and validate a grid spec from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"grid spec {path} is not a mapping")
    for key in ("models", "efforts"):
        if not raw.get(key):
            raise ValueError(f"grid spec {path} missing non-empty {key!r}")
    epochs = int(raw.get("epochs", 1))
    if epochs < 1:
        raise ValueError(f"grid spec {path}: epochs must be >= 1, got {epochs}")
    support_raw = raw.get("effort_support") or {}
    effort_support = {
        str(model): tuple(str(e) for e in efforts) for model, efforts in support_raw.items()
    }
    return Grid(
        models=tuple(str(m) for m in raw["models"]),
        efforts=tuple(str(e) for e in raw["efforts"]),
        variants=tuple(str(v) for v in (raw.get("variants") or [NONE_VARIANT])),
        epochs=epochs,
        effort_support=effort_support,
    )


def _compatible(task: Task, variant: str) -> bool:
    """True iff a task may run under a variant.

    Infra-agnostic tasks (``infra_repo is None``) run only under ``none``. An
    infra-sensitive task runs only under a variant whose **repo matches its own
    ``infra_repo``**, so a grid listing several infra repos never runs a task under an
    unrelated one (that would load the wrong project's config and measure nothing).
    """
    if task.infra_repo is None:
        return variant == NONE_VARIANT
    parsed = parse_variant(variant)  # None for the ``none`` variant
    return parsed is not None and parsed[0] == task.infra_repo


def expand_grid(grid: Grid, tasks: list[Task]) -> list[Cell]:
    """Expand the grid into the ordered list of valid cells (invalid combos logged).

    Iteration order is ``(task, variant, model, effort, epoch)`` — deterministic so
    a resumed sweep visits cells in the same sequence and the ledger is diff-stable.
    """
    cells: list[Cell] = []
    for task in tasks:
        for variant in grid.variants:
            if not _compatible(task, variant):
                logger.info(
                    "drop %s × %s: infra_repo=%s incompatible with variant",
                    task.id,
                    variant,
                    task.infra_repo,
                )
                continue
            for model in grid.models:
                for effort in grid.efforts:
                    if not grid.effort_ok(model, effort):
                        logger.info("drop %s: effort %r unsupported", model, effort)
                        continue
                    for epoch in range(grid.epochs):
                        cells.append(Cell(task.id, model, effort, variant, epoch))
    return cells
