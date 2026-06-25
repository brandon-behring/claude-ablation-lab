"""Materialize a variant (`infra_repo@ref`) as a persistent git worktree.

A variant under test is a git ref of an infra repo. The runner sets `cwd` to a
detached worktree of that ref so `claude -p` loads exactly that project's
CLAUDE.md/.claude. One worktree per (repo, sha) is created once and reused across
every model×effort×epoch cell of the variant, then removed at sweep end.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Worktree", "resolve_sha", "ensure_worktree", "remove_worktree", "DEFAULT_BASE"]

DEFAULT_BASE = Path(".worktrees")


@dataclass(frozen=True, slots=True)
class Worktree:
    """A materialized variant: the repo, the requested ref, its resolved sha, and the path."""

    repo: Path
    ref: str
    sha: str
    path: Path


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def resolve_sha(repo: Path, ref: str) -> str:
    """Resolve a ref (branch/tag/HEAD/sha) to a full commit sha."""
    return _git(repo, "rev-parse", ref)


def ensure_worktree(repo: Path, ref: str, *, base: Path = DEFAULT_BASE) -> Worktree:
    """Ensure a detached worktree of `repo@ref` exists under `base`; reuse if present.

    The worktree dir is named `<repo-name>@<sha12>` so the same commit reuses one
    checkout across the sweep. Idempotent.
    """
    repo = repo.resolve()
    sha = resolve_sha(repo, ref)
    base = base.resolve()
    path = base / f"{repo.name}@{sha[:12]}"
    if path.exists():
        return Worktree(repo=repo, ref=ref, sha=sha, path=path)
    base.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--detach", str(path), sha)
    return Worktree(repo=repo, ref=ref, sha=sha, path=path)


def remove_worktree(worktree: Worktree) -> None:
    """Remove a worktree created by `ensure_worktree` (force; discards scratch state)."""
    _git(worktree.repo, "worktree", "remove", "--force", str(worktree.path))
