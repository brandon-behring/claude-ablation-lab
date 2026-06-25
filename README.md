# claude-ablation-lab

A personal **model × thinking-effort × config** ablation/regression harness for **Claude Code**, run headless against *your own* use cases.

The goal is not to reproduce Anthropic's published base numbers — it's to measure how well Claude performs on **your tasks, inside your infrastructure**, so you can later prove whether a change to your `CLAUDE.md` / skills / MCP / prompts **actually helps** ("is the difference real?"). Inspired by the Anthropic talk *"Picking the right model"* (build a small private eval; optimize cheapest-per-*successful-outcome*; read your transcripts; separate infra failures from model failures).

## How it works

- **Substrate:** drives `claude -p --model X --effort Y --output-format json` (your real agent, your subscription auth).
- **Variant = `infra_repo@ref`:** each config under test is a git ref of an infra repo, materialized as a persistent **git worktree**; the runner runs with `cwd` there, so it loads exactly that project's `CLAUDE.md`/`.claude`. Comparing two refs = commit-over-commit "did this change help?".
- **Graders:** per-task; v1 ships AUROC (classification), an existing `research_toolkit` validator, and a verbatim-substring anchor check.
- **Ledger:** append-only JSONL (resumable) + sidecar transcripts; `report`/`compare` query it via **DuckDB**, with bootstrap CIs from **eval-toolkit**.

## Install

```bash
make install      # installs eval-toolkit (editable, local sibling) + this package [dev]
make hooks        # pre-commit (ruff/black @commit, mypy @pre-push)
```

`eval-toolkit` is consumed from `~/Claude/eval-toolkit` (override with `EVAL_TOOLKIT=...`).

## Quickstart

```bash
ablation estimate tasks/ grids/v1.yaml   # project rate-limit usage before a sweep
ablation run      tasks/ grids/v1.yaml   # sequential sweep → results/ledger.jsonl (+ transcripts)
ablation report   results/ledger.jsonl   # score±CI, cost, latency, Pareto (DuckDB)
ablation compare  results/ledger.jsonl --a repo@v1 --b repo@v2   # paired-bootstrap delta
```

> **Cost note:** on a Max/Pro subscription there is no per-call dollar charge; `total_cost_usd` is a comparability *metric*. The real budget is **rate-limit headroom** — a big sweep can throttle your normal Claude Code work, so runs are sequential, resumable, and `estimate` warns first.

## Status

Alpha — Exploration→Development phase. See `CLAUDE.md` for conventions and build phases, and the approved plan at `~/.claude/plans/on-one-of-the-lexical-star.md`.

## License

MIT.
