"""Invariants over the COMMITTED results/showcase.jsonl — CI-enforced, so an accidental
raw-ledger overwrite of the one tracked results path can never merge (review finding).
Skips cleanly if the artifact is absent (e.g. a branch predating the showcase)."""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from claude_ablation_lab.ledger import load_rows
from claude_ablation_lab.showcase import STRIP_FIELDS, sanitize_row

_PUBLISHED = Path(__file__).parent.parent / "results" / "showcase.jsonl"

pytestmark = pytest.mark.skipif(
    not _PUBLISHED.exists(), reason="no committed showcase ledger on this branch"
)


def _rows() -> list[dict[str, object]]:
    return [json.loads(line) for line in _PUBLISHED.read_text(encoding="utf-8").splitlines()]


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
    }
)


@pytest.mark.unit
def test_published_file_is_sanitized_and_rescannable() -> None:
    # Re-running the sanitizer over the published rows must be a no-op pass: no private
    # keys present, no path fragments / oversized strings anywhere. The keyset is pinned
    # EXACTLY — a future writer field cannot slip into the public artifact unreviewed.
    for row in _rows():
        for private in STRIP_FIELDS:
            assert private not in row
        assert set(row) == _PUBLISHED_KEYS
        assert sanitize_row(dict(row)) == row


@pytest.mark.unit
def test_published_file_is_a_loadable_ledger() -> None:
    # The artifact is named a ledger, so the ledger loader must accept it — not just
    # the DuckDB report path (review finding: session_id had no dataclass default).
    assert len(load_rows(_PUBLISHED)) == len(_rows())


@pytest.mark.unit
def test_published_file_has_the_registered_showcase_shape() -> None:
    rows = _rows()
    assert len(rows) == 54
    by_config = collections.Counter(
        (r["task_id"], r["model"], r["effort"], r["variant"]) for r in rows
    )
    assert all(n == 3 for n in by_config.values())  # 3 epochs everywhere
    t4 = {(m, e, v) for t, m, e, v in by_config if t == "t4_demo_infra"}
    models, efforts = {"haiku", "sonnet", "opus"}, {"low", "high"}
    variants = {".demo-infra@without-skill", ".demo-infra@with-skill"}
    assert t4 == {(m, e, v) for m in models for e in efforts for v in variants}
    t3 = {(m, e, v) for t, m, e, v in by_config if t == "t3_verbatim_anchor"}
    assert t3 == {(m, e, "none") for m in models for e in efforts}
