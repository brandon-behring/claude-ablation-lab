"""judge_orchestrate: baseline picking, pair enumeration, the call loop
(resume/retry/circuit-breaker), and the spot-check round trip. Judges are fakes;
contestant rows are canned; no subprocess, no Claude."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from claude_ablation_lab.judge import JudgeCall
from claude_ablation_lab.judge_ledger import JudgeRow, load_judge_rows
from claude_ablation_lab.judge_orchestrate import (
    JudgePassHaltedError,
    PairSpec,
    _consensus_by_pair,
    enumerate_pairs,
    pick_baseline,
    run_judge_pass,
    sample_spotcheck,
    score_spotcheck,
)
from claude_ablation_lab.ledger import LedgerRow
from claude_ablation_lab.task import Task

# --- canned contestant world ------------------------------------------------------


def _task(tmp_path: Path, task_id: str) -> Task:
    ref = tmp_path / f"{task_id}_ref.tex"
    if not ref.exists():
        ref.write_text("\\los{X-1.1}{define}{...} reference voice", encoding="utf-8")
    return Task(
        id=task_id,
        domain="authoring",
        grader="authoring_conventions",
        mode="single",
        prompt=f"Write the {task_id} section.",
        gold={"family": "latex_guide"},
        params={"reference_files": [str(ref)]},
    )


def _ledger_row(
    tmp_path: Path,
    *,
    task_id: str,
    model: str,
    effort: str,
    epoch: int,
    spec_sha: str,
    output: str = "stored output text",
    cost: float = 0.10,
) -> LedgerRow:
    out = tmp_path / "outputs" / f"{task_id}-{model}-{effort}-{epoch}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(output, encoding="utf-8")
    return LedgerRow(
        task_id=task_id,
        model=model,
        effort=effort,
        variant="none",
        epoch=epoch,
        grader_version="authoring-conv-v1",
        run_id=f"r-{task_id}-{model}-{effort}-{epoch}",
        run_status="ok",
        cost_usd=cost,
        latency_s=60.0,
        returncode=0,
        model_resolved=model,
        num_turns=1,
        grade_status="ok",
        value=1.0,
        spec_sha=spec_sha,
        output_path=str(out),
    )


def _world(tmp_path: Path, n_tasks: int = 2) -> tuple[list[Task], list[LedgerRow]]:
    """Two tasks x three configs x one epoch, spec_shas matching fresh prepare."""
    from claude_ablation_lab.prepare import prepare_task

    tasks = [_task(tmp_path, f"t9_fake_{i}") for i in range(n_tasks)]
    rows = []
    for task in tasks:
        sha = prepare_task(task).spec_sha
        for model, effort, cost in [
            ("sonnet", "high", 0.20),
            ("claude-fable-5", "low", 0.05),
            ("opus", "high", 0.90),
        ]:
            rows.append(
                _ledger_row(
                    tmp_path,
                    task_id=task.id,
                    model=model,
                    effort=effort,
                    epoch=0,
                    spec_sha=sha,
                    output=f"output of {model}/{effort} on {task.id}",
                    cost=cost,
                )
            )
    return tasks, rows


# --- fake judges --------------------------------------------------------------------


@dataclass
class FakeJudge:
    """Scripted judge: pops the next JudgeCall per invocation (thread-safe)."""

    judge_id_value: str = "codex"
    script: list[JudgeCall] = field(default_factory=list)
    default: JudgeCall = field(
        default_factory=lambda: JudgeCall(status="ok", verdict="A", raw_text='{"winner":"A"}')
    )
    calls: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def judge_id(self) -> str:
        return self.judge_id_value

    @property
    def version(self) -> str:
        return f"pj-v1+vp-v1/{self.judge_id_value}:fake"

    def judge(self, prompt: str, *, timeout_s: float = 240.0) -> JudgeCall:
        with self._lock:
            self.calls.append(prompt)
            return self.script.pop(0) if self.script else self.default


# --- pick_baseline ------------------------------------------------------------------


@pytest.mark.unit
def test_pick_baseline_is_cheapest_with_full_coverage(tmp_path: Path) -> None:
    tasks, rows = _world(tmp_path)
    assert pick_baseline(rows, {t.id for t in tasks}) == "claude-fable-5/low"


@pytest.mark.unit
def test_pick_baseline_excludes_partial_coverage_configs(tmp_path: Path) -> None:
    tasks, rows = _world(tmp_path)
    # haiku is cheapest but ran only one of the two tasks -> must not win.
    from claude_ablation_lab.prepare import prepare_task

    sha = prepare_task(tasks[0]).spec_sha
    rows.append(
        _ledger_row(
            tmp_path,
            task_id=tasks[0].id,
            model="haiku",
            effort="high",
            epoch=0,
            spec_sha=sha,
            cost=0.01,
        )
    )
    assert pick_baseline(rows, {t.id for t in tasks}) == "claude-fable-5/low"


@pytest.mark.unit
def test_pick_baseline_no_rows_refuses(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no ok contestant rows"):
        pick_baseline([], {"t9_fake_0"})


# --- enumerate_pairs ---------------------------------------------------------------


@pytest.mark.unit
def test_enumerate_baseline_pairs(tmp_path: Path) -> None:
    tasks, rows = _world(tmp_path)
    specs, dropped = enumerate_pairs(tasks, rows, baseline="claude-fable-5/low")
    assert dropped == []
    # 2 tasks x 1 epoch x 2 candidates (sonnet/high, opus/high) vs baseline.
    assert len(specs) == 4
    for s in specs:
        assert s.config_a < s.config_b
        assert "claude-fable-5/low" in (s.config_a, s.config_b)
        assert s.output_a.strip() and s.output_b.strip()
        assert s.output_sha_a != s.output_sha_b


@pytest.mark.unit
def test_enumerate_all_pairs_mode(tmp_path: Path) -> None:
    tasks, rows = _world(tmp_path)
    specs, _ = enumerate_pairs(tasks, rows, baseline="claude-fable-5/low", pairs="all")
    assert len(specs) == 6  # C(3,2) x 2 tasks


@pytest.mark.unit
def test_enumerate_drops_missing_side_with_reason(tmp_path: Path) -> None:
    tasks, rows = _world(tmp_path)
    rows = [r for r in rows if not (r.model == "opus" and r.task_id == tasks[0].id)]
    specs, dropped = enumerate_pairs(tasks, rows, baseline="claude-fable-5/low")
    assert len(specs) == 3
    assert len(dropped) == 1
    assert "no ok run for opus/high" in dropped[0]


@pytest.mark.unit
def test_enumerate_refuses_stale_spec_sha(tmp_path: Path) -> None:
    tasks, rows = _world(tmp_path)
    stale = [
        (
            r
            if r.task_id != tasks[0].id
            else _ledger_row(
                tmp_path,
                task_id=r.task_id,
                model=r.model,
                effort=r.effort,
                epoch=r.epoch,
                spec_sha="0" * 16,
            )
        )
        for r in rows
    ]
    with pytest.raises(ValueError, match="stale output"):
        enumerate_pairs(tasks, stale, baseline="claude-fable-5/low")


@pytest.mark.unit
def test_enumerate_drops_empty_output_with_reason(tmp_path: Path) -> None:
    from claude_ablation_lab.prepare import prepare_task

    tasks, rows = _world(tmp_path)
    sha = prepare_task(tasks[0]).spec_sha
    rows = [
        (
            r
            if not (r.model == "sonnet" and r.task_id == tasks[0].id)
            else _ledger_row(
                tmp_path,
                task_id=r.task_id,
                model="sonnet",
                effort="high",
                epoch=0,
                spec_sha=sha,
                output="   \n",
            )
        )
        for r in rows
    ]
    specs, dropped = enumerate_pairs(tasks, rows, baseline="claude-fable-5/low")
    assert len(specs) == 3
    assert "empty stored output for sonnet/high" in dropped[0]


@pytest.mark.unit
def test_enumerate_unknown_baseline_refuses(tmp_path: Path) -> None:
    tasks, rows = _world(tmp_path)
    with pytest.raises(ValueError, match="baseline 'haiku/low' has no ok rows"):
        enumerate_pairs(tasks, rows, baseline="haiku/low")


# --- run_judge_pass ----------------------------------------------------------------


def _one_pair(control: str = "none") -> PairSpec:
    return PairSpec(
        task_id="t9_fake_0",
        epoch=0,
        config_a="claude-fable-5/low",
        config_b="sonnet/high",
        spec_sha="s" * 16,
        assignment="Write the section.",
        output_a="fable text",
        output_b="sonnet text",
        output_sha_a="a" * 16,
        output_sha_b="b" * 16,
        control=control,
    )


@pytest.mark.unit
def test_pass_judges_both_orders_and_canonicalizes(tmp_path: Path) -> None:
    judge = FakeJudge()  # always answers "A" for whatever is shown first
    summary = run_judge_pass(
        [_one_pair()],
        [judge],
        ledger_path=tmp_path / "judge.jsonl",
        transcripts_dir=tmp_path / "transcripts",
        max_workers=1,
    )
    assert summary.n_ok == 2 and summary.n_failed_final == 0
    rows = load_judge_rows(tmp_path / "judge.jsonl")
    by_order = {r.order: r for r in rows}
    # "A" in ab-order means config_a; "A" in ba-order means config_b.
    assert by_order["ab"].verdict == "a"
    assert by_order["ba"].verdict == "b"
    for r in rows:
        assert r.transcript_path and Path(r.transcript_path).is_file()
        assert r.output_chars_a == len("fable text")
        assert r.cost_usd is None


@pytest.mark.unit
def test_pass_resumes_from_ok_rows(tmp_path: Path) -> None:
    judge = FakeJudge()
    kwargs: dict[str, object] = {
        "ledger_path": tmp_path / "judge.jsonl",
        "transcripts_dir": tmp_path / "t",
        "max_workers": 1,
    }
    run_judge_pass([_one_pair()], [judge], **kwargs)
    again = run_judge_pass([_one_pair()], [judge], **kwargs)
    assert again.n_skipped_resume == 2
    assert len(judge.calls) == 2  # no new CLI calls on resume


@pytest.mark.unit
def test_pass_retries_once_and_appends_failed_attempt(tmp_path: Path) -> None:
    judge = FakeJudge(
        script=[
            JudgeCall(status="timeout"),
            JudgeCall(status="ok", verdict="tie", raw_text='{"winner":"tie"}'),
        ]
    )
    slept: list[float] = []
    run_judge_pass(
        [_one_pair()],
        [judge],
        ledger_path=tmp_path / "judge.jsonl",
        transcripts_dir=tmp_path / "t",
        max_workers=1,
        backoff_s=30.0,
        sleep=slept.append,
    )
    rows = load_judge_rows(tmp_path / "judge.jsonl")
    assert [r.status for r in rows].count("timeout") == 1  # the failed attempt is on record
    assert [r.status for r in rows].count("ok") == 2
    assert slept == [30.0]


@pytest.mark.unit
def test_circuit_breaker_halts_on_consecutive_failures(tmp_path: Path) -> None:
    judge = FakeJudge(default=JudgeCall(status="error", reason="quota"))
    pairs = [
        PairSpec(
            task_id=f"t9_fake_{i}",
            epoch=0,
            config_a="claude-fable-5/low",
            config_b="sonnet/high",
            spec_sha="s" * 16,
            assignment="x",
            output_a="one",
            output_b="two",
            output_sha_a="a" * 16,
            output_sha_b="b" * 16,
        )
        for i in range(8)
    ]
    with pytest.raises(JudgePassHaltedError, match="consecutive failures"):
        run_judge_pass(
            pairs,
            [judge],
            ledger_path=tmp_path / "judge.jsonl",
            transcripts_dir=tmp_path / "t",
            max_workers=1,
            max_retries=0,
            sleep=lambda _s: None,
        )
    # The ledger stays resumable: appended failures, no ok rows.
    rows = load_judge_rows(tmp_path / "judge.jsonl")
    assert rows and all(r.status == "error" for r in rows)


# --- spot-check: consensus, decisive-gate sampling, dual-metric scoring ---------------


def _pair(task_id: str, config_a: str = "claude-fable-5/low") -> PairSpec:
    return PairSpec(
        task_id=task_id,
        epoch=0,
        config_a=config_a,
        config_b="sonnet/high",
        spec_sha="s" * 16,
        assignment="Write the section.",
        output_a=f"{config_a} text",
        output_b="sonnet text",
        output_sha_a="a" * 16,
        output_sha_b="b" * 16,
    )


def _judge_rows_for(spec: PairSpec, canonical: dict[str, str]) -> list[JudgeRow]:
    """Both-order rows per judge that debias to the given canonical verdict.

    Storing the same canonical verdict on the ``ab`` and ``ba`` rows makes
    ``debias`` return it; disagreeing judges force a consensus tie.
    """
    return [
        JudgeRow(
            task_id=spec.task_id,
            epoch=spec.epoch,
            config_a=spec.config_a,
            config_b=spec.config_b,
            order=order,
            judge_id=judge_id,
            judge_version=f"pj-v1+vp-v1/{judge_id}:fake",
            spec_sha=spec.spec_sha,
            output_sha_a=spec.output_sha_a,
            output_sha_b=spec.output_sha_b,
            status="ok",
            verdict=verdict,
        )
        for judge_id, verdict in canonical.items()
        for order in ("ab", "ba")
    ]


def _fill_pair1(text: str, target: str) -> str:
    """Fill pair 1's verdict so it maps to canonical ``target`` (a|b|tie)."""
    order = re.search(r"<!-- pair:1 key:[^ ]+ order:(ab|ba) -->", text).group(1)  # type: ignore[union-attr]
    if target == "tie":
        raw = "tie"
    elif order == "ab":
        raw = "A" if target == "a" else "B"
    else:
        raw = "B" if target == "a" else "A"
    return text.replace("your_verdict:  <!-- pair 1: A | B | tie -->", f"your_verdict: {raw}")


@pytest.mark.unit
def test_consensus_by_pair_decisive_and_forced_tie() -> None:
    dec = _pair("t9_fake_0")
    key = (dec.task_id, dec.epoch, dec.config_a, dec.config_b)
    assert _consensus_by_pair(_judge_rows_for(dec, {"codex": "a", "gemini": "a"}))[key] == "a"
    # judges disagree -> forced consensus tie (judge noise, not a property of outputs)
    assert _consensus_by_pair(_judge_rows_for(dec, {"codex": "a", "gemini": "b"}))[key] == "tie"


@pytest.mark.unit
def test_spotcheck_decisive_only_excludes_ties(tmp_path: Path) -> None:
    dec, tie = _pair("t9_dec"), _pair("t9_tie")
    rows = _judge_rows_for(dec, {"codex": "a", "gemini": "a"}) + _judge_rows_for(
        tie, {"codex": "a", "gemini": "b"}
    )
    out = sample_spotcheck(rows, [dec, tie], n=10, seed=1, out_path=tmp_path / "s.md")
    keys = re.findall(r"key:([^ ]+) order:", out.read_text(encoding="utf-8"))
    assert len(keys) == 1 and keys[0].startswith("t9_dec")  # the tie pair is not sampled


@pytest.mark.unit
def test_spotcheck_decisive_only_raises_when_all_ties(tmp_path: Path) -> None:
    tie = _pair("t9_tie")
    rows = _judge_rows_for(tie, {"codex": "a", "gemini": "b"})
    with pytest.raises(ValueError, match="no decisive"):
        sample_spotcheck(rows, [tie], n=1, seed=1, out_path=tmp_path / "s.md")


@pytest.mark.unit
def test_spotcheck_gate_agrees_when_human_matches_decisive(tmp_path: Path) -> None:
    dec = _pair("t9_fake_0")
    rows = _judge_rows_for(dec, {"codex": "a", "gemini": "a"})
    out = sample_spotcheck(rows, [dec], n=1, seed=3, out_path=tmp_path / "s.md")
    (tmp_path / "f.md").write_text(
        _fill_pair1(out.read_text(encoding="utf-8"), "a"), encoding="utf-8"
    )
    rep = score_spotcheck(tmp_path / "f.md", rows)
    assert rep.n_scored == 1 and rep.n_agree == 1 and rep.agreement == 1.0
    assert rep.n_strict_scored == 1 and rep.n_strict_agree == 1


@pytest.mark.unit
def test_spotcheck_human_tie_on_decisive_pair_is_a_miss(tmp_path: Path) -> None:
    dec = _pair("t9_fake_0")
    rows = _judge_rows_for(dec, {"codex": "a", "gemini": "a"})
    out = sample_spotcheck(rows, [dec], n=1, seed=3, out_path=tmp_path / "s.md")
    (tmp_path / "f.md").write_text(
        _fill_pair1(out.read_text(encoding="utf-8"), "tie"), encoding="utf-8"
    )
    rep = score_spotcheck(tmp_path / "f.md", rows)
    assert rep.n_scored == 1 and rep.n_agree == 0 and rep.agreement == 0.0
    assert rep.n_human_tie_on_decisive == 1
    assert rep.n_strict_scored == 1 and rep.n_strict_agree == 0  # strict also misses


@pytest.mark.unit
def test_spotcheck_strict_counts_tie_agreement_while_gate_excludes_it(tmp_path: Path) -> None:
    tie = _pair("t9_fake_0")
    rows = _judge_rows_for(tie, {"codex": "a", "gemini": "b"})  # consensus tie
    out = sample_spotcheck(
        rows, [tie], n=1, seed=2, decisive_only=False, out_path=tmp_path / "s.md"
    )
    (tmp_path / "f.md").write_text(
        _fill_pair1(out.read_text(encoding="utf-8"), "tie"), encoding="utf-8"
    )
    rep = score_spotcheck(tmp_path / "f.md", rows)
    assert rep.n_strict_scored == 1 and rep.n_strict_agree == 1  # tie == tie, strict view
    assert rep.n_scored == 0 and rep.agreement is None  # the gate excludes the tie pair


@pytest.mark.unit
def test_spotcheck_stratify_guarantees_headline_contrast(tmp_path: Path) -> None:
    specs: list[PairSpec] = []
    rows: list[JudgeRow] = []
    for i in range(3):
        fh = _pair(f"t9_fh_{i}", config_a="claude-fable-5/high")
        op = _pair(f"t9_op_{i}", config_a="opus/high")
        specs += [fh, op]
        rows += _judge_rows_for(fh, {"codex": "a", "gemini": "a"})
        rows += _judge_rows_for(op, {"codex": "a", "gemini": "a"})
    out = sample_spotcheck(
        rows,
        specs,
        n=4,
        seed=5,
        stratify=("claude-fable-5/high",),
        min_per_stratum=3,
        out_path=tmp_path / "s.md",
    )
    keys = re.findall(r"key:([^ ]+) order:", out.read_text(encoding="utf-8"))
    assert len(keys) == 4
    assert sum("claude-fable-5/high" in k for k in keys) >= 3  # stratum guaranteed
