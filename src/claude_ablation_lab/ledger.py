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
    "ok_row_by_ledger_key",
    "ok_row_by_run_key",
]

logger = logging.getLogger(__name__)

RunKey = tuple[str, str, str, str, int]
LedgerKey = tuple[str, str, str, str, int, str]

# Persisted as JSON strings (see module docstring); decoded on load.
_JSON_FIELDS = ("subscores", "details", "tool_calls")


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
    # --- grade outcome ---
    grade_status: str
    value: float
    # Defaulted so the published sanitized ledger (which strips it) stays loadable by
    # load_rows — the showcase file must remain a real ledger, not just a report input.
    session_id: str | None = None
    spec_sha: str = ""  # fingerprint of (prompt, schema, gold) — gates resume/re-grade
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
    #: Mechanism evidence: tool name → invocation count, derived from the runner's
    #: ``tools_used``. ``None`` means *not measured* (``ClaudeCodeRunner.
    #: capture_mechanism=False``, the pre-D6 default and the shape of every row on
    #: this machine before D6); ``{}`` means *measured, zero tool calls* — collapsing
    #: the two would make "we didn't look" indistinguishable from "we looked and saw
    #: nothing" (review finding). Persisted as a JSON string like
    #: ``subscores``/``details`` (see module docstring) — its key set varies per row
    #: (a T4 control cell: ``{}``; a with-skill cell: ``{"Skill": 1}``; a future T2
    #: cell: ``{"Bash": 3, "Write": 1, "Skill": 1}``), the same DuckDB-heterogeneity
    #: reason those two fields aren't native columns.
    tool_calls: dict[str, int] | None = None
    #: Token usage from the CLI's ``usage`` payload, persisted as native scalars so
    #: DuckDB can aggregate a token-denominated cost axis (on a flat subscription,
    #: token volume — not ``cost_usd`` — is the honest spend currency, and cache-read
    #: is empirically its largest component: 2026-07-03 spend audit). ``None`` means
    #: *not measured* — the key was absent from the payload, or the row predates
    #: 2026-07-06 — never a measured zero (the ``tool_calls`` None-vs-{} rule).
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

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
    """Inverse of :meth:`LedgerRow.to_jsonl_dict` (tolerant of legacy/native dicts).

    A key *present* but stored as a JSON string is decoded; a key *absent entirely*
    (an old row written before that field existed) is left out of the constructor
    call so ``LedgerRow`` applies that field's own dataclass default — ``{}`` for
    ``subscores``/``details``, ``None`` for ``tool_calls``. A shared hardcoded
    fallback here would be wrong for any field whose true default isn't ``{}``.
    """
    data = dict(raw)
    for key in _JSON_FIELDS:
        if key in data:
            value = data[key]
            data[key] = json.loads(value) if isinstance(value, str) else value
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
    """Read every row from a JSONL ledger; blank lines are ignored.

    A crash mid-``append_row`` can leave a truncated **final** line — that one is
    skipped with a warning so a resume can proceed. An unparseable line *anywhere
    else* signals real corruption (or a hand-edit), and is raised as a
    :class:`ValueError` rather than silently dropped: dropping a completed row
    would silently re-run (re-pay for) that cell and hand Phase 4 an incomplete
    dataset it would treat as authoritative.
    """
    path = Path(path)
    if not path.exists():
        return []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    last_idx = max((i for i, ln in enumerate(raw_lines) if ln.strip()), default=-1)
    rows: list[LedgerRow] = []
    for idx, line in enumerate(raw_lines):
        if not line.strip():
            continue
        try:
            rows.append(_row_from_jsonl_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if idx == last_idx:  # benign: a crash truncated the final write
                logger.warning("skipping truncated final ledger line %d in %s", idx + 1, path)
                continue
            raise ValueError(f"corrupt ledger line {idx + 1} in {path}: {exc}") from exc
    return rows


def ok_row_by_ledger_key(rows: list[LedgerRow]) -> dict[LedgerKey, LedgerRow]:
    """Latest ``ok``-run row per *ledger* key (incl. ``grader_version``).

    Used for the resume skip: a cell is fully done only if a stored row matches
    the ledger key *and* the current ``spec_sha`` (checked by the caller).
    """
    out: dict[LedgerKey, LedgerRow] = {}
    for row in rows:
        if row.run_status == "ok":
            out[row.ledger_key] = row
    return out


def ok_row_by_run_key(rows: list[LedgerRow]) -> dict[RunKey, LedgerRow]:
    """Latest ``ok``-run row per *run* key — the basis for re-grading without re-running.

    Later rows win, so the most recent successful run is reused. The stored
    ``output_path`` on the returned row is what a re-grade reads.
    """
    out: dict[RunKey, LedgerRow] = {}
    for row in rows:
        if row.run_status == "ok":
            out[row.run_key] = row
    return out
