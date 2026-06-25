"""grade.py — Score immutability, Grader protocol, and the run/grade seam."""

from __future__ import annotations

import dataclasses

import pytest

from claude_ablation_lab.grade import Grader, Score, grade_run
from claude_ablation_lab.runner import RunResult


def _run(status: str = "ok", output: str = "x") -> RunResult:
    return RunResult(
        run_id="r",
        status=status,  # type: ignore[arg-type]
        output=output,
        cost_usd=0.0,
        latency_s=0.0,
        returncode=0,
        model_resolved=None,
        num_turns=0,
        session_id=None,
        usage={},
        transcript_path=None,
        raw=None,
    )


class _StubGrader:
    """Minimal Grader: records call count and echoes the output it saw."""

    version = "stub-v1"

    def __init__(self) -> None:
        self.calls = 0

    def grade(self, *, output: str, gold: object) -> Score:
        self.calls += 1
        return Score(value=0.7, details={"seen": output})


@pytest.mark.unit
def test_score_is_frozen() -> None:
    score = Score(value=1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        score.value = 0.0  # frozen dataclass rejects mutation


@pytest.mark.unit
def test_stub_satisfies_runtime_protocol() -> None:
    assert isinstance(_StubGrader(), Grader)


@pytest.mark.unit
def test_grade_run_ok_invokes_grader() -> None:
    grader = _StubGrader()
    score = grade_run(grader, _run("ok", "hello"), gold={})
    assert grader.calls == 1
    assert score.value == 0.7
    assert score.details["seen"] == "hello"


@pytest.mark.unit
@pytest.mark.parametrize("status", ["infra_error", "timeout", "rate_limited", "parse_fail"])
def test_grade_run_shortcircuits_non_ok_runs(status: str) -> None:
    grader = _StubGrader()
    score = grade_run(grader, _run(status, "ignored"), gold={})
    assert grader.calls == 0  # grader never sees an infra failure
    assert score.value == 0.0
    assert score.status == "grader_error"
    assert score.details["run_status"] == status
