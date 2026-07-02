"""Sanitize a showcase ledger for publication.

The raw ledger is a local artifact: its rows embed absolute paths
(``output_path``/``transcript_path``), output previews, session ids, and host
provenance (``mcp_servers``/``global_layer``) that have no place in a public repo.
This module produces the committed ``results/showcase.jsonl``: showcase tasks only,
private fields stripped, then a paranoid final scan — any absolute-path fragment or
oversized string anywhere in a kept row is a hard error, never a warning. Failure
rows (``rate_limited``/``timeout``/…) are kept: METHODOLOGY promises failure *rates*
are always reported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "SHOWCASE_TASKS",
    "STRIP_FIELDS",
    "MAX_STRING_LEN",
    "FORBIDDEN_FRAGMENTS",
    "sanitize_row",
    "sanitize_ledger",
]

#: The only tasks the public showcase ledger may contain (anything else is an error —
#: a foreign task id means the wrong raw ledger was pointed at this script).
SHOWCASE_TASKS: frozenset[str] = frozenset({"t3_verbatim_anchor", "t4_demo_infra"})

#: Fields removed from every published row: model output (previews/grader details),
#: local filesystem pointers, session identity, and host-environment provenance.
STRIP_FIELDS: tuple[str, ...] = (
    "details",
    "output_preview",
    "output_path",
    "transcript_path",
    "session_id",
    "mcp_servers",
    "global_layer",
)

#: No kept string may exceed this — long strings are how prompt/output text sneaks out.
MAX_STRING_LEN = 200

#: Substrings that mark a leaked local path on any supported host.
FORBIDDEN_FRAGMENTS: tuple[str, ...] = ("/Users/", "/home/", "/private/", "\\Users\\", "/tmp/")


def _scan(value: Any, path: str) -> None:
    """Recursively reject path fragments and oversized strings anywhere in ``value``."""
    if isinstance(value, str):
        if len(value) > MAX_STRING_LEN:
            raise ValueError(f"{path}: string of {len(value)} chars exceeds {MAX_STRING_LEN}")
        for fragment in FORBIDDEN_FRAGMENTS:
            if fragment in value:
                raise ValueError(f"{path}: leaked path fragment {fragment!r} in {value!r}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _scan(key, f"{path}.{key}")
            _scan(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _scan(item, f"{path}[{i}]")


def sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return the publishable form of one ledger row (raises on anything unexpected)."""
    task_id = row.get("task_id")
    if task_id not in SHOWCASE_TASKS:
        raise ValueError(
            f"row for task {task_id!r} is not a showcase task {sorted(SHOWCASE_TASKS)} — "
            "wrong raw ledger?"
        )
    kept = {key: value for key, value in row.items() if key not in STRIP_FIELDS}
    _scan(kept, f"row[{task_id}]")
    return kept


def sanitize_ledger(raw_path: Path, out_path: Path) -> int:
    """Sanitize ``raw_path`` → ``out_path``; returns the row count (must be > 0)."""
    rows = [
        sanitize_row(json.loads(line))
        for line in raw_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"{raw_path}: no rows — refusing to publish an empty ledger")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)
