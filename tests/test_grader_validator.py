"""T2 validator grader — score logic (mocked subprocess) + a real end-to-end pass.

The unit tests mock ``subprocess.run`` to exercise the score/parse paths without
the external repo. The integration tests run ``research_toolkit``'s real
validator and skip when that repo is not present (e.g. on CI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_ablation_lab.graders.validator import (
    DEFAULT_TOOLKIT_ROOT,
    ERROR_CAP,
    ValidatorGrader,
)

FIXTURES = Path(__file__).parent / "fixtures"
_REAL_VALIDATOR = DEFAULT_TOOLKIT_ROOT / "validators" / "research_plan.py"


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout="", stderr=stderr)


def _grader_with_stub_validator(tmp_path: Path) -> tuple[ValidatorGrader, dict[str, Path]]:
    (tmp_path / "validators").mkdir()
    (tmp_path / "validators" / "research_plan.py").write_text("# stub\n")
    return ValidatorGrader(), {"toolkit_root": tmp_path}


# --- unit: score/parse logic with a mocked validator subprocess ---------------


@pytest.mark.unit
def test_pass_scores_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    grader, gold = _grader_with_stub_validator(tmp_path)
    monkeypatch.setattr(
        "claude_ablation_lab.graders.validator.subprocess.run",
        lambda *a, **k: _completed(0),
    )
    score = grader.grade(output="# Research Plan: X", gold=gold)
    assert score.value == 1.0
    assert score.status == "ok"
    assert score.subscores["errors"] == 0.0


@pytest.mark.unit
def test_failure_gives_error_count_partial_credit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grader, gold = _grader_with_stub_validator(tmp_path)
    stderr = (
        "  - '## Sub-areas': has 2 top-level bullets, need at least 4\n"
        "  - '## Claim family taxonomy': has 1 top-level bullets, need at least 3\n"
        "VALIDATION FAILED: 2 error(s) in /tmp/research_plan.md\n"
    )
    monkeypatch.setattr(
        "claude_ablation_lab.graders.validator.subprocess.run",
        lambda *a, **k: _completed(1, stderr),
    )
    score = grader.grade(output="...", gold=gold)
    assert score.value == pytest.approx(1.0 - 2 / ERROR_CAP)
    assert score.subscores["errors"] == 2.0
    assert any("Sub-areas" in e for e in score.details["errors"])


@pytest.mark.unit
def test_usage_error_is_grader_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    grader, gold = _grader_with_stub_validator(tmp_path)
    monkeypatch.setattr(
        "claude_ablation_lab.graders.validator.subprocess.run",
        lambda *a, **k: _completed(2, "usage: research_plan.py <file>"),
    )
    assert grader.grade(output="x", gold=gold).status == "grader_error"


@pytest.mark.unit
def test_missing_validator_is_grader_error(tmp_path: Path) -> None:
    # tmp_path has no validators/research_plan.py
    score = ValidatorGrader().grade(output="x", gold={"toolkit_root": tmp_path})
    assert score.status == "grader_error"
    assert "not found" in score.details["reason"]


@pytest.mark.unit
def test_crash_without_summary_is_grader_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # exit 1 with a traceback but no "VALIDATION FAILED" summary = crash, not 0.8 credit.
    grader, gold = _grader_with_stub_validator(tmp_path)
    monkeypatch.setattr(
        "claude_ablation_lab.graders.validator.subprocess.run",
        lambda *a, **k: _completed(1, "Traceback (most recent call last):\n  RuntimeError\n"),
    )
    score = grader.grade(output="x", gold=gold)
    assert score.status == "grader_error"
    assert score.value == 0.0


# --- integration: the real research_toolkit validator -------------------------


@pytest.mark.integration
@pytest.mark.skipif(not _REAL_VALIDATOR.is_file(), reason="research_toolkit not present")
def test_real_validator_passes_valid_plan() -> None:
    content = (FIXTURES / "research_plan_valid.md").read_text(encoding="utf-8")
    score = ValidatorGrader().grade(output=content, gold={})
    assert score.value == 1.0, score.details


@pytest.mark.integration
@pytest.mark.skipif(not _REAL_VALIDATOR.is_file(), reason="research_toolkit not present")
def test_real_validator_flags_broken_plan() -> None:
    content = (FIXTURES / "research_plan_broken.md").read_text(encoding="utf-8")
    score = ValidatorGrader().grade(output=content, gold={})
    assert score.value < 1.0
    assert score.subscores["errors"] >= 1.0
