"""demo-infra showcase A/B (Phase-6 headline). Structural guarantees: setup.sh builds a
worktree-able 2-ref repo, the task gold matches the skill reference, the anchor grader
shows the with-skill vs without-skill delta, and the showcase grid runs t4 under both demo
refs. The *live* delta (does Claude actually quote the skill) is a Phase-C run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from claude_ablation_lab.grid import expand_grid, load_grid
from claude_ablation_lab.task import load_task

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "examples" / "demo-infra"


def _t4_gold() -> dict[str, Any]:
    return dict(yaml.safe_load((REPO / "tasks" / "t4_demo_infra.yaml").read_text())["gold"])


@pytest.mark.unit
def test_gold_equals_the_skill_reference_body() -> None:
    # The model quotes the skill's reference; the grader scores against the task gold, so
    # the two must be IDENTICAL in both directions: gold text missing from the skill would
    # break with-skill quotes, and skill text missing from the gold would mark honest
    # quotes as misses (silently deflating the with-skill headline). Equality guards both.
    gold_src = _t4_gold()["source_text"].strip()
    skill = (DEMO / "content" / "project-reference.md").read_text()
    body = skill.split("---", 2)[2].strip()  # markdown body after the YAML frontmatter
    assert body == gold_src


@pytest.mark.unit
def test_anchor_delta_with_vs_without_reference() -> None:
    from claude_ablation_lab.graders.anchor import AnchorGrader

    gold = _t4_gold()
    with_skill = (
        '{"claims":[{"claim":"g","quote":"the geometric mean of recall and calibration,'
        ' floored at 0.2"},{"claim":"c","quote":"marked cold and skipped"},'
        '{"claim":"a","quote":"caches it under the amber namespace"}]}'
    )
    without = (
        '{"claims":[{"claim":"a","quote":"Vega uses cosine similarity over embeddings"},'
        '{"claim":"b","quote":"it ranks candidates by F1 score"},'
        '{"claim":"c","quote":"results are stored in Postgres"}]}'
    )
    g = AnchorGrader()
    assert g.grade(output=with_skill, gold=gold).value == 1.0  # exact quotes from the reference
    assert g.grade(output=without, gold=gold).value == 0.0  # fabricated (no reference) → misses


@pytest.mark.unit
def test_showcase_grid_runs_t4_under_both_demo_refs() -> None:
    grid = load_grid(REPO / "grids" / "showcase.yaml")
    t3 = load_task(REPO / "tasks" / "t3_verbatim_anchor.yaml")
    t4 = load_task(REPO / "tasks" / "t4_demo_infra.yaml")
    cells = expand_grid(grid, [t3, t4])
    assert {c.variant for c in cells if c.task_id == "t4_demo_infra"} == {
        ".demo-infra@with-skill",
        ".demo-infra@without-skill",
    }
    # t3 stays infra-agnostic — only `none`, never a demo ref.
    assert {c.variant for c in cells if c.task_id == "t3_verbatim_anchor"} == {"none"}


@pytest.mark.unit
def test_showcase_grid_haiku_effort_collapse_drops_below_significance_floor() -> None:
    # HONESTY (CV2, 2026-07-11): the showcase's original n=6 counted haiku/low and
    # haiku/high as distinct, but Haiku 4.5 has NO effort parameter — they are ONE config.
    # The provider capability matrix now collapses them, so the *reproducible* grid yields
    # 5 paired configs — BELOW the exact sign-flip floor (min p = 2/2^n needs n >= 6), so
    # the designed positive control can no longer print real=yes on reproduction. The
    # committed 54-row ledger predates the matrix and stays valid as history; restoring a
    # real=yes needs a genuine 6th config (e.g. a medium tier) + a re-run. Pin the honest
    # post-matrix state so this gap can't be silently forgotten.
    from claude_ablation_lab.analyze import MIN_PAIRS_FOR_REAL

    grid = load_grid(REPO / "grids" / "showcase.yaml")
    t4 = load_task(REPO / "tasks" / "t4_demo_infra.yaml")
    cells = expand_grid(grid, [t4])
    configs = {
        variant: {(c.model, c.effort) for c in cells if c.variant == variant}
        for variant in (".demo-infra@with-skill", ".demo-infra@without-skill")
    }
    with_skill, without_skill = configs.values()
    assert with_skill == without_skill  # still paired: identical config sets under both refs
    assert ("haiku", "low") in with_skill  # canonical haiku cell kept
    assert ("haiku", "high") not in with_skill  # inert duplicate collapsed
    assert len(with_skill) == 5  # 5 distinct configs, not the naive 6
    assert len(with_skill) < MIN_PAIRS_FOR_REAL  # honestly below the sign-flip floor now


@pytest.mark.integration
def test_setup_sh_builds_worktreeable_two_ref_repo(tmp_path) -> None:
    dest = tmp_path / "demo-infra"
    subprocess.run([str(DEMO / "setup.sh"), str(dest)], check=True, capture_output=True)

    branches = subprocess.run(
        ["git", "-C", str(dest), "branch", "--format=%(refname:short)"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert set(branches) == {"with-skill", "without-skill"}

    def _has_skill(ref: str) -> bool:
        target = f"{ref}:.claude/skills/project-reference/SKILL.md"
        return (
            subprocess.run(
                ["git", "-C", str(dest), "cat-file", "-e", target], capture_output=True
            ).returncode
            == 0
        )

    assert _has_skill("with-skill") and not _has_skill("without-skill")

    # worktree-able exactly as the orchestrator materialises a variant; the worktree loads the skill.
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(dest), "worktree", "add", "--detach", str(wt), "with-skill"],
        check=True,
        capture_output=True,
    )
    skill = wt / ".claude" / "skills" / "project-reference" / "SKILL.md"
    assert skill.is_file()
    # The materialized skill must carry the exact reference the task gold scores against —
    # existence alone would let setup.sh content drift break the A/B silently.
    assert _t4_gold()["source_text"].strip() in skill.read_text()
