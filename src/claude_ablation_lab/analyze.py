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

import duckdb
import numpy as np
from eval_toolkit.bootstrap import block_bootstrap_on_folds

__all__ = ["ReportCell", "CompareRow", "report", "compare", "LEAKAGE_BAND"]

logger = logging.getLogger(__name__)

#: |mean shuffled-label AUROC − 0.5| beyond this flags suspected leakage.
LEAKAGE_BAND = 0.15

# Latest ok grade per run_id (re-grades don't double-count), ok runs only.
_LATEST_OK = """
WITH ok_grades AS (
    SELECT * FROM read_json(?, format='newline_delimited')
    WHERE run_status = 'ok' AND grade_status = 'ok'
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY run_id ORDER BY ts DESC) AS rn FROM ok_grades
)
SELECT * FROM ranked WHERE rn = 1
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

    Returns an empty list for a missing/empty ledger. Cells are ordered by task,
    then descending mean quality.
    """
    path = Path(ledger_path)
    if not path.exists():
        return []
    sql = f"""
    SELECT task_id, model, effort, variant,
        count(*) AS n_epochs,
        count(DISTINCT spec_sha) AS n_spec,
        avg(value) AS mean_value,
        stddev_samp(value) AS sd_value,
        avg(cost_usd) AS mean_cost,
        avg(latency_s) AS mean_latency,
        avg(TRY_CAST(json_extract(subscores, '$.ci_low') AS DOUBLE)) AS ci_low,
        avg(TRY_CAST(json_extract(subscores, '$.ci_high') AS DOUBLE)) AS ci_high,
        avg(TRY_CAST(json_extract(subscores, '$.shuffled_auroc') AS DOUBLE)) AS shuffled
    FROM ({_LATEST_OK})
    GROUP BY task_id, model, effort, variant
    ORDER BY task_id, mean_value DESC
    """
    con = _connect()
    try:
        rows = con.execute(sql, [str(path)]).fetchall()
    finally:
        con.close()

    cells = [
        ReportCell(
            task_id=r[0],
            model=r[1],
            effort=r[2],
            variant=r[3],
            n_epochs=int(r[4]),
            n_spec=int(r[5]),
            mean_value=float(r[6]),
            sd_value=None if r[7] is None else float(r[7]),
            mean_cost=float(r[8]),
            mean_latency=float(r[9]),
            ci_low=None if r[10] is None else float(r[10]),
            ci_high=None if r[11] is None else float(r[11]),
            shuffled_auroc=None if r[12] is None else float(r[12]),
            leakage=r[12] is not None and abs(float(r[12]) - 0.5) > LEAKAGE_BAND,
        )
        for r in rows
    ]
    return _mark_pareto(cells)


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
    ``real`` is ``True`` only when the CI excludes 0 *and* there are ≥ 2 configs
    (a single config cannot support a bootstrap) — always exploratory at v1 scale.
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
    delta = float(diffs.mean())
    if len(diffs) < 2:
        return CompareRow(
            task_id=task_id,
            n_pairs=len(diffs),
            mean_a=float(a_vals.mean()),
            mean_b=float(b_vals.mean()),
            delta=delta,
            ci_low=None,
            ci_high=None,
            real=False,
            note="1 config — no CI (need ≥2)",
        )
    ci = block_bootstrap_on_folds(diffs, n_resamples=2000, rng=42)
    real = ci.ci_low > 0.0 or ci.ci_high < 0.0  # interval excludes 0
    return CompareRow(
        task_id=task_id,
        n_pairs=len(diffs),
        mean_a=float(a_vals.mean()),
        mean_b=float(b_vals.mean()),
        delta=delta,
        ci_low=float(ci.ci_low),
        ci_high=float(ci.ci_high),
        real=real,
        note="exploratory (small n)",
    )
