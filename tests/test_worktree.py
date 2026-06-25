"""Worktree lifecycle: add → reuse → remove on a throwaway git repo (integration: shells to git)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_ablation_lab.worktree import ensure_worktree, remove_worktree, resolve_sha

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
