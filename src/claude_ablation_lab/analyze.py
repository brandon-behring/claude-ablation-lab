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
- The **shuffled-label control** is checked here (not per-cell, which is too
  noisy): if a classification cell's shuffled-label AUROC strays from 0.5 by more
  than :data:`LEAKAGE_BAND`, the row is flagged. Honest scope (2026-07-01
  methodology audit): because the permutation happens at *grading* time over fixed
  predictions, this is a **metric-pipeline self-test** — it catches a broken
  permutation/metric implementation, not gold-leaked-into-prompt leakage (a
  perfect leak still shuffles to ~0.5). Real leakage defenses are the holdout
  design and the grader tests.
- ``compare`` verdicts use an **exact sign-flip permutation test** on the mean
  per-config delta (all ``2^n`` sign assignments; zero diffs carry no direction
  and are excluded, reported as ``n_nonzero``): ``real`` means ``p <= 0.05``. The
  paired-bootstrap CI is reported as *effect-size context only* — the 2026-07-01
  audit showed a same-sign percentile-bootstrap CI excludes 0 by construction at
  any magnitude (Type-I ≈ 21% at n=4), so it must never be the verdict rule.
- ``unparseable`` grades count as their honest ``0.0`` in aggregation: the model
  produced ungradeable output, which is a *quality* failure. Only infra failures
  (``run_status != 'ok'``) and ``grader_error`` rows are excluded.

Rows are de-duplicated to the **latest grade per ``run_id``** (so re-grades do not
double-count) and a cell mixing multiple ``spec_sha`` values is flagged rather
than silently averaged across different specs.
"""

from __future__ import annotations

import itertools
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

__all__ = [
    "ReportCell",
    "CompareRow",
    "AdviceRow",
    "report",
    "compare",
    "cost_advisor",
    "LEAKAGE_BAND",
]

logger = logging.getLogger(__name__)

#: max |shuffled-label AUROC − 0.5| over a cell's epochs beyond this flags a broken
#: metric pipeline. The statistic is a mean over 200 permutations (null SD ≈ 0.005 at
#: n=60), so 0.05 ≈ 11σ — effectively zero false-flag rate while 3× more sensitive to
#: gross breakage than the original 0.15 (which was ~33σ and could never fire).
LEAKAGE_BAND = 0.05
#: epochs needed before an across-epoch interval is reported. NOTE: at n=3 the
#: percentile bootstrap degenerates to the min–max epoch range (~74% coverage), so the
#: interval is presented as an "epoch range", not a 95% CI, below 5 epochs.
MIN_EPOCHS_FOR_CI = 3
#: fewest nonzero paired diffs at which the exact sign-flip test can reach p <= 0.05
#: (min two-sided p = 2/2^n → n >= 6). Below this, a verdict row is noted underpowered.
MIN_PAIRS_FOR_REAL = 6
#: significance level for the exact sign-flip permutation verdict.
ALPHA = 0.05

# Latest grade per run_id, then keep runs whose LATEST grade is definitive.
#
# Order matters: filtering grade_status BEFORE the window would let a run whose
# *latest* grade is a grader_error fall back to an older ``ok`` grade — silently
# reporting a stale score the re-grade meant to replace. So we dedupe first
# (run_status='ok' only — infra failures never count), pick the most recent grade
# per run (ts, tie-broken by grader_version for determinism), then keep runs whose
# surviving grade is definitive: ``ok`` OR ``unparseable``. An unparseable grade is
# a *model quality* failure carrying its honest value=0.0 — excluding it would
# silently inflate the surviving mean (2026-07-01 methodology audit). Only
# ``grader_error`` (the grader itself failed; re-gradable) stays out.
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
SELECT * FROM ranked WHERE rn = 1 AND grade_status IN ('ok', 'unparseable')
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
    #: unparseable epochs included in the mean at their honest 0.0 (surfaced, not hidden).
    n_unparseable: int = 0
    #: distinct grader_versions mixed into this cell (⚠ if > 1: metric definitions differ).
    n_grader_versions: int = 1


@dataclass(frozen=True, slots=True)
class CompareRow:
    """A variant A/B delta for one task with an exact sign-flip "is it real" verdict.

    ``real`` is ``p_value <= ALPHA`` from the exact sign-flip permutation test; the
    bootstrap ``ci_low``/``ci_high`` is effect-size context only (never the verdict —
    a same-sign percentile bootstrap excludes 0 by construction).
    """

    task_id: str
    n_pairs: int
    mean_a: float
    mean_b: float
    delta: float
    ci_low: float | None
    ci_high: float | None
    real: bool
    note: str = ""
    #: exact two-sided sign-flip permutation p for the mean delta (None: no nonzero diffs).
    p_value: float | None = None
    #: nonzero paired diffs the test ran on (zero diffs carry no directional evidence).
    n_nonzero: int = 0


@dataclass(frozen=True, slots=True)
class AdviceRow:
    """A per-(task, variant) cost-downgrade recommendation.

    The cheapest config whose quality is *non-inferior* to the reflex config —
    within ``margin`` on the mean. At the few epochs this harness runs the decision
    is deliberately a **point estimate within a margin**, not a significance test:
    epochs are exploratory run-variance (see the module docstring), and two
    *different configs'* epochs are not the matched pairs ``compare``'s sign-flip
    test needs (same inputs, one axis changed), so a p-value here would be theatre.
    The quality delta is surfaced verbatim, so a downgrade that trades a little
    quality for a lot of cost is never hidden — the reader (or ``--margin``) judges.
    """

    task_id: str
    variant: str
    #: the expensive default this recommendation is measured against (possibly a
    #: fallback if the exact reflex config was not in the ledger — see ``note``).
    reflex_model: str
    reflex_effort: str
    reflex_value: float
    reflex_cost: float
    reflex_latency: float
    rec_model: str
    rec_effort: str
    rec_value: float
    rec_cost: float
    rec_latency: float
    #: recommended − reflex mean quality (≥ −margin by construction; ≤ 0 = a downgrade).
    quality_delta: float
    #: reflex − recommended mean cost, USD per run (the per-run overpay if positive).
    cost_saving: float
    #: reflex_cost / rec_cost, or ``None`` when the recommendation is free.
    cost_multiple: float | None
    #: reflex − recommended mean latency, seconds (may be negative: cheaper yet slower).
    latency_saving: float
    #: True if the exact reflex config was absent and a fallback stood in (see ``note``).
    reflex_fallback: bool = False
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
        TRY_CAST(json_extract(subscores, '$.shuffled_auroc') AS DOUBLE) AS shuffled,
        grade_status, grader_version
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
    if n >= MIN_EPOCHS_FOR_CI:
        # An across-epoch interval; at n=3–4 the percentile bootstrap degenerates to
        # the min–max epoch range (~74% coverage at n=3), so downstream presentation
        # labels it "epoch range", not a 95% CI, below 5 epochs.
        try:
            from eval_toolkit.bootstrap import block_bootstrap_on_folds
        except ImportError:  # optional stats dep absent → no interval, not a crash
            logger.warning("eval_toolkit missing — across-epoch interval omitted")
        else:
            ci = block_bootstrap_on_folds(values, n_resamples=2000, rng=42)
            ci_low, ci_high = float(ci.ci_low), float(ci.ci_high)
    # The self-test fires on the WORST epoch — averaging would let one broken run hide.
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
        n_unparseable=sum(1 for row in group if row[9] == "unparseable"),
        n_grader_versions=len({row[10] for row in group}),
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
    """Per-task A→B delta with an exact sign-flip verdict over matched (model, effort) configs.

    For each task, every (model, effort) present under *both* variants contributes
    one paired observation (epochs averaged within a config). The delta is
    ``mean_b − mean_a``. ``real`` is decided by the exact sign-flip permutation test
    (``p <= ALPHA`` over the nonzero diffs; min possible p is ``2/2^n``, so ``real``
    is mechanically unreachable below :data:`MIN_PAIRS_FOR_REAL` nonzero pairs and
    the row says so). The paired-bootstrap CI is effect-size context only. No
    multiple-comparison correction is applied across tasks (v1 exploratory) — read
    several "real" verdicts with care.
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


def _sign_flip_p(diffs: np.ndarray) -> tuple[float | None, int]:
    """Exact two-sided sign-flip permutation p-value for ``mean(diffs)``.

    Enumerates all ``2^n`` sign assignments of the *nonzero* diffs (exact at the tiny
    n this harness produces; Monte-Carlo above 16 to bound memory). Zero diffs carry
    no directional evidence, so they are excluded and ``n_nonzero`` reported — the
    consulted-on convention that also keeps quantized/tied scores honest. Returns
    ``(None, 0)`` when every diff is zero (no evidence either way).
    """
    nonzero = diffs[diffs != 0.0]
    n = int(nonzero.size)
    if n == 0:
        return None, 0
    observed = abs(float(nonzero.mean()))
    if n <= 16:
        signs = np.array(list(itertools.product((1.0, -1.0), repeat=n)))
    else:  # pragma: no cover — grids here never exceed ~9 pairs
        signs = np.random.default_rng(42).choice((1.0, -1.0), size=(100_000, n))
    flipped = np.abs((signs * nonzero).mean(axis=1))
    # >= with an fp tolerance so the observed assignment always counts itself.
    return float(np.mean(flipped >= observed - 1e-12)), n


def _compare_task(
    task_id: str,
    by_config: dict[tuple[str, str, str], dict[str, float]],
    variant_a: str,
    variant_b: str,
) -> CompareRow:
    """Build one :class:`CompareRow` from the paired per-config means of a task.

    The verdict is the exact sign-flip permutation test (``real = p <= ALPHA``); the
    bootstrap CI is effect-size context only. A same-sign percentile-bootstrap CI
    excludes 0 by construction at any magnitude (measured Type-I ≈ 21% at n=4), so
    it must never decide ``real`` — the 2026-07-01 methodology audit's core fix.
    """
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

    p_value, n_nonzero = _sign_flip_p(diffs)

    ci_low = ci_high = None
    if n >= 2:  # bootstrap CI as effect-size context (lazy optional import)
        try:
            from eval_toolkit.bootstrap import block_bootstrap_on_folds
        except ImportError:
            logger.warning("eval_toolkit missing — effect-size CI omitted")
        else:
            ci = block_bootstrap_on_folds(diffs, n_resamples=2000, rng=42)
            ci_low, ci_high = float(ci.ci_low), float(ci.ci_high)

    notes: list[str] = []
    if p_value is None:
        notes.append("all diffs zero — no directional evidence")
    elif n_nonzero < MIN_PAIRS_FOR_REAL:
        notes.append(
            f"n_nonzero={n_nonzero} < {MIN_PAIRS_FOR_REAL} — p cannot reach {ALPHA:g} "
            f"(min p = {2 / 2**n_nonzero:g})"
        )
    if ci_low is not None and ci_low == ci_high:
        notes.append("CI degenerate (all diffs identical)")
    return CompareRow(
        task_id=task_id,
        n_pairs=n,
        mean_a=mean_a,
        mean_b=mean_b,
        delta=delta,
        ci_low=ci_low,
        ci_high=ci_high,
        real=p_value is not None and p_value <= ALPHA,
        note="; ".join(notes) if notes else "exact sign-flip test",
        p_value=p_value,
        n_nonzero=n_nonzero,
    )


#: effort ordering for the reflex "highest available effort" fallback.
_EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2, "max": 3}


def _resolve_reflex(group: list[ReportCell], model: str, effort: str) -> tuple[ReportCell, str]:
    """Pick the cell standing in for the user's expensive reflex, with fallbacks.

    exact ``(model, effort)`` → same model at its highest effort that ran → the
    single priciest cell in the group. Returns the cell and a note naming the
    fallback used (empty string when the exact reflex config was present). The
    priciest-cell fallback keeps ``advise`` meaningful on a ledger that never ran
    the named reflex at all — it measures against the most expensive thing that did.
    """
    exact = [c for c in group if c.model == model and c.effort == effort]
    if exact:
        return exact[0], ""
    same_model = [c for c in group if c.model == model]
    if same_model:
        cell = max(same_model, key=lambda c: _EFFORT_ORDER.get(c.effort, -1))
        return cell, f"{model}/{effort} absent — measured vs {cell.model}/{cell.effort}"
    priciest = max(group, key=lambda c: c.mean_cost)
    return (
        priciest,
        f"{model}/{effort} absent — measured vs priciest {priciest.model}/{priciest.effort}",
    )


def cost_advisor(
    cells: list[ReportCell],
    reflex: str = "opus/max",
    margin: float = 0.02,
) -> list[AdviceRow]:
    """Per (task, variant): the cheapest config non-inferior to the reflex, and the saving.

    For each ``(task_id, variant)`` group, resolve the *reflex* config (the
    expensive default, e.g. ``opus/max``, with the fallbacks in
    :func:`_resolve_reflex`) and recommend the **lowest-cost** cell whose
    ``mean_value`` is within ``margin`` of the reflex's. ``margin`` is an absolute
    tolerance on the ``[0, 1]`` metric (default ``0.02``). Rows are ordered by
    dollar saving descending — the biggest overpay first. See :class:`AdviceRow`
    on why this is a margin decision rather than a p-value.
    """
    try:
        r_model, r_effort = reflex.split("/", 1)
    except ValueError:
        raise ValueError(f"reflex must be 'model/effort' (got {reflex!r})") from None
    if not 0.0 <= margin <= 1.0:
        raise ValueError(f"margin must be in [0, 1] (got {margin})")

    groups: dict[tuple[str, str], list[ReportCell]] = {}
    for c in cells:
        groups.setdefault((c.task_id, c.variant), []).append(c)

    advice: list[AdviceRow] = []
    for (task_id, variant), group in groups.items():
        reflex_cell, fallback_note = _resolve_reflex(group, r_model, r_effort)
        # Non-inferior = within margin on the mean. Reflex itself always qualifies.
        candidates = [c for c in group if c.mean_value >= reflex_cell.mean_value - margin]
        # Cheapest wins; deterministic tie-break so equal-cost cells never reorder.
        rec = min(candidates, key=lambda c: (c.mean_cost, c.mean_latency, c.model, c.effort))

        notes = [fallback_note] if fallback_note else []
        if rec.model == reflex_cell.model and rec.effort == reflex_cell.effort:
            notes.append(
                "already cheapest non-inferior" if len(group) > 1 else "only one config ran"
            )
        elif rec.mean_value < reflex_cell.mean_value:
            notes.append(
                f"−{reflex_cell.mean_value - rec.mean_value:.3f} quality within margin {margin:g}"
            )
        else:
            notes.append("cheaper at equal-or-better quality")

        advice.append(
            AdviceRow(
                task_id=task_id,
                variant=variant,
                reflex_model=reflex_cell.model,
                reflex_effort=reflex_cell.effort,
                reflex_value=reflex_cell.mean_value,
                reflex_cost=reflex_cell.mean_cost,
                reflex_latency=reflex_cell.mean_latency,
                rec_model=rec.model,
                rec_effort=rec.effort,
                rec_value=rec.mean_value,
                rec_cost=rec.mean_cost,
                rec_latency=rec.mean_latency,
                quality_delta=rec.mean_value - reflex_cell.mean_value,
                cost_saving=reflex_cell.mean_cost - rec.mean_cost,
                cost_multiple=(
                    (reflex_cell.mean_cost / rec.mean_cost) if rec.mean_cost > 0 else None
                ),
                latency_saving=reflex_cell.mean_latency - rec.mean_latency,
                reflex_fallback=bool(fallback_note),
                note="; ".join(notes),
            )
        )
    advice.sort(key=lambda a: a.cost_saving, reverse=True)
    return advice
