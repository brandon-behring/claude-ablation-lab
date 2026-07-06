"""Sanitizer tests: the published ledger must carry scores + provenance and nothing
private — foreign tasks, path fragments, and oversized strings are hard errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_ablation_lab.showcase import (
    KEEP_FIELDS,
    MAX_STRING_LEN,
    SHOWCASE_TASKS,
    sanitize_ledger,
    sanitize_row,
)


def _raw_row(**overrides: object) -> dict[str, object]:
    """A realistic raw ledger row (private fields populated the way a run writes them)."""
    row: dict[str, object] = {
        "task_id": "t4_demo_infra",
        "model": "haiku",
        "effort": "low",
        "variant": ".demo-infra@with-skill",
        "epoch": 0,
        "run_status": "ok",
        "grade_status": "ok",
        "value": 1.0,
        "grader_version": "t3-anchor-v2",
        "spec_sha": "a" * 12,
        # live rows resolve this to an ABSOLUTE path — the sanitizer must drop it
        "infra_repo": "/Users/someone/claude-ablation-lab/.demo-infra",
        "infra_sha": "b" * 12,
        "harness_sha": "c" * 12,
        "claude_version": "2.1.0",
        "model_resolved": "claude-haiku-4-5-20251001",
        "cost_usd": 0.03,
        "latency_s": 10.1,
        "num_turns": 3,
        "returncode": 0,
        "run_id": "d" * 32,
        "ts": "2026-07-02T09:00:00+00:00",
        "subscores": {},
        "tool_calls": {"Skill": 1},
        # The private fields the sanitizer exists to remove:
        "details": {"quotes": ["the whole reference text..."]},
        "output_preview": "```json\\n{...}",
        "output_path": "/Users/someone/claude-ablation-lab/results/outputs/x.txt",
        "transcript_path": "/Users/someone/claude-ablation-lab/results/transcripts/x.json",
        "session_id": "f20041c4-3c41-4387-9e94-9b740592dc6b",
        "mcp_servers": ["plugin:github:github"],
        "global_layer": "9cbddfb9260d",
    }
    row.update(overrides)
    return row


@pytest.mark.unit
def test_sanitize_row_keeps_only_keep_fields() -> None:
    raw = _raw_row()
    kept = sanitize_row(raw)
    # Allow-list semantics (D6 inversion): nothing survives beyond KEEP_FIELDS, full
    # stop — not "everything except the fields we remembered to name as private".
    assert set(kept) <= KEEP_FIELDS
    assert set(kept) == set(raw) & KEEP_FIELDS
    # The analysis-critical surface survives — report/compare must work off the
    # published file exactly as off the raw one.
    for field in (
        "task_id",
        "model",
        "effort",
        "variant",
        "epoch",
        "run_status",
        "grade_status",
        "value",
        "grader_version",
        "spec_sha",
        "infra_sha",
        "cost_usd",
        "latency_s",
        "ts",
        "tool_calls",
    ):
        assert field in kept
    # The formerly hand-maintained STRIP_FIELDS are gone because they're simply not
    # on the allow-list — no special-case logic keeps them out.
    for field in (
        "details",
        "output_preview",
        "output_path",
        "transcript_path",
        "session_id",
        "mcp_servers",
        "global_layer",
        "infra_repo",
    ):
        assert field not in kept


@pytest.mark.unit
def test_foreign_task_is_an_error_not_a_silent_drop() -> None:
    with pytest.raises(ValueError, match="not in the allowed set"):
        sanitize_row(_raw_row(task_id="t1_prompt_injection"))
    assert {"t3_verbatim_anchor", "t4_demo_infra"} == SHOWCASE_TASKS


@pytest.mark.unit
def test_explicit_tasks_allowlist_admits_named_task_only() -> None:
    # Publishing a non-showcase ledger (the claude5-refresh snapshots) is an explicit
    # per-task opt-in: the named task sanitizes with the same field allow-list, and
    # anything NOT named still aborts — never a blanket export.
    kept = sanitize_row(
        _raw_row(task_id="t8_hard_math", output_tokens=998),
        tasks=frozenset({"t8_hard_math"}),
    )
    assert kept["task_id"] == "t8_hard_math"
    assert kept["output_tokens"] == 998
    assert "session_id" not in kept and "output_preview" not in kept
    with pytest.raises(ValueError, match="not in the allowed set"):
        sanitize_row(_raw_row(task_id="t4_demo_infra"), tasks=frozenset({"t8_hard_math"}))


@pytest.mark.unit
def test_sanitize_ledger_threads_tasks_allowlist(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    rows = [_raw_row(task_id="t8_hard_math", epoch=i) for i in range(2)]
    raw.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = tmp_path / "refresh.jsonl"
    assert sanitize_ledger(raw, out, tasks=frozenset({"t8_hard_math"})) == 2
    with pytest.raises(ValueError, match="not in the allowed set"):
        sanitize_ledger(raw, tmp_path / "default.jsonl")  # default stays showcase-only


@pytest.mark.unit
def test_leaked_path_fragment_in_a_kept_field_aborts() -> None:
    # Stripping the known fields is not enough — a path smuggled into any surviving
    # value (here: variant) must abort the publish.
    with pytest.raises(ValueError, match="leaked path fragment"):
        sanitize_row(_raw_row(variant="/Users/someone/.demo-infra@with-skill"))


@pytest.mark.unit
def test_oversized_string_aborts() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        sanitize_row(_raw_row(variant="x" * (MAX_STRING_LEN + 1)))


@pytest.mark.unit
def test_nested_values_are_scanned() -> None:
    with pytest.raises(ValueError, match="leaked path fragment"):
        sanitize_row(_raw_row(subscores={"note": "/private/tmp/leak"}))


@pytest.mark.unit
def test_sanitize_ledger_roundtrip(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    rows = [
        _raw_row(epoch=i, task_id=t)
        for i in range(2)
        for t in ("t3_verbatim_anchor", "t4_demo_infra")
    ]
    raw.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = tmp_path / "showcase.jsonl"

    assert sanitize_ledger(raw, out) == 4
    published = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(published) == 4
    text = out.read_text(encoding="utf-8")
    assert "/Users/" not in text and "session_id" not in text and "output_preview" not in text


@pytest.mark.unit
def test_empty_ledger_refused(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    raw.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no rows"):
        sanitize_ledger(raw, tmp_path / "out.jsonl")


@pytest.mark.unit
def test_token_fields_survive_sanitize() -> None:
    # Token counts are the token-denominated cost axis and carry no text — the
    # published showcase ledger must keep them (deliberate KEEP_FIELDS addition).
    kept = sanitize_row(
        _raw_row(
            input_tokens=10,
            output_tokens=40,
            cache_read_tokens=15757,
            cache_creation_tokens=16861,
        )
    )
    assert kept["output_tokens"] == 40
    assert kept["cache_read_tokens"] == 15757
