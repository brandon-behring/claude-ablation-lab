# claude-ablation-lab — working conventions

> Last updated: 2026-06-25

A personal **model × thinking-effort × config** ablation/regression harness for Claude Code. See [README.md](README.md). Approved plan: `~/.claude/plans/on-one-of-the-lexical-star.md`.

## Hub pattern references (MANDATORY)

- `~/Claude/lever_of_archimedes/patterns/ds_ml_lifecycle.md` — Exploration → Development → Deployment. **This harness IS a DS/ML experiment system.**
- `~/Claude/lever_of_archimedes/patterns/data_leakage_prevention.md` — the shuffled-label control (below).
- `~/Claude/lever_of_archimedes/patterns/testing.md` — 6-layer validation; real tests only.
- `~/Claude/lever_of_archimedes/patterns/library-design-playbook.md` — Protocols not ABCs; frozen dataclasses; stdlib exceptions.
- `~/Claude/lever_of_archimedes/patterns/git.md` — commit format (`Co-Authored-By: Claude Opus 4.8`).

## Conventions (style)

Enforceable style is **config — the single source of truth** (`pyproject.toml`: ruff `E,F,I,W,UP,B,N,SIM,C4,S101` + black@100 + mypy-strict); don't restate it in prose (drift). Fleet philosophy → the hub `patterns/style/*.yaml`. Dev flow + commit/issue conventions → `CONTRIBUTING.md`. Repo-specific **non-enforceable** rules:
- Seams = `@runtime_checkable Protocol`; results = `@dataclass(frozen=True, slots=True)` (`RunResult`/`Score`/`Worktree`); stdlib exceptions only.
- **numpydoc** docstrings for graders/runner/stats (Parameters/Returns/Raises); plain prose for CLI/orchestration.
- `status` taxonomy `ok|rate_limited|infra_error|timeout|parse_fail` — infra/throttle is never counted as model quality.
- Ledger rows keyed by `grader_version` (re-grade without re-running); `reset_clean` operates only on linked worktrees (`.git` is a file).
- Coverage tiers: graders/stats 90%+, runner/worktree 80%+, CLI best-effort.

## Current phase: EXPLORATION → DEVELOPMENT

**Hybrid rigor (decided):** correctness-critical code is Development-grade *from day 1*; orchestration is Exploration-loose until the loop is proven.

| Code | Rigor | Coverage |
|------|-------|----------|
| `graders/`, eval-toolkit stats, leakage gate | Development (tested + typed) | 90%+ |
| `runner.py`, `worktree.py`, `grid.py`, `ledger.py` | Tested at the seams | 80%+ |
| `analyze.py`, `cli/` | Exploration-loose, then graduate | best-effort |

| Checkpoint | Status |
|------------|--------|
| Leakage gate (T1 shuffled-label) | Not yet |
| Test coverage | — |
| Pre-commit hooks | `make hooks` |

## Core principles

1. **Never fail silently** — explicit errors with diagnostics; never `assert` in `src/`.
2. **Separate infra failure from model failure** — CLI/tool/throttle errors get a distinct `status` (`infra_error`/`timeout`/`rate_limited`) and are excluded from quality aggregation but always reported.
3. **Never trust a point estimate** — `epochs` resampling + bootstrap CIs; report mean±CI.
4. **A buggy grader poisons every number** — graders are tested before any result is believed.
5. **Reproducible** — seed RNG; stamp `claude_version`, harness `git_sha`, and `infra_repo@sha` on every ledger row.

## The leakage / sanity gate (T1)

Before trusting any classification number, run the **shuffled-label control**: shuffle the gold labels and re-grade — **AUROC must collapse to ~0.5**. If a shuffled run still scores high, the harness/grader is leaking → **halt**. (Analog of the talk's "don't mistake noise / infra-failure for model signal.")

## Substrate & key concepts

- **Runner** drives `claude -p --model <m> --effort <e> --output-format json --max-budget-usd <cap>` with `cwd` = the variant's worktree.
- **Variant = `infra_repo@ref`**, materialized as a persistent `git worktree` per `(repo, ref)` (reused across the sweep). Infra-agnostic tasks set `infra_repo: null`.
- **Grid** = tasks × valid `(model, effort)` pairs × variants × epochs. `xhigh` is Opus-only; the validity matrix is probed empirically (Phase 1), not assumed.
- **Ledger** = append-only JSONL (resumable: skip completed cells) + `results/transcripts/<run_id>.json` sidecars.

## Project structure

```
src/claude_ablation_lab/  runner.py worktree.py task.py grade.py graders/ grid.py ledger.py analyze.py cli/main.py
tasks/                    t1_prompt_injection.yaml t2_research_plan.yaml t3_verbatim_anchor.yaml
grids/                    v1.yaml (models × efforts × variants × epochs)
tests/                    conftest.py fixtures/ test_*.py
experiments/log.txt       one-line experiment log
docs/                     plans/{active,done}/ audits/ design/ METHODOLOGY.md
results/  data/  .worktrees/   (gitignored)
```

## Key commands

```bash
make install   # eval-toolkit (editable, local) + this package [dev]
make ci        # ruff + black + mypy + pytest
pytest -m "unit or golden"     # fast loop
pytest -m integration          # tests that shell out to claude / git
ablation estimate|run|report|compare ...
```

## Reuse map (don't re-derive)

- `eval-toolkit` → bootstrap CIs, paired-bootstrap diff, AUROC/PR-AUC (installed editable from `~/Claude/eval-toolkit`).
- `research_toolkit/validators/research_plan.py` → T2 grader (subprocess; `validate(path)->list[str]`).
- `prompt_injection_detector/evals/final_holdout/` → T1 gold; `evals/judge_cache/` → injection-judge prompt+parse pattern to lift.

## Upstream-friction discipline

Changes needed in `eval-toolkit` / `research_toolkit` → file a `consumer:claude-ablation-lab` issue **upstream**, never patch locally. Work tracked via GitHub issues (`tracked` + P1/P2/P3). Commits branch off `main`, `type(scope): msg`, ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Build phases

0. ✅ Scaffold. 1. ✅ Runner + worktree. **1.5 (NEXT — harden the run cell): per-cell worktree isolation (`reset --hard && clean -fdx`), full subprocess envelope (argv/cwd/returncode/stdout/stderr + timeout `as e`), catch-all → `infra_error`, robust JSON, worktree validation/lock, cell-contract test.** 2. Graders (tested/90%) consuming stored transcripts, keyed by `grader_version` (run/grade decoupled) + leakage gate. 3. Grid + JSONL ledger (key incl. `grader_version`; provenance: claude version + global layer + MCP; orchestrator back-off). 4. DuckDB report/compare (CI over within-cell examples; epochs = run-variance; v1 exploratory). 5. `--estimate` (tokens+turns) → smoke → focused v1 sweep. 6 (deferred): API adapter, plotting, backlog, book spinoff.

> Roadmap refined 2026-06-25 after a 2-voice independent review — see `docs/design/2026-06-25_independent-review.md`.
