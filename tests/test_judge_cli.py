"""CLI wiring for the judge commands: the controls gate refusal, controls-only
scoring against stored rows, and judge-report rendering. Fake judges are injected
by monkeypatching the registry — no external CLI runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_ablation_lab.cli.main import app
from claude_ablation_lab.judge import JudgeCall
from claude_ablation_lab.judge_ledger import append_judge_row
from claude_ablation_lab.ledger import LedgerRow, append_row
from tests.test_judge_controls import _passing_rows

cli = CliRunner()


def _suite(tmp_path: Path) -> Path:
    ref = tmp_path / "ref.tex"
    ref.write_text("\\los{X-1.1}{define}{...}", encoding="utf-8")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "t9_fake.yaml").write_text(
        f"""
id: t9_fake
domain: authoring
grader: authoring_conventions
mode: single
infra_repo: null
prompt: "Write the section."
params:
  reference_files: ["{ref}"]
gold:
  family: latex_guide
""",
        encoding="utf-8",
    )
    return suite


def _contestant_ledger(tmp_path: Path, suite: Path) -> Path:
    from claude_ablation_lab.prepare import prepare_task
    from claude_ablation_lab.task import load_all

    [task] = load_all(suite)
    sha = prepare_task(task).spec_sha
    led = tmp_path / "contestants.jsonl"
    for config, cost in [("claude-fable-5/low", 0.05), ("sonnet/high", 0.20)]:
        model, effort = config.split("/")
        out = tmp_path / f"out-{model}-{effort}.txt"
        out.write_text(f"output of {config}", encoding="utf-8")
        append_row(
            led,
            LedgerRow(
                task_id="t9_fake",
                model=model,
                effort=effort,
                variant="none",
                epoch=0,
                grader_version="authoring-conv-v1",
                run_id=f"r-{config}",
                run_status="ok",
                cost_usd=cost,
                latency_s=60.0,
                returncode=0,
                model_resolved=model,
                num_turns=1,
                grade_status="ok",
                value=1.0,
                spec_sha=sha,
                output_path=str(out),
            ),
        )
    return led


class _FakeJudge:
    def __init__(self, judge_id: str, version: str) -> None:
        self._id, self._version = judge_id, version

    @property
    def judge_id(self) -> str:
        return self._id

    @property
    def version(self) -> str:
        return self._version

    def judge(self, prompt: str, *, timeout_s: float = 240.0) -> JudgeCall:
        return JudgeCall(status="ok", verdict="A", raw_text='{"winner":"A"}')


_VERSIONS = {"codex": "pj-v1+vp-v1/codex:x", "gemini": "pj-v1+vp-v1/gemini:x"}


@pytest.fixture
def fake_judges(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_ablation_lab import judges

    monkeypatch.setattr(judges, "get_judge", lambda name: _FakeJudge(name, _VERSIONS[name]))


@pytest.mark.unit
def test_judge_refuses_real_pairs_without_passing_controls(
    tmp_path: Path, fake_judges: None
) -> None:
    suite = _suite(tmp_path)
    led = _contestant_ledger(tmp_path, suite)
    result = cli.invoke(
        app,
        [
            "judge",
            str(suite),
            "--ledger",
            str(led),
            "--judge-ledger",
            str(tmp_path / "judge.jsonl"),
        ],
    )
    assert result.exit_code == 2
    assert "controls" in result.stdout.lower()
    assert "--controls-only" in result.stdout


@pytest.mark.unit
def test_judge_runs_after_stored_controls_pass(tmp_path: Path, fake_judges: None) -> None:
    suite = _suite(tmp_path)
    led = _contestant_ledger(tmp_path, suite)
    judge_led = tmp_path / "judge.jsonl"
    for judge_id, version in _VERSIONS.items():
        for row in _passing_rows(judge_id, version):
            append_judge_row(judge_led, row)
    result = cli.invoke(
        app,
        ["judge", str(suite), "--ledger", str(led), "--judge-ledger", str(judge_led)],
    )
    assert result.exit_code == 0, result.stdout
    out = " ".join(result.stdout.split())
    assert "baseline: claude-fable-5/low (measured cheapest" in out
    assert "privacy:" in result.stdout  # the OpenAI/Google notice printed pre-call
    assert "judge pass: 4 ok" in out  # 1 pair x 2 orders x 2 judges
    assert "pairwise-judge verdicts vs claude-fable-5/low" in out


@pytest.mark.unit
def test_judge_report_command_renders_contrasts(tmp_path: Path) -> None:
    from tests.test_judge_analyze import _contestant_row, _unanimous_rows

    judge_led = tmp_path / "judge.jsonl"
    for row in _unanimous_rows("claude-fable-5/high", ["claude-fable-5/high"] * 8):
        append_judge_row(judge_led, row)
    led = tmp_path / "contestants.jsonl"
    for i in range(8):
        for config in ("claude-fable-5/high", "claude-fable-5/low"):
            append_row(led, _contestant_row(f"t9_p{i:02d}", config, cost=0.1))
    result = cli.invoke(
        app,
        ["judge-report", str(judge_led), "--ledger", str(led), "--baseline", "claude-fable-5/low"],
    )
    assert result.exit_code == 0, result.stdout
    out = " ".join(result.stdout.split())
    assert "★" in out  # the primary marker survives table truncation
    assert "8/0/0" in out
    assert "Preference, not correctness" in out


@pytest.mark.unit
def test_judge_report_empty_ledger_exits_1(tmp_path: Path) -> None:
    empty = tmp_path / "judge.jsonl"
    empty.write_text("", encoding="utf-8")
    result = cli.invoke(app, ["judge-report", str(empty)])
    assert result.exit_code == 1
    assert "no real judged pairs" in result.stdout
