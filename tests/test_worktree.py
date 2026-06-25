"""Worktree lifecycle: add → reuse → remove on a throwaway git repo (integration: shells to git)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_ablation_lab.worktree import (
    Worktree,
    ensure_worktree,
    remove_worktree,
    reset_clean,
    resolve_sha,
)

pytestmark = pytest.mark.integration


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)

    def run(*a: str) -> None:
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)

    run("init", "-b", "main")
    run("config", "user.email", "t@t.t")
    run("config", "user.name", "t")
    (path / "CLAUDE.md").write_text("# variant config\n")
    run("add", "-A")
    run("-c", "commit.gpgsign=false", "commit", "-m", "init")
    out = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def test_ensure_reuse_remove(tmp_path: Path) -> None:
    repo = tmp_path / "infra"
    sha = _init_repo(repo)
    base = tmp_path / ".worktrees"

    assert resolve_sha(repo, "HEAD") == sha

    wt = ensure_worktree(repo, "HEAD", base=base)
    assert wt.sha == sha
    assert wt.path.exists()
    assert (wt.path / "CLAUDE.md").exists()  # the variant's config is materialized

    wt2 = ensure_worktree(repo, "HEAD", base=base)  # idempotent reuse
    assert wt2.path == wt.path

    remove_worktree(wt)
    assert not wt.path.exists()


def test_bad_ref_raises(tmp_path: Path) -> None:
    repo = tmp_path / "infra"
    _init_repo(repo)
    with pytest.raises(RuntimeError):
        resolve_sha(repo, "no-such-ref")


# --- Phase 1.5: reset_clean + recovery + the cell-contract -------------------


def test_reset_clean_restores_pristine(tmp_path: Path) -> None:
    repo = tmp_path / "infra"
    _init_repo(repo)
    wt = ensure_worktree(repo, "HEAD", base=tmp_path / ".worktrees")

    (wt.path / "research_plan.md").write_text("cell-1 output\n")  # untracked
    (wt.path / "CLAUDE.md").write_text("# MUTATED by cell-1\n")  # tracked

    reset_clean(wt)

    assert not (wt.path / "research_plan.md").exists()  # untracked wiped
    assert (wt.path / "CLAUDE.md").read_text() == "# variant config\n"  # tracked restored


def test_reset_clean_refuses_primary_checkout(tmp_path: Path) -> None:
    # A primary checkout's .git is a directory → guardrail must refuse it.
    repo = tmp_path / "infra"
    _init_repo(repo)
    fake = Worktree(repo=repo, ref="HEAD", sha="0" * 40, path=repo)
    with pytest.raises(ValueError):
        reset_clean(fake)


def test_ensure_worktree_recovers_invalid_dir(tmp_path: Path) -> None:
    repo = tmp_path / "infra"
    sha = _init_repo(repo)
    base = tmp_path / ".worktrees"
    target = base / f"infra@{sha[:12]}"
    target.mkdir(parents=True)
    (target / "junk.txt").write_text("not a worktree\n")  # corrupt/partial dir

    wt = ensure_worktree(repo, "HEAD", base=base)  # log + remove + recreate
    assert wt.path == target
    assert (wt.path / ".git").is_file()  # now a healthy linked worktree
    assert (wt.path / "CLAUDE.md").exists()


def test_cell_contract_isolation(tmp_path: Path) -> None:
    # Phase 1.5 acceptance gate: cell-1's write must NOT leak into cell-2.
    repo = tmp_path / "infra"
    _init_repo(repo)
    wt = ensure_worktree(repo, "HEAD", base=tmp_path / ".worktrees")

    (wt.path / "research_plan.md").write_text("cell-1\n")  # simulated T2 cell-1 output
    reset_clean(wt)  # the between-cell step the orchestrator runs in Phase 3
    assert not (wt.path / "research_plan.md").exists()  # cell-2 sees pristine state
