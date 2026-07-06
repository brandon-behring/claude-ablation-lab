"""Sanitize a showcase ledger for publication.

The raw ledger is a local artifact: its rows embed absolute paths
(``output_path``/``transcript_path``), output previews, session ids, and host
provenance (``mcp_servers``/``global_layer``) that have no place in a public repo.
This module produces the committed ``results/showcase.jsonl``: showcase tasks only,
only ``KEEP_FIELDS`` survive (an allow-list — a field not on it is excluded by
default, not published-unless-remembered), then a paranoid final scan — any
absolute-path fragment or oversized string anywhere in a kept row is a hard error,
never a warning. Failure rows (``rate_limited``/``timeout``/…) are kept: METHODOLOGY
promises failure *rates* are always reported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "SHOWCASE_TASKS",
    "KEEP_FIELDS",
    "MAX_STRING_LEN",
    "FORBIDDEN_FRAGMENTS",
    "sanitize_row",
    "sanitize_ledger",
]

#: The only tasks the public showcase ledger may contain (anything else is an error —
#: a foreign task id means the wrong raw ledger was pointed at this script).
SHOWCASE_TASKS: frozenset[str] = frozenset({"t3_verbatim_anchor", "t4_demo_infra"})

#: The only fields a published row may carry: identity/config, scores, cost/latency,
#: and provenance a reader can act on. Everything else is excluded **by default** —
#: an allow-list (D6 inversion), not a hand-maintained deny-list, so a future ledger
#: field is private-until-reviewed rather than public-unless-remembered. Deliberately
#: excluded (was a hand-maintained ``STRIP_FIELDS`` deny-list before this inversion):
#: ``details``/``output_preview`` (model output — grader internals, prompt/output
#: text), ``output_path``/``transcript_path`` (local filesystem pointers),
#: ``session_id`` (session identity), ``mcp_servers``/``global_layer`` (host-env
#: provenance), and ``infra_repo`` (live rows store it as a *resolved absolute
#: path* — caught by this scanner's first real run; ``variant``/``infra_sha``
#: already carry the reader-relevant provenance).
KEEP_FIELDS: frozenset[str] = frozenset(
    {
        "task_id",
        "model",
        "effort",
        "variant",
        "epoch",
        "grader_version",
        "run_id",
        "run_status",
        "cost_usd",
        "latency_s",
        "returncode",
        "model_resolved",
        "num_turns",
        "grade_status",
        "value",
        "spec_sha",
        "subscores",
        "ts",
        "claude_version",
        "harness_sha",
        "infra_sha",
        "tool_calls",
        # Token usage (native scalars, 2026-07-06): the token-denominated cost axis.
        # Counts carry no prompt/output text, so they are safe to publish.
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    }
)

#: No kept string may exceed this — long strings are how prompt/output text sneaks out.
MAX_STRING_LEN = 200

#: Substrings that mark a leaked local path (common Unix/macOS/Windows roots). This is a
#: deny-list, not a parser — it exists on top of KEEP_FIELDS as a second net (a field
#: being on the allow-list doesn't guarantee its *value* is clean), and the kept fields
#: are short structured values where any of these reads as a leak.
FORBIDDEN_FRAGMENTS: tuple[str, ...] = (
    "/Users/",
    "/home/",
    "/private/",
    "/tmp/",
    "/var/",
    "/etc/",
    "/opt/",
    "/srv/",
    "/mnt/",
    "/root/",
    "/Volumes/",
    "~/",
    "\\Users\\",
    "C:\\",
)


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


def sanitize_row(row: dict[str, Any], *, tasks: frozenset[str] = SHOWCASE_TASKS) -> dict[str, Any]:
    """Return the publishable form of one ledger row (raises on anything unexpected).

    ``tasks`` is the explicit allow-list of task ids this publication run may
    contain (default: the showcase tasks). Publishing any other ledger — e.g. the
    claude5-refresh release snapshots — requires *naming* its tasks at the call
    site: a foreign task id still means the wrong raw ledger was pointed here.
    """
    task_id = row.get("task_id")
    if task_id not in tasks:
        raise ValueError(
            f"row for task {task_id!r} is not in the allowed set {sorted(tasks)} — "
            "wrong raw ledger?"
        )
    kept = {key: value for key, value in row.items() if key in KEEP_FIELDS}
    _scan(kept, f"row[{task_id}]")
    return kept


def sanitize_ledger(
    raw_path: Path, out_path: Path, *, tasks: frozenset[str] = SHOWCASE_TASKS
) -> int:
    """Sanitize ``raw_path`` → ``out_path``; returns the row count (must be > 0)."""
    with raw_path.open(encoding="utf-8") as handle:
        rows = [sanitize_row(json.loads(line), tasks=tasks) for line in handle if line.strip()]
    if not rows:
        raise ValueError(f"{raw_path}: no rows — refusing to publish an empty ledger")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)
