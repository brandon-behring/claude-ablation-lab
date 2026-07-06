#!/usr/bin/env python3
"""Produce a committed public ledger from a raw local one.

Usage (from the repo root, venv active):

    python scripts/make_showcase.py results/showcase-raw.jsonl results/showcase.jsonl
    python scripts/make_showcase.py results/claude5-refresh.jsonl \
        results/claude5-refresh-2026-07-06.jsonl --tasks t8_hard_math

Thin wrapper over :mod:`claude_ablation_lab.showcase` so the logic is importable and
unit-tested; any leaked path fragment or oversized string aborts with a nonzero exit.
``--tasks`` names the task ids the publication may contain (default: the showcase
tasks) — publishing stays an explicit opt-in per task, never a blanket export.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claude_ablation_lab.showcase import SHOWCASE_TASKS, sanitize_ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, help="raw local ledger (JSONL)")
    parser.add_argument("out", type=Path, help="sanitized output path (JSONL)")
    parser.add_argument(
        "--tasks",
        default=None,
        help="comma-separated task ids allowed in this publication (default: showcase tasks)",
    )
    args = parser.parse_args()
    if args.tasks is None:
        tasks = SHOWCASE_TASKS
    else:
        tasks = frozenset(t.strip() for t in args.tasks.split(",") if t.strip())
        if not tasks:
            # An explicitly-empty allow-list would reject every row with a confusing
            # "not in the allowed set []" — fail at the flag, where the mistake is.
            parser.error("--tasks was given but named no task ids")
    count = sanitize_ledger(args.raw, args.out, tasks=tasks)
    print(f"wrote {count} sanitized rows → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
