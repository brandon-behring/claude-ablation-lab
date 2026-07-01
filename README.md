# claude-ablation-lab

[![CI](https://github.com/brandon-behring/claude-ablation-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/brandon-behring/claude-ablation-lab/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A personal **model × thinking-effort × config** ablation/regression harness for **Claude Code**, run headless against *your own* use cases.

The goal is not to reproduce Anthropic's published base numbers — it's to measure how well Claude performs on **your tasks, inside your infrastructure**, so you can later prove whether a change to your `CLAUDE.md` / skills / MCP / prompts **actually helps** ("is the difference real?"). Inspired by the Anthropic talk *"Picking the right model"* (build a small private eval; optimize cheapest-per-*successful-outcome*; read your transcripts; separate infra failures from model failures).

## How it works

- **Substrate:** drives `claude -p --model X --effort Y --output-format json` (your real agent, your subscription auth).
- **Variant = `infra_repo@ref`:** each config under test is a git ref of an infra repo, materialized as a persistent **git worktree**; the runner runs with `cwd` there, so it loads exactly that project's `CLAUDE.md`/`.claude`. Comparing two refs = commit-over-commit "did this change help?".
- **Graders:** per-task; 4 registered — AUROC classification, an external `research_toolkit` validator, and the verbatim-anchor pair (`anchor` reflow-tolerant / `anchor_strict` char-exact; ≥3-word distinct quotes only).
- **Ledger:** append-only JSONL (resumable) + sidecar transcripts; `report`/`compare` query it via **DuckDB**, with bootstrap CIs from **eval-toolkit**.

## Setup

**Prerequisites:** Python **3.13+**, `git`, the [`claude`](https://docs.claude.com/en/docs/claude-code) CLI (logged in to your subscription), and a virtualenv tool ([`uv`](https://docs.astral.sh/uv/) recommended).

```bash
uv venv --python 3.13 --seed && source .venv/bin/activate   # --seed puts pip in the venv; or: python3.13 -m venv .venv
make install      # eval-toolkit (pinned, from GitHub) + this package [dev]
make hooks        # optional: pre-commit (ruff/black @commit, mypy @pre-push)
```

Two dependencies are **public but not on PyPI**:

- **[eval-toolkit](https://github.com/brandon-behring/eval-toolkit)** — bootstrap CIs + AUROC behind the graders and `report`/`compare`. `make install` fetches a pinned release from GitHub; for editable dev pass a local checkout: `EVAL_TOOLKIT=~/eval-toolkit make install`.
- **[research_toolkit](https://github.com/brandon-behring/research_toolkit)** — only for task **T2** (its `/research-plan` validator, and as an infra-variant worktree). Optional — T1/T3 are infra-agnostic and run without it.

## Quickstart

```bash
# Smoke first — 4 cheap cells, self-contained, proves the loop end-to-end:
ablation run tasks/ grids/smoke.yaml --task t3_verbatim_anchor --ledger results/smoke.jsonl
ablation report results/smoke.jsonl

# The focused v1 sweep (63 cells = 3 tasks × valid model×effort × 3 epochs):
ablation run      tasks/ grids/v1.yaml --dry-run   # preview the expanded grid, no calls
ablation estimate tasks/ grids/v1.yaml             # run ONE cell, project tokens/turns/cost
ablation run      tasks/ grids/v1.yaml             # sequential, resumable → results/ledger.jsonl
ablation report   results/ledger.jsonl             # mean±CI, cost, latency, Pareto, leakage flag
ablation compare  results/ledger.jsonl --a repo@v1 --b repo@v2   # paired-bootstrap "is it real?"
ablation regrade  tasks/ --ledger results/ledger.jsonl           # re-score stored runs, no calls
ablation plot     results/ledger.jsonl --a repo@v1 --b repo@v2   # Pareto / effort / A-B forest figures
```

> **T1 prerequisite:** T1 needs a balanced `text`+`label` holdout parquet — set `$T1_HOLDOUT_PATH`
> (or drop one at `data/t1_holdout.parquet`); e.g. a split from the public MIT-licensed
> [prompt-injection-detection-prototype](https://github.com/brandon-behring/prompt-injection-detection-prototype).
> Without it the full-suite commands abort up front — **T3 (and the demo A/B below) run out of the box.**
>
> **Reproducible showcase:** the self-contained skill A/B — the harness detecting an infra
> change with an exact-test verdict — is [`examples/demo-infra/`](examples/demo-infra/README.md)
> (`grids/showcase.yaml`).

> **T2 prerequisite:** the `t2_research_plan` cells run in a worktree of the variant repo in `grids/v1.yaml` (default `~/Claude/research_toolkit@HEAD`). Two conditions must hold: the path is a worktree-able git checkout (else those cells are logged and skipped), **and the `/research-plan` skill is in the `.claude/skills/research-plan/SKILL.md` directory form — flat `.claude/skills/*.md` files do not load** (verified live: [the probe](docs/design/2026-07-01_infra-loading-probe.md)), in which case T2 *runs* — the most expensive, agentic cells — and scores ~0 for infra reasons rather than being skipped.

> **Cost note:** on a Max/Pro subscription there is no per-call dollar charge; `total_cost_usd` is a comparability *metric*. The real budget is **rate-limit headroom** — a big sweep can throttle your normal Claude Code work, so runs are sequential, resumable, and `estimate` warns first. A hard usage cap halts the sweep cleanly and leaves the ledger resumable.

## Status

Alpha — Development phase. **Build phases 0–6 complete**: runner + worktree isolation, 4 graders (run/grade decoupled), grid + JSONL ledger + orchestrator (resumable, provenance-stamped, back-off/halt + an infra circuit breaker), DuckDB `report`/`compare` (exact sign-flip verdicts; honest unparseable accounting), `estimate`, `ablation plot` figures, and the reproducible [demo-infra showcase A/B](examples/demo-infra/README.md). Verified live end-to-end on a 4-cell smoke (run → grade → ledger → report → resume); a full-repo 3-lens ship-review (correctness · methodology · cold-read) is recorded in [`docs/design/2026-07-01_comprehensive-review.md`](docs/design/2026-07-01_comprehensive-review.md). The focused v1 sweep is user-driven (it spends real rate-limit headroom). See `CLAUDE.md` for conventions, [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) for how the numbers stay honest, and per-phase reviews in [`docs/design/`](docs/design/).

## License

MIT.
