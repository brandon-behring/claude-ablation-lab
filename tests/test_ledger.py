"""Append-only ledger: JSONL round-trip, idempotency keys, resume helpers."""

from __future__ import annotations

import json

import pytest

from claude_ablation_lab.ledger import (
    LedgerRow,
    append_row,
    load_rows,
    ok_row_by_ledger_key,
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
def test_jsonl_roundtrip_tool_calls_is_a_string_on_disk_like_subscores(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_row(path, _row(tool_calls={"Skill": 1, "Bash": 2}))
    raw = json.loads(path.read_text(encoding="utf-8").strip())
    assert raw["tool_calls"] == '{"Bash": 2, "Skill": 1}'  # JSON string, sort_keys=True
    [loaded] = load_rows(path)
    assert loaded.tool_calls == {"Skill": 1, "Bash": 2}


@pytest.mark.unit
def test_tool_calls_defaults_none_so_old_rows_without_it_still_load(tmp_path) -> None:
    # A row written before D6 (no tool_calls key at all) must still load — same
    # reason session_id got a default: the published showcase ledger predates this
    # field and must stay loadable. Defaults to None ("not measured"), not {} —
    # an old row genuinely has no mechanism evidence, which is a different claim
    # from "measured, zero tool calls".
    path = tmp_path / "ledger.jsonl"
    raw = _row().to_jsonl_dict()
    del raw["tool_calls"]
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    [loaded] = load_rows(path)
    assert loaded.tool_calls is None


@pytest.mark.unit
def test_tool_calls_none_vs_empty_dict_round_trip_distinctly(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_row(path, _row(run_id="not-measured", tool_calls=None))
    append_row(path, _row(run_id="measured-zero", tool_calls={}))
    raw_lines = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    assert raw_lines[0]["tool_calls"] == "null"
    assert raw_lines[1]["tool_calls"] == "{}"
    loaded = load_rows(path)
    assert loaded[0].tool_calls is None
    assert loaded[1].tool_calls == {}


@pytest.mark.unit
def test_append_row_coerces_nonserialisable_value_instead_of_raising(tmp_path) -> None:
    # B3: a grader that stashes a non-JSON value in `details` must not crash the paid-row
    # append (a lost row re-pays the cell on resume). The value is coerced to str (logged),
    # the row still lands, and load_rows round-trips it.
    path = tmp_path / "l.jsonl"
    append_row(path, _row(details={"weird": {1, 2, 3}}))  # a set is not JSON-serialisable
    loaded = load_rows(path)
    assert len(loaded) == 1  # the paid row survived
    assert "weird" in loaded[0].details  # the odd value was coerced, not dropped


@pytest.mark.unit
def test_append_row_writes_degraded_row_when_serialisation_is_impossible(tmp_path) -> None:
    # B3 (adversarial review, findings 2+3): the paid row must land even when `default=`
    # can't help — a non-deep-copyable value (asdict raises before json is reached) or a
    # non-str dict key (json raises during key sort). Degrade to a placeholder, don't drop.
    path = tmp_path / "l.jsonl"
    gen = (i for i in (1, 2))  # a generator is not deep-copyable → asdict raises
    append_row(path, _row(run_id="nodeepcopy", details={"gen": gen}))
    append_row(path, _row(run_id="badkeys", details={"ok": 1, 2: "mixed"}))  # json key-sort fails
    loaded = load_rows(path)
    assert [r.run_id for r in loaded] == ["nodeepcopy", "badkeys"]  # both paid rows landed
    assert all("_unserialisable" in r.details for r in loaded)  # degraded, logged, not lost


@pytest.mark.unit
def test_append_row_degrades_on_self_referential_details(tmp_path) -> None:
    # B3 (2nd review round): a self-referential details makes asdict() recurse to RecursionError
    # BEFORE json — an arbitrary grader object can raise an arbitrary error, so the paid row must
    # still land (degraded), not crash into a re-pay. Guards the broadened `except Exception`.
    path = tmp_path / "l.jsonl"
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    append_row(path, _row(run_id="cyclic", details=cyclic))
    loaded = load_rows(path)
    assert len(loaded) == 1 and "_unserialisable" in loaded[0].details


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
def test_ok_row_by_ledger_key_only_counts_ok_runs() -> None:
    rows = [_row(run_id="a"), _row(run_id="b", epoch=1, run_status="infra_error")]
    done = ok_row_by_ledger_key(rows)
    assert ("t1", "haiku", "low", "none", 0, "v1") in done
    assert ("t1", "haiku", "low", "none", 1, "v1") not in done  # infra_error not done


@pytest.mark.unit
def test_load_rows_raises_on_midfile_corruption(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_row(path, _row(run_id="a"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ not json }\n")  # a corrupt line that is NOT the last write
    append_row(path, _row(run_id="c", epoch=2))
    # Silently dropping a middle row would re-run a paid cell → must fail loudly.
    with pytest.raises(ValueError, match="corrupt ledger line"):
        load_rows(path)


@pytest.mark.unit
def test_ok_row_by_run_key_latest_wins() -> None:
    early = _row(run_id="early", value=0.1)
    late = _row(run_id="late", value=0.9)
    by_key = ok_row_by_run_key([early, late])
    assert by_key[early.run_key].run_id == "late"  # most recent ok run reused


@pytest.mark.unit
def test_token_fields_roundtrip_as_native_scalars(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_row(
        path,
        _row(
            input_tokens=10,
            output_tokens=40,
            cache_read_tokens=15757,
            cache_creation_tokens=16861,
        ),
    )
    raw = json.loads(path.read_text(encoding="utf-8").strip())
    assert raw["output_tokens"] == 40  # native scalar, not a JSON string
    [loaded] = load_rows(path)
    assert (loaded.input_tokens, loaded.output_tokens) == (10, 40)
    assert (loaded.cache_read_tokens, loaded.cache_creation_tokens) == (15757, 16861)


@pytest.mark.unit
def test_token_fields_default_none_so_old_rows_still_load(tmp_path) -> None:
    # A pre-2026-07-06 row (no token keys at all) must load with None — "not
    # measured" — exactly the tool_calls absent-key rule.
    path = tmp_path / "ledger.jsonl"
    append_row(path, _row())
    raw = json.loads(path.read_text(encoding="utf-8").strip())
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        del raw[key]
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    [loaded] = load_rows(path)
    assert loaded.input_tokens is None
    assert loaded.output_tokens is None
    assert loaded.cache_read_tokens is None
    assert loaded.cache_creation_tokens is None
