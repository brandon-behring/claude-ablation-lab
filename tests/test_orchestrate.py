"""Orchestrator: run/skip/re-grade paths, back-off, halt, capture, isolation."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from claude_ablation_lab import orchestrate
from claude_ablation_lab.grade import Score
from claude_ablation_lab.grid import Cell, Grid
from claude_ablation_lab.ledger import LedgerRow, load_rows
from claude_ablation_lab.orchestrate import (
    SweepHaltedError,
    _capture_output,
    _regrade_row,
    estimate_sweep,
    regrade_ledger,
    run_sweep,
    run_with_backoff,
)
from claude_ablation_lab.prepare import Prepared
from claude_ablation_lab.provenance import Provenance
from claude_ablation_lab.runner import RunResult
from claude_ablation_lab.task import Task

# Anchor gold: a quote that IS a substring of the source → grades 1.0.
_SOURCE = "alpha beta gamma delta"
_CLAIMS = json.dumps({"claims": [{"claim": "c", "quote": "beta gamma"}]})


def _ok(*, run_id: str, output: str = _CLAIMS) -> RunResult:
    return RunResult(
        run_id=run_id,
        status="ok",
        output=output,
        cost_usd=0.01,
        latency_s=0.5,
        returncode=0,
        model_resolved="m",
        num_turns=1,
        session_id="s",
        usage={},
        transcript_path=None,
        raw=None,
    )


def _failed(*, run_id: str, status: str, output: str) -> RunResult:
    return RunResult(
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        output=output,
        cost_usd=0.0,
        latency_s=0.1,
        returncode=1,
        model_resolved=None,
        num_turns=0,
        session_id=None,
        usage={},
        transcript_path=None,
        raw=None,
    )


_HARD_LIMIT = "API Error: 400 You have reached your specified API usage limits. You will regain access on 2026-07-01"  # noqa: E501


@dataclass
class FakeRunner:
    """Records call count; delegates each call to a responder(**kw, n=call_index)."""

    responder: Callable[..., RunResult]
    calls: int = 0

    def run(self, prompt: str, *, model: str, effort: str, cwd: Path, **kw: Any) -> RunResult:
        self.calls += 1
        return self.responder(
            prompt=prompt, model=model, effort=effort, cwd=Path(cwd), n=self.calls
        )


@dataclass
class FakeGrader:
    """Versioned grader: scores 1.0 for any non-empty output (no eval_toolkit needed)."""

    version: str = "fake-v1"

    def grade(self, *, output: str, gold: Any) -> Score:
        return Score(value=1.0 if output else 0.0, subscores={"hit": 1.0}, details={})


@dataclass
class RaisingGrader:
    """A buggy grader that raises — must become grader_error, not crash the sweep."""

    version: str = "raise-v1"

    def grade(self, *, output: str, gold: Any) -> Score:
        raise RuntimeError("boom in grader")


@pytest.fixture(autouse=True)
def _stub_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep run_sweep hermetic — no real claude/git/mcp subprocess in unit tests."""
    monkeypatch.setattr(
        orchestrate,
        "gather_provenance",
        lambda **_k: Provenance("2.1.193", "deadbeef", "globl", ("mcp1",)),
    )


def _anchor_task() -> Task:
    return Task(
        id="t3",
        domain="extraction",
        grader="anchor",
        mode="single",
        prompt="x",
        infra_repo=None,
        gold={"source_text": _SOURCE, "expected_claims": 1},
    )


def _sweep_kwargs(tmp_path: Path) -> dict[str, Any]:
    return {
        "ledger_path": tmp_path / "ledger.jsonl",
        "outputs_dir": tmp_path / "outputs",
        "now": lambda: "2026-06-25T00:00:00+00:00",
        "sleep": lambda _s: None,
    }


# --- run_with_backoff -------------------------------------------------------- #


@pytest.mark.unit
def test_backoff_retries_transient_then_succeeds() -> None:
    seq = [_failed(run_id="a", status="rate_limited", output="429 overloaded"), _ok(run_id="b")]
    runner = FakeRunner(lambda **kw: seq.pop(0))
    slept: list[float] = []
    result = run_with_backoff(
        runner,
        Prepared(prompt="p"),
        Cell("t", "haiku", "low", "none", 0),
        Path("."),
        max_retries=3,
        base_s=1.0,
        sleep=slept.append,
    )
    assert result.status == "ok"
    assert slept == [1.0]  # one back-off before the retry succeeded


@pytest.mark.unit
def test_backoff_hard_limit_halts_immediately() -> None:
    runner = FakeRunner(lambda **kw: _failed(run_id="x", status="rate_limited", output=_HARD_LIMIT))
    with pytest.raises(SweepHaltedError, match="hard usage limit"):
        run_with_backoff(
            runner,
            Prepared(prompt="p"),
            Cell("t", "haiku", "low", "none", 0),
            Path("."),
            max_retries=3,
            base_s=1.0,
            sleep=lambda _s: None,
        )
    assert runner.calls == 1  # no pointless retries against a dated cap


@pytest.mark.unit
def test_backoff_persistent_throttle_halts_after_retries() -> None:
    runner = FakeRunner(
        lambda **kw: _failed(run_id="x", status="rate_limited", output="429 overloaded")
    )
    with pytest.raises(SweepHaltedError, match="still rate-limited"):
        run_with_backoff(
            runner,
            Prepared(prompt="p"),
            Cell("t", "haiku", "low", "none", 0),
            Path("."),
            max_retries=2,
            base_s=0.1,
            sleep=lambda _s: None,
        )
    assert runner.calls == 3  # initial + 2 retries


# --- _capture_output --------------------------------------------------------- #


@pytest.mark.unit
def test_capture_stdout_when_no_artifact() -> None:
    out, missing = _capture_output(Prepared(prompt="p"), _ok(run_id="r", output="hi"), Path("."))
    assert (out, missing) == ("hi", False)


@pytest.mark.unit
def test_capture_reads_artifact_file(tmp_path) -> None:
    (tmp_path / "plan.md").write_text("PLAN BODY", encoding="utf-8")
    prep = Prepared(prompt="p", artifact="plan.md")
    out, missing = _capture_output(prep, _ok(run_id="r", output="stdout"), tmp_path)
    assert out == "PLAN BODY" and missing is False  # artifact wins over stdout


@pytest.mark.unit
def test_capture_missing_artifact_flags_quality_failure(tmp_path) -> None:
    prep = Prepared(prompt="p", artifact="plan.md")
    out, missing = _capture_output(prep, _ok(run_id="r"), tmp_path)
    assert out == "" and missing is True


@pytest.mark.unit
def test_capture_ignores_artifact_older_than_run(tmp_path) -> None:
    # A file restored by reset_clean (older than the run) must NOT be read as output.
    (tmp_path / "plan.md").write_text("PRE-EXISTING (committed)", encoding="utf-8")
    prep = Prepared(prompt="p", artifact="plan.md")
    out, missing = _capture_output(prep, _ok(run_id="r"), tmp_path, since=time.time() + 10)
    assert out == "" and missing is True


# --- run_sweep --------------------------------------------------------------- #


@pytest.mark.unit
def test_run_sweep_happy_path_writes_graded_rows(tmp_path) -> None:
    runner = FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}"))
    grid = Grid(("haiku", "sonnet"), ("low",), ("none",), 1)
    summary = run_sweep([_anchor_task()], grid, runner=runner, **_sweep_kwargs(tmp_path))
    assert (summary.total, summary.ran, summary.failed) == (2, 2, 0)
    assert summary.graded_ok == 2  # grade-status breakdown surfaced
    rows = load_rows(tmp_path / "ledger.jsonl")
    assert len(rows) == 2
    assert all(r.run_status == "ok" and r.value == 1.0 for r in rows)
    assert all(r.claude_version == "2.1.193" for r in rows)  # provenance stamped
    assert all(r.spec_sha for r in rows)  # spec fingerprint stamped
    # The gradeable output was persisted (enables re-grade).
    assert all(r.output_path and Path(r.output_path).is_file() for r in rows)


@pytest.mark.unit
def test_run_sweep_is_resumable(tmp_path) -> None:
    grid = Grid(("haiku",), ("low",), ("none",), 1)
    first = FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}"))
    run_sweep([_anchor_task()], grid, runner=first, **_sweep_kwargs(tmp_path))
    second = FakeRunner(lambda n, **kw: _ok(run_id=f"x{n}"))
    summary = run_sweep([_anchor_task()], grid, runner=second, **_sweep_kwargs(tmp_path))
    assert summary.skipped == 1 and summary.ran == 0
    assert second.calls == 0  # no re-run of an already-ok cell


@pytest.mark.unit
def test_run_sweep_regrades_stored_output_on_version_bump(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = {"v": "g1"}
    monkeypatch.setattr(orchestrate, "get_grader", lambda _name: FakeGrader(version["v"]))
    grid = Grid(("haiku",), ("low",), ("none",), 1)

    first = FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}"))
    run_sweep([_anchor_task()], grid, runner=first, **_sweep_kwargs(tmp_path))

    version["v"] = "g2"  # grader fixed → new version
    second = FakeRunner(lambda n, **kw: _ok(run_id=f"x{n}"))
    summary = run_sweep([_anchor_task()], grid, runner=second, **_sweep_kwargs(tmp_path))
    assert summary.regraded == 1 and summary.ran == 0
    assert second.calls == 0  # re-graded from STORED output, no Claude call
    rows = load_rows(tmp_path / "ledger.jsonl")
    assert {r.grader_version for r in rows} == {"g1", "g2"}
    # The re-grade row carries no model cost (P6 — naive SUM(cost) stays correct).
    regraded_row = next(r for r in rows if r.grader_version == "g2")
    assert regraded_row.cost_usd == 0.0 and regraded_row.details.get("regrade_of")


@pytest.mark.unit
def test_run_sweep_infra_failure_is_grader_error_not_quality_zero(tmp_path) -> None:
    runner = FakeRunner(
        lambda n, **kw: _failed(run_id=f"r{n}", status="infra_error", output="boom")
    )
    grid = Grid(("haiku",), ("low",), ("none",), 1)
    summary = run_sweep([_anchor_task()], grid, runner=runner, **_sweep_kwargs(tmp_path))
    assert summary.failed == 1 and summary.ran == 0
    [row] = load_rows(tmp_path / "ledger.jsonl")
    assert row.run_status == "infra_error"
    assert row.grade_status == "grader_error"  # NOT a quality-0 model result
    assert row.output_path is None  # failed runs don't persist a gradeable output


@pytest.mark.unit
def test_run_sweep_halts_on_hard_limit_and_stays_resumable(tmp_path) -> None:
    runner = FakeRunner(
        lambda n, **kw: _failed(run_id=f"r{n}", status="rate_limited", output=_HARD_LIMIT)
    )
    grid = Grid(("haiku", "sonnet"), ("low",), ("none",), 1)
    summary = run_sweep([_anchor_task()], grid, runner=runner, **_sweep_kwargs(tmp_path))
    assert summary.halted is True and summary.halt_reason
    assert summary.ran == 0
    assert load_rows(tmp_path / "ledger.jsonl") == []  # nothing committed before the cap


@pytest.mark.unit
def test_regrade_ledger_appends_new_version_rows(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    version = {"v": "g1"}
    monkeypatch.setattr(orchestrate, "get_grader", lambda _name: FakeGrader(version["v"]))
    grid = Grid(("haiku",), ("low",), ("none",), 1)
    runner = FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}"))
    run_sweep([_anchor_task()], grid, runner=runner, **_sweep_kwargs(tmp_path))

    version["v"] = "g2"
    summary = regrade_ledger(
        [_anchor_task()], ledger_path=tmp_path / "ledger.jsonl", now=lambda: "T2"
    )
    assert summary.regraded == 1
    rows = load_rows(tmp_path / "ledger.jsonl")
    assert {r.grader_version for r in rows} == {"g1", "g2"}

    # Re-grading again at the same version is a no-op (already done).
    again = regrade_ledger(
        [_anchor_task()], ledger_path=tmp_path / "ledger.jsonl", now=lambda: "T3"
    )
    assert again.regraded == 0 and again.skipped >= 1


@pytest.mark.unit
def test_run_sweep_reruns_when_task_spec_changes(tmp_path) -> None:
    grid = Grid(("haiku",), ("low",), ("none",), 1)
    first = FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}"))
    run_sweep([_anchor_task()], grid, runner=first, **_sweep_kwargs(tmp_path))
    # Same task id, DIFFERENT gold → new spec_sha → must re-run, never stale-skip.
    changed = Task(
        id="t3",
        domain="extraction",
        grader="anchor",
        mode="single",
        prompt="x",
        gold={"source_text": "an entirely different source", "expected_claims": 1},
    )
    second = FakeRunner(lambda n, **kw: _ok(run_id=f"x{n}"))
    summary = run_sweep([changed], grid, runner=second, **_sweep_kwargs(tmp_path))
    assert summary.ran == 1 and summary.skipped == 0  # re-ran, not silently skipped
    assert second.calls == 1
    assert len({r.spec_sha for r in load_rows(tmp_path / "ledger.jsonl")}) == 2


@pytest.mark.unit
def test_run_sweep_grader_exception_is_grader_error_not_crash(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrate, "get_grader", lambda _name: RaisingGrader())
    grid = Grid(("haiku",), ("low",), ("none",), 1)
    runner = FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}"))
    summary = run_sweep([_anchor_task()], grid, runner=runner, **_sweep_kwargs(tmp_path))
    assert summary.ran == 1 and summary.grader_error == 1  # sweep survived
    [row] = load_rows(tmp_path / "ledger.jsonl")
    assert row.run_status == "ok"  # the paid run is preserved, not lost to the crash
    assert row.grade_status == "grader_error"
    assert "grader_exception" in row.details
    assert row.output_path and Path(row.output_path).is_file()  # re-gradable later


@pytest.mark.unit
def test_run_sweep_rejects_duplicate_task_ids(tmp_path) -> None:
    grid = Grid(("haiku",), ("low",), ("none",), 1)
    runner = FakeRunner(lambda n, **kw: _ok(run_id="r"))
    with pytest.raises(ValueError, match="duplicate task ids"):
        run_sweep([_anchor_task(), _anchor_task()], grid, runner=runner, **_sweep_kwargs(tmp_path))


@pytest.mark.unit
def test_regrade_row_carries_artifact_missing_and_zeros_cost() -> None:
    prior = LedgerRow(
        task_id="t2",
        model="haiku",
        effort="low",
        variant="r@HEAD",
        epoch=0,
        grader_version="g1",
        run_id="run-abc",
        run_status="ok",
        cost_usd=0.5,
        latency_s=3.0,
        returncode=0,
        model_resolved="m",
        num_turns=2,
        session_id="s",
        grade_status="ok",
        value=0.0,
        spec_sha="S",
        details={"artifact_missing": True},
        output_path="x",
    )
    row, score = _regrade_row(prior, FakeGrader("g2"), "fresh output", {}, "T2")
    assert row.grader_version == "g2"
    assert row.details["artifact_missing"] is True  # run-level marker carried forward
    assert row.details["regrade_of"] == "run-abc"  # audit trail to the original run
    assert row.cost_usd == 0.0 and row.latency_s == 0.0  # no model cost on a re-grade
    assert score.status == "ok"


# --- estimate ---------------------------------------------------------------- #


def _ok_usage(*, run_id: str) -> RunResult:
    return RunResult(
        run_id=run_id,
        status="ok",
        output=_CLAIMS,
        cost_usd=0.01,
        latency_s=0.5,
        returncode=0,
        model_resolved="m",
        num_turns=2,
        session_id="s",
        usage={"input_tokens": 100, "output_tokens": 50},
        transcript_path=None,
        raw=None,
    )


@pytest.mark.unit
def test_estimate_projects_from_one_cell(tmp_path) -> None:
    runner = FakeRunner(lambda n, **kw: _ok_usage(run_id=f"r{n}"))
    grid = Grid(("haiku", "sonnet"), ("low", "high"), ("none",), 1)  # 4 cells
    est = estimate_sweep(
        [_anchor_task()], grid, runner=runner, neutral_cwd=tmp_path, sleep=lambda _s: None
    )
    assert est.n_cells == 4 and runner.calls == 1  # only ONE cell actually run
    assert est.cell_input_tokens == 100 and est.projected_input_tokens == 400
    assert est.projected_turns == 8  # 2 turns × 4 cells
    assert est.projected_cost_usd == pytest.approx(0.04)
    assert est.calibration_status == "ok"


@pytest.mark.unit
def test_estimate_handles_halt_without_raising(tmp_path) -> None:
    runner = FakeRunner(
        lambda n, **kw: _failed(run_id="x", status="rate_limited", output=_HARD_LIMIT)
    )
    grid = Grid(("haiku",), ("low",), ("none",), 1)
    est = estimate_sweep(
        [_anchor_task()], grid, runner=runner, neutral_cwd=tmp_path, sleep=lambda _s: None
    )
    assert est.calibration_status == "halted" and est.projected_cost_usd == 0.0


# --- integration: real worktree isolation ----------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.mark.integration
def test_run_sweep_resets_worktree_between_cells(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    monkeypatch.setattr(orchestrate, "get_grader", lambda _name: FakeGrader("v1"))
    leftover_seen: list[bool] = []

    def responder(*, cwd: Path, n: int, **_kw: Any) -> RunResult:
        leftover_seen.append((cwd / "leftover.txt").exists())  # from a prior, un-reset cell?
        (cwd / "leftover.txt").write_text("untracked", encoding="utf-8")
        (cwd / "plan.md").write_text(f"PLAN {n}", encoding="utf-8")
        return _ok(run_id=f"r{n}", output="ignored-stdout")

    task = Task(
        id="t2",
        domain="r",
        grader="validator",
        mode="agent",
        prompt="go",
        infra_repo=str(repo),
        params={"artifact": "plan.md"},
    )
    grid = Grid(("haiku",), ("low",), (f"{repo}@HEAD",), 2)  # 2 epochs of the same cell
    summary = run_sweep(
        [task],
        grid,
        runner=FakeRunner(responder),
        ledger_path=tmp_path / "ledger.jsonl",
        outputs_dir=tmp_path / "outputs",
        worktree_base=tmp_path / ".wt",
        now=lambda: "T",
        sleep=lambda _s: None,
    )
    assert summary.ran == 2
    assert leftover_seen == [False, False]  # reset_clean wiped the untracked file each cell
    rows = load_rows(tmp_path / "ledger.jsonl")
    assert all(r.infra_sha for r in rows)  # worktree sha stamped as provenance
    assert all("PLAN" in r.output_preview for r in rows)  # artifact captured, not stdout


@pytest.mark.integration
def test_run_sweep_bad_variant_skips_its_cells_without_crashing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrate, "get_grader", lambda _name: FakeGrader("v1"))
    task = Task(id="t2", domain="r", grader="validator", mode="agent", prompt="go", infra_repo="x")
    # A variant pointing at a non-git path: ensure_worktree fails → cells skipped, not a crash.
    grid = Grid(("haiku",), ("low",), (f"{tmp_path / 'nope'}@HEAD",), 2)
    summary = run_sweep(
        [task],
        grid,
        runner=FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}")),
        ledger_path=tmp_path / "ledger.jsonl",
        outputs_dir=tmp_path / "outputs",
        worktree_base=tmp_path / ".wt",
        now=lambda: "T",
        sleep=lambda _s: None,
    )
    assert summary.failed == 2 and summary.ran == 0  # both epochs of the bad variant dropped
    assert load_rows(tmp_path / "ledger.jsonl") == []


@pytest.mark.integration
def test_run_sweep_missing_artifact_is_quality_zero_not_infra(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    monkeypatch.setattr(orchestrate, "get_grader", lambda _name: FakeGrader("v1"))
    task = Task(
        id="t2",
        domain="r",
        grader="validator",
        mode="agent",
        prompt="go",
        infra_repo=str(repo),
        params={"artifact": "plan.md"},
    )
    grid = Grid(("haiku",), ("low",), (f"{repo}@HEAD",), 1)
    # Responder runs ok but never writes the artifact → a *quality* failure, not infra.
    run_sweep(
        [task],
        grid,
        runner=FakeRunner(lambda n, **kw: _ok(run_id=f"r{n}", output="stdout")),
        ledger_path=tmp_path / "ledger.jsonl",
        outputs_dir=tmp_path / "outputs",
        worktree_base=tmp_path / ".wt",
        now=lambda: "T",
        sleep=lambda _s: None,
    )
    [row] = load_rows(tmp_path / "ledger.jsonl")
    assert row.run_status == "ok"  # the run itself succeeded
    assert row.grade_status == "ok" and row.value == 0.0  # graded honestly as a 0, not grader_error
    assert row.details.get("artifact_missing") is True
