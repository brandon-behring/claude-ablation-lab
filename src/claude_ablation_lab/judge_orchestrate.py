"""Judge-pass orchestration: pair enumeration, controls gate, the call loop.

Mirrors :mod:`claude_ablation_lab.orchestrate`'s discipline at the judge seam:

- **Resumable** — a call is skipped iff an ``ok`` row exists for its full judge
  key; failed attempts are appended (never silent) and superseded by retries.
- **Fail loud, halt on systemic failure** — a bounded retry per call, then a
  circuit breaker (consecutive or overall non-``ok``) raises
  :class:`JudgePassHaltedError`, leaving the judge ledger resumable.
- **Controls first** — real pairs refuse to run until the four validity controls
  pass for every judge at its CURRENT ``judge_version``
  (:func:`evaluate_controls` reads stored rows, so controls re-run only when a
  template/parser/model pin changes).
- **Baseline frozen before judging** — :func:`pick_baseline` is deterministic
  from contestant COST only (never quality), so the comparison set cannot be
  chosen after peeking at outcomes.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from claude_ablation_lab.judge import (
    Judge,
    JudgeCall,
    PairVerdict,
    RawVerdict,
    build_judge_prompt,
    canonical_verdict,
    debias,
)
from claude_ablation_lab.judge_ledger import (
    REAL_PAIR,
    JudgeRow,
    append_judge_row,
    latest_rows_by_judge_key,
    load_judge_rows,
    ok_rows_by_judge_key,
)
from claude_ablation_lab.ledger import LedgerRow, ok_row_by_run_key
from claude_ablation_lab.prepare import prepare_task

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from claude_ablation_lab.task import Task

__all__ = [
    "PairSpec",
    "JudgePassSummary",
    "JudgePassHaltedError",
    "ControlOutcome",
    "ControlsReport",
    "pick_baseline",
    "enumerate_pairs",
    "load_control_pairs",
    "run_judge_pass",
    "evaluate_controls",
    "sample_spotcheck",
    "score_spotcheck",
]

logger = logging.getLogger(__name__)

#: A judge prompt larger than this is dropped-with-report (agy folds the payload
#: into argv; macOS ARG_MAX is ~1 MB — stay far below it).
MAX_JUDGE_PROMPT_CHARS = 150_000
#: Circuit breaker: this many consecutive non-``ok`` finals on ONE judge halts.
MAX_CONSECUTIVE_FAILURES = 5
#: Circuit breaker: this overall non-``ok`` final rate halts (after >= 10 calls).
MAX_FAILURE_RATE = 0.20
#: Controls health gate: latest-row non-``ok`` rate above this fails the gate.
MAX_CONTROL_NONOK_RATE = 0.10


class JudgePassHaltedError(RuntimeError):
    """Systemic judge failure (circuit breaker) — the ledger stays resumable."""


@dataclass(frozen=True, slots=True)
class PairSpec:
    """One judgeable pair: two stored outputs answering the same assignment."""

    task_id: str
    epoch: int
    config_a: str  # canonical: config_a < config_b lexicographically
    config_b: str
    spec_sha: str
    assignment: str
    output_a: str
    output_b: str
    output_sha_a: str
    output_sha_b: str
    run_id_a: str = ""
    run_id_b: str = ""
    control: str = REAL_PAIR


@dataclass(frozen=True, slots=True)
class JudgePassSummary:
    """Outcome counts for one judge pass (controls or real)."""

    n_calls_planned: int
    n_skipped_resume: int
    n_ok: int
    n_failed_final: int
    dropped_pairs: tuple[str, ...] = ()


def _config(row: LedgerRow) -> str:
    return f"{row.model}/{row.effort}"


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def pick_baseline(rows: Sequence[LedgerRow], task_ids: set[str]) -> str:
    """The cheapest contestant config by measured mean ``cost_usd`` — COST ONLY.

    Deterministic and quality-blind (Codex plan review: the baseline must be
    frozen before any judge outcome exists, and must never be chosen by peeking
    at conventions scores). Only configs with the fullest ``(task, epoch)``
    coverage compete — a config that failed half its runs must not win "cheapest"
    on the cells it happened to finish. Ties break lexicographically.
    """
    ok_rows = [r for r in ok_row_by_run_key(list(rows)).values() if r.task_id in task_ids]
    if not ok_rows:
        raise ValueError("no ok contestant rows for the requested tasks — run the sweep first")
    by_config: dict[str, list[LedgerRow]] = {}
    for row in ok_rows:
        by_config.setdefault(_config(row), []).append(row)
    max_coverage = max(len(v) for v in by_config.values())
    candidates = {c: v for c, v in by_config.items() if len(v) == max_coverage}

    def mean_cost(config: str) -> float:
        cell_rows = candidates[config]
        return sum(r.cost_usd for r in cell_rows) / len(cell_rows)

    return min(candidates, key=lambda c: (mean_cost(c), c))


def enumerate_pairs(
    tasks: Sequence[Task],
    rows: Sequence[LedgerRow],
    *,
    baseline: str,
    pairs: str = "baseline",
) -> tuple[list[PairSpec], list[str]]:
    """Build the judgeable pairs from stored contestant runs.

    ``pairs="baseline"`` (the success criterion's shape) pairs every other config
    against ``baseline``; ``"all"`` pairs every config combination. Rules:

    - only the latest ``ok`` run per run key is eligible;
    - a row whose ``spec_sha`` differs from the freshly prepared task REFUSES
      loudly (stale output against a changed assignment — re-run the sweep);
    - an empty stored output is dropped-with-report, never judged;
    - a missing side or an over-cap prompt is dropped-with-report.

    Returns ``(pair_specs, dropped)`` where ``dropped`` are human-readable
    reasons — informative missingness, always surfaced by the CLI.
    """
    by_task = {t.id: t for t in tasks}
    prepared = {tid: prepare_task(t) for tid, t in by_task.items()}
    latest = ok_row_by_run_key(list(rows))

    grade_issues = sorted(
        {r.grade_status for r in latest.values() if r.task_id in by_task} - {"ok"}
    )
    if grade_issues:  # judged anyway (the conventions grader is a separate instrument)
        logger.warning("contestant rows carry non-ok grade_status values: %s", grade_issues)

    per_cell: dict[tuple[str, int], dict[str, LedgerRow]] = {}
    for row in latest.values():
        if row.task_id in by_task:
            per_cell.setdefault((row.task_id, row.epoch), {})[_config(row)] = row

    configs = sorted({c for cell in per_cell.values() for c in cell})
    if baseline not in configs:
        raise ValueError(f"baseline {baseline!r} has no ok rows (configs seen: {configs})")
    if pairs == "baseline":
        combos = [(c, baseline) for c in configs if c != baseline]
    elif pairs == "all":
        combos = [(a, b) for i, a in enumerate(configs) for b in configs[i + 1 :]]
    else:
        raise ValueError(f"unknown pairs mode: {pairs!r} (baseline|all)")

    specs: list[PairSpec] = []
    dropped: list[str] = []
    for (task_id, epoch), cell in sorted(per_cell.items()):
        prep = prepared[task_id]
        for left, right in combos:
            config_a, config_b = sorted((left, right))
            label = f"{task_id}/e{epoch} {config_a} vs {config_b}"
            row_a, row_b = cell.get(config_a), cell.get(config_b)
            if row_a is None or row_b is None:
                missing = config_a if row_a is None else config_b
                dropped.append(f"{label}: no ok run for {missing}")
                continue
            for row in (row_a, row_b):
                if row.spec_sha != prep.spec_sha:
                    raise ValueError(
                        f"stale output: {task_id} run {row.run_id} was produced against "
                        f"spec {row.spec_sha}, but the task now prepares to {prep.spec_sha} "
                        "— re-run the contestant sweep before judging"
                    )
            text_a, text_b = _read_output(row_a), _read_output(row_b)
            if not text_a.strip() or not text_b.strip():
                empty = config_a if not text_a.strip() else config_b
                dropped.append(f"{label}: empty stored output for {empty}")
                continue
            prompt_size = len(prep.prompt) + len(text_a) + len(text_b)
            if prompt_size > MAX_JUDGE_PROMPT_CHARS:
                dropped.append(f"{label}: judge prompt would be {prompt_size} chars (cap)")
                continue
            specs.append(
                PairSpec(
                    task_id=task_id,
                    epoch=epoch,
                    config_a=config_a,
                    config_b=config_b,
                    spec_sha=prep.spec_sha,
                    assignment=prep.prompt,
                    output_a=text_a,
                    output_b=text_b,
                    output_sha_a=_sha16(text_a),
                    output_sha_b=_sha16(text_b),
                    run_id_a=row_a.run_id,
                    run_id_b=row_b.run_id,
                )
            )
    return specs, dropped


def _read_output(row: LedgerRow) -> str:
    if not row.output_path:
        return ""
    path = Path(row.output_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"stored output missing for run {row.run_id}: {path} — the contestant "
            "ledger references an output file that no longer exists"
        )
    return path.read_text(encoding="utf-8", errors="replace")


# --- controls -------------------------------------------------------------------

CONTROL_TASKS = {
    "same_output": "control_same_output",
    "verbosity": "control_verbosity",
    "positive": "control_positive",
}
#: How many of the positive fixtures double as same-output null texts.
SAME_OUTPUT_FIXTURES = 4


def load_control_pairs(fixtures_root: Path | str) -> list[PairSpec]:
    """Build the control pairs from ``examples/judge-controls/`` fixtures.

    ``positive/p*/`` (good vs degraded) and ``verbosity/v*/`` (concise vs padded)
    become pairs whose config names carry the fixture role (``control/good`` …);
    the same-output null reuses the first :data:`SAME_OUTPUT_FIXTURES` good texts
    against themselves. Fixture index rides in ``epoch``.
    """
    root = Path(fixtures_root)
    if not root.is_dir():
        raise FileNotFoundError(f"controls fixtures not found: {root}")
    specs: list[PairSpec] = []

    def read(d: Path, name: str) -> str:
        return (d / name).read_text(encoding="utf-8")

    positive_dirs = sorted(p for p in (root / "positive").iterdir() if p.is_dir())
    verbosity_dirs = sorted(p for p in (root / "verbosity").iterdir() if p.is_dir())
    if not positive_dirs or not verbosity_dirs:
        raise ValueError(f"no control fixtures under {root} (positive/, verbosity/)")

    for i, d in enumerate(positive_dirs):
        prompt, good, degraded = read(d, "prompt.md"), read(d, "good.md"), read(d, "degraded.md")
        specs.append(
            PairSpec(
                task_id=CONTROL_TASKS["positive"],
                epoch=i,
                config_a="control/degraded",  # canonical: degraded < good
                config_b="control/good",
                spec_sha=_sha16(prompt),
                assignment=prompt,
                output_a=degraded,
                output_b=good,
                output_sha_a=_sha16(degraded),
                output_sha_b=_sha16(good),
                control="positive",
            )
        )
    for i, d in enumerate(verbosity_dirs):
        prompt, concise, padded = read(d, "prompt.md"), read(d, "concise.md"), read(d, "padded.md")
        specs.append(
            PairSpec(
                task_id=CONTROL_TASKS["verbosity"],
                epoch=i,
                config_a="control/concise",  # canonical: concise < padded
                config_b="control/padded",
                spec_sha=_sha16(prompt),
                assignment=prompt,
                output_a=concise,
                output_b=padded,
                output_sha_a=_sha16(concise),
                output_sha_b=_sha16(padded),
                control="verbosity",
            )
        )
    for i, d in enumerate(positive_dirs[:SAME_OUTPUT_FIXTURES]):
        prompt, good = read(d, "prompt.md"), read(d, "good.md")
        specs.append(
            PairSpec(
                task_id=CONTROL_TASKS["same_output"],
                epoch=i,
                config_a="control/left",
                config_b="control/right",
                spec_sha=_sha16(prompt),
                assignment=prompt,
                output_a=good,
                output_b=good,
                output_sha_a=_sha16(good),
                output_sha_b=_sha16(good),
                control="same_output",
            )
        )
    return specs


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ControlsReport:
    """Per-judge control outcomes; the gate passes only if every row passes."""

    per_judge: dict[str, tuple[ControlOutcome, ...]]

    @property
    def passed(self) -> bool:
        return bool(self.per_judge) and all(
            o.passed for outcomes in self.per_judge.values() for o in outcomes
        )


def evaluate_controls(rows: Sequence[JudgeRow], judge_versions: dict[str, str]) -> ControlsReport:
    """Score the stored control rows against the numeric gate (pure function).

    Operates on the LATEST row per judge key (a failed attempt superseded by a
    successful retry never poisons the health rate — plan-review finding), and
    only on rows at each judge's CURRENT version, so a template bump re-arms the
    gate automatically.

    Per judge (single denominator per rule): same-output — >= 7/8 calls ``tie``
    and 0 texts consistently preferring one side; verbosity — the padded side
    wins <= 1 debiased pair; positive — good wins >= 5/6 debiased pairs and
    degraded wins 0; health — latest-row non-``ok`` rate <= 10 %.
    """
    per_judge: dict[str, tuple[ControlOutcome, ...]] = {}
    for judge_id, version in sorted(judge_versions.items()):
        latest = [
            r
            for r in latest_rows_by_judge_key(list(rows)).values()
            if r.judge_id == judge_id and r.judge_version == version and r.control != REAL_PAIR
        ]
        if not latest:
            per_judge[judge_id] = (
                ControlOutcome("controls", False, "no control rows at the current judge_version"),
            )
            continue
        per_judge[judge_id] = (
            _same_output_outcome(latest),
            _debiased_outcome(latest, "verbosity", loser="control/padded"),
            _debiased_outcome(latest, "positive", winner="control/good"),
            _health_outcome(latest),
        )
    return ControlsReport(per_judge=per_judge)


def _same_output_outcome(rows: list[JudgeRow]) -> ControlOutcome:
    calls = [r for r in rows if r.control == "same_output" and r.status == "ok"]
    n = len(calls)
    ties = sum(1 for r in calls if r.verdict == "tie")
    by_fixture: dict[int, list[str | None]] = {}
    for r in calls:
        by_fixture.setdefault(r.epoch, []).append(r.verdict)
    consistent = sum(
        1 for vs in by_fixture.values() if len(vs) == 2 and vs[0] == vs[1] and vs[0] != "tie"
    )
    passed = n > 0 and ties >= n - 1 and consistent == 0
    return ControlOutcome(
        "same_output", passed, f"{ties}/{n} ties, {consistent} consistent side preferences"
    )


def _pair_verdicts(rows: list[JudgeRow], control: str) -> dict[int, str | None]:
    """Debiased verdict per fixture index for one control family."""
    by_order: dict[tuple[int, str], JudgeRow] = {
        (r.epoch, r.order): r for r in rows if r.control == control
    }
    fixtures = sorted({e for e, _ in by_order})
    out: dict[int, str | None] = {}
    for e in fixtures:
        ab, ba = by_order.get((e, "ab")), by_order.get((e, "ba"))
        v_ab = ab.verdict if ab is not None and ab.status == "ok" else None
        v_ba = ba.verdict if ba is not None and ba.status == "ok" else None
        out[e] = debias(v_ab, v_ba)  # type: ignore[arg-type]
    return out


def _debiased_outcome(
    rows: list[JudgeRow],
    control: str,
    *,
    winner: str | None = None,
    loser: str | None = None,
) -> ControlOutcome:
    """Verbosity: ``loser`` must win <= 1 pair. Positive: ``winner`` must win >= n-1 and the other side 0."""
    sample = [r for r in rows if r.control == control]
    if not sample:
        return ControlOutcome(control, False, "no rows")
    config_a, config_b = sample[0].config_a, sample[0].config_b
    verdicts = _pair_verdicts(rows, control)
    n = len(verdicts)
    wins_a = sum(1 for v in verdicts.values() if v == "a")
    wins_b = sum(1 for v in verdicts.values() if v == "b")
    side_wins = {config_a: wins_a, config_b: wins_b}
    if loser is not None:  # verbosity
        passed = side_wins[loser] <= 1
        detail = f"padded side won {side_wins[loser]}/{n} debiased pairs"
    else:  # positive
        assert_winner = winner if winner is not None else config_b
        other = config_a if assert_winner == config_b else config_b
        passed = side_wins[assert_winner] >= n - 1 and side_wins[other] == 0
        detail = f"good side won {side_wins[assert_winner]}/{n}, degraded won {side_wins[other]}"
    return ControlOutcome(control, passed, detail)


def _health_outcome(rows: list[JudgeRow]) -> ControlOutcome:
    non_ok = sum(1 for r in rows if r.status != "ok")
    rate = non_ok / len(rows)
    return ControlOutcome(
        "call_health",
        rate <= MAX_CONTROL_NONOK_RATE,
        f"{non_ok}/{len(rows)} latest rows non-ok ({rate:.0%})",
    )


# --- the call loop ----------------------------------------------------------------


def run_judge_pass(
    pairs: Sequence[PairSpec],
    judges: Sequence[Judge],
    *,
    ledger_path: Path | str,
    transcripts_dir: Path | str,
    timeout_s: float = 240.0,
    max_workers: int = 4,
    max_retries: int = 1,
    backoff_s: float = 30.0,
    harness_sha: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> JudgePassSummary:
    """Judge every (pair × judge × order) not already settled; append every attempt.

    Bounded retry per call (a failed attempt is appended, then superseded), then
    the circuit breaker: :data:`MAX_CONSECUTIVE_FAILURES` consecutive final
    failures on one judge, or an overall final-failure rate above
    :data:`MAX_FAILURE_RATE` (after 10+ finals), raises
    :class:`JudgePassHaltedError` — the ledger is resumable, so a halted pass
    costs nothing to restart.
    """
    ledger_path = Path(ledger_path)
    transcripts = Path(transcripts_dir)
    transcripts.mkdir(parents=True, exist_ok=True)
    done = ok_rows_by_judge_key(load_judge_rows(ledger_path))

    calls: list[tuple[PairSpec, Judge, str]] = []
    skipped = 0
    for spec in pairs:
        for judge in judges:
            for order in ("ab", "ba"):
                key = (
                    spec.task_id,
                    spec.epoch,
                    spec.config_a,
                    spec.config_b,
                    order,
                    judge.judge_id,
                    judge.version,
                    spec.spec_sha,
                    spec.output_sha_a,
                    spec.output_sha_b,
                    spec.control,
                )
                if key in done:
                    skipped += 1
                else:
                    calls.append((spec, judge, order))

    lock = threading.Lock()
    halted = threading.Event()
    consecutive: dict[str, int] = {j.judge_id: 0 for j in judges}
    finals = {"ok": 0, "failed": 0}
    halt_reason: list[str] = []

    def one_call(spec: PairSpec, judge: Judge, order: str) -> None:
        if halted.is_set():
            return
        first, second = (
            (spec.output_a, spec.output_b) if order == "ab" else (spec.output_b, spec.output_a)
        )
        prompt = build_judge_prompt(assignment=spec.assignment, first=first, second=second)
        call = judge.judge(prompt, timeout_s=timeout_s)
        for attempt in range(max_retries + 1):
            if call.status == "ok" or attempt == max_retries:
                break
            _append(spec, judge, order, call)  # the failed attempt is never silent
            if halted.is_set():
                return
            sleep(backoff_s)
            call = judge.judge(prompt, timeout_s=timeout_s)
        _append(spec, judge, order, call)
        with lock:
            if call.status == "ok":
                consecutive[judge.judge_id] = 0
                finals["ok"] += 1
            else:
                consecutive[judge.judge_id] += 1
                finals["failed"] += 1
                if consecutive[judge.judge_id] >= MAX_CONSECUTIVE_FAILURES:
                    halt_reason.append(
                        f"{judge.judge_id}: {consecutive[judge.judge_id]} consecutive failures"
                    )
                    halted.set()
                total = finals["ok"] + finals["failed"]
                if total >= 10 and finals["failed"] / total > MAX_FAILURE_RATE:
                    halt_reason.append(
                        f"overall failure rate {finals['failed']}/{total} exceeds "
                        f"{MAX_FAILURE_RATE:.0%}"
                    )
                    halted.set()

    def _append(spec: PairSpec, judge: Judge, order: str, call: JudgeCall) -> None:
        judge_run_id = uuid.uuid4().hex
        transcript: str | None = None
        if call.raw_text:
            path = transcripts / f"{judge_run_id}.txt"
            path.write_text(call.raw_text, encoding="utf-8")
            transcript = str(path)
        verdict = (
            canonical_verdict(call.verdict, order)  # type: ignore[arg-type]
            if call.status == "ok" and call.verdict is not None
            else None
        )
        row = JudgeRow(
            task_id=spec.task_id,
            epoch=spec.epoch,
            config_a=spec.config_a,
            config_b=spec.config_b,
            order=order,
            judge_id=judge.judge_id,
            judge_version=judge.version,
            spec_sha=spec.spec_sha,
            output_sha_a=spec.output_sha_a,
            output_sha_b=spec.output_sha_b,
            control=spec.control,
            status=call.status,
            verdict=verdict,
            reason=call.reason,
            judge_run_id=judge_run_id,
            run_id_a=spec.run_id_a,
            run_id_b=spec.run_id_b,
            transcript_path=transcript,
            latency_s=call.latency_s,
            output_bytes=call.output_bytes,
            output_chars_a=len(spec.output_a),
            output_chars_b=len(spec.output_b),
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            harness_sha=harness_sha,
        )
        with lock:
            append_judge_row(ledger_path, row)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(one_call, *call) for call in calls]
        for future in as_completed(futures):
            future.result()  # surface worker exceptions immediately

    summary = JudgePassSummary(
        n_calls_planned=len(calls) + skipped,
        n_skipped_resume=skipped,
        n_ok=finals["ok"],
        n_failed_final=finals["failed"],
    )
    if halted.is_set():
        raise JudgePassHaltedError(
            f"judge pass halted ({'; '.join(sorted(set(halt_reason)))}) after "
            f"{finals['ok']} ok / {finals['failed']} failed finals — "
            "the judge ledger is resumable; inspect transcripts, then re-run"
        )
    return summary


# --- human spot-check ---------------------------------------------------------------

#: Default guaranteed pairs per requested stratum in a stratified spot-check sample.
DEFAULT_SPOTCHECK_MIN_PER_STRATUM = 3

PairKey = tuple[str, int, str, str]


def _consensus_by_pair(judge_rows: Sequence[JudgeRow]) -> dict[PairKey, str | None]:
    """Strict cross-judge consensus per REAL pair.

    Each judge's two order verdicts are order-debiased (:func:`debias`: an
    order-flip disagreement collapses to ``tie``); the pair's consensus is that
    shared verdict only when every judge is present and agrees, otherwise ``tie``.
    ``None`` marks a pair with no usable verdict from any judge.
    """
    per_judge: dict[tuple[str, int, str, str, str], dict[str, PairVerdict | None]] = {}
    for r in judge_rows:
        if r.control != REAL_PAIR:
            continue
        k = (r.task_id, r.epoch, r.config_a, r.config_b, r.judge_id)
        stored = cast("PairVerdict | None", r.verdict if r.status == "ok" else None)
        per_judge.setdefault(k, {})[r.order] = stored
    judge_ids = sorted({k[4] for k in per_judge})
    consensus: dict[PairKey, str | None] = {}
    for key in {k[:4] for k in per_judge}:
        debiased = [
            debias(
                per_judge.get((*key, j), {}).get("ab"),
                per_judge.get((*key, j), {}).get("ba"),
            )
            for j in judge_ids
        ]
        present = [d for d in debiased if d is not None]
        if not present:
            consensus[key] = None
        elif all(d == present[0] for d in present) and len(present) == len(judge_ids):
            consensus[key] = present[0]
        else:
            consensus[key] = "tie"
    return consensus


def _stratified_pick(
    eligible: Sequence[PairKey],
    n: int,
    strata: Sequence[str],
    rng: random.Random,
    min_per_stratum: int,
) -> list[PairKey]:
    """Up to ``min_per_stratum`` picks per requested contestant config, rest random."""
    picks: list[PairKey] = []
    used: set[PairKey] = set()
    for stratum in strata:
        pool = [k for k in eligible if stratum in (k[2], k[3]) and k not in used]
        rng.shuffle(pool)
        for k in pool[:min_per_stratum]:
            picks.append(k)
            used.add(k)
    rest = [k for k in eligible if k not in used]
    rng.shuffle(rest)
    picks.extend(rest[: max(0, n - len(picks))])
    rng.shuffle(picks)
    return picks[:n]


def sample_spotcheck(
    judge_rows: Sequence[JudgeRow],
    pairs: Sequence[PairSpec],
    *,
    n: int = 10,
    seed: int = 42,
    out_path: Path | str,
    decisive_only: bool = True,
    stratify: Sequence[str] = (),
    min_per_stratum: int = DEFAULT_SPOTCHECK_MIN_PER_STRATUM,
) -> Path:
    """Write a blinded spot-check file of ``n`` seeded-random REAL judged pairs.

    ``decisive_only`` (default) restricts the sample to pairs whose cross-judge
    consensus is decisive (``a``/``b``): the gate scores agreement tie-excluded
    (Zheng et al. 2023's without-tie convention), so a consensus ``tie`` — often a
    mere cross-judge disagreement, not a property of the outputs — is nothing a
    human can meaningfully match. ``stratify`` names contestant configs to
    guarantee ``min_per_stratum`` pairs each (e.g. the headline contrasts), so the
    sample is not dominated by a contrast that does not headline. Presentation
    order is re-randomized per pair and recorded in an HTML comment so
    :func:`score_spotcheck` can map verdicts back to the canonical frame without
    the human ever seeing config names.
    """
    real = {(p.task_id, p.epoch, p.config_a, p.config_b): p for p in pairs}
    consensus = _consensus_by_pair(judge_rows)
    judged = sorted(
        {
            (r.task_id, r.epoch, r.config_a, r.config_b)
            for r in judge_rows
            if r.control == REAL_PAIR and r.status == "ok"
        }
    )
    eligible = [k for k in judged if k in real]
    if decisive_only:
        eligible = [k for k in eligible if consensus.get(k) in ("a", "b")]
    if not eligible:
        raise ValueError(
            "no decisive judged real pairs available to spot-check"
            if decisive_only
            else "no judged real pairs available to spot-check"
        )
    rng = random.Random(seed)
    picks = _stratified_pick(eligible, n, stratify, rng, min_per_stratum)
    lines = [
        "# Judge spot-check — blinded",
        "",
        "For each pair, read both responses and fill in `your_verdict:` with",
        "`A`, `B`, or `tie`. Do not consult the judge ledger first.",
        "",
    ]
    for i, key in enumerate(picks, start=1):
        spec = real[key]
        flip = rng.random() < 0.5
        first, second = (spec.output_b, spec.output_a) if flip else (spec.output_a, spec.output_b)
        order = "ba" if flip else "ab"
        lines += [
            f"<!-- pair:{i} key:{key[0]}|{key[1]}|{key[2]}|{key[3]} order:{order} -->",
            f"## Pair {i} — {spec.task_id}",
            "",
            "### Assignment (truncated)",
            "",
            spec.assignment[:1500],
            "",
            "### Response A",
            "",
            first,
            "",
            "### Response B",
            "",
            second,
            "",
            f"your_verdict:  <!-- pair {i}: A | B | tie -->",
            "",
        ]
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@dataclass(frozen=True, slots=True)
class SpotcheckReport:
    """Human↔judge agreement: gated tie-excluded, with the strict view as context.

    ``n_scored``/``n_agree`` (and :attr:`agreement`) are the GATE — agreement on
    decisive-consensus pairs only, the without-tie convention whose ~80% bar the
    gate echoes (Zheng et al. 2023, MT-Bench/Chatbot Arena Tables 5–6). The strict
    fields report the exact 3-way agreement over every answered pair (ties
    included), reported for honesty, never gated on.
    """

    n_scored: int
    n_agree: int
    n_strict_scored: int = 0
    n_strict_agree: int = 0
    n_human_tie_on_decisive: int = 0

    @property
    def agreement(self) -> float | None:
        return self.n_agree / self.n_scored if self.n_scored else None

    @property
    def strict_agreement(self) -> float | None:
        return self.n_strict_agree / self.n_strict_scored if self.n_strict_scored else None


def score_spotcheck(path: Path | str, judge_rows: Sequence[JudgeRow]) -> SpotcheckReport:
    """Agreement between the human's blinded verdicts and the cross-judge consensus.

    Two metrics over the same filled file: the GATE (decisive-consensus pairs
    only — a human ``tie`` on a decisive pair is a miss, per the without-tie
    convention) and, for context, the strict 3-way agreement over all answered
    pairs. Human ``A``/``B`` map through the recorded permutation back to the
    canonical frame.
    """
    text = Path(path).read_text(encoding="utf-8")
    headers = re.findall(r"<!-- pair:(\d+) key:([^ ]+) order:(ab|ba) -->", text)
    answers = re.findall(r"your_verdict:\s*([ABab]|tie|Tie|TIE)?\s*(?:<!--|$)", text, re.MULTILINE)
    consensus = _consensus_by_pair(judge_rows)

    n_scored = n_agree = 0
    n_strict_scored = n_strict_agree = n_human_tie_on_decisive = 0
    for (num, raw_key, order), answer in zip(headers, answers, strict=False):
        if not answer:
            continue
        task_id, epoch_s, config_a, config_b = raw_key.split("|")
        judge_verdict = consensus.get((task_id, int(epoch_s), config_a, config_b))
        if judge_verdict is None:
            continue
        human = answer.strip().lower()
        human_canonical = (
            "tie"
            if human == "tie"
            else canonical_verdict(
                cast("RawVerdict", human.upper()), cast('Literal["ab", "ba"]', order)
            )
        )
        n_strict_scored += 1
        if human_canonical == judge_verdict:
            n_strict_agree += 1
        if judge_verdict in ("a", "b"):  # the gate: decisive consensus only
            n_scored += 1
            if human_canonical == judge_verdict:
                n_agree += 1
            else:
                if human_canonical == "tie":
                    n_human_tie_on_decisive += 1
                logger.info(
                    "spot-check pair %s: human=%s judge=%s", num, human_canonical, judge_verdict
                )
    return SpotcheckReport(
        n_scored=n_scored,
        n_agree=n_agree,
        n_strict_scored=n_strict_scored,
        n_strict_agree=n_strict_agree,
        n_human_tie_on_decisive=n_human_tie_on_decisive,
    )
