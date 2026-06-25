# Contributing

## Setup

```bash
make install   # eval-toolkit (editable, from ~/Claude/eval-toolkit) + this package [dev]
make hooks     # pre-commit: ruff+black @commit, mypy @pre-push
```

`eval-toolkit` is not on PyPI; `make install` installs it editable from the local sibling repo (override with `EVAL_TOOLKIT=...`). Python 3.13 (use `uv venv --python 3.13` — the system Python may be newer than the available wheels).

## Checks

```bash
make ci        # ruff + black --check + mypy + pytest
```

Coverage tiers: graders + `eval-toolkit` stats **90%+**; `runner.py`/`worktree.py` **80%+**; CLI/analysis best-effort. Fast loop: `pytest -m "unit or golden"`; `pytest -m integration` shells out to `claude`/git.

## Style

**Enforceable style is config — the single source of truth** (`pyproject.toml`: ruff `E,F,I,W,UP,B,N,SIM,C4,S101` + black@100 + mypy-strict). Don't restate enforceable rules in prose. The authoritative fleet philosophy lives in `~/Claude/lever_of_archimedes/patterns/style/*.yaml` + `library-design-playbook.md` + `testing.md`. Repo-specific non-enforceable conventions are in `CLAUDE.md` → **Conventions**.

## Commits & issues

- Branch off `main`; messages `type(scope): summary`, ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Upstream-friction discipline:** if a change is needed in `eval-toolkit` or `research_toolkit`, file a `consumer:claude-ablation-lab` issue **upstream** — never patch a vendored copy locally.
- Track multi-session work via GitHub issues (`tracked` + P1/P2/P3 labels).
