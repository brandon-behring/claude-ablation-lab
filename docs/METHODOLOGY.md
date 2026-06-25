# Methodology

How the harness produces trustworthy numbers. Expanded as phases land.

## Measurement model

- **Unit of measurement:** a *cell* = `(task, model, effort, variant, epoch)`. Each cell is one headless `claude -p` run, graded to a score in `[0, 1]`, with `cost_usd`, `latency_s`, and a `status`.
- **Variant = `infra_repo@ref`:** the configuration under test, materialized as a git worktree so a run loads exactly that project's `CLAUDE.md`/`.claude`.
- **Comparability metric:** `total_cost_usd` (API-equivalent; on a subscription it is a metric, not a charge). Optimize *cheapest-per-successful-outcome*, not cheapest-per-token.

## Statistical discipline (from the eval_harnesses dossier)

- `epochs ≥ 3` resampling per cell; report **mean ± bootstrap CI**, never a point estimate.
- `compare` uses **paired bootstrap / permutation** to test whether a variant delta is real.
- `infra_error` / `timeout` / `rate_limited` cells are **excluded from quality aggregation** but their rate is always reported (don't mistake infra failure for model failure).

## Leakage / sanity gates (data_leakage_prevention.md)

- **T1 shuffled-label control:** shuffle gold labels → AUROC must collapse to ~0.5. If not, the harness/grader leaks → halt.
- Graders are tested against known input+gold before any number is trusted.

## Audit trail

| Date | Check | Result |
|------|-------|--------|
| 2026-06-25 | Phase 0 scaffold | repo stands up; smoke test green |
