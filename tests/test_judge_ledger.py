"""Judge ledger: round-trip, resume key discipline, corrupt-line contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_ablation_lab.judge_ledger import (
    JudgeRow,
    append_judge_row,
    latest_rows_by_judge_key,
    load_judge_rows,
    ok_rows_by_judge_key,
)


def _row(**overrides: object) -> JudgeRow:
    base: dict[str, object] = {
        "task_id": "t9_interleaving",
        "epoch": 0,
        "config_a": "claude-fable-5/low",
        "config_b": "sonnet/high",
        "order": "ab",
        "judge_id": "codex",
        "judge_version": "pj-v1+vp-v1/codex:gpt-5.5:medium",
        "spec_sha": "s" * 16,
        "output_sha_a": "a" * 16,
        "output_sha_b": "b" * 16,
        "status": "ok",
        "verdict": "a",
        "judge_run_id": "j1",
    }
    base.update(overrides)
    return JudgeRow(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    row = _row(latency_s=42.5, output_chars_a=900, output_chars_b=1400, reason="tighter")
    append_judge_row(path, row)
    loaded = load_judge_rows(path)
    assert loaded == [row]
    assert loaded[0].cost_usd is None  # not measured, never zero
    assert loaded[0].control == "none"


@pytest.mark.unit
def test_judge_key_covers_every_verdict_dependency() -> None:
    base = _row()
    assert base.judge_key == (
        "t9_interleaving",
        0,
        "claude-fable-5/low",
        "sonnet/high",
        "ab",
        "codex",
        "pj-v1+vp-v1/codex:gpt-5.5:medium",
        "s" * 16,
        "a" * 16,
        "b" * 16,
        "none",
    )
    # Each identity edit produces a distinct key (spec_sha included: a changed
    # assignment must never silently reuse a verdict — plan-review critical).
    for field, value in [
        ("order", "ba"),
        ("judge_id", "gemini"),
        ("judge_version", "pj-v2+vp-v1/codex:gpt-5.5:medium"),
        ("spec_sha", "x" * 16),
        ("output_sha_a", "y" * 16),
        ("control", "verbosity"),
        ("epoch", 1),
    ]:
        assert _row(**{field: value}).judge_key != base.judge_key


@pytest.mark.unit
def test_resume_skips_only_ok_and_latest_wins(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    append_judge_row(path, _row(status="timeout", verdict=None, judge_run_id="j1"))
    append_judge_row(path, _row(status="ok", verdict="b", judge_run_id="j2"))
    rows = load_judge_rows(path)
    ok = ok_rows_by_judge_key(rows)
    assert len(ok) == 1
    assert next(iter(ok.values())).judge_run_id == "j2"
    # The failed attempt does NOT create a second key entry; latest-per-key shows
    # the retry superseding it (the controls health gate reads this view).
    latest = latest_rows_by_judge_key(rows)
    assert len(latest) == 1
    assert next(iter(latest.values())).status == "ok"


@pytest.mark.unit
def test_truncated_final_line_skipped_corrupt_middle_raises(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    append_judge_row(path, _row())
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"task_id": "t9_interleaving", "epo')  # crash mid-write
    assert len(load_judge_rows(path)) == 1

    corrupt = tmp_path / "corrupt.jsonl"
    with corrupt.open("w", encoding="utf-8") as fh:
        fh.write("not json at all\n")
    append_judge_row(corrupt, _row())
    with pytest.raises(ValueError, match="corrupt judge-ledger line 1"):
        load_judge_rows(corrupt)


@pytest.mark.unit
def test_unknown_fields_from_future_rows_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    append_judge_row(path, _row())
    text = path.read_text(encoding="utf-8").rstrip()
    path.write_text(text[:-1] + ', "future_field": 7}\n', encoding="utf-8")
    assert load_judge_rows(path)[0].task_id == "t9_interleaving"
