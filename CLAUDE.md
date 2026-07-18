# claude-ablation-lab — working conventions

> Last updated: 2026-07-11

A personal **model × thinking-effort × config** ablation/regression harness for Claude Code. **Mission (reframed by the 2026-07-11 audit): capability-worth on real work** — which config is worth it for which task across *quality, latency, efficiency (token/context burn), and context hygiene*, with API-equivalent USD retained only as one comparability axis (the subscription is flat, so dollars are not the constraint that binds). Prove where cheaper configs are safe and where the expensive reflex earns its keep; the infra A/B machinery exists to make that measurement honest. See [README.md](README.md) and the [2026-07-11 big-picture audit](docs/audits/2026-07-11_big-picture-mission-and-roadmap.md). Build history: `experiments/log.txt` + per-phase reviews in `docs/design/` + audits in `docs/audits/`.

## Grounding principles

This harness IS a DS/ML experiment system: Exploration → Development → Deployment
discipline, leakage prevention via controls, real tests only, Protocols not ABCs +
frozen dataclasses + stdlib exceptions, conventional commits ending
`Co-Authored-By: Claude <model> <noreply@anthropic.com>`. (Distilled from the
author's private pattern library; the principles are stated here so the repo is
self-contained.)

## Conventions (style)

Enforceable style is **config — the single source of truth** (`pyproject.toml`: ruff `E,F,I,W,UP,B,N,SIM,C4,S101` + black@100 + mypy-strict); don't restate it in prose (drift). Dev flow + commit/issue conventions → `CONTRIBUTING.md`. Repo-specific **non-enforceable** rules:
- Seams = `@runtime_checkable Protocol`; results = `@dataclass(frozen=True, slots=True)` (`RunResult`/`Score`/`Worktree`); stdlib exceptions only.
- **numpydoc** docstrings for graders/runner/stats (Parameters/Returns/Raises); plain prose for CLI/orchestration.
- `status` taxonomy `ok|rate_limited|infra_error|timeout|parse_fail` — infra/throttle is never counted as model quality.
- Ledger rows keyed by `grader_version` (re-grade without re-running); `reset_clean` operates only on linked worktrees (`.git` is a file).
- Coverage tiers: graders/stats 90%+, runner/worktree 80%+, CLI best-effort.

## Current phase: DEVELOPMENT (phases 0–6 complete; Phase C showcase SHIPPED 2026-07-02)

Post-phase arc (not in the numbered phases): `advise` cost-frontier verdicts → the 2026-07-03 spend audit + t5/t6 `books-validate` (first discriminating task) → the 2026-07-04 pressure test (t7/t8, exact-match graders; **no opus edge _detected_ on any single-turn checkable probe** — "not detected at this power," not "absent," and scoped to saturated single-turn tasks) → the 2026-07-06 independent audit (Pareto plumbing: tokens persisted, cost/latency intervals, selectable frontier axis; `docs/audits/2026-07-06_independent-audit.md`) → the **2026-07-11 big-picture audit** (mission reframe + construct-validity findings; `docs/audits/2026-07-11_big-picture-mission-and-roadmap.md`). The LLM-judge pairwise phase has been **piloted on the PR #18 branch but is not yet validated** (the human spot-check gate `results/judge_spotcheck.md` is unfilled) — provisional, not a headline (`docs/plans/active/2026-07-06_llm-judge-phase.md`).

**Hybrid rigor (decided):** correctness-critical code is Development-grade *from day 1*; orchestration was Exploration-loose until the loop was proven — now graduated (the run→grade→ledger→report loop is verified live end-to-end).

| Code | Rigor | Coverage |
|------|-------|----------|
| `graders/`, eval-toolkit stats, leakage gate | Development (tested + typed) | 90%+ |
| `runner.py`, `worktree.py`, `grid.py`, `ledger.py` | Tested at the seams | 80%+ |
| `analyze.py`, `cli/` | Exploration-loose, then graduate | best-effort |

| Checkpoint | Status |
|------------|--------|
| Leakage gate (T1 shuffled-label) | Operationalized as a **metric-pipeline self-test** (Phase 4 — flags at aggregation, `analyze.LEAKAGE_BAND`; see the honest-scope note below) |
| Test coverage | CI floor 90 (enforced; don't quote a point estimate this file can't verify) |
| Pre-commit hooks | `make hooks` (ruff/black/mypy + gitleaks + detect-private-key) |

## Core principles

1. **Never fail silently** — explicit errors with diagnostics; never `assert` in `src/`.
2. **Separate infra failure from model failure** — CLI/tool/throttle errors get a distinct `status` (`infra_error`/`timeout`/`rate_limited`) and are excluded from quality aggregation but always reported.
3. **Never trust a point estimate** — `epochs` resampling + bootstrap CIs; report mean±CI.
4. **A buggy grader poisons every number** — graders are tested before any result is believed.
5. **Reproducible** — seed RNG; stamp `claude_version`, harness `git_sha`, and `infra_repo@sha` on every ledger row.

## The shuffled-label self-test (T1)

Shuffle the gold labels and re-grade — AUROC must collapse to ~0.5; a deviation flags the run (⚠LEAK) → halt and inspect. **Honest scope** (2026-07-01 methodology audit): because the permutation happens at *grading* time over fixed predictions, this is a **metric-pipeline self-test** — it catches a broken permutation/metric implementation, not gold-leaked-into-prompt leakage (a perfect leak still shuffles to ~0.5). Real leakage defenses are the holdout design and the grader tests.

## Substrate & key concepts

- **Runner** drives `claude -p --model <m> --effort <e> --output-format json --max-budget-usd <cap>` with `cwd` = the variant's worktree.
- **Variant = `infra_repo@ref`**, materialized as a persistent `git worktree` per `(repo, ref)` (reused across the sweep). Infra-agnostic tasks set `infra_repo: null`.
- **Grid** = tasks × valid `(model, effort)` pairs × variants × epochs. Validity has two layers: (1) a **provider capability matrix** (`grid._EFFORT_CAPABILITY`, consulted inside `effort_ok`) that drops provider-inert pairs with a warning at grid-load — **Haiku 4.5 has no effort parameter** (documented: absent from every effort-support list; it uses `budget_tokens`, not adaptive-thinking effort), so `haiku/{low..max}` all resolve to one default config and are dropped rather than run as redundant paid cells; and (2) the per-grid `effort_support` budget-narrowing on top. The CLI *accepts* every alias×effort pair (re-probed 2026-07-06 on CLI 2.1.201, 20/20 minimal cells ok), but acceptance ≠ *application*: the refresh data shows haiku/xhigh ≈ haiku/high (silently clamped), which the matrix now encodes. ⚠ CLI footgun: an *unknown* effort value warns and silently runs at the default — never trust an effort label you didn't validate.
- **Ledger** = append-only JSONL (resumable: skip completed cells) + `results/transcripts/<run_id>.json` sidecars.

## Project structure

```
src/claude_ablation_lab/  runner.py worktree.py task.py t1_dataset.py prepare.py grade.py graders/ grid.py ledger.py orchestrate.py provenance.py analyze.py plot.py showcase.py cli/main.py
tasks/                    t1_prompt_injection t2_research_plan t3_verbatim_anchor t4_demo_infra t5_books_validate t6_books_validate_agent t7_find_bug t8_hard_math (.yaml)
grids/                    smoke v1 showcase books-pilot pressure-test claude5-refresh (.yaml)
examples/                 demo-infra/ (showcase A/B fixture) books-validate/ (discriminating constrained-MDX-repair probe)
tests/                    conftest.py fixtures/ test_*.py
experiments/log.txt       one-line experiment log
docs/                     plans/active/ audits/ design/ METHODOLOGY.md
results/  data/  .worktrees/   (gitignored except results/showcase.jsonl + dated sanitized release snapshots, e.g. results/claude5-refresh-2026-07-06.jsonl)
```

## Key commands

```bash
make install   # eval-toolkit (pinned from GitHub; editable via EVAL_TOOLKIT=) + this package [dev]
make ci        # ruff + black + mypy + pytest
pytest -m "unit or golden"     # fast loop
pytest -m integration          # tests that shell out to claude / git
ablation run|regrade|estimate|report|compare|plot ...
```

## Reuse map (don't re-derive)

- `eval-toolkit` → bootstrap CIs, paired-bootstrap diff, AUROC/PR-AUC (pinned from GitHub by `make install`; editable via `EVAL_TOOLKIT=`).
- `research_toolkit/validators/research_plan.py` → T2 grader (subprocess; `validate(path)->list[str]`).
- T1 gold → any balanced `text`+`label` parquet via `$T1_HOLDOUT_PATH` (wins) / `params.gold_parquet` / `data/t1_holdout.parquet`; e.g. a split from the public MIT-licensed `prompt-injection-detection-prototype`.

## Upstream-friction discipline

Changes needed in `eval-toolkit` / `research_toolkit` → file a `consumer:claude-ablation-lab` issue **upstream**, never patch locally. Work tracked via GitHub issues (`tracked` + P1/P2/P3). Commits branch off `main`, `type(scope): msg`, ending `Co-Authored-By: Claude <model> <noreply@anthropic.com>`.

## Build phases

0. ✅ Scaffold. 1. ✅ Runner + worktree. 1.5. ✅ Hardened run cell (per-cell worktree isolation, full subprocess envelope, catch-all → `infra_error`, robust JSON, worktree validation/lock, cell-contract test). 2. ✅ Graders (tested/90%+) keyed by `grader_version` (run/grade decoupled) + leakage gate. 3. ✅ Grid + JSONL ledger (provenance-stamped; orchestrator back-off/halt). 4. ✅ DuckDB report/compare (within-cell + across-epoch CIs; v1 exploratory). 5. ✅ `estimate` → smoke; the focused v1 sweep is user-driven. 6. ✅ Plotting (`ablation plot`) + anchor-strict grader + the public demo-infra A/B + a full-repo ship-review (exact sign-flip compare verdict, honest unparseable accounting — `docs/design/2026-07-01_comprehensive-review.md`). Deferred within 6: API adapter, probability-AUROC, book spinoff (`docs/design/2026-07-01_phase6-deferrals.md`). **C. ✅ Public showcase run (2026-07-02).** Post-phase: `advise` → spend audit → books-validate → pressure test → **2026-07-06 independent audit** (token persistence, cost/latency intervals, `--x-axis` frontiers, claude5-refresh release grid) → **2026-07-11 big-picture audit** (mission reframe + construct-validity findings). LLM-judge pairwise phase: **piloted (PR #18 branch), validation gate pending** — provisional, not a headline.

> Roadmap refined 2026-06-25 after a 2-voice independent review — see `docs/design/2026-06-25_independent-review.md`.
