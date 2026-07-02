#!/usr/bin/env python3
"""Produce the committed public showcase ledger from a raw local one.

Usage (from the repo root, venv active):

    python scripts/make_showcase.py results/showcase-raw.jsonl results/showcase.jsonl

Thin wrapper over :mod:`claude_ablation_lab.showcase` so the logic is importable and
unit-tested; any leaked path fragment or oversized string aborts with a nonzero exit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claude_ablation_lab.showcase import sanitize_ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path, help="raw local ledger (JSONL)")
    parser.add_argument("out", type=Path, help="sanitized output path (JSONL)")
    args = parser.parse_args()
    count = sanitize_ledger(args.raw, args.out)
    print(f"wrote {count} sanitized rows → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
