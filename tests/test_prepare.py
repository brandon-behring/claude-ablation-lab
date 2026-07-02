"""Task preparation: per-grader dispatch (anchor / validator / classification)."""

from __future__ import annotations

import pandas as pd
import pytest

from claude_ablation_lab.prepare import (
    DEFAULT_AGENT_PERMISSION_MODE,
    DEFAULT_ARTIFACT,
    prepare_task,
)
from claude_ablation_lab.runner import HERMETIC_DISALLOWED_TOOLS
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
def test_env_holdout_path_beats_task_pinned_parquet(tmp_path, monkeypatch) -> None:
    # $T1_HOLDOUT_PATH is the documented escape hatch — it must win over a task-pinned
    # gold_parquet, or the override is dead exactly when the pinned path doesn't exist
    # (the walk-through audit's fresh-reader traceback).
    env_parquet = tmp_path / "env.parquet"
    pd.DataFrame({"text": [f"e{i}" for i in range(8)], "label": [1, 0] * 4}).to_parquet(env_parquet)
    monkeypatch.setenv("T1_HOLDOUT_PATH", str(env_parquet))
    task = Task(
        id="t1",
        domain="classification",
        grader="classification",
        mode="single",
        params={"gold_parquet": str(tmp_path / "does-not-exist.parquet"), "subsample_n": 4},
    )
    prep = prepare_task(task)  # would raise FileNotFoundError if the pinned path won
    assert len(prep.gold["labels"]) == 4


@pytest.mark.unit
def test_prepare_validator_relaxes_declared_tools_from_hermetic_default() -> None:
    task = Task(
        id="t2",
        domain="r",
        grader="validator",
        mode="agent",
        prompt="/research-plan x",
        tools=("Read", "Write", "Bash"),
    )
    prep = prepare_task(task)
    assert prep.disallowed_tools is not None
    assert set(prep.disallowed_tools) == set(HERMETIC_DISALLOWED_TOOLS) - {"Read", "Write", "Bash"}
    for allowed in ("Read", "Write", "Bash"):
        assert allowed not in prep.disallowed_tools
    assert "Skill" not in prep.disallowed_tools  # already excluded from the base catalog
    # Tool policy DOES change the cell's gradeable identity (D6 review finding):
    # unlike permission_mode, it changes what the cell can even do, so a resume
    # against an old ledger row from before a tools: change must not silently
    # reuse output measured under a different tool boundary.
    no_tools = prepare_task(
        Task(id="t2", domain="r", grader="validator", mode="agent", prompt="/research-plan x")
    )
    assert prep.spec_sha != no_tools.spec_sha


@pytest.mark.unit
def test_prepare_validator_with_no_declared_tools_keeps_full_hermetic_default() -> None:
    task = Task(id="t2", domain="r", grader="validator", mode="agent", prompt="/research-plan x")
    prep = prepare_task(task)
    assert prep.disallowed_tools == HERMETIC_DISALLOWED_TOOLS


@pytest.mark.unit
def test_prepare_anchor_and_classification_leave_disallowed_tools_unset() -> None:
    # Only the agentic (validator) preparer relaxes the hermetic default — a
    # single-turn task has no business touching the tool boundary.
    anchor = prepare_task(
        Task(id="t3", domain="e", grader="anchor", mode="single", prompt="x", gold={})
    )
    assert anchor.disallowed_tools is None


@pytest.mark.unit
def test_prepare_unknown_grader_raises() -> None:
    task = Task(id="x", domain="d", grader="mystery", mode="single")
    with pytest.raises(ValueError, match="no preparer"):
        prepare_task(task)


@pytest.mark.unit
def test_spec_sha_is_stable_and_changes_with_gold() -> None:
    def anchor(quote_gold: str) -> Task:
        return Task(
            id="t3",
            domain="extraction",
            grader="anchor",
            mode="single",
            prompt="extract",
            gold={"source_text": quote_gold, "expected_claims": 5},
        )

    same_a = prepare_task(anchor("abc")).spec_sha
    same_b = prepare_task(anchor("abc")).spec_sha
    different = prepare_task(anchor("xyz")).spec_sha
    assert same_a and same_a == same_b  # deterministic for identical spec
    assert same_a != different  # a gold change changes the fingerprint


@pytest.mark.unit
def test_spec_sha_changes_with_declared_tools() -> None:
    def validator(tools: tuple[str, ...]) -> Task:
        return Task(
            id="t2",
            domain="r",
            grader="validator",
            mode="agent",
            prompt="/research-plan x",
            tools=tools,
        )

    none = prepare_task(validator(())).spec_sha
    some = prepare_task(validator(("Read",))).spec_sha
    more = prepare_task(validator(("Read", "Write"))).spec_sha
    assert len({none, some, more}) == 3  # each distinct tool set is a distinct spec
