# Comprehensive pre-Phase-C ship-review — 3 lenses, 9 sources

*2026-07-01. Before running the public showcase (Phase C), the whole series (PRs #5–#8,
34 files, +1195/−68) went through a full ship-review: **correctness** (3-voice adversarial
engine over three commit ranges — codex gpt-5.5@xhigh + Gemini 3.1 Pro + a blind Claude
pass — plus a full cold read of all 22 modules and docs-truth / test-integrity subagent
audits), **methodology** (an adversarial statistics audit that computed against the
production code, cross-checked by an independent codex+gemini consult), and a
**portfolio cold-read** (senior-engineer skim + a README walk-through that executed every
claim it could without spending quota). Previously-"defended" judgments were re-litigated
with no exemptions. Roughly 35 findings were confirmed and fixed; 8 were refuted by
grounding. Fixes landed in two tranches: the PR #8 branch (demo/grid surface) and this
follow-up PR (methodology core, robustness, tests, docs).*

## The headline: the compare verdict was statistically unsound

The audit ran the production `block_bootstrap_on_folds` on same-sign diff vectors:
**a same-sign percentile-bootstrap CI excludes 0 by construction at any magnitude**
(diffs of `1e-6` → `real=yes`). Under a symmetric null, the old rule's Type-I error was
**20.6% at n=4** (15.4% at n=5, 14.3% at n=6) against a nominal 5% — and the code's own
n=2–3 "tautology guard" comment stated the exact argument that indicts n=4. The public
showcase had been designed to exactly 4 pairs.

**Fix (consult-confirmed by both external models):** `compare` verdicts now use the
**exact sign-flip permutation test** (all `2^n` sign assignments of the nonzero diffs;
zero diffs excluded and reported as `n_nonzero`; `real = p ≤ 0.05`; min two-sided
p = `2/2^n`, so ≥6 nonzero pairs are required). The bootstrap CI is demoted to
effect-size context. The showcase grid gained `opus [low, high]` → 6 matched pairs, so
the designed positive control can reach an honest `real=yes` at p = 0.031 — satisfying
one consulted model's "the flagship must exercise the real-verdict path" and the other's
"don't threshold-engineer" (opus was always part of the model axis; the framing stays
*designed positive control*).

## Other methodology verdicts (each computed, then fixed or reworded)

| Finding | Evidence | Fix |
|---|---|---|
| `unparseable` grades were **excluded** from aggregation, inflating the surviving mean | a +0.667 delta published as +1.000 in the audit's reconstruction | count them as their honest 0.0 (`⚠Nunp` surfaced per cell); only `grader_error` (re-gradable) stays out |
| the n=3 "95% CI" is arithmetically the min–max epoch range | actual coverage 0.74 vs nominal 0.95; `sd(ddof=1)` CV ≈ 52% at n=3 | relabelled *epoch range* below 5 epochs (report legend + docs) |
| the "leakage gate" could never fire and cannot detect real leakage | 0.15 band = 32.6σ of the mean-of-200 statistic; a *perfect* leak (`y_pred == y_true`) still shuffles to 0.5023 | reframed as a **metric-pipeline self-test** (METHODOLOGY + CLAUDE.md); band tightened to 0.05 (≈11σ, still zero false-flag, 3× more sensitive to gross breakage) |
| anchor grader gaming hole | `"the"×3` and `"Project Vega"×3` (leaked by the T4 prompt itself) scored **1.0** | anchor **v2**: ≥3-word quotes, distinct-only counting (version bump → free re-grade) |
| partial re-grades silently mixed metric definitions within a cell | latest-grade-per-run dedupe crosses grader versions with no flag | `⚠VER` flag (`n_grader_versions > 1`) |
| `estimate` skews low on mixed grids | 2.15–4.8× under-projection on the showcase mix | wording: "floor … commonly 2–5×" (a two-cell floor/ceiling bracket recorded as backlog) |
| Pareto tie semantics; anchor denominator; strict edge-trim | ties both-flagged (standard weak dominance); `max(expected, n)` resists gaming; edge-trim re-judged **upheld** | sound — no change |

## Correctness fixes (engine + deep read + test-integrity)

- **expand_grid regression** (introduced by the repo-matching fix): one malformed variant
  string aborted the whole expansion → now drops-and-logs like every invalid combo.
- **Infra circuit breaker:** 5 consecutive `infra_error`/`timeout` runs halt the sweep
  (resumable) — a broken environment no longer burns every remaining cell at up to
  `timeout_s` each.
- **`$T1_HOLDOUT_PATH` was dead as shipped** (a task-pinned `gold_parquet` beat it →
  fresh readers hit `FileNotFoundError` before cell 1): the env var now wins; the
  personal default path was removed from the task YAML and the package default
  neutralized to `data/t1_holdout.parquet`.
- **`pareto_scatter` was variant-blind** (3-voice consensus): now one colour per
  `(model, variant)` series — the showcase A/B is distinguishable.
- Loud CLI signals: a task whose cells all drop is announced; `failed > 0` prints a red
  warning; a requested `--a/--b` forest with no overlap says so; `--format` and task-id
  filenames are validated/slugified.
- Drift test made bidirectional (SKILL.md body **==** gold), the materialized worktree's
  skill content asserted, the showcase pinned to `≥ MIN_PAIRS_FOR_REAL` *paired* configs,
  `setup.sh` marker ordering fixed (no permanent clobber-refuse after an early crash).
- Test hygiene from the integrity audit: exact `<msg>` block structure (the `>=`
  weakening was itself a reviewed-and-overturned earlier "fix"), `monkeypatch.undo()`
  before module reload, estimate-stub arg capture + a live-call escape guard, CLI command
  set-equality (it had already rotted once), `GRADER_NAMES`↔`_PREPARERS` parity, fixture
  purity, runner argv value-pairing, plot CI-branch coverage.

## Docs-truth + portfolio (16 inaccuracies + leak-scrub)

`experiments/log.txt` and the METHODOLOGY audit trail brought current; `xhigh`→`max`;
"3 graders"→4; README gained `plot`, the showcase link, the T1 prerequisite, and the
honest T2 flat-skill caveat; `CONTRIBUTING.md`'s backwards install text fixed; private
fleet paths removed from tracked docs (principles inlined); "private research_toolkit"
corrected to the author's *public* repo; `make install` now installs the `plot` extra.
Cold-read verdict: *"strong hire-signal … one committed real result away from
excellent"* — that result is Phase C. **Phase-C note:** any committed ledger must scrub
the provenance fields (`mcp_servers`, `global_layer`, absolute paths), not just text.

## Refuted by grounding (kept for the record)

gemini: gitleaks-`git`-invalid (×2 — the command exits 0 and the CI job is green),
setup.sh unborn-HEAD crash (runs clean), `np.mean([])` (unreachable behind the overlap
filter), anchor non-dict `AttributeError` (`_parse_claims` filters), inverted-grader
(quote-in-source, tested). codex: zsh-tilde in the README's env-prefix form (expands),
pre-commit golang hook needs system Go (pre-commit bootstrapped its own toolchain,
hook passed), `init.defaultBranch=without-skill` breaking `checkout -b` (exit 0).
And one of ours: the `>= 10` closer-count defense from PR #6 — overturned unanimously.
