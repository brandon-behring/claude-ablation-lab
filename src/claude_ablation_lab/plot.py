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

from claude_ablation_lab.analyze import x_value  # noqa: E402

if TYPE_CHECKING:
    from matplotlib.figure import Figure

    from claude_ablation_lab.analyze import CompareRow, ReportCell

__all__ = ["pareto_scatter", "effort_curves", "ab_forest", "render_all"]

#: Deterministic effort ordering on the x-axis (unknown efforts sort last).
_EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}
#: Distinct markers per effort (cycled if a grid uses more effort levels).
_EFFORT_MARKERS = ["o", "s", "^", "D", "v", "P"]

#: Plot-only metadata per Pareto axis: (ci-low attr, ci-high attr, x label). The x
#: positions and figure membership come from ``analyze.x_value`` — the same predicate
#: that computes the ``pareto`` flag, so the frontier and the picture can never
#: disagree on which cells compete (PR-wide review). Keys must mirror
#: ``analyze.X_AXES``; a drift-canary test asserts the keysets match.
_X_AXIS_SPEC = {
    "cost": ("cost_ci_low", "cost_ci_high", "mean cost ($ / cell)"),
    "latency": ("latency_ci_low", "latency_ci_high", "mean latency (s / cell)"),
    "tokens": ("tokens_ci_low", "tokens_ci_high", "mean output tokens / cell"),
    "throughput": ("total_tokens_ci_low", "total_tokens_ci_high", "mean total tokens / cell"),
}
#: use a log x-scale when the (positive) x values span at least this ratio — wide
#: cost ranges (haiku/low → opus/max is routinely >10×) squash to a left-edge blob
#: on a linear axis (the Artificial-Analysis / compute-frontier convention).
_LOG_X_RATIO = 10.0


def _effort_rank(effort: str) -> tuple[int, str]:
    """Sort key: known efforts in order, unknown ones alphabetical after (deterministic)."""
    return (_EFFORT_ORDER.get(effort, len(_EFFORT_ORDER)), effort)


def _slug(name: str) -> str:
    """Filename-safe task id — path separators and shell-noise never reach savefig."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "task"


def pareto_scatter(cells: list[ReportCell], *, task: str, x_axis: str = "cost") -> Figure:
    """Quality-vs-x scatter for one task: colour = model, marker = effort, CI bars.

    ``x_axis`` selects the cost dimension (``cost`` USD / ``latency`` seconds /
    ``tokens`` output tokens) and must match the axis ``report(x_axis=...)`` marked
    ``pareto`` against — the frontier flag is axis-specific. Pareto-frontier cells
    get a filled marker and a dashed **staircase** frontier (the achievable-quality
    envelope: between frontier points, the best attainable quality is the previous
    point's); leaky cells (``cell.leakage``) get a red ring. Error bars are drawn on
    both axes where intervals exist. Cells without a usable x (``analyze.x_value``
    returns ``None``: unmeasured, NaN, or partial token coverage) are dropped from
    the figure and counted in the title — the same predicate that decides frontier
    membership, so figure and flag can never disagree.
    One error-bar container is emitted per plotted cell, so a test can assert
    ``len(ax.containers) == len(plotted_cells)``. A positive x-range wider than
    ``_LOG_X_RATIO`` switches to a log x-scale.
    """
    if x_axis not in _X_AXIS_SPEC:
        raise ValueError(f"x_axis must be one of {sorted(_X_AXIS_SPEC)} (got {x_axis!r})")
    xlo_attr, xhi_attr, x_label = _X_AXIS_SPEC[x_axis]
    all_task_cells = [c for c in cells if c.task_id == task]
    task_cells: list[ReportCell] = []
    xs: list[float] = []
    for c in all_task_cells:
        x_pos = x_value(c, x_axis)
        if x_pos is not None:
            task_cells.append(c)
            xs.append(x_pos)
    n_unmeasured = len(all_task_cells) - len(task_cells)
    fig, ax = plt.subplots(figsize=(7, 5))
    if not task_cells:
        ax.set_title(
            f"{task}: no cells with a measured {x_axis} axis"
            if all_task_cells
            else f"{task}: no cells"
        )
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

    for c, x in zip(task_cells, xs, strict=True):
        yerr = None
        if c.ci_low is not None and c.ci_high is not None:
            yerr = [[c.mean_value - c.ci_low], [c.ci_high - c.mean_value]]
        xerr = None
        x_lo, x_hi = getattr(c, xlo_attr), getattr(c, xhi_attr)
        if x_lo is not None and x_hi is not None:
            # Clamped: matplotlib raises on a negative bar, and a percentile-bootstrap
            # endpoint landing past the mean must degrade to a zero-length bar, not a crash.
            xerr = [[max(0.0, x - x_lo)], [max(0.0, x_hi - x)]]
        key = (c.model, c.variant)
        ax.errorbar(
            x,
            c.mean_value,
            yerr=yerr,
            xerr=xerr,
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
                [x],
                [c.mean_value],
                s=260,
                facecolors="none",
                edgecolors="red",
                linewidths=1.6,
                zorder=4,
            )

    # Staircase, not point-to-point: between frontier points the best *achievable*
    # quality is the previous (cheaper) point's, so the envelope holds flat then
    # steps up at each frontier cell (the leaderboard-scatter convention).
    frontier = sorted(
        ((x, c) for c, x in zip(task_cells, xs, strict=True) if c.pareto), key=lambda t: t[0]
    )
    if len(frontier) >= 2:
        ax.step(
            [x for x, _ in frontier],
            [c.mean_value for _, c in frontier],
            where="post",
            linestyle="--",
            color="black",
            linewidth=1,
            alpha=0.5,
            zorder=2,
        )

    if min(xs) > 0 and max(xs) / min(xs) >= _LOG_X_RATIO:
        ax.set_xscale("log")

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
    ax.set_xlabel(x_label)
    ax.set_ylabel("mean quality")
    dropped = f" · {n_unmeasured} cell(s) lack {x_axis} data" if n_unmeasured else ""
    ax.set_title(f"{task}: quality vs {x_axis}  (filled = Pareto · red ring = leakage){dropped}")
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
    x_axis: str = "cost",
) -> list[Path]:
    """Write a Pareto + effort figure per task and (if any) one A/B forest to ``out_dir``.

    ``x_axis`` (must match the axis ``cells`` were Pareto-marked against) selects the
    Pareto figure's cost dimension. The default USD axis keeps its historical
    ``<task>_pareto.<fmt>`` filename; other axes suffix it (``<task>_pareto_latency``)
    so regenerating a different view never silently overwrites the cost figure.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pareto_name = "pareto" if x_axis == "cost" else f"pareto_{x_axis}"
    written: list[Path] = []
    for task in sorted({c.task_id for c in cells}):
        for name, fig in (
            (pareto_name, pareto_scatter(cells, task=task, x_axis=x_axis)),
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
