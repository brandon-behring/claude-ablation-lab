# Methodology

How the harness produces trustworthy numbers. Expanded as phases land.

## Measurement model

- **Unit of measurement:** a *cell* = `(task, model, effort, variant, epoch)`. Each cell is one headless `claude -p` run, graded to a score in `[0, 1]`, with `cost_usd`, `latency_s`, and a `status`.
- **Variant = `infra_repo@ref`:** the configuration under test, materialized as a git worktree so a run loads exactly that project's `CLAUDE.md`/`.claude`.
- **Comparability metric:** `total_cost_usd` (API-equivalent; on a subscription it is a metric, not a charge). Optimize *cheapest-per-successful-outcome*, not cheapest-per-token.

## Statistical discipline

- `epochs ≥ 3` resampling per cell; report the mean with an across-epoch interval.
  **Honesty note:** below 5 epochs the percentile bootstrap degenerates to the min–max
  epoch range (~74% coverage at n=3), so the report labels it an *epoch range*, never
  a 95% CI.
- `compare` verdicts use the **exact sign-flip permutation test** on the mean paired
  delta over matched (model, effort) configs: `real` = `p ≤ 0.05`, zero diffs excluded
  (`n_nonzero` reported; min two-sided p = `2/2^n`, so ≥6 nonzero pairs are needed for
  a verdict). The paired-bootstrap CI is **effect-size context only** — a same-sign
  percentile-bootstrap CI excludes 0 by construction at any magnitude (measured
  Type-I ≈ 21% at n=4), so it must never be the decision rule (2026-07-01 audit).
- `infra_error` / `timeout` / `rate_limited` cells are **excluded from quality
  aggregation** but their rate is always reported (don't mistake infra failure for
  model failure). `unparseable` grades are the opposite case — the model produced
  ungradeable output — and count as their honest **0.0** (surfaced per cell as ⚠unp).

## Self-tests and leakage defenses

- **T1 shuffled-label self-test:** shuffle gold labels at grading time → AUROC must
  collapse to ~0.5; a deviation flags ⚠LEAK → halt and inspect. **Honest scope
  (2026-07-01 audit):** permuting labels over *fixed* predictions can only catch a
  broken permutation/metric implementation — a genuine gold-leaked-into-prompt leak
  still shuffles to ~0.5. The real leakage defenses are the holdout design and the
  grader tests; the gate's band (`LEAKAGE_BAND = 0.05` ≈ 11σ of the mean-of-200
  statistic) is a zero-false-flag tripwire for gross breakage.
- Graders are tested against known input+gold before any number is trusted, and carry
  anti-gaming floors (anchor v2: ≥3-word **distinct** quotes only).

## Audit trail

| Date | Check | Result |
|------|-------|--------|
| 2026-06-25 | Phase 0 scaffold | repo stands up; smoke test green |
| 2026-06-25 | Phases 1–2 + reviews | live subscription cell; run/grade decoupled; silent-failure sweep (10 findings fixed) |
| 2026-06-26 | Phases 3–5 + methodology audit | spec_sha resume honesty; stale-grade dedupe fix; live 4-cell smoke 4/4 → report → resume |
| 2026-07-01 | Phases A/B/D (PRs #5–#8) | install + CI honesty; coverage floor 90 + gitleaks; plotting; demo-infra A/B; the flat-skill probe |
| 2026-07-01 | Comprehensive 3-lens ship-review | exact sign-flip verdicts; unparseable = honest 0; epoch-range labeling; leakage self-test reframing; anchor v2 floor — `docs/design/2026-07-01_comprehensive-review.md` |
