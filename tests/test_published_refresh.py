"""Invariants over the COMMITTED results/claude5-refresh-2026-07-06.jsonl — CI-enforced,
so an accidental raw-ledger overwrite or an unreviewed key drifting into the published
release snapshot can never merge (same net as test_published_showcase.py; PR-wide
review, F6/Codex risk). Skips cleanly if the artifact is absent (pre-snapshot branch)."""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from claude_ablation_lab.ledger import load_rows
from claude_ablation_lab.showcase import KEEP_FIELDS, sanitize_row

_PUBLISHED = Path(__file__).parent.parent / "results" / "claude5-refresh-2026-07-06.jsonl"
_TASKS = frozenset({"t8_hard_math"})

pytestmark = pytest.mark.skipif(
    not _PUBLISHED.exists(), reason="no committed claude5-refresh snapshot on this branch"
)


def _rows() -> list[dict[str, object]]:
    return [json.loads(line) for line in _PUBLISHED.read_text(encoding="utf-8").splitlines()]


# Pinned EXACTLY and independently of showcase.py's KEEP_FIELDS (which may grow):
# a future writer field cannot slip into the public artifact unreviewed.
_PUBLISHED_KEYS = frozenset(
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
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    }
)


@pytest.mark.unit
def test_published_refresh_is_sanitized_and_rescannable() -> None:
    for row in _rows():
        assert set(row) <= KEEP_FIELDS
        assert set(row) == _PUBLISHED_KEYS
        assert sanitize_row(dict(row), tasks=_TASKS) == row


@pytest.mark.unit
def test_published_refresh_is_a_loadable_ledger() -> None:
    assert len(load_rows(_PUBLISHED)) == len(_rows())


@pytest.mark.unit
def test_published_refresh_has_the_release_grid_shape() -> None:
    # 13 configs × 3 epochs (4 models × low/high/xhigh + fable/max), all t8, all ok —
    # the §5 audit table must be re-derivable from this artifact alone.
    rows = _rows()
    assert len(rows) == 39
    assert {r["task_id"] for r in rows} == {"t8_hard_math"}
    assert all(r["run_status"] == "ok" for r in rows)
    by_config = collections.Counter((r["model"], r["effort"]) for r in rows)
    assert len(by_config) == 13
    assert all(n == 3 for n in by_config.values())
    assert all(r["output_tokens"] is not None for r in rows)  # full token coverage
