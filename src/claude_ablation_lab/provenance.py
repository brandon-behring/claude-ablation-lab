"""Sweep provenance — the environment fingerprint stamped on every ledger row.

The talk's reproducibility discipline: a result is only comparable if you know
*what produced it*. Gathered once per sweep and copied onto each row:

- ``claude_version`` — the CLI build under test.
- ``harness_sha`` — this harness's git commit (the grader/runner code).
- ``mcp_servers`` — the MCP server set Claude loads (captured with auth stripped,
  matching the runner's subprocess view).
- ``global_layer`` — a coarse digest of the constant global ``~/.claude`` config
  layer (v1 holds it fixed and varies only the per-variant *project* layer).

Every probe is best-effort: a missing ``claude`` / ``git`` / config file yields a
``None`` field, never an exception — provenance must not abort a sweep.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from claude_ablation_lab.runner import AUTH_ENV_STRIP

__all__ = ["Provenance", "gather_provenance", "HARNESS_ROOT"]

logger = logging.getLogger(__name__)

#: Repo root inferred from this file (…/src/claude_ablation_lab/provenance.py).
HARNESS_ROOT = Path(__file__).resolve().parents[2]

#: Global config files whose contents define the constant ``~/.claude`` layer.
_GLOBAL_LAYER_FILES = ("CLAUDE.md", "settings.json", "settings.local.json")


@dataclass(frozen=True, slots=True)
class Provenance:
    """Immutable environment fingerprint for a sweep (stamped on each row)."""

    claude_version: str | None
    harness_sha: str | None
    global_layer: str | None
    mcp_servers: tuple[str, ...]


def _stripped_env() -> dict[str, str]:
    """Process env minus auth keys, so probes see the same login as the runner."""
    env = dict(os.environ)
    for key in AUTH_ENV_STRIP:
        env.pop(key, None)
    return env


def _run(argv: list[str], *, cwd: Path | None = None, timeout: float = 30.0) -> str | None:
    """Run a probe command; return stripped stdout, or ``None`` on any failure."""
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=_stripped_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("provenance probe %s failed: %s", argv[0], exc)
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _claude_version() -> str | None:
    """Parse ``claude --version`` (``"2.1.193 (Claude Code)"`` → ``"2.1.193"``)."""
    out = _run(["claude", "--version"])
    return out.split()[0] if out else None


def _harness_sha(repo: Path) -> str | None:
    """Resolve this harness's ``HEAD`` commit (``None`` outside a git checkout)."""
    return _run(["git", "-C", str(repo), "rev-parse", "HEAD"])


def _mcp_servers() -> tuple[str, ...]:
    """Names of configured MCP servers from ``claude mcp list`` (best-effort).

    Each server prints a ``"<name>: <transport> - <status>"`` line. The split is on
    ``": "`` (colon-*space*), not the first colon, because a server name may itself
    contain colons (e.g. ``plugin:context7:context7``). Health-check chatter and
    blank lines have no ``": "`` and are ignored. Returns ``()`` if the probe fails.
    """
    out = _run(["claude", "mcp", "list"], timeout=60.0)
    if not out:
        return ()
    names: list[str] = []
    for line in out.splitlines():
        head, sep, _ = line.partition(": ")
        name = head.strip()
        if sep and name and " " not in name:
            names.append(name)
    return tuple(names)


def _global_layer_digest(home: Path) -> str | None:
    """12-hex digest over the global ``~/.claude`` config files that exist.

    A coarse "did the constant global layer change" signal — not an exhaustive
    audit. ``None`` if none of the tracked files are present.
    """
    base = home / ".claude"
    hasher = hashlib.sha256()
    found = False
    for name in _GLOBAL_LAYER_FILES:
        path = base / name
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:  # unreadable (perms/symlink) — honor "never abort a sweep"
            logger.debug("global-layer file %s unreadable: %s", path, exc)
            continue
        found = True
        hasher.update(name.encode())
        hasher.update(data)
    return hasher.hexdigest()[:12] if found else None


def gather_provenance(*, harness_repo: Path | None = None, home: Path | None = None) -> Provenance:
    """Collect the sweep fingerprint once (each field independently best-effort)."""
    repo = harness_repo or HARNESS_ROOT
    home = home or Path(os.path.expanduser("~"))
    return Provenance(
        claude_version=_claude_version(),
        harness_sha=_harness_sha(repo),
        global_layer=_global_layer_digest(home),
        mcp_servers=_mcp_servers(),
    )
