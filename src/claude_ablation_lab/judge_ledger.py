"""Append-only JSONL judge ledger — one row per judge CLI call.

Mirrors :mod:`claude_ablation_lab.ledger` ("one graded cell = one row"): one
judge invocation (one ORDER of one pair, by one judge) = one row. Aggregation
(order-debias, cross-judge) happens at analysis time from stored rows, exactly as
``compare`` derives verdicts from ledger rows.

The **judge key** — the resume/skip identity — is
``(task_id, epoch, config_a, config_b, order, judge_id, judge_version, spec_sha,
output_sha_a, output_sha_b, control)``. Everything a verdict depends on is in the
key: a template/parser/model bump (``judge_version``), a changed assignment or
reference context (``spec_sha`` — identity, not just lineage: a contestant could
produce byte-identical output against a changed spec), or a changed contestant
output (the output SHAs) re-judges automatically; anything else is skipped.
Re-judging stored outputs costs zero contestant runs — the regrade property.

``cost_usd`` and the token fields are ``None`` on every row: the subscription
CLIs report neither, and *not measured* is never a measured zero (the
``tool_calls`` rule). Judge latency/bytes live here and are NEVER folded into a
contestant's cost — the frontier must show what the contestant costs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "JudgeRow",
    "JudgeKey",
    "append_judge_row",
    "load_judge_rows",
    "ok_rows_by_judge_key",
    "latest_rows_by_judge_key",
]

logger = logging.getLogger(__name__)

JudgeKey = tuple[str, int, str, str, str, str, str, str, str, str, str]

#: The ``control`` value for real (non-control) pairs, stored as a string so the
#: key tuple stays flat-JSONL-safe. Control rows carry ``same_output`` /
#: ``verbosity`` / ``positive``.
REAL_PAIR = "none"


@dataclass(frozen=True, slots=True)
class JudgeRow:
    """One judge CLI call, ready to append as a single JSONL line."""

    # --- identity (the judge key; see module docstring) ---
    task_id: str
    epoch: int
    config_a: str  # canonical "model/effort"; config_a < config_b lexicographically
    config_b: str
    order: str  # "ab" -> config_a shown as "Response A"; "ba" -> swapped
    judge_id: str
    judge_version: str
    spec_sha: str
    output_sha_a: str
    output_sha_b: str
    control: str = REAL_PAIR  # none | same_output | verbosity | positive
    # --- outcome ---
    status: str = "ok"  # ok | unparsed | error | timeout | missing
    verdict: str | None = None  # CANONICAL frame: "a" | "b" | "tie"; None unless ok
    reason: str = ""
    # --- lineage ---
    judge_run_id: str = ""
    run_id_a: str = ""
    run_id_b: str = ""
    transcript_path: str | None = None
    # --- measurement (judge-side; never a contestant's) ---
    latency_s: float = 0.0
    output_bytes: int = 0
    output_chars_a: int = 0  # length-ratio tripwire for verbosity bias
    output_chars_b: int = 0
    cost_usd: float | None = None  # not measured (subscription CLI)
    input_tokens: int | None = None
    output_tokens: int | None = None
    # --- provenance ---
    ts: str = ""
    harness_sha: str | None = None

    @property
    def judge_key(self) -> JudgeKey:
        return (
            self.task_id,
            self.epoch,
            self.config_a,
            self.config_b,
            self.order,
            self.judge_id,
            self.judge_version,
            self.spec_sha,
            self.output_sha_a,
            self.output_sha_b,
            self.control,
        )


def append_judge_row(path: Path | str, row: JudgeRow) -> None:
    """Append one row as a JSON line (crash-safe: one flushed write per call)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(row)) + "\n")
        handle.flush()


def load_judge_rows(path: Path | str) -> list[JudgeRow]:
    """Read every row; skip only a truncated FINAL line (crash), raise elsewhere.

    Same contract as :func:`claude_ablation_lab.ledger.load_rows`: silently
    dropping a completed row would re-pay for that judge call and hand the
    analysis an incomplete dataset it would treat as authoritative.
    """
    path = Path(path)
    if not path.exists():
        return []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    last_idx = max((i for i, ln in enumerate(raw_lines) if ln.strip()), default=-1)
    rows: list[JudgeRow] = []
    known = set(JudgeRow.__dataclass_fields__)
    for idx, line in enumerate(raw_lines):
        if not line.strip():
            continue
        try:
            data: dict[str, Any] = json.loads(line)
            rows.append(JudgeRow(**{k: v for k, v in data.items() if k in known}))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if idx == last_idx:  # benign: a crash truncated the final write
                logger.warning("skipping truncated final judge-ledger line %d in %s", idx + 1, path)
                continue
            raise ValueError(f"corrupt judge-ledger line {idx + 1} in {path}: {exc}") from exc
    return rows


def ok_rows_by_judge_key(rows: list[JudgeRow]) -> dict[JudgeKey, JudgeRow]:
    """Latest ``ok`` row per judge key — the resume skip set (later rows win)."""
    out: dict[JudgeKey, JudgeRow] = {}
    for row in rows:
        if row.status == "ok":
            out[row.judge_key] = row
    return out


def latest_rows_by_judge_key(rows: list[JudgeRow]) -> dict[JudgeKey, JudgeRow]:
    """Latest row per judge key at ANY status.

    The controls health gate reads this, not the raw row list: a failed attempt
    superseded by a successful retry must not keep failing the gate forever
    (plan-review finding), while a key whose *latest* state is non-ok still
    counts against health honestly.
    """
    out: dict[JudgeKey, JudgeRow] = {}
    for row in rows:
        out[row.judge_key] = row
    return out
