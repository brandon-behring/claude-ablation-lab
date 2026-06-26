"""Append-only JSONL results ledger — the regression backbone.

One row per graded cell. The ledger is the source of truth a sweep resumes from
and Phase 4 analyses. Two identity tuples drive idempotency:

- **run key** ``(task, model, effort, variant, epoch)`` — identifies one *call to
  Claude*. If an ``ok`` row exists for this key, the expensive model call can be
  skipped and its stored output re-graded.
- **ledger key** ``(task, model, effort, variant, epoch, grader_version)`` —
  identifies one *graded row*. A sweep fully skips a cell only when an ``ok`` row
  for the **current** ``grader_version`` already exists; bumping the grader
  version yields a new key, so a re-grade re-scores without re-running the model.

Persistence is deliberately split for DuckDB (Phase 4): every column Phase 4
aggregates (``value``, ``cost_usd``, ``latency_s``, statuses, the identity dims,
``grader_version``) is a **flat native scalar**, while the grader-specific
``subscores`` / ``details`` (whose shape differs per task type) are stored as
**JSON strings** so DuckDB never has to unify heterogeneous nested structs. They
round-trip back to dicts on :func:`load_rows`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "LedgerRow",
    "RunKey",
    "LedgerKey",
    "append_row",
    "load_rows",
    "completed_ledger_keys",
    "ok_row_by_run_key",
]

logger = logging.getLogger(__name__)

RunKey = tuple[str, str, str, str, int]
LedgerKey = tuple[str, str, str, str, int, str]

# Persisted as JSON strings (see module docstring); decoded on load.
_JSON_FIELDS = ("subscores", "details")


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One graded cell, ready to append as a single JSONL line."""

    # --- identity (run key = first 5; ledger key = + grader_version) ---
    task_id: str
    model: str
    effort: str
    variant: str
    epoch: int
    grader_version: str
    # --- run outcome ---
    run_id: str
    run_status: str
    cost_usd: float
    latency_s: float
    returncode: int | None
    model_resolved: str | None
    num_turns: int
    session_id: str | None
    # --- grade outcome ---
    grade_status: str
    value: float
    subscores: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    # --- output + provenance ---
    output_path: str | None = None
    output_preview: str = ""
    transcript_path: str | None = None
    ts: str = ""
    claude_version: str | None = None
    harness_sha: str | None = None
    infra_repo: str | None = None
    infra_sha: str | None = None
    global_layer: str | None = None
    mcp_servers: tuple[str, ...] = ()

    @property
    def run_key(self) -> RunKey:
        return (self.task_id, self.model, self.effort, self.variant, self.epoch)

    @property
    def ledger_key(self) -> LedgerKey:
        return (*self.run_key, self.grader_version)

    def to_jsonl_dict(self) -> dict[str, Any]:
        """Plain dict for one JSONL line (``subscores``/``details`` → JSON strings)."""
        row = asdict(self)
        row["mcp_servers"] = list(self.mcp_servers)
        for key in _JSON_FIELDS:
            row[key] = json.dumps(row[key], sort_keys=True)
        return row


def _row_from_jsonl_dict(raw: dict[str, Any]) -> LedgerRow:
    """Inverse of :meth:`LedgerRow.to_jsonl_dict` (tolerant of legacy/native dicts)."""
    data = dict(raw)
    for key in _JSON_FIELDS:
        value = data.get(key)
        data[key] = json.loads(value) if isinstance(value, str) else (value or {})
    data["mcp_servers"] = tuple(data.get("mcp_servers") or ())
    known = set(LedgerRow.__dataclass_fields__)
    return LedgerRow(**{k: v for k, v in data.items() if k in known})


def append_row(path: Path | str, row: LedgerRow) -> None:
    """Append one row as a JSON line (crash-safe: one flushed write per cell)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row.to_jsonl_dict()) + "\n")
        handle.flush()


def load_rows(path: Path | str) -> list[LedgerRow]:
    """Read every row from a JSONL ledger (skips blank / unparseable lines).

    A crash mid-write can leave a truncated final line; it is skipped with a
    warning rather than aborting a resume.
    """
    path = Path(path)
    if not path.exists():
        return []
    rows: list[LedgerRow] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(_row_from_jsonl_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("skipping unparseable ledger line %d in %s: %s", lineno, path, exc)
    return rows


def completed_ledger_keys(rows: list[LedgerRow]) -> set[LedgerKey]:
    """Ledger keys of ``ok``-run rows — a cell here is fully done (skip entirely)."""
    return {row.ledger_key for row in rows if row.run_status == "ok"}


def ok_row_by_run_key(rows: list[LedgerRow]) -> dict[RunKey, LedgerRow]:
    """Latest ``ok``-run row per run key — the basis for re-grading without re-running.

    Later rows win, so the most recent successful run is reused. The stored
    ``output_path`` on the returned row is what a re-grade reads.
    """
    out: dict[RunKey, LedgerRow] = {}
    for row in rows:
        if row.run_status == "ok":
            out[row.run_key] = row
    return out
