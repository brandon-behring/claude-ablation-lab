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
  produced ungradeable output, which is a *quality* failure, surfaced per cell as
  ``n_unparseable`` (and its rate). Only infra failures (``run_status != 'ok'``) and
  ``grader_error`` rows are excluded from the mean — but the excluded non-ok runs are
  now counted per cell as ``n_dropped`` so a systematic failure isn't invisible (B4).
  **Mind the naming collision:** ``run_status='parse_fail'`` (the *harness* couldn't
  parse the CLI's JSON envelope — infra-class, excluded, → ``n_dropped``) is a different
  axis from ``grade_status='unparseable'`` (the *model* produced ungradeable output —
  a quality failure, included at 0.0, → ``n_unparseable``).

Rows are de-duplicated to the **latest grade per ``run_id``** (so re-grades do not
double-count) and a cell mixing multiple ``spec_sha`` values is flagged rather
than silently averaged across different specs.
"""

from __future__ import annotations

import functools
import itertools
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

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
    "X_AXES",
    "x_value",
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

# Every ledger column the analysis queries touch, with explicit types. Declaring
# the schema (instead of letting read_json infer it) makes old ledgers forward-
# compatible: a column absent from the file (e.g. the 2026-07-06 token fields on a
# pre-token ledger) reads as NULL instead of raising — the SQL-side mirror of
# ``LedgerRow``'s defaulted dataclass fields. A new analysis column must be added
# here as well as to the dataclass.
_LEDGER_COLUMNS = """{
    task_id: 'VARCHAR', model: 'VARCHAR', effort: 'VARCHAR', variant: 'VARCHAR',
    epoch: 'BIGINT', grader_version: 'VARCHAR', run_id: 'VARCHAR',
    run_status: 'VARCHAR', grade_status: 'VARCHAR', value: 'DOUBLE',
    cost_usd: 'DOUBLE', latency_s: 'DOUBLE', spec_sha: 'VARCHAR',
    subscores: 'VARCHAR', ts: 'VARCHAR',
    input_tokens: 'BIGINT', output_tokens: 'BIGINT',
    cache_read_tokens: 'BIGINT', cache_creation_tokens: 'BIGINT'
}"""

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
_LATEST_OK = f"""
WITH ok_runs AS (
    SELECT * FROM read_json(?, format='newline_delimited', columns={_LEDGER_COLUMNS})
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
    #: A ``grade_status`` — the model produced ungradeable output (a *quality* failure).
    n_unparseable: int = 0
    #: non-ok runs for this cell (``run_status`` parse_fail/infra_error/timeout/rate_limited),
    #: EXCLUDED from the quality mean and otherwise invisible — surfaced so a systematic
    #: ``parse_fail`` can't hide behind the epochs that *did* parse (B4). Distinct axis from
    #: ``n_unparseable``: that is a grade_status counted IN the mean; this is a run_status
    #: kept OUT of it. A cell with **zero** ok runs is absent entirely (warned at report time).
    n_dropped: int = 0
    #: distinct grader_versions mixed into this cell (⚠ if > 1: metric definitions differ).
    n_grader_versions: int = 1
    # Across-epoch intervals for the cost axes (same estimator and honesty rules as
    # the quality ``ci_low``/``ci_high``: computed at ≥ MIN_EPOCHS_FOR_CI epochs and
    # presented as an "epoch range", not a 95% CI, below 5 epochs).
    cost_ci_low: float | None = None
    cost_ci_high: float | None = None
    latency_ci_low: float | None = None
    latency_ci_high: float | None = None
    #: Token means over the epochs that measured them (None: no epoch carried token
    #: counts — every row predates 2026-07-06). The token cost axis is OUTPUT tokens —
    #: the effort/headroom proxy: unlike input+cache they are what effort levels
    #: modulate. Input+cache tokens are the bulk of measured *spend* (2026-07-03 spend
    #: audit: cache reads were the single largest component); they are persisted on
    #: every row but feed no frontier axis yet.
    mean_input_tokens: float | None = None
    mean_output_tokens: float | None = None
    tokens_ci_low: float | None = None
    tokens_ci_high: float | None = None
    #: epochs with measured token counts; < n_epochs means a mixed-era ledger and the
    #: token statistics cover only the measured subset (surfaced, never silent).
    n_token_epochs: int = 0
    #: Cache-token means over the same measured epochs (input/output/cache tokens entered
    #: the ledger together on 2026-07-06, so their coverage is exactly ``n_token_epochs``).
    #: Cache-read from long sessions is the single largest *spend* component (2026-07-03
    #: audit) yet bills nothing on a flat plan — a throughput/headroom cost, not a dollar one.
    mean_cache_read_tokens: float | None = None
    mean_cache_creation_tokens: float | None = None
    #: TOTAL token throughput = input + output + cache_read + cache_creation: the honest
    #: "what this run costs your rate-limit headroom" axis (the ``throughput`` frontier),
    #: with an across-epoch interval over the epochs that carried ALL FOUR components.
    mean_total_tokens: float | None = None
    total_tokens_ci_low: float | None = None
    total_tokens_ci_high: float | None = None
    #: epochs with a fully-measured total (all four token components present). Its own
    #: counter (not ``n_token_epochs``, which gates on output only) so a cell missing cache
    #: on some epochs never reads as fully measured; ``< n_epochs`` ⇒ partial, off the frontier.
    n_total_token_epochs: int = 0


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
    #: ``unparseable`` rows over the *paired* configs (both variants) underlying this delta —
    #: each scored 0.0 IN the per-config means the test runs on, so read a nonzero count with
    #: care. Excludes unpaired configs, which feed no diff (adversarial review, finding 5).
    n_unparseable: int = 0


@dataclass(frozen=True, slots=True)
class AdviceRow:
    """A per-(task, variant) downgrade recommendation on the selected advice axis.

    The config that **minimises the selected axis** (latency / throughput / cost) while
    its mean quality is within ``margin`` of the **best** config that ran for this
    (task, variant), reported against the user's *reflex* config (their expensive default)
    so the saving is "vs what you reach for." Flooring at the best, not the reflex, is
    deliberate: if the reflex itself under-performs a cheaper config, "best within margin
    of the reflex" would recommend a **failing** config; flooring at the best never does.

    Why a **margin**, not a significance test: ``cost_advisor`` sees only per-cell
    epoch means (``ReportCell``), and at the few epochs this harness runs a per-cell
    test is underpowered — *not* because a paired test is impossible in principle
    (configs share the same task examples), but because the per-example/per-epoch
    scores are not plumbed to this layer. So the decision is an honest point estimate
    within ``margin``; the recommendation's **absolute** quality (``rec_value``) and
    its delta vs the reflex are both surfaced, and ``report`` still carries the
    bootstrap CI for anyone who wants the uncertainty.
    """

    task_id: str
    variant: str
    #: the expensive default the saving is measured against (a fallback if the exact
    #: reflex config was absent — see ``reflex_fallback``).
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
    #: best mean quality any config reached in this group (the non-inferiority anchor).
    best_value: float
    #: recommended − reflex mean quality (> 0 means the reflex was itself suboptimal).
    quality_delta: float
    #: reflex − recommended mean cost, USD per run (the per-run overpay if positive; can
    #: be negative only for a *cheap* reflex, where reaching top quality costs more).
    cost_saving: float
    #: reflex_cost / rec_cost, or ``None`` when the recommendation is free.
    cost_multiple: float | None
    #: reflex − recommended mean latency, seconds (may be negative: cheaper yet slower).
    latency_saving: float
    #: recommended config's epoch count (few epochs → treat as exploratory).
    n_epochs: int
    #: best config scored ≤ margin — nothing meaningfully works, so the row is advisory
    #: only and is excluded from the headline savings total (latency / throughput / $).
    vacuous: bool = False
    #: the reflex or recommended cell carries a ``report`` validity flag (leakage /
    #: mixed spec / mixed grader-version / unparseable epochs) — its number is not clean.
    suspect: bool = False
    #: True if the exact reflex config was absent and a fallback stood in.
    reflex_fallback: bool = False
    #: reflex/recommended TOTAL token throughput (input+output+cache) and the reflex−rec
    #: saving — None when the ledger predates token persistence (2026-07-06). Efficiency axis.
    reflex_total_tokens: float | None = None
    rec_total_tokens: float | None = None
    throughput_saving: float | None = None
    #: which axis drove the recommendation + ordering (latency | throughput | cost).
    x_axis: str = "latency"
    note: str = ""


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


#: valid ``x_axis`` values for the Pareto frontier and how each reads its x off a
#: cell. ``latency`` is wall-clock seconds and ``throughput`` is TOTAL tokens
#: (input+output+cache) — the two budgets that actually bind on a flat subscription;
#: ``cost`` is API-equivalent USD (a comparability metric, not a charge); ``tokens``
#: is mean OUTPUT tokens (the effort/headroom proxy). The token axes are ``None`` on
#: pre-2026-07-06 rows — such cells sit off the frontier rather than scored as free.
X_AXES: dict[str, str] = {
    "cost": "mean_cost",
    "latency": "mean_latency",
    "tokens": "mean_output_tokens",
    "throughput": "mean_total_tokens",
}


def x_value(cell: ReportCell, x_axis: str) -> float | None:
    """The cell's position on the chosen cost axis (``None``: not usable as a cost).

    The single predicate for frontier membership AND figure membership
    (``plot.pareto_scatter`` filters through it too), so "which cells compete"
    can never disagree between the flag and the picture. Two normalization rules:

    - NaN → ``None``: a NaN x compares false against everything, so without this a
      cell whose cost/latency was null in the ledger (hand-edited — the harness's
      own writers always populate them) would dodge every domination test and sit
      spuriously *on* the frontier (adversarial re-review guard).
    - Partial token coverage → ``None``: on the ``tokens`` axis a mixed-era cell
      (``n_token_epochs < n_epochs``) has a mean over only the measured subset —
      letting it compete would let a partially-unknown cost read as measured
      (PR-wide review, F1). It stays visible in the report table with its
      ``(n/N)`` marker; it just cannot sit on or shape the frontier.
    """
    if x_axis == "tokens" and cell.n_token_epochs < cell.n_epochs:
        return None
    if x_axis == "throughput" and cell.n_total_token_epochs < cell.n_epochs:
        return None
    value = getattr(cell, X_AXES[x_axis])
    if value is None or math.isnan(value):
        return None
    return float(value)


def report(ledger_path: Path | str, *, x_axis: str = "latency") -> list[ReportCell]:
    """Aggregate the ledger into per-cell quality/cost rows (Pareto + leakage flagged).

    Per-epoch rows are pulled and aggregated in Python so the cell's CI is an
    honest **across-epoch bootstrap of the mean** (reported only at ≥
    :data:`MIN_EPOCHS_FOR_CI` epochs) — never the meaningless average of per-epoch
    CI endpoints — and leakage fires on the **worst** epoch, not the average.
    ``x_axis`` selects which cost axis (:data:`X_AXES`) the ``pareto`` flag is
    computed against — the flag is *axis-specific*, so a latency frontier and a USD
    frontier can disagree. Returns an empty list for a missing/empty ledger; cells
    are ordered by task, then descending mean quality.
    """
    if x_axis not in X_AXES:
        raise ValueError(f"x_axis must be one of {sorted(X_AXES)} (got {x_axis!r})")
    path = Path(ledger_path)
    if not path.exists():
        return []
    sql = f"""
    SELECT task_id, model, effort, variant, spec_sha, value, cost_usd, latency_s,
        TRY_CAST(json_extract(subscores, '$.shuffled_auroc') AS DOUBLE) AS shuffled,
        grade_status, grader_version,
        input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
    FROM ({_LATEST_OK})
    """
    # Companion count of the runs _LATEST_OK filters out (run_status != 'ok'): parse_fail
    # and the infra statuses. Surfaced per cell as n_dropped so a systematic parse_fail
    # can't hide behind the epochs that did parse (B4) — distinct run_ids, not re-grade rows.
    drop_sql = f"""
    SELECT task_id, model, effort, variant, count(DISTINCT run_id) AS n_dropped
    FROM read_json(?, format='newline_delimited', columns={_LEDGER_COLUMNS})
    WHERE run_status != 'ok'
    GROUP BY task_id, model, effort, variant
    """
    con = _connect()
    try:
        cur = con.execute(sql, [str(path)])
        # Name the rows off the cursor description: downstream reads row["value"],
        # never row[5] — a mid-SELECT insertion must not silently shift every field
        # (PR-wide review, F5). strict= catches a description/row length mismatch.
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        dropped = {
            (t, m, e, v): int(n) for t, m, e, v, n in con.execute(drop_sql, [str(path)]).fetchall()
        }
    finally:
        con.close()

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault((r["task_id"], r["model"], r["effort"], r["variant"]), []).append(r)
    # A cell with SOME ok runs carries its n_dropped; a cell with ZERO ok runs never groups,
    # so it would vanish from the report entirely — warn so its burned quota isn't invisible.
    fully_dropped = sorted(k for k in dropped if k not in grouped)
    if fully_dropped:
        logger.warning(
            "%d cell(s) produced only non-ok runs (absent from report): %s",
            len(fully_dropped),
            ", ".join("/".join(k) for k in fully_dropped),
        )
    cells = [
        _aggregate_cell(key, group, n_dropped=dropped.get(key, 0)) for key, group in grouped.items()
    ]
    cells.sort(key=lambda c: (c.task_id, -c.mean_value))
    return _mark_pareto(cells, x_axis=x_axis)


@functools.cache
def _bootstrap_fn() -> Callable[..., Any] | None:
    """The optional bootstrap estimator, or ``None`` (warned ONCE) when absent.

    Cached so the intervals-omitted warning fires once per process, not once per
    ``_epoch_interval`` call — which is 4× per cell (quality/cost/latency/tokens)
    and would flood the logs on any real grid (PR-wide review, F4).
    """
    try:
        from eval_toolkit.bootstrap import block_bootstrap_on_folds
    except ImportError:  # optional stats dep absent → no intervals, not a crash
        logger.warning("eval_toolkit missing — across-epoch intervals omitted")
        return None
    return cast("Callable[..., Any]", block_bootstrap_on_folds)


def _epoch_interval(values: np.ndarray) -> tuple[float | None, float | None]:
    """Across-epoch bootstrap interval of the mean, or ``(None, None)`` below the gate.

    One estimator for every aggregated axis (quality, cost, latency, tokens) so no
    axis's uncertainty is computed to a different standard. At n=3–4 the percentile
    bootstrap degenerates to the min–max epoch range (~74% coverage at n=3), so
    downstream presentation labels it "epoch range", not a 95% CI, below 5 epochs.
    """
    if len(values) < MIN_EPOCHS_FOR_CI:
        return None, None
    block_bootstrap_on_folds = _bootstrap_fn()
    if block_bootstrap_on_folds is None:
        return None, None
    ci = block_bootstrap_on_folds(values, n_resamples=2000, rng=42)
    return float(ci.ci_low), float(ci.ci_high)


def _aggregate_cell(
    key: tuple[str, str, str, str], group: list[dict[str, Any]], *, n_dropped: int = 0
) -> ReportCell:
    """Aggregate one cell's per-epoch rows into a :class:`ReportCell` (honest stats).

    ``n_dropped`` is the count of this cell's non-ok runs (surfaced from a companion query,
    since :data:`_LATEST_OK` filters them out before aggregation) — carried, not averaged.
    """
    task_id, model, effort, variant = key
    values = np.array([row["value"] for row in group], dtype=float)
    costs = np.array([row["cost_usd"] for row in group], dtype=float)
    lats = np.array([row["latency_s"] for row in group], dtype=float)
    shuffles = [float(row["shuffled"]) for row in group if row["shuffled"] is not None]
    n = len(values)

    ci_low, ci_high = _epoch_interval(values)
    cost_ci_low, cost_ci_high = _epoch_interval(costs)
    latency_ci_low, latency_ci_high = _epoch_interval(lats)

    # Token statistics cover only the epochs that measured them (pre-2026-07-06 rows
    # carry NULLs). Means over the measured subset, with the subset size surfaced as
    # ``n_token_epochs`` — a partial denominator must be visible, never implied.
    in_toks = np.array(
        [row["input_tokens"] for row in group if row["input_tokens"] is not None], dtype=float
    )
    out_toks = np.array(
        [row["output_tokens"] for row in group if row["output_tokens"] is not None], dtype=float
    )
    tokens_ci_low, tokens_ci_high = _epoch_interval(out_toks)
    cache_read = np.array(
        [row["cache_read_tokens"] for row in group if row["cache_read_tokens"] is not None],
        dtype=float,
    )
    cache_creation = np.array(
        [row["cache_creation_tokens"] for row in group if row["cache_creation_tokens"] is not None],
        dtype=float,
    )
    # TOTAL throughput per epoch = input + output + cache_read + cache_creation. A measured
    # total needs ALL FOUR present — a missing component is *unmeasured*, not free (a warm
    # cache's measured 0 is kept; a NULL excludes the epoch). Coverage is surfaced as
    # n_total_token_epochs; empty ⇒ a pre-token ledger ⇒ mean_total_tokens=None.
    total_toks = np.array(
        [
            row["input_tokens"]
            + row["output_tokens"]
            + row["cache_read_tokens"]
            + row["cache_creation_tokens"]
            for row in group
            if row["input_tokens"] is not None
            and row["output_tokens"] is not None
            and row["cache_read_tokens"] is not None
            and row["cache_creation_tokens"] is not None
        ],
        dtype=float,
    )
    total_tokens_ci_low, total_tokens_ci_high = _epoch_interval(total_toks)

    # The self-test fires on the WORST epoch — averaging would let one broken run hide.
    max_dev = max((abs(s - 0.5) for s in shuffles), default=None)
    return ReportCell(
        task_id=task_id,
        model=model,
        effort=effort,
        variant=variant,
        n_epochs=n,
        n_spec=len({row["spec_sha"] for row in group}),
        mean_value=float(values.mean()),
        sd_value=float(values.std(ddof=1)) if n >= 2 else None,
        mean_cost=float(costs.mean()),
        mean_latency=float(lats.mean()),
        ci_low=ci_low,
        ci_high=ci_high,
        shuffled_auroc=(sum(shuffles) / len(shuffles)) if shuffles else None,
        leakage=max_dev is not None and max_dev > LEAKAGE_BAND,
        n_unparseable=sum(1 for row in group if row["grade_status"] == "unparseable"),
        n_dropped=n_dropped,
        n_grader_versions=len({row["grader_version"] for row in group}),
        cost_ci_low=cost_ci_low,
        cost_ci_high=cost_ci_high,
        latency_ci_low=latency_ci_low,
        latency_ci_high=latency_ci_high,
        mean_input_tokens=float(in_toks.mean()) if in_toks.size else None,
        mean_output_tokens=float(out_toks.mean()) if out_toks.size else None,
        tokens_ci_low=tokens_ci_low,
        tokens_ci_high=tokens_ci_high,
        n_token_epochs=int(out_toks.size),
        mean_cache_read_tokens=float(cache_read.mean()) if cache_read.size else None,
        mean_cache_creation_tokens=float(cache_creation.mean()) if cache_creation.size else None,
        mean_total_tokens=float(total_toks.mean()) if total_toks.size else None,
        total_tokens_ci_low=total_tokens_ci_low,
        total_tokens_ci_high=total_tokens_ci_high,
        n_total_token_epochs=int(total_toks.size),
    )


def _mark_pareto(cells: list[ReportCell], *, x_axis: str = "cost") -> list[ReportCell]:
    """Flag cells on the per-task quality-vs-x Pareto frontier (max value, min x).

    ``x_axis`` picks the cost dimension (:data:`X_AXES`). A cell whose x is
    unmeasured (``None`` — e.g. tokens on a pre-token ledger) is never on the
    frontier and never dominates: an unknown cost must not read as a free one.
    """
    out: list[ReportCell] = []
    for cell in cells:
        x = x_value(cell, x_axis)
        if x is None:
            out.append(replace(cell, pareto=False))
            continue
        dominated = False
        for other in cells:
            if other is cell or other.task_id != cell.task_id:
                continue
            ox = x_value(other, x_axis)
            if ox is None:
                continue
            if (
                other.mean_value >= cell.mean_value
                and ox <= x
                and (other.mean_value > cell.mean_value or ox < x)
            ):
                dominated = True
                break
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
    # Unparseable rows per (config, variant) — folded into the per-config means the test runs
    # on, so surface a "read with care" count. Summed below over *paired* configs only (a
    # config present under just one variant contributes no diff, so its unparseables don't
    # underlie the delta — 2026-07-11 adversarial review, finding 5).
    unp_sql = f"""
    SELECT task_id, model, effort, variant, count(*) AS n_unparseable
    FROM ({_LATEST_OK})
    WHERE variant IN (?, ?) AND grade_status = 'unparseable'
    GROUP BY task_id, model, effort, variant
    """
    con = _connect()
    try:
        rows = con.execute(sql, [str(path), variant_a, variant_b]).fetchall()
        unp_by_config: dict[tuple[str, str, str, str], int] = {
            (t, m, e, v): int(n)
            for t, m, e, v, n in con.execute(unp_sql, [str(path), variant_a, variant_b]).fetchall()
        }
    finally:
        con.close()

    # (task, model, effort) -> {variant: mean_value}
    by_config: dict[tuple[str, str, str], dict[str, float]] = {}
    for task_id, model, effort, variant, mean_value in rows:
        by_config.setdefault((task_id, model, effort), {})[variant] = float(mean_value)

    tasks = sorted({task_id for task_id, _, _ in by_config})
    return [
        replace(
            _compare_task(task_id, by_config, variant_a, variant_b),
            n_unparseable=_paired_unparseables(
                task_id, by_config, unp_by_config, variant_a, variant_b
            ),
        )
        for task_id in tasks
        if any(
            variant_a in v and variant_b in v for (t, _, _), v in by_config.items() if t == task_id
        )
    ]


def _paired_unparseables(
    task_id: str,
    by_config: dict[tuple[str, str, str], dict[str, float]],
    unp_by_config: dict[tuple[str, str, str, str], int],
    variant_a: str,
    variant_b: str,
) -> int:
    """Unparseable rows underlying a task's delta: summed only over configs present under
    *both* variants (an unpaired config feeds no diff, so its unparseables must not count —
    else the CLI flags ``⚠Nunp`` "underlying this delta" for a row the pairing excluded)."""
    total = 0
    for (t, model, effort), variants in by_config.items():
        if t == task_id and variant_a in variants and variant_b in variants:
            total += unp_by_config.get((t, model, effort, variant_a), 0)
            total += unp_by_config.get((t, model, effort, variant_b), 0)
    return total


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


#: effort ordering for the reflex "highest available effort" fallback (xhigh sits
#: between high and max — the Claude 4.7+/5 tier for long-horizon agentic work).
_EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}


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


def _cell_suspect(cell: ReportCell) -> bool:
    """A ``report`` validity flag on this cell — its number is not clean to advise on."""
    return cell.leakage or cell.n_spec > 1 or cell.n_grader_versions > 1 or cell.n_unparseable > 0


#: The axes ``advise`` can rank/optimize on. ``latency`` (default) and ``throughput``
#: (total input+output+cache tokens) are the budgets that bind on a flat subscription;
#: ``cost`` (API-equivalent USD) is a comparability metric, not a charge.
_ADVISE_AXES = ("latency", "throughput", "cost")
#: (better, worse, equal, superlative) display words per ranking axis.
_ADVISE_AXIS_WORDS: dict[str, tuple[str, str, str, str]] = {
    "latency": ("faster", "slower", "same latency", "fastest"),
    "throughput": ("leaner", "heavier", "same throughput", "leanest"),
    "cost": ("cheaper", "pricier", "equal cost", "cheapest"),
}


def _usable_total_tokens(cell: ReportCell) -> float | None:
    """Cell total throughput only when FULLY measured — every epoch carried all four token
    components (``n_total_token_epochs == n_epochs``). Partial coverage → ``None``, mirroring
    the ``x_value`` frontier guard so ``advise`` never ranks on a partially-unknown total."""
    if cell.mean_total_tokens is None or cell.n_total_token_epochs < cell.n_epochs:
        return None
    return cell.mean_total_tokens


def _advise_axis_value(cell: ReportCell, x_axis: str) -> float:
    """The cell's position on an advise ranking axis; ``+inf`` when unmeasured (deprioritized)."""
    metric = {
        "latency": cell.mean_latency,
        "throughput": _usable_total_tokens(cell),
        "cost": cell.mean_cost,
    }[x_axis]
    return float("inf") if metric is None or math.isnan(metric) else float(metric)


def _advice_note(
    reflex_cell: ReportCell,
    rec: ReportCell,
    quality_delta: float,
    axis_saving: float | None,
    x_axis: str,
    margin: float,
    group_size: int,
    vacuous: bool,
    suspect: bool,
) -> str:
    """The terse, honest explanation shown in the advice table (single source of truth)."""
    better, worse, equal, superlative = _ADVISE_AXIS_WORDS[x_axis]
    if vacuous:
        base = "n/a: best config scores ≤ margin"
    elif rec.model == reflex_cell.model and rec.effort == reflex_cell.effort:
        base = f"reflex is already the {superlative}" if group_size > 1 else "only one config ran"
    else:
        if quality_delta > 0:
            qual = f"+{quality_delta:.3f} quality vs reflex"
        elif quality_delta < 0:
            qual = f"{-quality_delta:.3f} quality drop (≤ margin {margin:g})"
        else:
            qual = "same quality as reflex"
        # Never claim "faster/leaner/cheaper" when the tie-break picked an equal cell, and
        # say so explicitly when the ranking axis is unmeasured — throughput is None when
        # EITHER side lacks full token coverage: a pre-token ledger OR partial mixed-era rows.
        if axis_saving is None:
            adj = f"{x_axis} unmeasured (incomplete token coverage)"
        else:
            adj = better if axis_saving > 0 else (equal if axis_saving == 0 else worse)
        base = f"{qual}, {adj}"
    return f"{base} ⚠suspect" if suspect else base


def cost_advisor(
    cells: list[ReportCell],
    reflex: str = "opus/max",
    margin: float = 0.02,
    x_axis: str = "latency",
) -> list[AdviceRow]:
    """Per (task, variant): the non-inferior config that minimises ``x_axis``, vs the reflex.

    For each ``(task_id, variant)`` group, anchor non-inferiority at the **best** mean
    quality any config reached (``best − margin``), then recommend the config that
    minimises the chosen ``x_axis`` — **latency** by default, or **throughput** (total
    input+output+cache tokens) or **cost** (API-equivalent USD, a comparability metric on
    a flat subscription, not a charge). Savings on all three axes are reported against the
    *reflex* config (the expensive default, e.g. ``opus/max``, resolved with the fallbacks
    in :func:`_resolve_reflex`); each row leads with, and the list is ordered by, the
    chosen axis. ``margin`` is an absolute tolerance on the ``[0, 1]`` metric (default
    ``0.02``). A group whose best config scores ``≤ margin`` (nothing works) is marked
    ``vacuous`` and kept out of the headline total. See :class:`AdviceRow` on why this is
    a margin decision, not a p-value.
    """
    parts = reflex.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"reflex must be 'model/effort' (got {reflex!r})")
    r_model, r_effort = parts
    if not 0.0 <= margin <= 1.0:
        raise ValueError(f"margin must be in [0, 1] (got {margin})")
    if x_axis not in _ADVISE_AXES:
        raise ValueError(f"x_axis must be one of {list(_ADVISE_AXES)} (got {x_axis!r})")

    groups: dict[tuple[str, str], list[ReportCell]] = {}
    for c in cells:
        groups.setdefault((c.task_id, c.variant), []).append(c)

    advice: list[AdviceRow] = []
    for (task_id, variant), group in groups.items():
        reflex_cell, fallback_note = _resolve_reflex(group, r_model, r_effort)
        best_value = max(c.mean_value for c in group)
        # Non-inferior = within margin of the BEST config — never the reflex, whose
        # own failure must not drag the floor down to admit failing configs.
        candidates = [c for c in group if c.mean_value >= best_value - margin]
        # Minimise the chosen axis; tie-break by the other binding axes then name so equal
        # cells never reorder (an unmeasured axis sorts +inf → deprioritized, not chosen).
        rec = min(
            candidates,
            key=lambda c: (
                _advise_axis_value(c, x_axis),
                c.mean_latency,
                c.mean_cost,
                c.model,
                c.effort,
            ),
        )

        vacuous = best_value <= margin
        suspect = _cell_suspect(reflex_cell) or _cell_suspect(rec)
        cost_saving = reflex_cell.mean_cost - rec.mean_cost
        latency_saving = reflex_cell.mean_latency - rec.mean_latency
        quality_delta = rec.mean_value - reflex_cell.mean_value
        # Both must be FULLY measured (partial coverage → None), so a saving is never
        # computed against a partially-unknown total (mirrors the frontier's x_value guard).
        reflex_tp = _usable_total_tokens(reflex_cell)
        rec_tp = _usable_total_tokens(rec)
        throughput_saving = (
            reflex_tp - rec_tp if reflex_tp is not None and rec_tp is not None else None
        )
        axis_saving = {
            "latency": latency_saving,
            "throughput": throughput_saving,
            "cost": cost_saving,
        }[x_axis]

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
                best_value=best_value,
                quality_delta=quality_delta,
                cost_saving=cost_saving,
                cost_multiple=(
                    (reflex_cell.mean_cost / rec.mean_cost) if rec.mean_cost > 0 else None
                ),
                latency_saving=latency_saving,
                n_epochs=rec.n_epochs,
                vacuous=vacuous,
                suspect=suspect,
                reflex_fallback=bool(fallback_note),
                reflex_total_tokens=reflex_tp,
                rec_total_tokens=rec_tp,
                throughput_saving=throughput_saving,
                x_axis=x_axis,
                note=_advice_note(
                    reflex_cell,
                    rec,
                    quality_delta,
                    axis_saving,
                    x_axis,
                    margin,
                    len(group),
                    vacuous,
                    suspect,
                ),
            )
        )
    # Order by the chosen axis's saving descending (biggest win first); an unmeasured
    # throughput saving (a pre-token ledger) sorts last.
    advice.sort(
        key=lambda a: (
            a.latency_saving
            if a.x_axis == "latency"
            else (
                a.cost_saving
                if a.x_axis == "cost"
                else (a.throughput_saving if a.throughput_saving is not None else float("-inf"))
            )
        ),
        reverse=True,
    )
    return advice
