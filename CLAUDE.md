# claude-ablation-lab — working conventions

> Last updated: 2026-07-01

A personal **model × thinking-effort × config** ablation/regression harness for Claude Code. See [README.md](README.md). Build history: `experiments/log.txt` + per-phase reviews in `docs/design/`.

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

## Current phase: DEVELOPMENT (build phases 0–6 complete; Phase C showcase pending)

**Hybrid rigor (decided):** correctness-critical code is Development-grade *from day 1*; orchestration was Exploration-loose until the loop was proven — now graduated (the run→grade→ledger→report loop is verified live end-to-end).

| Code | Rigor | Coverage |
|------|-------|----------|
| `graders/`, eval-toolkit stats, leakage gate | Development (tested + typed) | 90%+ |
| `runner.py`, `worktree.py`, `grid.py`, `ledger.py` | Tested at the seams | 80%+ |
| `analyze.py`, `cli/` | Exploration-loose, then graduate | best-effort |

| Checkpoint | Status |
|------------|--------|
| Leakage gate (T1 shuffled-label) | Operationalized (Phase 4 — flags at aggregation, `analyze.LEAKAGE_BAND`) |
| Test coverage | ~93% (CI floor 90) |
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
- **Grid** = tasks × valid `(model, effort)` pairs × variants × epochs. `max` effort is Opus-only; the validity matrix is probed empirically (Phase 1), not assumed.
- **Ledger** = append-only JSONL (resumable: skip completed cells) + `results/transcripts/<run_id>.json` sidecars.

## Project structure

```
src/claude_ablation_lab/  runner.py worktree.py task.py t1_dataset.py prepare.py grade.py graders/ grid.py ledger.py orchestrate.py provenance.py analyze.py plot.py cli/main.py
tasks/                    t1_prompt_injection.yaml t2_research_plan.yaml t3_verbatim_anchor.yaml t4_demo_infra.yaml
grids/                    smoke.yaml v1.yaml showcase.yaml (models × efforts × variants × epochs)
examples/demo-infra/      the public showcase A/B fixture (setup.sh → 2-ref repo)
tests/                    conftest.py fixtures/ test_*.py
experiments/log.txt       one-line experiment log
docs/                     plans/active/ audits/ design/ METHODOLOGY.md
results/  data/  .worktrees/   (gitignored)
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

0. ✅ Scaffold. 1. ✅ Runner + worktree. 1.5. ✅ Hardened run cell (per-cell worktree isolation, full subprocess envelope, catch-all → `infra_error`, robust JSON, worktree validation/lock, cell-contract test). 2. ✅ Graders (tested/90%+) keyed by `grader_version` (run/grade decoupled) + leakage gate. 3. ✅ Grid + JSONL ledger (provenance-stamped; orchestrator back-off/halt). 4. ✅ DuckDB report/compare (within-cell + across-epoch CIs; v1 exploratory). 5. ✅ `estimate` → smoke; the focused v1 sweep is user-driven. 6. ✅ Plotting (`ablation plot`) + anchor-strict grader + the public demo-infra A/B + a full-repo ship-review (exact sign-flip compare verdict, honest unparseable accounting — `docs/design/2026-07-01_comprehensive-review.md`). Deferred within 6: API adapter, probability-AUROC, book spinoff (`docs/design/2026-07-01_phase6-deferrals.md`).

> Roadmap refined 2026-06-25 after a 2-voice independent review — see `docs/design/2026-06-25_independent-review.md`.
