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

## Showcase pre-registration (2026-07-02, committed before any sweep cell ran)

The public showcase (`grids/showcase.yaml`: 54 cells; headline = the t4 skill A/B over 6
matched (model, effort) config pairs) runs under the following rules, fixed in advance:

- **What the A/B measures:** *prompt-directed skill consultation.* The T4 prompt names the
  `project-reference` skill and asks Claude to consult it; the same prompt runs in both arms
  and only the infra differs (the `with-skill` ref ships the skill, `without-skill` doesn't).
  This is a designed positive control for the harness's detection machinery — it is **not**
  a test of autonomous skill discovery, and is never described as one.
- **First-run primacy:** the first full run of the pre-registered spec is the published
  primary outcome, whatever its verdict. If any of the 6 pairs has a zero diff
  (`n_nonzero < 6` → `real=no` is mechanically forced), that verdict is published and the
  failing configs are characterized from their transcripts. Any re-run under an amended
  spec is a clearly labeled follow-up, reported alongside — never replacing — the primary,
  and is written to a **fresh ledger file** (aggregation dedupes grade rows per `run_id`,
  not per spec; mixing specs in one ledger would silently average them).
- **Completeness gate:** the headline verdict is only quoted if all 6 configs have 3/3
  epochs `ok` in **both** arms; otherwise the incompleteness is reported first.
- **Retry policy:** two mechanisms, both bounded. In-cell: transient `rate_limited` backs
  off and retries (hard usage caps halt the sweep, resumably). Across the sweep: at most
  **2 resume passes** (`ablation run` re-invocations; settled cells skip) for
  `rate_limited`/`timeout`/`infra_error` cells, then whatever remains is published as-is
  with its failure rate.
- **Hermetic cells (tool-minimal by construction):** every cell runs with
  `--strict-mcp-config` (no user MCP servers) and disallows the full escape surface —
  `Bash Read Grep Glob Task WebSearch WebFetch Write Edit NotebookEdit` — so a cell sees
  only its prompt, its worktree's auto-loaded memory/skills, and the `Skill` tool. This
  is not paranoia: in the extended pilot, a control-arm opus cell *ran Bash* under
  headless defaults and grepped beyond its worktree, locating host files that contain
  the gold (prior sessions' transcripts; the public repo is one `curl` away). Web-tool
  denial alone is not a boundary. The showcase sweep also materializes worktrees
  **outside** the harness repo (`--worktree-base`) so the harness's own `CLAUDE.md` is
  not ancestor memory for any cell, and the fixture README is neutral: the subject model
  is never told the experiment design, its arm, or the expected outcome. (The ledger's
  `mcp_servers` provenance field records what the *host environment* configures, not
  what a cell loads — cells load none.)

## Audit trail

| Date | Check | Result |
|------|-------|--------|
| 2026-06-25 | Phase 0 scaffold | repo stands up; smoke test green |
| 2026-06-25 | Phases 1–2 + reviews | live subscription cell; run/grade decoupled; silent-failure sweep (10 findings fixed) |
| 2026-06-26 | Phases 3–5 + methodology audit | spec_sha resume honesty; stale-grade dedupe fix; live 4-cell smoke 4/4 → report → resume |
| 2026-07-01 | Phases A/B/D (PRs #5–#8) | install + CI honesty; coverage floor 90 + gitleaks; plotting; demo-infra A/B; the flat-skill probe |
| 2026-07-01 | Comprehensive 3-lens ship-review | exact sign-flip verdicts; unparseable = honest 0; epoch-range labeling; leakage self-test reframing; anchor v2 floor — `docs/design/2026-07-01_comprehensive-review.md` |
| 2026-07-02 | Checkpoint adversarial review (pre-sweep, 3 voices) | mechanism wording fixed; first-run primacy replaces the rescue amendment; fixture README neutralized; hermetic cell flags + out-of-repo worktrees; this pre-registration committed before the sweep — `docs/design/2026-07-02_checkpoint-review.md` |
| 2026-07-02 | **Public showcase run** (54 cells, the pre-registered primary) | 54/54 `ok`, 0 unparseable, 18/18 configs at 3/3 epochs; t4 A/B: 6/6 pairs 0.0→1.0, Δ=+1.000, exact p=0.0312, `real=yes`; t3 saturated at 1.000; sanitized ledger (`results/showcase.jsonl`, sanitizer caught a live absolute-path leak in `infra_repo` on first use) + figures committed |
