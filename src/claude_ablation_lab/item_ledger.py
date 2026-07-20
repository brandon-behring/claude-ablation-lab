"""Item-level ledger: two sibling JSONL files, joined on ``run_id`` at read time.

This is the load-bearing change of the rebuild (decision 14). The old ledger's unit of
record was the *run*, which collapsed six item outcomes into one scalar before anything
was persisted — making paired item-level inference, clustered standard errors, and power
analysis impossible by construction (the defect ``analyze.py:205-213`` concedes). Here
the unit of record is the **item**:

- ``runs.jsonl`` — one :class:`RunRow` per inference call: identity, provenance, status,
  cost, latency, tokens. Resume/dedupe stays a runs-file concern.
- ``items.jsonl`` — one :class:`ItemRow` per graded item within a run. All statistics
  read the join.

Each file is schema-homogeneous (no polymorphic rows to guard in every query), and a
published snapshot is the pair.

Conventions inherited verbatim from the original ledger, because they were right:

- **Append-only JSONL**, one object per line; DuckDB reads both files directly.
- **``None`` means "not measured", never "measured zero"** — ``reasoning_tokens=None``
  says the backend reports no thinking/answer split (every Claude surface), not that
  thinking was free. Same rule as the old ledger's ``tool_calls`` (``ledger.py:91-104``).
- **A paid run must never be lost to serialisation** — the degraded-row fallback pattern.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

__all__ = [
    "RunRow",
    "ItemRow",
    "append_run",
    "append_items",
    "load_runs",
    "load_items",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunRow:
    """One inference call: what was asked for, what ran, and what it cost.

    Identity is ``run_id`` (unique per call). The configuration key is
    ``(family, backend, model, effort_label, epoch)`` — ``effort_label`` is
    :attr:`~claude_ablation_lab.provider.Effort.label`, so a tier and a token budget
    can never collide in one column.

    Parameters
    ----------
    run_id:
        Unique id for this call; the join key into ``items.jsonl``.
    family:
        Task family (``causal``, ``code``, ``synthesis``, ``writing``).
    backend:
        Provider name (``claude-cli``, ``ollama``, ``anthropic``) — also the plot
        facet, because effort axes are never pooled across backends (decision 4).
    model, effort_label, epoch:
        The configuration cell.
    status:
        The harness-wide taxonomy (``ok|rate_limited|infra_error|timeout|parse_fail``).
        Anything non-``ok`` excludes the run from quality aggregation but keeps it
        reported.
    control_verdict:
        The Control pre-flight's verdict for this (model, effort) at sweep time
        (``applied``/``no_op``/``unmeasurable``) — stamped so a figure can prove its
        x-axis was validated, not assumed.
    input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, cache_creation_tokens:
        ``None`` = not reported by the backend; never a fabricated zero.
    cost_usd:
        API-equivalent cost; ``None`` on backends with no dollar price (local).
    latency_s:
        Wall-clock for the call.
    model_resolved:
        The concrete model the backend reports having served.
    stop_reason:
        Why generation ended; ``length`` marks budget truncation, which graders must
        treat as truncation rather than a wrong answer.
    prompt_sha, git_sha:
        Provenance: fingerprint of the rendered prompt + harness commit.
    ts:
        ISO-8601 timestamp.
    """

    run_id: str
    family: str
    backend: str
    model: str
    effort_label: str
    epoch: int
    status: str
    control_verdict: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float | None = None
    latency_s: float = 0.0
    model_resolved: str | None = None
    stop_reason: str | None = None
    prompt_sha: str = ""
    git_sha: str | None = None
    ts: str = ""


@dataclass(frozen=True, slots=True)
class ItemRow:
    """One graded item within one run — the atom every statistic consumes.

    Parameters
    ----------
    run_id:
        Join key into ``runs.jsonl``.
    item_id:
        Stable item identifier; the pairing key across configurations (Miller rec. 4:
        inference happens on item-level paired differences, so this must be identical
        for the same item under every configuration).
    score:
        Quality in ``[0, 1]``, higher better.
    correct:
        Binary verdict where one exists; ``None`` for inherently fractional scores.
    cluster_id:
        The randomization unit — items sharing a generator scenario/seed. Clustered
        standard errors group on this; Miller measured clustered SEs over 3× naive
        ones, and omitting the field makes that correction impossible.
    difficulty_stratum:
        ``easy``/``medium``/``hard`` from the frozen calibration ladder. The
        difficulty × effort interaction is the finding, so this is not optional
        metadata.
    replicate_group:
        ``0`` for the breadth pass (every item once); ``1..k`` for the stratified
        replicate subset that carries the within-item variance estimate (decision 5).
    grader_version:
        Same re-grade contract as the old ledger: bump it, re-grade stored output,
        never re-run paid inference.
    subscores:
        Named secondary metrics (e.g. ``minimality`` for adjustment sets). Float-only,
        persisted as a JSON string for DuckDB-schema stability.
    """

    run_id: str
    item_id: str
    score: float
    cluster_id: str
    difficulty_stratum: str
    correct: bool | None = None
    replicate_group: int = 0
    grader_version: str = ""
    subscores: dict[str, float] | None = None


_ITEM_JSON_FIELDS = ("subscores",)


def _encode(row: RunRow | ItemRow) -> str:
    """One JSONL line; grader-provided dict fields stringified for schema stability."""
    payload = asdict(row)
    for key in _ITEM_JSON_FIELDS:
        if key in payload and payload[key] is not None:
            payload[key] = json.dumps(payload[key], sort_keys=True)
    try:
        return json.dumps(payload, sort_keys=True)
    except (TypeError, ValueError) as exc:
        # A paid run must never be lost to a serialisation failure: keep every scalar,
        # replace the offender, and say so loudly (the old ledger's degraded-row rule).
        logger.warning("degraded ledger row for %s: %s", getattr(row, "run_id", "?"), exc)
        degraded = {
            k: (v if isinstance(v, str | int | float | bool) or v is None else str(v))
            for k, v in payload.items()
        }
        return json.dumps(degraded, sort_keys=True)


def append_run(path: Path, row: RunRow) -> None:
    """Append one run to ``runs.jsonl``, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_encode(row) + "\n")


def append_items(path: Path, rows: list[ItemRow]) -> None:
    """Append a batch of item rows (one run's grading output) to ``items.jsonl``."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(_encode(row) + "\n")


def _load(path: Path, cls: type) -> list[Any]:
    """Decode a JSONL file back into rows, tolerating unknown (newer-schema) keys."""
    if not path.exists():
        return []
    known = {f.name for f in fields(cls)}
    rows: list[Any] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            # A corrupt line is evidence of a crashed writer — surface it, don't skip
            # silently into a subtly shorter dataset.
            raise ValueError(f"{path}:{line_no}: undecodable ledger line: {exc}") from exc
        for key in _ITEM_JSON_FIELDS:
            if isinstance(payload.get(key), str):
                payload[key] = json.loads(payload[key])
        rows.append(cls(**{k: v for k, v in payload.items() if k in known}))
    return rows


def load_runs(path: Path) -> list[RunRow]:
    """All runs from ``runs.jsonl`` (empty list for a missing file)."""
    return _load(path, RunRow)


def load_items(path: Path) -> list[ItemRow]:
    """All items from ``items.jsonl`` (empty list for a missing file)."""
    return _load(path, ItemRow)
