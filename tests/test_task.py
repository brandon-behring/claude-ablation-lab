"""Task loader + T1 dataset prep (subsample determinism, gold/prompt build)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from claude_ablation_lab import t1_dataset as t1
from claude_ablation_lab.task import Task, load_all, load_task

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"


# --- task loader --------------------------------------------------------------


@pytest.mark.unit
def test_load_all_seed_tasks() -> None:
    tasks = {t.id: t for t in load_all(TASKS_DIR)}
    assert set(tasks) == {
        "t1_prompt_injection",
        "t2_research_plan",
        "t3_verbatim_anchor",
        "t4_demo_infra",
        "t5_books_validate",
        "t6_books_validate_agent",
        "t7_find_bug",
    }
    assert tasks["t2_research_plan"].mode == "agent"
    assert tasks["t1_prompt_injection"].infra_repo is None
    assert tasks["t4_demo_infra"].infra_repo is not None  # infra-sensitive (the demo A/B)
    assert tasks["t5_books_validate"].mode == "single"  # the discriminating authoring probe
    assert tasks["t6_books_validate_agent"].tools == ("Read", "Edit", "Write", "Bash")
    assert tasks["t7_find_bug"].mode == "single"  # reasoning pressure-test (find-the-bug)
    assert tasks["t7_find_bug"].grader == "exact_match"
    # D6: T2 declares exactly what its skill needs (matches its own SKILL.md
    # allowed-tools frontmatter — see the task YAML's comment for the citation).
    assert tasks["t2_research_plan"].tools == ("Read", "Write", "Bash")
    assert tasks["t3_verbatim_anchor"].tools == ()  # single-turn tasks declare none
    for task in tasks.values():
        assert isinstance(task, Task)
        assert task.grader in {
            "classification",
            "validator",
            "anchor",
            "books_validate",
            "exact_match",
        }


@pytest.mark.unit
def test_tools_defaults_empty_when_absent_from_yaml(tmp_path: Path) -> None:
    spec = tmp_path / "notools.yaml"
    spec.write_text("id: x\ndomain: y\ngrader: anchor\nmode: single\n")
    assert load_task(spec).tools == ()


@pytest.mark.unit
def test_tools_loads_from_yaml_list(tmp_path: Path) -> None:
    spec = tmp_path / "withtools.yaml"
    spec.write_text("id: x\ndomain: y\ngrader: validator\nmode: agent\ntools: [Read, Bash]\n")
    assert load_task(spec).tools == ("Read", "Bash")


@pytest.mark.unit
def test_tools_rejects_a_bare_scalar_instead_of_a_list(tmp_path: Path) -> None:
    # A real footgun: `tools: Bash` parses as the STRING "Bash", and iterating a
    # string yields characters — silently becomes ('B','a','s','h') without this
    # guard, denying everything and relaxing nothing while claiming success.
    spec = tmp_path / "scalar.yaml"
    spec.write_text("id: x\ndomain: y\ngrader: validator\nmode: agent\ntools: Bash\n")
    with pytest.raises(ValueError, match="must be a YAML list"):
        load_task(spec)


@pytest.mark.unit
def test_tools_rejects_unknown_tool_names(tmp_path: Path) -> None:
    # A typo here would otherwise silently relax nothing (prepare.py's subtraction
    # never matches an unknown name) while the CLI still claims the tools were
    # relaxed — fail loud at load time instead.
    spec = tmp_path / "typo.yaml"
    spec.write_text("id: x\ndomain: y\ngrader: validator\nmode: agent\ntools: [Bahs]\n")
    with pytest.raises(ValueError, match="unknown tool"):
        load_task(spec)


@pytest.mark.unit
def test_t3_source_templated_and_json_braces_survive() -> None:
    task = {t.id: t for t in load_all(TASKS_DIR)}["t3_verbatim_anchor"]
    assert "Efron" in task.prompt  # {source_text} was substituted
    assert "{source_text}" not in task.prompt
    assert '{"claims"' in task.prompt  # literal JSON braces in the prompt survived
    assert task.gold["source_text"].strip().startswith("The bootstrap")


@pytest.mark.unit
def test_missing_required_key_raises(tmp_path: Path) -> None:
    spec = tmp_path / "bad.yaml"
    spec.write_text("id: x\ndomain: y\n")  # no grader / mode
    with pytest.raises(ValueError, match="missing required keys"):
        load_task(spec)


@pytest.mark.unit
def test_bad_mode_raises(tmp_path: Path) -> None:
    spec = tmp_path / "bad.yaml"
    spec.write_text("id: x\ndomain: y\ngrader: anchor\nmode: weird\n")
    with pytest.raises(ValueError, match="mode must be"):
        load_task(spec)


# --- T1 dataset prep ----------------------------------------------------------


def _synthetic(n: int = 100) -> pd.DataFrame:
    return pd.DataFrame({"text": [f"m{i}" for i in range(n)], "label": [i % 2 for i in range(n)]})


@pytest.mark.unit
def test_subsample_is_balanced_and_seed_stable() -> None:
    frame = _synthetic()
    first = t1.subsample(frame, n=20, seed=42)
    second = t1.subsample(frame, n=20, seed=42)
    assert len(first) == 20
    assert int(first["label"].sum()) == 10  # 10 pos / 10 neg
    assert first["text"].tolist() == second["text"].tolist()


@pytest.mark.unit
def test_subsample_varies_with_seed() -> None:
    frame = _synthetic()
    assert (
        t1.subsample(frame, n=20, seed=1)["text"].tolist()
        != t1.subsample(frame, n=20, seed=2)["text"].tolist()
    )


@pytest.mark.unit
def test_build_gold_and_prompt() -> None:
    sub = t1.subsample(_synthetic(), n=10, seed=7)
    gold = t1.build_gold(sub)
    assert set(gold) == set(range(10))
    assert set(gold.values()) <= {0, 1}
    prompt = t1.build_prompt(sub)
    assert "<msg idx=0>" in prompt and "<msg idx=9>" in prompt  # delimited as data
    assert "untrusted DATA" in prompt  # injection-hardening instruction
    assert "injection" in prompt  # the reused judge definition is embedded


@pytest.mark.unit
def test_subsample_insufficient_class_raises() -> None:
    frame = pd.DataFrame({"text": ["a", "b"], "label": [1, 1]})  # no negatives
    with pytest.raises(ValueError, match="per class"):
        t1.subsample(frame, n=4, seed=1)


@pytest.mark.unit
def test_subsample_rejects_odd_or_nonpositive_n() -> None:
    frame = _synthetic()
    with pytest.raises(ValueError, match="even"):
        t1.subsample(frame, n=61, seed=1)  # odd would silently return 60 rows
    with pytest.raises(ValueError, match="even"):
        t1.subsample(frame, n=0, seed=1)


@pytest.mark.unit
def test_load_holdout_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "holdout.parquet"
    _synthetic().to_parquet(path)
    loaded = t1.load_holdout(path)
    assert {"text", "label"} <= set(loaded.columns)
    assert len(loaded) == 100


@pytest.mark.unit
def test_load_holdout_missing_columns_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.parquet"
    pd.DataFrame({"text": ["a"]}).to_parquet(path)
    with pytest.raises(ValueError, match="missing columns"):
        t1.load_holdout(path)
