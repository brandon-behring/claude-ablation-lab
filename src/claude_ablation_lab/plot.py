"""Static figures for a sweep (Phase 6): consume ``analyze`` outputs, never re-aggregate.

The plot layer is deliberately thin — every figure is built from the frozen
:class:`~claude_ablation_lab.analyze.ReportCell` / :class:`~claude_ablation_lab.analyze.CompareRow`
dataclasses that ``report``/``compare`` return, so a figure can never disagree with the
table: all the statistical honesty (latest-grade dedupe, across-epoch bootstrap CIs,
Pareto marking, leakage flag) lives in ``analyze``. Builders **return** a ``Figure`` and do
no file I/O, so they are unit-testable headlessly; :func:`render_all` is the only writer.

The ``Agg`` backend is forced at import so this works on a headless box / in CI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")  # headless: select before pyplot is imported

import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)
from matplotlib.lines import Line2D  # noqa: E402

if TYPE_CHECKING:
    from matplotlib.figure import Figure

    from claude_ablation_lab.analyze import CompareRow, ReportCell

__all__ = ["pareto_scatter", "effort_curves", "ab_forest", "render_all"]

#: Deterministic effort ordering on the x-axis (unknown efforts sort last).
_EFFORT_ORDER = {"low": 0, "high": 1, "max": 2}
#: Distinct markers per effort (cycled if a grid uses more effort levels).
_EFFORT_MARKERS = ["o", "s", "^", "D", "v", "P"]


def _effort_rank(effort: str) -> tuple[int, str]:
    """Sort key: known efforts in order, unknown ones alphabetical after (deterministic)."""
    return (_EFFORT_ORDER.get(effort, len(_EFFORT_ORDER)), effort)


def _slug(name: str) -> str:
    """Filename-safe task id — path separators and shell-noise never reach savefig."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "task"


def pareto_scatter(cells: list[ReportCell], *, task: str) -> Figure:
    """Quality-vs-cost scatter for one task: colour = model, marker = effort, CI y-bars.

    Pareto-frontier cells (``cell.pareto``) get a filled marker and a dashed frontier
    line; leaky cells (``cell.leakage``) get a red ring. One error-bar container is
    emitted per cell, so a test can assert ``len(ax.containers) == len(task_cells)``.
    """
    task_cells = [c for c in cells if c.task_id == task]
    fig, ax = plt.subplots(figsize=(7, 5))
    if not task_cells:
        ax.set_title(f"{task}: no cells")
        return fig
    # One colour per (model, variant) series — a multi-variant ledger (the A/B showcase)
    # must not render two variants of one model indistinguishably (review consensus).
    variants = {c.variant for c in task_cells}
    series = sorted({(c.model, c.variant) for c in task_cells})
    efforts = sorted({c.effort for c in task_cells}, key=_effort_rank)
    cmap = plt.get_cmap("tab10")
    color_of = {s: cmap(i % 10) for i, s in enumerate(series)}
    label_of = {s: s[0] if len(variants) == 1 else f"{s[0]} @ {s[1]}" for s in series}
    marker_of = {e: _EFFORT_MARKERS[i % len(_EFFORT_MARKERS)] for i, e in enumerate(efforts)}

    for c in task_cells:
        yerr = None
        if c.ci_low is not None and c.ci_high is not None:
            yerr = [[c.mean_value - c.ci_low], [c.ci_high - c.mean_value]]
        key = (c.model, c.variant)
        ax.errorbar(
            c.mean_cost,
            c.mean_value,
            yerr=yerr,
            marker=marker_of[c.effort],
            markersize=11 if c.pareto else 7,
            markerfacecolor=color_of[key] if c.pareto else "white",
            markeredgecolor=color_of[key],
            color=color_of[key],
            ecolor="gray",
            elinewidth=1,
            capsize=3,
            linestyle="none",
            zorder=3,
        )
        if c.leakage:
            ax.scatter(
                [c.mean_cost],
                [c.mean_value],
                s=260,
                facecolors="none",
                edgecolors="red",
                linewidths=1.6,
                zorder=4,
            )

    frontier = sorted((c for c in task_cells if c.pareto), key=lambda c: c.mean_cost)
    if len(frontier) >= 2:
        ax.plot(
            [c.mean_cost for c in frontier],
            [c.mean_value for c in frontier],
            linestyle="--",
            color="black",
            linewidth=1,
            alpha=0.5,
            zorder=2,
        )

    series_handles = [
        Line2D([], [], marker="o", linestyle="none", color=color_of[s], label=label_of[s])
        for s in series
    ]
    effort_handles = [
        Line2D([], [], marker=marker_of[e], linestyle="none", color="gray", label=e)
        for e in efforts
    ]
    series_title = "model" if len(variants) == 1 else "model @ variant"
    ax.add_artist(
        ax.legend(handles=series_handles, title=series_title, loc="lower right", fontsize=8)
    )
    ax.legend(handles=effort_handles, title="effort", loc="upper left", fontsize=8)
    ax.set_xlabel("mean cost ($ / cell)")
    ax.set_ylabel("mean quality")
    ax.set_title(f"{task}: quality vs cost  (filled = Pareto · red ring = leakage)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def effort_curves(cells: list[ReportCell], *, task: str) -> Figure:
    """Quality-vs-effort curves for one task: one line per (model, variant); CI band if present."""
    task_cells = [c for c in cells if c.task_id == task]
    fig, ax = plt.subplots(figsize=(7, 5))
    if not task_cells:
        ax.set_title(f"{task}: no cells")
        return fig
    efforts = sorted({c.effort for c in task_cells}, key=_effort_rank)
    x_of = {e: i for i, e in enumerate(efforts)}
    variants = {c.variant for c in task_cells}
    series = sorted({(c.model, c.variant) for c in task_cells})
    cmap = plt.get_cmap("tab10")

    for i, (model, variant) in enumerate(series):
        # A curve must not span variants: cells for one model under different variants
        # are distinct configurations, not points on a single effort trend.
        scells = sorted(
            (c for c in task_cells if c.model == model and c.variant == variant),
            key=lambda c: _effort_rank(c.effort),
        )
        xs = [x_of[c.effort] for c in scells]
        ys = [c.mean_value for c in scells]
        color = cmap(i % 10)
        label = model if len(variants) == 1 else f"{model} @ {variant}"
        ax.plot(xs, ys, marker="o", color=color, label=label)
        band_lo = [c.ci_low for c in scells if c.ci_low is not None]
        band_hi = [c.ci_high for c in scells if c.ci_high is not None]
        if len(band_lo) == len(xs) == len(band_hi):  # CI band only if every point has one
            ax.fill_between(xs, band_lo, band_hi, color=color, alpha=0.15)

    ax.set_xticks(range(len(efforts)))
    ax.set_xticklabels(efforts)
    ax.set_xlabel("thinking effort")
    ax.set_ylabel("mean quality")
    ax.set_title(f"{task}: quality vs effort")
    ax.legend(title="model" if len(variants) == 1 else "model @ variant", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def ab_forest(rows: list[CompareRow], *, a: str, b: str) -> Figure:
    """Forest plot of per-task A→B deltas with paired-bootstrap CIs; a line at 0."""
    fig, ax = plt.subplots(figsize=(7, max(2.0, 0.7 * len(rows) + 1)))
    ax.axvline(0, color="black", linestyle="--", linewidth=1, alpha=0.6)  # no-effect line
    if not rows:
        ax.set_title("compare: no overlapping tasks")
        return fig
    for y, r in enumerate(rows):
        color = "green" if r.real else "gray"
        xerr = None
        if r.ci_low is not None and r.ci_high is not None:
            xerr = [[r.delta - r.ci_low], [r.ci_high - r.delta]]
        ax.errorbar(
            r.delta,
            y,
            xerr=xerr,
            marker="o",
            color=color,
            ecolor=color,
            capsize=4,
            linestyle="none",
        )
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r.task_id for r in rows])
    ax.invert_yaxis()  # first task at the top, matching reading order
    ax.set_xlabel(f"Δ mean quality   (B = {b})  −  (A = {a})")
    ax.set_title("is the difference real?   (green = 95% CI excludes 0)")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    return fig


def render_all(
    cells: list[ReportCell],
    compare_rows: list[CompareRow],
    out_dir: Path | str,
    *,
    fmt: str = "png",
    a: str = "A",
    b: str = "B",
) -> list[Path]:
    """Write a Pareto + effort figure per task and (if any) one A/B forest to ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for task in sorted({c.task_id for c in cells}):
        for name, fig in (
            ("pareto", pareto_scatter(cells, task=task)),
            ("effort", effort_curves(cells, task=task)),
        ):
            path = out / f"{_slug(task)}_{name}.{fmt}"
            fig.savefig(path, dpi=120, bbox_inches="tight")
            plt.close(fig)
            written.append(path)
    if compare_rows:
        path = out / f"compare_forest.{fmt}"
        fig = ab_forest(compare_rows, a=a, b=b)
        fig.savefig(path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        written.append(path)
    return written
