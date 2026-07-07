"""Shared subprocess envelope for judge CLIs (codex / agy).

The failure taxonomy mirrors the runner's infra-vs-quality split: ``missing``
(binary not on PATH), ``timeout``, ``error`` (nonzero exit — authoritative, the
output is NEVER parsed as a verdict), or ``ok`` (exit 0; the caller still has to
parse). Argv NUL bytes are stripped (they would raise inside ``exec``), stdin is
``DEVNULL`` (codex deadlocks waiting for EOF on a non-TTY pipe), and every CLI
runs in a caller-supplied throwaway cwd — agy has no read-only flag, so isolation
is structural.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CliOutcome", "run_cli"]


@dataclass(frozen=True, slots=True)
class CliOutcome:
    """One CLI invocation: transport status + captured streams + wall latency."""

    status: str  # "ok" | "error" | "timeout" | "missing"
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    latency_s: float = 0.0


def run_cli(argv: list[str], *, timeout_s: float, cwd: Path) -> CliOutcome:
    """Run one judge CLI call; never raises for the expected failure modes."""
    clean = [a.replace("\0", "") for a in argv]
    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 — fixed binaries, list argv, shell=False
            clean,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
        )
    except FileNotFoundError:
        return CliOutcome(status="missing", latency_s=time.monotonic() - start)
    except subprocess.TimeoutExpired:
        return CliOutcome(status="timeout", latency_s=time.monotonic() - start)
    latency = time.monotonic() - start
    status = "ok" if proc.returncode == 0 else "error"
    return CliOutcome(
        status=status,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        returncode=proc.returncode,
        latency_s=latency,
    )
