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
- `advise` gives the last-mile **cost verdict** over the `report` cells: per (task, variant), the
  cheapest config within `margin` of the **best** config that ran, plus the dollars and latency
  saved versus a reflex config (e.g. `opus/max`). Flooring at the *best*, not the reflex, is
  deliberate — a reflex that itself fails must not drag the floor down to admit a cheaper *failing*
  config. Non-inferiority is a **margin** on the mean (`mean_value ≥ best − margin`, default `0.02`),
  a point estimate rather than a p-value: `advise` sees only per-cell epoch means, and at these epoch
  counts a per-cell test is underpowered — *not* because a paired test is impossible in principle
  (the configs share the same task examples), but because the per-example/per-epoch scores are not
  plumbed to this layer (`report` still carries the bootstrap CI for the uncertainty). Honesty rails:
  the recommendation's **absolute** quality and its delta vs the reflex are both shown; a group whose
  best config scores ≤ margin is flagged `n/a` and kept out of the overpay total; a cell carrying a
  `report` validity flag (leakage / mixed spec / grader-version / unparseable) is flagged `⚠suspect`;
  the reflex falls back transparently (`opus/max` → highest `opus` effort that ran → priciest cell,
  flagged `*`); and `latency saved` may be negative (cheaper yet slower) — shown signed, never hidden.
  **Scope:** this sees model×effort cost/latency-vs-quality only — workflow-level spend (multi-agent
  review, planning rounds) is outside a single-task harness.
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

## Discriminating tasks (books-validate: t5 / t6)

The showcase tasks (t3/t4) are **saturated** — every config scores ~1.0, which proves the
plumbing but cannot show where a cheaper model *loses* quality. `books-validate` is the first task
built to **discriminate**: fix a seeded-broken MDX chapter against book-scaffold editorial
conventions, on a ladder from mechanical (a malformed `<BookLink>`) through fuzzy near-miss ids to
**semantic** items (the correct `<XRef>` is derivable only by reading which section the prose is
about) and a required **citation addition** (prose names a source with no `<Cite>` tag). It ships in
two shapes over one fixture: `t5` (single-turn, chapter in the prompt) and `t6` (agentic, the model
edits the chapter in a `.books-validate@v1` worktree and can run the fidelity validator itself).

Scoring is **anti-gaming by construction** (a pre-build adversarial design review drove every choice):
- **Checklist, not violation-count.** Score = mean over N=15 required-correct items, so an empty or
  tag-deleted submission scores **0** — it cannot beat an honest half-fix. The gradient is verified:
  `empty/delete → 0.0`, `do-nothing → 0.5`, `fix-only-what-the-validator-sees → 0.77`, `full → 1.0`.
- **Partial credit on the semantic rungs** ({0, ½, 1}: a valid-but-wrong-*family* id = ½). The
  per-epoch score quantum is ½/15 = 1/30 ≈ 0.033, but `advise` compares config *means over 3 epochs*,
  whose lattice is ½/45 = 1/90 ≈ 0.011 — below the 0.02 margin, so a config one-half-credit behind on
  a single epoch is still "within margin," while a config that reliably misses an item (mean ≥ 0.033)
  is correctly excluded. A pure-binary ladder (quantum 1/15 = 0.067) would degenerate "within margin"
  into "exact tie" and read config noise as "opus earns its keep."
- **Census is excess-only with anti-spray headroom** (max = gold-count + 2, floored at ≥1) — spraying
  ids fails the count, but a single benign extra tag doesn't, an omission is charged once by its own
  item, and an empty doc earns no free census credit.
- **Not every item discriminates — some are gates.** The 2 census + 3 tripwire items score ~1.0 for
  any non-adversarial submission; they exist to catch *gaming* (spray) and *regression* (breaking an
  already-correct element), not to separate strong from weak models. Discrimination lives in the 9
  mechanical/fuzzy/semantic/addition rungs; the ladder is honestly "9 discriminating + 6 gate" of 15.
- **Anti-gaming hardening (a 3-voice pre-commit review, findings confirmed by running the checker):**
  tags inside comments or code fences are stripped before scoring (a commented-out chapter scored
  15/15 before this); a duplicated prose anchor is rejected (a preamble carrying a correct tag can't
  farm an item while the body stays broken); a `<CodeRef>` must cite an in-range line on the *expected*
  file (deleting the line, or repointing to another valid file, no longer passes); brace-quoted values
  (`id={"x"}`) are normalized so a correct value isn't mis-scored for its delimiter style.
- **Agentic (t6) confidentiality is NOT enforced — sandbox required.** t6 grants Bash, and the
  grade-time answer key lives in the repo and its public mirror, so an un-sandboxed cell can `curl`/
  `cat` the gold and saturate the task (git-worktree write-isolation is not read-confidentiality).
  **t5 (no tools) is the clean pilot;** run t6 only under an OS sandbox that blocks egress + parent-FS
  access, or after the answer key is moved out of the checkout.
- **The answer key never reaches the model.** `expected.json`/`check.py` are grader-only; the
  worktree and prompt get a *fidelity* validator (`validate_fixture.py`, a faithful `validate.mjs`
  subset) that is blind to semantics — the gap between "passes the validator" and "understands the
  chapter" is the discrimination signal.
- **The verdict is trusted only from the final `CHECK PASSED`/`FAILED` line, cross-checked against
  the exit code** (no echo-injection); degenerate outputs (oversize, NUL) score a deterministic 0.0,
  never `grader_error` (which would exclude them from the mean). A fixture edit changes both `spec_sha`
  (via a content hash in `gold`) and the grader `version` (via a rubric hash) — no silent metric-mixing.
- **Fairness gate:** before any quota, independent blind solvers attempt the fixture; an item on which
  competent solvers disagree measures grader ambiguity, not model quality, and is rewritten. Two such
  items (self-referential XRefs a figure could equally satisfy) were caught and fixed exactly this way.

**Run done (t5, 2026-07-03):** the 27-cell `t5` sweep (3 models × {low, high, max} × 3 epochs) fed
`ablation advise --reflex opus/max`, and the discriminating task did its job. It **separates** (haiku
~0.10 below the field — not saturated) yet the opus/max reflex **does not earn its keep**: opus/max
(0.978) ties sonnet/high (0.978) to four decimals at 3.6× the cost and ~200s more latency, and `max`
effort was waste on every model. So the "opus earns it on hard authoring" hypothesis is tested and
falsified on this probe. (`t6` stays sandbox-gated — not run.)

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
  denial alone is not a boundary — and the deny-list is a deny-list, not a sandbox:
  every run's transcripts are checked behaviorally on top of it. Worktrees materialize
  **outside any repo by default** (`~/.cache/claude-ablation-lab/worktrees`,
  `--worktree-base` to override) so the harness's own `CLAUDE.md` is not ancestor memory
  for any cell, and the fixture README is neutral: the subject model is never told the
  experiment design, its arm, or the expected outcome. (The ledger's `mcp_servers`
  provenance field records what the *host environment* configures, not what a cell
  loads — cells load none.) **Post-run hardening (PR #11 review):** cells now also run
  `--no-session-persistence`, so gold-bearing session files stop accumulating on the
  host at the source; the published 2026-07-02 run predates that flag — its session
  files are exactly what the per-cell mechanism evidence was harvested from, and the
  tool denials closed the access path. **Task-scoped relaxation (D6, 2026-07-02):** an
  *agentic* task (one that legitimately needs Bash/file tools, like T2) declares exactly
  what it needs via `tools:` in its task YAML; the preparer computes that task's
  effective deny-list (the hermetic catalog minus the declared tools) and the runner
  applies it per-cell — the showcase tasks declare none and keep the full hermetic
  default. **In-band mechanism capture (D6):** `--no-session-persistence` made the
  post-hoc session-file harvest above impossible for any future sweep, so a real sweep
  now runs with `--output-format stream-json` by default and records each cell's actual
  tool invocations directly on its ledger row (`tool_calls`) — mechanism evidence no
  longer depends on transcripts surviving on the host.

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
| 2026-07-02 | D6 hardening (PR #11 review follow-ups) | task-scoped tool policy (agentic tasks declare `tools:`, no more hand-relaxing the runner); tool deny-list catalog live-verified against the installed CLI, catching a dead `"SlashCommand"` entry that gave zero actual protection; in-band mechanism capture via `stream-json` replaces the now-impossible session-file harvest; sanitizer inverted to an allow-list (`KEEP_FIELDS`) — `docs/design/2026-07-01_phase6-deferrals.md` §D6, `docs/design/2026-07-02_t2-runway.md` |
| 2026-07-02 | D6 hardening — 3-voice adversarial review (codex + gemini + blind Claude subagent) | caught and fixed a live regression this PR would otherwise have shipped: `capture_mechanism`'s new default combined with T1's `--json-schema` would have silently broken every T1 cell (`--json-schema` is implemented as a synthetic `StructuredOutput` tool call, confirmed live, which the hermetic default was about to deny); also fixed `task.tools` YAML validation, `spec_sha` now covering tool-policy changes, `tools_used`/`tool_calls` `None`-vs-`{}` (not-measured vs. measured-zero) semantics, `estimate` sharing `run`'s version gate, and a CI-portability bug (the version gate broke on any machine without a `claude` binary matching the pin, including GitHub Actions — caught by literally stripping `claude` from `PATH` and re-running the suite). 5 findings refuted with evidence. Full tally — `docs/design/2026-07-02_d6-review.md` |
| 2026-07-02 | `advise` — cost-frontier verdict (Phase 1) + 3-voice review | turns `report` into the "where am I overpaying?" answer: cheapest config within `margin` of the **best** that ran, saving vs a reflex config, with a transparent fallback. On the committed showcase, opus→haiku is **11–15× cheaper for +0.000 quality** (Σ $0.1704) on the saturated t3/t4 tasks; the without-skill control is flagged `n/a` and excluded. A codex + gemini + blind-Claude review then fixed, before merge: a **best-floor** selection bug (a failing reflex could recommend a cheaper *failing* config), the vacuous-row Σ inflation (37% of the headline came from an all-zero control), a mislabel and dead `note` path, strict reflex parsing, and an **overclaim** — the margin decision is a data-plumbing + low-power limit, *not* "a p-value would be theatre" (configs do share task examples). Honest scope: overpay on *easy* work only — a discriminating task is the next build. Zero new quota; 282 tests green — `docs/design/2026-07-02_cost-benchmark-map.md` |
