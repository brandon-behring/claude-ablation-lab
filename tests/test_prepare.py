"""Task preparation: per-grader dispatch (anchor / validator / classification)."""

from __future__ import annotations

import pandas as pd
import pytest

from claude_ablation_lab.prepare import (
    DEFAULT_AGENT_PERMISSION_MODE,
    DEFAULT_ARTIFACT,
    prepare_task,
)
from claude_ablation_lab.task import Task


@pytest.mark.unit
def test_prepare_anchor_is_passthrough() -> None:
    task = Task(
        id="t3",
        domain="extraction",
        grader="anchor",
        mode="single",
        prompt="extract claims",
        gold={"source_text": "abc", "expected_claims": 5},
    )
    prep = prepare_task(task)
    assert prep.prompt == "extract claims"
    assert prep.gold["source_text"] == "abc"
    assert prep.json_schema is None and prep.artifact is None and prep.permission_mode is None


@pytest.mark.unit
def test_prepare_validator_sets_artifact_and_permission() -> None:
    task = Task(id="t2", domain="r", grader="validator", mode="agent", prompt="/research-plan x")
    prep = prepare_task(task)
    assert prep.artifact == DEFAULT_ARTIFACT
    assert prep.permission_mode == DEFAULT_AGENT_PERMISSION_MODE
    # Overridable via params.
    task2 = Task(
        id="t2",
        domain="r",
        grader="validator",
        mode="agent",
        prompt="/research-plan x",
        params={"artifact": "out/plan.md", "permission_mode": "bypassPermissions"},
    )
    prep2 = prepare_task(task2)
    assert prep2.artifact == "out/plan.md"
    assert prep2.permission_mode == "bypassPermissions"


@pytest.mark.unit
def test_prepare_classification_builds_live_from_parquet(tmp_path) -> None:
    parquet = tmp_path / "gold.parquet"
    pd.DataFrame({"text": [f"msg {i}" for i in range(12)], "label": [1, 0] * 6}).to_parquet(parquet)
    task = Task(
        id="t1",
        domain="classification",
        grader="classification",
        mode="single",
        params={"gold_parquet": str(parquet), "subsample_n": 4, "seed": 42},
    )
    prep = prepare_task(task)
    labels = prep.gold["labels"]
    assert len(labels) == 4 and sum(labels.values()) == 2  # balanced
    assert "<msg idx=0>" in prep.prompt  # batched, delimited prompt
    assert prep.json_schema is not None and "classifications" in prep.json_schema["properties"]


@pytest.mark.unit
def test_prepare_unknown_grader_raises() -> None:
    task = Task(id="x", domain="d", grader="mystery", mode="single")
    with pytest.raises(ValueError, match="no preparer"):
        prepare_task(task)
