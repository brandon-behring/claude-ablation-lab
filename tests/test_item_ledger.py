"""Item-level ledger: round-trip fidelity, the None-vs-zero rule, and loud corruption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_ablation_lab.item_ledger import (
    ItemRow,
    RunRow,
    append_items,
    append_run,
    load_items,
    load_runs,
)

pytestmark = pytest.mark.unit


def _run(**overrides: object) -> RunRow:
    base: dict = {
        "run_id": "r1",
        "family": "causal",
        "backend": "claude-cli",
        "model": "claude-sonnet-5",
        "effort_label": "high",
        "epoch": 0,
        "status": "ok",
    }
    base.update(overrides)
    return RunRow(**base)  # type: ignore[arg-type]


class TestRoundTrip:
    def test_runs_round_trip_exactly(self, tmp_path: Path) -> None:
        row = _run(output_tokens=5100, cost_usd=0.12, control_verdict="applied")
        append_run(tmp_path / "runs.jsonl", row)
        assert load_runs(tmp_path / "runs.jsonl") == [row]

    def test_items_round_trip_including_subscores(self, tmp_path: Path) -> None:
        rows = [
            ItemRow(
                run_id="r1",
                item_id=f"dgp-{i:03d}",
                score=i / 10,
                cluster_id=f"scen-{i // 4}",
                difficulty_stratum="hard",
                correct=i % 2 == 0,
                replicate_group=i % 3,
                grader_version="backdoor-v1",
                subscores={"minimality": 0.5},
            )
            for i in range(6)
        ]
        append_items(tmp_path / "items.jsonl", rows)
        assert load_items(tmp_path / "items.jsonl") == rows

    def test_append_is_append(self, tmp_path: Path) -> None:
        path = tmp_path / "runs.jsonl"
        append_run(path, _run(run_id="a"))
        append_run(path, _run(run_id="b"))
        assert [r.run_id for r in load_runs(path)] == ["a", "b"]


class TestNoneVsZero:
    def test_unmeasured_tokens_stay_none(self, tmp_path: Path) -> None:
        """None must survive persistence — 'not reported' is not 'zero'."""
        append_run(tmp_path / "runs.jsonl", _run(reasoning_tokens=None, output_tokens=None))
        loaded = load_runs(tmp_path / "runs.jsonl")[0]
        assert loaded.reasoning_tokens is None
        assert loaded.output_tokens is None

    def test_measured_zero_stays_zero(self, tmp_path: Path) -> None:
        append_run(tmp_path / "runs.jsonl", _run(cache_read_tokens=0))
        assert load_runs(tmp_path / "runs.jsonl")[0].cache_read_tokens == 0

    def test_unpriced_backend_cost_stays_none(self, tmp_path: Path) -> None:
        """A local run has no dollar price; 0.0 would crown it on any cost frontier."""
        append_run(tmp_path / "runs.jsonl", _run(backend="ollama", cost_usd=None))
        assert load_runs(tmp_path / "runs.jsonl")[0].cost_usd is None


class TestRobustness:
    def test_missing_file_is_empty_not_error(self, tmp_path: Path) -> None:
        assert load_runs(tmp_path / "absent.jsonl") == []
        assert load_items(tmp_path / "absent.jsonl") == []

    def test_corrupt_line_raises_with_location(self, tmp_path: Path) -> None:
        """A crashed writer must surface, not silently shorten the dataset."""
        path = tmp_path / "runs.jsonl"
        append_run(path, _run())
        path.write_text(path.read_text() + "{not json\n", encoding="utf-8")
        with pytest.raises(ValueError, match=r"runs\.jsonl:2"):
            load_runs(path)

    def test_unknown_keys_from_newer_schema_are_tolerated(self, tmp_path: Path) -> None:
        """Forward compatibility: an older reader must load a newer writer's rows."""
        path = tmp_path / "items.jsonl"
        payload = {
            "run_id": "r1",
            "item_id": "i1",
            "score": 1.0,
            "cluster_id": "c1",
            "difficulty_stratum": "easy",
            "some_future_field": "ignored",
        }
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        assert load_items(path)[0].item_id == "i1"
