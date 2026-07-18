"""Grid spec + cell expansion.

A *grid* is the sweep's cartesian axes — models × efforts × variants × epochs —
plus an ``effort_support`` budget narrowing (e.g. ``max`` effort is Opus-only) on top
of a fixed **provider capability matrix**. A *cell* is one concrete point
``(task, model, effort, variant, epoch)`` the orchestrator will run.

Expansion is pure (only reads YAML): it drops three kinds of invalid combination,
logging each so a silently-missing cell never masquerades as a covered one:

1. **effort inert for a model** (provider capability, ``_EFFORT_CAPABILITY``) — a model
   with no effort parameter (e.g. Haiku 4.5) has every effort resolve to one default
   config, so all but a single canonical cell are dropped with a *warning* (the rest
   would be redundant, provider-identical paid cells — a "config" that isn't one).
2. **effort not in a grid's ``effort_support``** — a per-grid budget narrowing on top
   of the provider floor (an unlisted model supports every effort).
3. **task / variant incompatibility** — an infra-agnostic task (``infra_repo:
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
    "model_effort_inert",
    "parse_variant",
]

logger = logging.getLogger(__name__)

#: Sentinel variant: run in a neutral cwd with no worktree (infra-agnostic tasks).
NONE_VARIANT = "none"

#: Provider effort-capability matrix: does the CLI expose an effort/thinking lever for a
#: model family (matched as a case-insensitive substring of the alias)? A family not
#: listed is assumed effort-capable. **Haiku 4.5 has no effort parameter** (documented:
#: absent from every effort-support list; it uses ``budget_tokens``, not adaptive-thinking
#: effort), so all its effort values resolve to one default config — the grid keeps a
#: single canonical cell rather than running redundant, provider-identical paid cells (a
#: "config" that isn't one). Acceptance of an effort value by the CLI is *not* application
#: of it, so only families documented to have no effort lever belong here.
_EFFORT_CAPABILITY: dict[str, bool] = {
    "haiku": False,
    "sonnet": True,
    "opus": True,
    "fable": True,
}


def model_effort_inert(model: str) -> bool:
    """True iff the provider exposes no effort lever for ``model`` (e.g. Haiku 4.5)."""
    import re

    lowered = model.lower()
    for family, capable in _EFFORT_CAPABILITY.items():
        if re.search(rf"\b{re.escape(family)}\b", lowered):
            return not capable
    return False


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
        ``model -> [allowed efforts]``, a per-grid budget narrowing *on top of* the
        provider capability floor (:func:`model_effort_inert`). A model absent from the
        map supports every effort in ``efforts`` (no per-grid restriction); an
        effort-inert model is collapsed to one :meth:`canonical_effort` regardless.
    """

    models: tuple[str, ...]
    efforts: tuple[str, ...]
    variants: tuple[str, ...]
    epochs: int
    effort_support: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def canonical_effort(self, model: str) -> str | None:
        """The single effort this grid runs for an effort-inert ``model``, or ``None``.

        All efforts resolve to one config, so one representative cell is kept: the first
        listed effort permitted by ``effort_support`` (honouring an author's narrowing).
        Returns ``None`` when the grid's ``effort_support`` explicitly permits *none* of the
        listed efforts — the model is then dropped entirely, never silently run at an effort
        the author excluded (matching how a capable model with no permitted effort is
        dropped). An unlisted model (no ``effort_support`` entry) keeps the first effort.
        """
        allowed = self.effort_support.get(model)
        for effort in self.efforts:
            if allowed is None or effort in allowed:
                return effort
        return None  # a non-None support set disjoint from `efforts` → no canonical cell

    def effort_ok(self, model: str, effort: str) -> bool:
        """True iff this grid should run ``(model, effort)``.

        Two layers, AND-composed: (1) a **provider capability floor** — an effort-inert
        model (:func:`model_effort_inert`, e.g. Haiku 4.5, which has no effort parameter)
        is valid only for the single :meth:`canonical_effort` (and is dropped entirely when
        that is ``None``); and (2) the per-grid ``effort_support`` budget narrowing (a model
        unlisted there supports every effort).
        """
        if model_effort_inert(model):
            canonical = self.canonical_effort(model)
            return canonical is not None and effort == canonical
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
    # Vet variant syntax once up front: a malformed spec entry is dropped-and-logged
    # (matching the effort/compatibility drop semantics) rather than aborting the
    # whole expansion mid-loop via parse_variant's ValueError.
    variants: list[str] = []
    for variant in grid.variants:
        try:
            parse_variant(variant)
        except ValueError as exc:
            logger.warning("drop malformed variant %r: %s", variant, exc)
            continue
        variants.append(variant)

    cells: list[Cell] = []
    for task in tasks:
        for variant in variants:
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
                        if model_effort_inert(model):
                            canonical = grid.canonical_effort(model)
                            if canonical is None:
                                logger.warning(
                                    "drop %s/%s: %s has no effort parameter and this grid's "
                                    "effort_support permits none of its efforts — no cell kept",
                                    model,
                                    effort,
                                    model,
                                )
                            else:
                                logger.warning(
                                    "drop %s/%s: %s has no effort parameter — every effort "
                                    "resolves to one config; kept the single canonical cell %s/%s",
                                    model,
                                    effort,
                                    model,
                                    model,
                                    canonical,
                                )
                        else:
                            logger.info(
                                "drop %s/%s: effort not in grid effort_support", model, effort
                            )
                        continue
                    for epoch in range(grid.epochs):
                        cells.append(Cell(task.id, model, effort, variant, epoch))
    return cells
