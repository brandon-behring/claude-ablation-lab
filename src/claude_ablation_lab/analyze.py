"""Analysis over the JSONL ledger (Phase 4): ``report`` and ``compare``.

DuckDB reads the ledger directly (scalar columns are native; grader ``subscores``
are JSON strings, unpacked with ``json_extract`` only where a metric is needed).

Statistical honesty (the talk's failure-mode #2 and the independent review's
"stats honesty" delta):

- A cell's headline number is the **mean over epochs**; epochs are a small
  *run-variance* axis, so v1 is labelled **exploratory** (3 epochs ≠ a population).
- The **within-cell** bootstrap CI (``ci_low``/``ci_high`` from the T1 grader, a
  bootstrap over the cell's ~60 examples) is the statistically meaningful interval
  and is surfaced verbatim — *not* conflated with epoch spread.
- The **shuffled-label leakage gate** is enforced here (not per-cell, which is too
  noisy): if a classification cell's mean shuffled-label AUROC strays from 0.5 by
  more than :data:`LEAKAGE_BAND`, the row is flagged — a high score on shuffled
  labels means the harness/grader is leaking, so trust nothing until it is fixed.
- ``compare`` reports a variant A/B delta with a **paired bootstrap** CI over the
  matched (model, effort) configs; "real" means the CI excludes 0. With few
  configs the CI is wide on purpose.

Rows are de-duplicated to the **latest grade per ``run_id``** (so re-grades do not
double-count) and a cell mixing multiple ``spec_sha`` values is flagged rather
than silently averaged across different specs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

# ``eval_toolkit.bootstrap`` is imported lazily inside the functions that use it
# (see ``_aggregate_cell`` / ``_compare_task``) so ``import analyze`` — and the CLI
# ``report``/``compare`` --help paths — do not hard-require the optional eval-toolkit
# dependency. ``tests/test_analyze.py`` guards with ``pytest.importorskip``.

__all__ = ["ReportCell", "CompareRow", "report", "compare", "LEAKAGE_BAND"]

logger = logging.getLogger(__name__)

#: max |shuffled-label AUROC − 0.5| over a cell's epochs beyond this flags leakage.
LEAKAGE_BAND = 0.15
#: epochs needed before an across-epoch bootstrap CI of the mean is reported.
MIN_EPOCHS_FOR_CI = 3
#: matched configs needed before compare may call a delta "real" (Type-I control).
MIN_PAIRS_FOR_REAL = 4

# Latest grade per run_id, then keep only runs whose LATEST grade is ok.
#
# Order matters: filtering grade_status BEFORE the window would let a run whose
# *latest* grade is a grader_error fall back to an older ``ok`` grade — silently
# reporting a stale score the re-grade meant to replace. So we dedupe first
# (run_status='ok' only — infra failures never count), pick the most recent grade
# per run (ts, tie-broken by grader_version for determinism), then drop runs whose
# surviving grade is not ok.
_LATEST_OK = """
WITH ok_runs AS (
    SELECT * FROM read_json(?, format='newline_delimited')
    WHERE run_status = 'ok'
),
ranked AS (
    SELECT *, row_number() OVER (
        PARTITION BY run_id ORDER BY ts DESC, grader_version DESC
    ) AS rn FROM ok_runs
)
SELECT * FROM ranked WHERE rn = 1 AND grade_status = 'ok'
"""


@dataclass(frozen=True, slots=True)
class ReportCell:
    """Aggregated quality/cost/latency for one (task, model, effort, variant) cell."""

    task_id: str
    model: str
    effort: str
    variant: str
    n_epochs: int
    n_spec: int
    mean_value: float
    sd_value: float | None
    mean_cost: float
    mean_latency: float
    ci_low: float | None
    ci_high: float | None
    shuffled_auroc: float | None
    pareto: bool = False
    leakage: bool = False


@dataclass(frozen=True, slots=True)
class CompareRow:
    """A variant A/B delta for one task with a paired-bootstrap "is it real" verdict."""

    task_id: str
    n_pairs: int
    mean_a: float
    mean_b: float
    delta: float
    ci_low: float | None
    ci_high: float | None
    real: bool
    note: str = ""


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def report(ledger_path: Path | str) -> list[ReportCell]:
    """Aggregate the ledger into per-cell quality/cost rows (Pareto + leakage flagged).

    Per-epoch rows are pulled and aggregated in Python so the cell's CI is an
    honest **across-epoch bootstrap of the mean** (reported only at ≥
    :data:`MIN_EPOCHS_FOR_CI` epochs) — never the meaningless average of per-epoch
    CI endpoints — and leakage fires on the **worst** epoch, not the average.
    Returns an empty list for a missing/empty ledger; cells are ordered by task,
    then descending mean quality.
    """
    path = Path(ledger_path)
    if not path.exists():
        return []
    sql = f"""
    SELECT task_id, model, effort, variant, spec_sha, value, cost_usd, latency_s,
        TRY_CAST(json_extract(subscores, '$.shuffled_auroc') AS DOUBLE) AS shuffled
    FROM ({_LATEST_OK})
    """
    con = _connect()
    try:
        rows = con.execute(sql, [str(path)]).fetchall()
    finally:
        con.close()

    grouped: dict[tuple[str, str, str, str], list[tuple[Any, ...]]] = {}
    for r in rows:
        grouped.setdefault((r[0], r[1], r[2], r[3]), []).append(r)
    cells = [_aggregate_cell(key, group) for key, group in grouped.items()]
    cells.sort(key=lambda c: (c.task_id, -c.mean_value))
    return _mark_pareto(cells)


def _aggregate_cell(key: tuple[str, str, str, str], group: list[tuple[Any, ...]]) -> ReportCell:
    """Aggregate one cell's per-epoch rows into a :class:`ReportCell` (honest stats)."""
    task_id, model, effort, variant = key
    values = np.array([row[5] for row in group], dtype=float)
    costs = np.array([row[6] for row in group], dtype=float)
    lats = np.array([row[7] for row in group], dtype=float)
    shuffles = [float(row[8]) for row in group if row[8] is not None]
    n = len(values)

    ci_low = ci_high = None
    if n >= MIN_EPOCHS_FOR_CI:  # a proper bootstrap CI of the across-epoch mean
        from eval_toolkit.bootstrap import block_bootstrap_on_folds

        ci = block_bootstrap_on_folds(values, n_resamples=2000, rng=42)
        ci_low, ci_high = float(ci.ci_low), float(ci.ci_high)
    # Leakage fires on the WORST epoch — averaging would let one leaky run hide.
    max_dev = max((abs(s - 0.5) for s in shuffles), default=None)
    return ReportCell(
        task_id=task_id,
        model=model,
        effort=effort,
        variant=variant,
        n_epochs=n,
        n_spec=len({row[4] for row in group}),
        mean_value=float(values.mean()),
        sd_value=float(values.std(ddof=1)) if n >= 2 else None,
        mean_cost=float(costs.mean()),
        mean_latency=float(lats.mean()),
        ci_low=ci_low,
        ci_high=ci_high,
        shuffled_auroc=(sum(shuffles) / len(shuffles)) if shuffles else None,
        leakage=max_dev is not None and max_dev > LEAKAGE_BAND,
    )


def _mark_pareto(cells: list[ReportCell]) -> list[ReportCell]:
    """Flag cells on the per-task quality-vs-cost Pareto frontier (max value, min cost)."""
    out: list[ReportCell] = []
    for cell in cells:
        dominated = any(
            other is not cell
            and other.task_id == cell.task_id
            and other.mean_value >= cell.mean_value
            and other.mean_cost <= cell.mean_cost
            and (other.mean_value > cell.mean_value or other.mean_cost < cell.mean_cost)
            for other in cells
        )
        out.append(replace(cell, pareto=not dominated))
    return out


def compare(ledger_path: Path | str, variant_a: str, variant_b: str) -> list[CompareRow]:
    """Per-task A→B delta with a paired-bootstrap CI over matched (model, effort) configs.

    For each task, every (model, effort) present under *both* variants contributes
    one paired observation (epochs averaged within a config). The delta is
    ``mean_b − mean_a``; the CI is a bootstrap over those per-config differences.
    ``real`` is ``True`` only when the CI excludes 0 *and* there are ≥
    :data:`MIN_PAIRS_FOR_REAL` configs: with 2–3 same-sign diffs the bootstrap CI
    excludes 0 by construction (a tautology, not evidence), so a smaller n yields
    ``real=False`` with an explanatory note. No multiple-comparison correction is
    applied across tasks (v1 exploratory) — read several "real" verdicts with care.
    """
    path = Path(ledger_path)
    if not path.exists():
        return []
    sql = f"""
    SELECT task_id, model, effort, variant, avg(value) AS mean_value
    FROM ({_LATEST_OK})
    WHERE variant IN (?, ?)
    GROUP BY task_id, model, effort, variant
    """
    con = _connect()
    try:
        rows = con.execute(sql, [str(path), variant_a, variant_b]).fetchall()
    finally:
        con.close()

    # (task, model, effort) -> {variant: mean_value}
    by_config: dict[tuple[str, str, str], dict[str, float]] = {}
    for task_id, model, effort, variant, mean_value in rows:
        by_config.setdefault((task_id, model, effort), {})[variant] = float(mean_value)

    tasks = sorted({task_id for task_id, _, _ in by_config})
    return [
        _compare_task(task_id, by_config, variant_a, variant_b)
        for task_id in tasks
        if any(
            variant_a in v and variant_b in v for (t, _, _), v in by_config.items() if t == task_id
        )
    ]


def _compare_task(
    task_id: str,
    by_config: dict[tuple[str, str, str], dict[str, float]],
    variant_a: str,
    variant_b: str,
) -> CompareRow:
    """Build one :class:`CompareRow` from the paired per-config means of a task."""
    pairs = [
        (v[variant_a], v[variant_b])
        for (t, _, _), v in by_config.items()
        if t == task_id and variant_a in v and variant_b in v
    ]
    a_vals = np.array([a for a, _ in pairs], dtype=float)
    b_vals = np.array([b for _, b in pairs], dtype=float)
    diffs = b_vals - a_vals
    n = len(diffs)
    mean_a, mean_b, delta = float(a_vals.mean()), float(b_vals.mean()), float(diffs.mean())

    if n < MIN_PAIRS_FOR_REAL:
        # Too few configs: a same-sign 2–3 diff bootstrap excludes 0 by construction,
        # so report the CI for context but never call it "real".
        ci = None
        if n >= 2:  # lazy import keeps eval_toolkit off analyze's module-import path
            from eval_toolkit.bootstrap import block_bootstrap_on_folds

            ci = block_bootstrap_on_folds(diffs, n_resamples=2000, rng=42)
        return CompareRow(
            task_id=task_id,
            n_pairs=n,
            mean_a=mean_a,
            mean_b=mean_b,
            delta=delta,
            ci_low=None if ci is None else float(ci.ci_low),
            ci_high=None if ci is None else float(ci.ci_high),
            real=False,
            note=f"n={n} configs — below the >={MIN_PAIRS_FOR_REAL} floor for a verdict",
        )
    from eval_toolkit.bootstrap import block_bootstrap_on_folds

    ci = block_bootstrap_on_folds(diffs, n_resamples=2000, rng=42)
    return CompareRow(
        task_id=task_id,
        n_pairs=n,
        mean_a=mean_a,
        mean_b=mean_b,
        delta=delta,
        ci_low=float(ci.ci_low),
        ci_high=float(ci.ci_high),
        real=ci.ci_low > 0.0 or ci.ci_high < 0.0,  # interval excludes 0
        note="exploratory (small n)",
    )
