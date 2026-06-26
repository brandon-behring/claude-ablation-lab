"""Append-only ledger: JSONL round-trip, idempotency keys, resume helpers."""

from __future__ import annotations

import json

import pytest

from claude_ablation_lab.ledger import (
    LedgerRow,
    append_row,
    completed_ledger_keys,
    load_rows,
    ok_row_by_run_key,
)


def _row(**over: object) -> LedgerRow:
    base: dict[str, object] = {
        "task_id": "t1",
        "model": "haiku",
        "effort": "low",
        "variant": "none",
        "epoch": 0,
        "grader_version": "v1",
        "run_id": "r0",
        "run_status": "ok",
        "cost_usd": 0.01,
        "latency_s": 1.2,
        "returncode": 0,
        "model_resolved": "claude-haiku",
        "num_turns": 1,
        "session_id": "s0",
        "grade_status": "ok",
        "value": 0.8,
        "subscores": {"f1": 0.5},
        "details": {"misses": [1, 2]},
        "output_path": "results/outputs/r0.txt",
    }
    base.update(over)
    return LedgerRow(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_run_and_ledger_keys() -> None:
    row = _row()
    assert row.run_key == ("t1", "haiku", "low", "none", 0)
    assert row.ledger_key == ("t1", "haiku", "low", "none", 0, "v1")


@pytest.mark.unit
def test_jsonl_roundtrip_subscores_are_strings_on_disk(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_row(path, _row(subscores={"f1": 0.5}, details={"k": "v"}))
    # On disk, subscores/details are JSON strings (DuckDB-friendly scalars elsewhere).
    raw = json.loads(path.read_text(encoding="utf-8").strip())
    assert raw["subscores"] == '{"f1": 0.5}'
    assert isinstance(raw["value"], float)  # value stays a native scalar
    # But load_rows decodes them back to dicts.
    [loaded] = load_rows(path)
    assert loaded.subscores == {"f1": 0.5}
    assert loaded.details == {"k": "v"}
    assert loaded.value == 0.8


@pytest.mark.unit
def test_append_is_additive(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_row(path, _row(run_id="a"))
    append_row(path, _row(run_id="b", epoch=1))
    assert [r.run_id for r in load_rows(path)] == ["a", "b"]


@pytest.mark.unit
def test_load_rows_skips_blank_and_truncated_lines(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_row(path, _row(run_id="good"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")  # blank
        handle.write('{"truncated": ')  # crash mid-write
    rows = load_rows(path)
    assert [r.run_id for r in rows] == ["good"]  # the partial line is skipped


@pytest.mark.unit
def test_completed_keys_only_counts_ok_runs() -> None:
    rows = [_row(run_id="a"), _row(run_id="b", epoch=1, run_status="infra_error")]
    done = completed_ledger_keys(rows)
    assert ("t1", "haiku", "low", "none", 0, "v1") in done
    assert ("t1", "haiku", "low", "none", 1, "v1") not in done  # infra_error not done


@pytest.mark.unit
def test_ok_row_by_run_key_latest_wins() -> None:
    early = _row(run_id="early", value=0.1)
    late = _row(run_id="late", value=0.9)
    by_key = ok_row_by_run_key([early, late])
    assert by_key[early.run_key].run_id == "late"  # most recent ok run reused
