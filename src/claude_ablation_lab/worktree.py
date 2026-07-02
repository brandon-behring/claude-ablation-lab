"""Materialize a variant (`infra_repo@ref`) as a persistent git worktree.

A variant under test is a git ref of an infra repo. The orchestrator sets a run's
`cwd` to a detached worktree of that ref so `claude -p` loads exactly that
project's CLAUDE.md/.claude. One worktree per (repo, sha) is created once and
reused across every model×effort×epoch cell of the variant, then removed at sweep
end. Between cells the orchestrator calls `reset_clean` so an agentic task's writes
never leak into the next cell (the Phase 1.5 isolation fix).

Locking is intentionally omitted: sweeps run sequentially (no concurrent worktree
creation), so there is no race to guard.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Worktree",
    "resolve_sha",
    "ensure_worktree",
    "reset_clean",
    "remove_worktree",
    "DEFAULT_BASE",
]

logger = logging.getLogger(__name__)

# OUTSIDE any repo by construction (PR-review finding): a base under the harness repo
# makes the harness's own CLAUDE.md ancestor memory for every cell — the exact leak the
# hermetic-cell design closes. Overridable per run via `ablation run --worktree-base`.
DEFAULT_BASE = Path.home() / ".cache" / "claude-ablation-lab" / "worktrees"


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
    """Resolve a ref (branch/tag/HEAD/sha) to a full commit sha.

    Uses ``rev-parse --verify <ref>^{commit}`` so a non-commit object (tree/blob)
    or an unknown ref fails loudly rather than silently resolving.
    """
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _is_valid_worktree(path: Path, expected_sha: str) -> bool:
    """True iff `path` is a healthy *linked* worktree checked out at `expected_sha`.

    A linked worktree has a ``.git`` *file* (the primary checkout has a ``.git``
    directory). A half-created or corrupt dir fails the HEAD check.
    """
    if not (path / ".git").is_file():
        return False
    try:
        return _git(path, "rev-parse", "HEAD") == expected_sha
    except RuntimeError:
        return False


def _force_remove(repo: Path, path: Path) -> None:
    """Best-effort removal of a (possibly broken) worktree dir + registry entry."""
    if path.exists():
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        shutil.rmtree(path, ignore_errors=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"],
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_worktree(repo: Path, ref: str, *, base: Path = DEFAULT_BASE) -> Worktree:
    """Ensure a healthy detached worktree of `repo@ref` exists under `base`; reuse if valid.

    The worktree dir is named ``<repo-name>@<sha12>`` so the same commit reuses one
    checkout across the sweep. If a dir is present but not a healthy worktree at the
    expected sha (e.g. a partial/failed prior creation), it is logged, removed, and
    recreated. Idempotent.
    """
    repo = repo.resolve()
    sha = resolve_sha(repo, ref)
    base = base.resolve()
    path = base / f"{repo.name}@{sha[:12]}"

    if path.exists():
        if _is_valid_worktree(path, sha):
            return Worktree(repo=repo, ref=ref, sha=sha, path=path)
        logger.warning("worktree at %s is invalid/partial; removing and recreating", path)
        _force_remove(repo, path)

    base.mkdir(parents=True, exist_ok=True)
    try:
        _git(repo, "worktree", "add", "--detach", str(path), sha)
    except RuntimeError:
        _force_remove(repo, path)  # clean up a half-created dir, then surface the failure
        raise
    return Worktree(repo=repo, ref=ref, sha=sha, path=path)


def reset_clean(worktree: Worktree) -> None:
    """Restore the worktree to a pristine checkout of its sha (call before each cell).

    `git reset --hard <sha>` + `git clean -fdx` discards all tracked changes and
    untracked/ignored files so an agentic task's writes never leak into the next cell.

    Safety guardrail: refuses any path that is not a *linked* worktree (a primary
    checkout's ``.git`` is a directory, not a file) — structurally prevents this
    destructive op from ever hitting a real checkout.
    """
    if not (worktree.path / ".git").is_file():
        raise ValueError(
            f"refusing reset_clean: {worktree.path} is not a linked worktree "
            "(.git is not a file) — would risk a real checkout"
        )
    _git(worktree.path, "reset", "--hard", worktree.sha)
    _git(worktree.path, "clean", "-fdx")


def remove_worktree(worktree: Worktree) -> None:
    """Remove a worktree created by `ensure_worktree` (force; discards scratch state)."""
    _git(worktree.repo, "worktree", "remove", "--force", str(worktree.path))
