"""books-validate fixture invariants — the answer key is satisfiable, the ladder is pinned, and
the seeded/gold scores are what the discrimination design assumes. Guards against silent fixture
drift (an edited chapter or registry that changes k0, or an unsatisfiable expected id)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from claude_ablation_lab.grid import expand_grid, load_grid
from claude_ablation_lab.task import load_task

REPO = Path(__file__).resolve().parents[1]
FX = REPO / "examples" / "books-validate"
GOLD = REPO / "tests" / "fixtures" / "books_validate_gold.mdx"

_SPEC = json.loads((FX / "expected.json").read_text())
_LABELS = json.loads((FX / "labels.json").read_text())
_REFS = json.loads((FX / "references.json").read_text())
_CHAPTER = (FX / "chapter.mdx").read_text()


def _check(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FX / "check.py"), str(path), "--fixture", str(FX)],
        capture_output=True,
        text=True,
    )


def _validate(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FX / "validate_fixture.py"), str(path)], capture_output=True, text=True
    )


@pytest.mark.unit
def test_answer_key_is_satisfiable() -> None:
    # Every expected id/key the grader wants must exist in the registries — else the task is
    # unwinnable and the "gold = 1.0" invariant is a lie.
    for item in _SPEC["items"]:
        if item["kind"] == "xref":
            assert item["expected_id"] in _LABELS, item["id"]
            assert set(item.get("family", [])) <= set(_LABELS), item["id"]
        elif item["kind"] == "cite":
            assert item["expected_key"] in _REFS, item["id"]


@pytest.mark.unit
def test_census_maxima_give_antispray_headroom_above_the_gold_counts() -> None:
    # Census is anti-SPRAY, not anti-one-extra: max must sit ABOVE the gold count (so a single
    # benign correctly-targeted tag isn't punished like a spray) while min stays <= it.
    gold = GOLD.read_text()
    counts = {"xref": len(re.findall(r"<XRef\b", gold)), "cite": len(re.findall(r"<Cite\b", gold))}
    for item in _SPEC["items"]:
        if item["kind"] == "census":
            assert item["max"] == counts[item["tag"]] + 2, item["id"]  # gold + headroom
            assert item["min"] <= counts[item["tag"]], item["id"]


@pytest.mark.unit
def test_every_anchor_occurs_exactly_once_case_insensitive() -> None:
    low = _CHAPTER.lower()
    for item in _SPEC["items"]:
        if "anchor" in item:
            assert low.count(item["anchor"].lower()) == 1, item["id"]


@pytest.mark.unit
def test_chapter_is_fence_free() -> None:
    # A triple-backtick in the chapter would make single-turn fence extraction ambiguous.
    assert "```" not in _CHAPTER


@pytest.mark.unit
def test_seeded_chapter_scores_the_pinned_ladder() -> None:
    proc = _check(FX / "chapter.mdx")
    assert proc.returncode == 10  # 10 items below full
    assert "CHECK FAILED: 7.5/15 points, 10 items below full" in proc.stdout.splitlines()[-1]


@pytest.mark.unit
def test_gold_fix_passes_cleanly() -> None:
    proc = _check(GOLD)
    assert proc.returncode == 0
    assert proc.stdout.splitlines()[-1] == "CHECK PASSED: 15/15 points"


@pytest.mark.unit
def test_fidelity_validator_pins_seeded_violations() -> None:
    # The agent-visible validator sees exactly the 4 structural breaks (booklink, unknown cite key,
    # unknown xref id, out-of-range coderef) — NOT the semantic near-misses or the missing citation.
    assert _validate(FX / "chapter.mdx").returncode == 4
    assert _validate(GOLD).returncode == 0


@pytest.mark.unit
def test_tripwires_pass_on_the_seeded_chapter() -> None:
    # The three already-correct elements must score full on seeded (regression detectors).
    lines = {
        ln.split(":")[0].replace("item ", ""): ln
        for ln in _check(FX / "chapter.mdx").stdout.splitlines()
    }
    for tw in ("tripwire_xref", "tripwire_cite", "tripwire_coderef"):
        assert " 1.0 " in lines[tw + " [tripwire]"], lines.get(tw + " [tripwire]")


@pytest.mark.unit
def test_grader_is_order_and_quote_agnostic() -> None:
    # An honest reformat of a correct tag (attribute reorder + quote flip) must not flip its item —
    # the grader parses attributes into a dict, unlike the order-sensitive fidelity validator.
    from claude_ablation_lab.graders.books_validate import BooksValidateGrader

    reformatted = GOLD.read_text().replace(
        '<CodeRef path="src/resample.py" line={30} lineEnd={70}/>',
        "<CodeRef line={30} lineEnd={70} path='src/resample.py'/>",
    )
    assert BooksValidateGrader().grade(output=reformatted, gold={}).value == 1.0


@pytest.mark.unit
def test_grid_routes_t5_neutral_and_t6_to_the_worktree() -> None:
    grid = load_grid(REPO / "grids" / "books-pilot.yaml")
    t5 = load_task(REPO / "tasks" / "t5_books_validate.yaml")
    t6 = load_task(REPO / "tasks" / "t6_books_validate_agent.yaml")
    cells = expand_grid(grid, [t5, t6])
    assert {c.variant for c in cells if c.task_id == "t5_books_validate"} == {"none"}
    assert {c.variant for c in cells if c.task_id == "t6_books_validate_agent"} == {
        ".books-validate@v1"
    }
    # max effort IS present (the reflex under test is opus/max), 9 configs × 3 epochs.
    assert len([c for c in cells if c.task_id == "t5_books_validate"]) == 27
    assert {c.effort for c in cells} == {"low", "high", "max"}


@pytest.mark.integration
def test_setup_sh_ships_only_the_whitelist_no_answer_key(tmp_path) -> None:
    dest = tmp_path / "books-validate"
    subprocess.run([str(FX / "setup.sh"), str(dest)], check=True, capture_output=True)
    tracked = subprocess.run(
        ["git", "-C", str(dest), "ls-files"], check=True, capture_output=True, text=True
    ).stdout.split()
    assert "expected.json" not in tracked and "check.py" not in tracked  # answer key withheld
    assert {
        "chapter.mdx",
        "labels.json",
        "references.json",
        "files.json",
        "CLAUDE.md",
        "validate_fixture.py",
    } <= set(tracked)
    tags = subprocess.run(
        ["git", "-C", str(dest), "tag"], check=True, capture_output=True, text=True
    ).stdout.split()
    assert "v1" in tags
