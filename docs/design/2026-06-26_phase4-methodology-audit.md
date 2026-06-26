# Phase 4 methodology audit — report + compare (2026-06-26)

A methodology+correctness review ran on `dc3abe8` (the analysis layer — the
numbers a user trusts to decide "did my change help?"). Four findings, all about
**statistical honesty**; all fixed with tests.

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| F1 | HIGH | `_LATEST_OK` filtered `grade_status='ok'` *before* the per-run dedupe window → a run whose **latest** grade is `grader_error` silently fell back to an older `ok` score (the stale number a re-grade meant to replace). | Filter `run_status='ok'` first → rank by `ts DESC, grader_version DESC` → take latest → **then** require `grade_status='ok'`. A run whose latest grade failed now drops out entirely. |
| F2 | MED-HIGH | `report` averaged per-epoch bootstrap **CI endpoints** — not a valid CI of anything (docstring even claimed "verbatim"). | Aggregate epoch arrays in Python; report a real **across-epoch bootstrap CI of the mean** (`block_bootstrap_on_folds`), only at ≥ `MIN_EPOCHS_FOR_CI` (3). `sd_value` carries epoch spread. |
| F3 | MED-HIGH | `compare` called a delta "real" at `n_pairs=2`: two same-sign diffs ⇒ the bootstrap CI excludes 0 **by construction** (a tautology, not evidence) — over-claiming significance, the worst failure for this tool. | `real` requires `n_pairs >= MIN_PAIRS_FOR_REAL` (4); below that the CI is shown for context but the verdict is withheld with an explanatory note. |
| F4 | MED | Leakage gate thresholded the **mean** shuffled-AUROC → one leaky epoch among clean ones was masked. | Gate on the **max** `\|shuffled − 0.5\|` across the cell's epochs (a safety check fires on the worst, not the average). |

Also addressed: deterministic dedupe tie-break (`grader_version DESC`); a docstring
note that `compare` applies no multiple-comparison correction across tasks (v1).

Verified correct (not changed): Pareto dominance test; B−A delta direction +
rendering; infra-failed runs excluded from aggregation by design.

Result: 168 tests (+5 regression: stale-grade exclusion, worst-epoch leakage,
≥3-epoch CI, ≥4-pair floor); coverage 94%; ruff + black + mypy --strict clean.
