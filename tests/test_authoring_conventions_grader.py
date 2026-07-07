"""AuthoringConventionsGrader — the SECONDARY conventions check for the t9 family,
plus the _prepare_authoring preparer (reference assembly, truncation, reference_sha,
missing-corpus refusal). All corpus paths here are tmp_path fakes — CI-safe."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_ablation_lab.graders.authoring_conventions import AuthoringConventionsGrader
from claude_ablation_lab.prepare import prepare_task
from claude_ablation_lab.task import Task

_LATEX_GOOD = r"""
\section{Switchback Randomization}
\los{EXP-9.1}{define}{Explain when switchback designs beat unit-level randomization.}
\los{EXP-9.2}{apply}{Choose a switchback window under carryover.}
\companytags{DS-DoorDash-L5, DS-Lyft-L4}
\marginnote[Interview]{Lead with the interference problem, not the design.}
At the heart of marketplace experimentation lies interference \textcite{bojinov2023}.
"""

_MDX_GOOD = """
## The frame: judged pairs

<NoteBox title="Section — at a glance">Goal. Build the pairing.</NoteBox>

Preference is not correctness <Citation src="arena" />.
"""


def _grade(output: str, **gold: object):
    return AuthoringConventionsGrader().grade(output=output, gold=gold)


@pytest.mark.unit
def test_version_and_protocol() -> None:
    from claude_ablation_lab.grade import Grader

    grader = AuthoringConventionsGrader()
    assert isinstance(grader, Grader)
    assert grader.version == "authoring-conv-v1"


@pytest.mark.unit
def test_latex_guide_full_pass() -> None:
    score = _grade(_LATEX_GOOD, family="latex_guide", min_los=2)
    assert score.status == "ok"
    assert score.value == 1.0
    assert score.details["missed"] == []


@pytest.mark.unit
def test_latex_guide_misses_are_named() -> None:
    stripped = _LATEX_GOOD.replace("\\companytags{DS-DoorDash-L5, DS-Lyft-L4}", "")
    score = _grade(stripped + "\n```python\nx=1\n```", family="latex_guide", min_los=2)
    assert score.status == "ok"
    assert score.subscores["companytags"] == 0.0
    assert score.subscores["no_md_fences"] == 0.0
    assert set(score.details["missed"]) == {"companytags", "no_md_fences"}
    assert score.value == pytest.approx(4 / 6)


@pytest.mark.unit
def test_latex_guide_min_los_threshold() -> None:
    # Two \los present: min_los=2 passes, min_los=3 fails that single check.
    assert _grade(_LATEX_GOOD, family="latex_guide", min_los=2).subscores["los"] == 1.0
    assert _grade(_LATEX_GOOD, family="latex_guide", min_los=3).subscores["los"] == 0.0


@pytest.mark.unit
def test_astro_book_components_from_gold() -> None:
    score = _grade(_MDX_GOOD, family="astro_book", required_components=["<NoteBox", "<Citation"])
    assert score.value == 1.0
    missing = _grade(_MDX_GOOD, family="astro_book", required_components=["<WorkedExample"])
    assert missing.subscores["component:<WorkedExample"] == 0.0
    assert missing.value == pytest.approx(2 / 3)


@pytest.mark.unit
def test_astro_book_rejects_latex_voice() -> None:
    score = _grade(
        _MDX_GOOD + "\n\\begin{interviewcontext}wrong voice\\end{interviewcontext}",
        family="astro_book",
    )
    assert score.subscores["no_latex_env"] == 0.0


@pytest.mark.unit
def test_empty_output_is_unparseable() -> None:
    score = _grade("   \n\t", family="latex_guide")
    assert score.status == "unparseable"
    assert score.value == 0.0


@pytest.mark.unit
def test_unknown_family_is_grader_error() -> None:
    score = _grade("some text", family="screenplay")
    assert score.status == "grader_error"
    assert "screenplay" in str(score.details["reason"])


# --- the preparer ---------------------------------------------------------------


def _fake_corpus(tmp_path: Path, *, chars: int = 100) -> Path:
    ref = tmp_path / "chapter_01.tex"
    ref.write_text("R" * chars, encoding="utf-8")
    return ref


def _task(tmp_path: Path, refs: list[str] | None, **params: object) -> Task:
    return Task(
        id="t9_fake",
        domain="authoring",
        grader="authoring_conventions",
        mode="single",
        prompt="Write the section.",
        gold={"family": "latex_guide", "min_los": 2},
        params={"reference_files": refs, **params} if refs is not None else dict(params),
    )


@pytest.mark.unit
def test_prepare_assembles_reference_excerpts(tmp_path: Path) -> None:
    ref = _fake_corpus(tmp_path, chars=100)
    prep = prepare_task(_task(tmp_path, [str(ref)]))
    assert "Write the section." in prep.prompt
    assert "## Reference material" in prep.prompt
    assert "### chapter_01.tex" in prep.prompt
    assert "R" * 100 in prep.prompt
    assert prep.gold["family"] == "latex_guide"
    assert len(prep.gold["reference_sha"]) == 16
    assert prep.spec_sha


@pytest.mark.unit
def test_prepare_truncates_but_hashes_full_content(tmp_path: Path) -> None:
    ref = _fake_corpus(tmp_path, chars=500)
    short = prepare_task(_task(tmp_path, [str(ref)], max_reference_chars=200))
    assert "R" * 200 in short.prompt
    assert "R" * 201 not in short.prompt
    assert "truncated at 200 chars" in short.prompt
    # An edit BEYOND the excerpt window still flips reference_sha → spec_sha (Codex
    # review of the plan: stale reuse across a corpus edit must be impossible).
    ref.write_text("R" * 200 + "S" * 300, encoding="utf-8")
    edited = prepare_task(_task(tmp_path, [str(ref)], max_reference_chars=200))
    assert edited.prompt == short.prompt
    assert edited.gold["reference_sha"] != short.gold["reference_sha"]
    assert edited.spec_sha != short.spec_sha


@pytest.mark.unit
def test_prepare_missing_corpus_refuses_with_path(tmp_path: Path) -> None:
    gone = tmp_path / "not_there.tex"
    with pytest.raises(FileNotFoundError, match="not_there.tex"):
        prepare_task(_task(tmp_path, [str(gone)]))


@pytest.mark.unit
def test_prepare_no_reference_files_is_a_spec_bug(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reference_files"):
        prepare_task(_task(tmp_path, []))
    with pytest.raises(ValueError, match="reference_files"):
        prepare_task(_task(tmp_path, None))
