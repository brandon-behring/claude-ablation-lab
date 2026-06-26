"""The sweep orchestrator — run every grid cell, grade it, append to the ledger.

This is where the seams meet: :func:`run_sweep` expands the grid, then for each
cell decides among three paths in increasing cost:

1. **skip** — an ``ok`` row already exists for this *ledger* key (same
   ``grader_version``): nothing to do (resumability).
2. **re-grade** — an ``ok`` row exists for the *run* key but at an older
   ``grader_version``: re-score the *stored output* with the current grader, no
   call to Claude (the run/grade decoupling thesis — fix a grader, re-score free).
3. **run** — no stored ``ok`` run: call Claude (with rate-limit back-off), capture
   the gradeable output (stdout, or an agentic artifact file), grade, append.

Isolation: worktree variants are materialized once and ``reset_clean``-ed *before
each cell* so an agentic task's writes never leak into the next cell. Throttling:
a transient 429 backs off and retries; a hard usage limit (or persistent throttle)
raises :class:`SweepHaltedError`, leaving the ledger resumable. Provenance is gathered
once and stamped on every row.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from claude_ablation_lab import worktree as wt_mod
from claude_ablation_lab.grade import Grader, Score
from claude_ablation_lab.graders import get_grader
from claude_ablation_lab.grid import NONE_VARIANT, Cell, Grid, expand_grid, parse_variant
from claude_ablation_lab.ledger import (
    LedgerRow,
    RunKey,
    append_row,
    load_rows,
    ok_row_by_ledger_key,
    ok_row_by_run_key,
)
from claude_ablation_lab.prepare import Prepared, prepare_task
from claude_ablation_lab.provenance import Provenance, gather_provenance
from claude_ablation_lab.runner import Runner, RunResult
from claude_ablation_lab.task import Task

__all__ = ["SweepHaltedError", "SweepSummary", "run_sweep", "regrade_ledger", "run_with_backoff"]

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 500
_BACKOFF_CAP_S = 60.0


class SweepHaltedError(RuntimeError):
    """Raised when the sweep stops early (hard usage limit / persistent throttle).

    The ledger is always left consistent and resumable — re-running the same
    command continues from the first unfinished cell.
    """


@dataclass(frozen=True, slots=True)
class SweepSummary:
    """Counts of how each cell was handled (``total`` = expanded valid cells).

    ``ran``/``regraded``/``skipped``/``failed`` partition by *disposition*;
    ``graded_ok``/``unparseable``/``grader_error`` partition the rows that were
    *graded* (run or re-grade) by grade status — so an all-``grader_error`` sweep
    (e.g. a missing validator) cannot look identical to a perfect one.
    """

    total: int
    ran: int
    regraded: int
    skipped: int
    failed: int
    graded_ok: int = 0
    unparseable: int = 0
    grader_error: int = 0
    halted: bool = False
    halt_reason: str | None = None


# --------------------------------------------------------------------------- #
# Rate-limit handling
# --------------------------------------------------------------------------- #
def _is_hard_limit(run_result: RunResult) -> bool:
    """True for the account *usage* cap (vs a transient ``rate limit`` / overload).

    The runner classifies both the dated usage cap and a transient 429 as
    ``rate_limited``; here we separate them. ``"usage limit"`` is the account cap
    phrase (retrying before the reset date cannot help → halt). A transient
    throttle (``"rate limit"`` / ``"overloaded"``) lacks it and is retried. Even
    if a reworded cap slips past, ``run_with_backoff`` still halts after
    ``max_retries`` — this only avoids burning those retries.
    """
    return "usage limit" in (run_result.output or "").lower()


def _backoff_seconds(attempt: int, base_s: float) -> float:
    """Exponential back-off (capped): ``base · 2^attempt``."""
    return float(min(_BACKOFF_CAP_S, base_s * (2**attempt)))


def run_with_backoff(
    runner: Runner,
    prepared: Prepared,
    cell: Cell,
    cwd: Path,
    *,
    max_retries: int,
    base_s: float,
    sleep: Callable[[float], None],
) -> RunResult:
    """Run one cell, retrying transient throttles; raise :class:`SweepHaltedError` on a hard cap.

    A hard usage limit halts immediately (retrying cannot help before the reset
    date). A transient ``rate_limited`` backs off and retries up to ``max_retries``;
    if it never clears, that too becomes a halt (the sweep is resumable later).
    """
    for attempt in range(max_retries + 1):
        run_result = runner.run(
            prepared.prompt,
            model=cell.model,
            effort=cell.effort,
            cwd=cwd,
            json_schema=prepared.json_schema,
            permission_mode=prepared.permission_mode,
        )
        if run_result.status != "rate_limited":
            return run_result
        if _is_hard_limit(run_result):
            raise SweepHaltedError(
                f"hard usage limit at {_cell_label(cell)}: {run_result.output[:200]}"
            )
        if attempt < max_retries:
            delay = _backoff_seconds(attempt, base_s)
            logger.warning(
                "rate-limited at %s; backing off %.0fs (retry %d/%d)",
                _cell_label(cell),
                delay,
                attempt + 1,
                max_retries,
            )
            sleep(delay)
    raise SweepHaltedError(f"still rate-limited after {max_retries} retries at {_cell_label(cell)}")


def _cell_label(cell: Cell) -> str:
    return f"{cell.task_id}/{cell.model}/{cell.effort}/{cell.variant}#{cell.epoch}"


# --------------------------------------------------------------------------- #
# Output capture + grading
# --------------------------------------------------------------------------- #
def _capture_output(
    prepared: Prepared, run_result: RunResult, cwd: Path, *, since: float = 0.0
) -> tuple[str, bool]:
    """Return ``(gradeable_output, artifact_missing)``.

    Single-turn tasks grade stdout. An agentic task grades the file it was meant to
    produce (``prepared.artifact``): preferred at ``cwd/<artifact>``, else the most
    recently modified match anywhere under ``cwd`` (the skill may nest it). Only
    files **written/modified during this run** (``mtime >= since``) qualify — so a
    committed file of the same name, restored by ``reset_clean`` just before the
    run, is not mistaken for the model's output (which would silently turn a
    quality-0 into a high score). A successful run that produced no qualifying
    artifact yields ``("", True)`` — an honest quality failure, not an infra error.
    An unreadable artifact is likewise treated as missing rather than crashing.
    """
    if run_result.status != "ok" or prepared.artifact is None:
        return run_result.output, False
    name = Path(prepared.artifact).name
    candidates = [cwd / prepared.artifact, *cwd.glob(f"**/{name}")]
    fresh = [p for p in candidates if _modified_since(p, since)]
    for path in sorted(fresh, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            return path.read_text(encoding="utf-8"), False
        except (OSError, UnicodeError) as exc:
            logger.warning("artifact %s unreadable: %s", path, exc)
    return "", True


def _modified_since(path: Path, since: float) -> bool:
    """True iff ``path`` is a file whose mtime is at/after ``since`` (best-effort)."""
    try:
        return path.is_file() and path.stat().st_mtime >= since
    except OSError:
        return False


def _safe_grade(grader: Grader, output: str, gold: object) -> Score:
    """Call ``grader.grade`` but turn any exception into ``grader_error``.

    "A buggy grader poisons every number" (CLAUDE.md #4): a grader that raises
    (bad gold value, numpy/eval_toolkit edge) must not crash the sweep and lose an
    already-paid run — it is recorded as a grader failure, re-gradable later.
    """
    try:
        return grader.grade(output=output, gold=gold)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 — deliberate: isolate grader faults
        logger.exception("grader %r raised", getattr(grader, "version", "?"))
        return Score(0.0, status="grader_error", details={"grader_exception": repr(exc)})


def _grade(grader: Grader, run_status: str, output: str, gold: object) -> Score:
    """Grade an ``ok`` run's output; short-circuit a failed run to ``grader_error``."""
    if run_status != "ok":
        return Score(0.0, status="grader_error", details={"run_status": run_status})
    return _safe_grade(grader, output, gold)


def _persist_output(outputs_dir: Path, run_id: str, output: str) -> str:
    """Write the exact gradeable output to ``outputs/<run_id>.txt`` (enables re-grade)."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / f"{run_id}.txt"
    path.write_text(output, encoding="utf-8")
    return str(path)


def _build_row(
    cell: Cell,
    grader_version: str,
    spec_sha: str,
    run_result: RunResult,
    score: Score,
    *,
    output_path: str | None,
    output_preview: str,
    provenance: Provenance,
    infra_repo: str | None,
    infra_sha: str | None,
    ts: str,
    extra_details: dict[str, object] | None = None,
) -> LedgerRow:
    details: dict[str, object] = dict(score.details)
    if extra_details:
        details.update(extra_details)
    return LedgerRow(
        task_id=cell.task_id,
        model=cell.model,
        effort=cell.effort,
        variant=cell.variant,
        epoch=cell.epoch,
        grader_version=grader_version,
        run_id=run_result.run_id,
        run_status=run_result.status,
        cost_usd=run_result.cost_usd,
        latency_s=run_result.latency_s,
        returncode=run_result.returncode,
        model_resolved=run_result.model_resolved,
        num_turns=run_result.num_turns,
        session_id=run_result.session_id,
        grade_status=score.status,
        value=score.value,
        spec_sha=spec_sha,
        subscores=dict(score.subscores),
        details=details,
        output_path=output_path,
        output_preview=output_preview,
        transcript_path=run_result.transcript_path,
        ts=ts,
        claude_version=provenance.claude_version,
        harness_sha=provenance.harness_sha,
        infra_repo=infra_repo,
        infra_sha=infra_sha,
        global_layer=provenance.global_layer,
        mcp_servers=provenance.mcp_servers,
    )


@dataclass
class _Tally:
    """Mutable per-sweep counters (disposition + grade-status of graded rows)."""

    ran: int = 0
    regraded: int = 0
    skipped: int = 0
    failed: int = 0
    graded_ok: int = 0
    unparseable: int = 0
    grader_error: int = 0

    def bump_grade(self, status: str) -> None:
        if status == "ok":
            self.graded_ok += 1
        elif status == "unparseable":
            self.unparseable += 1
        else:
            self.grader_error += 1


def _resolve_tasks(
    tasks: Sequence[Task],
) -> tuple[dict[str, Grader], dict[str, Prepared]]:
    """Index graders + prepared cells by task id, rejecting duplicate ids.

    Two tasks sharing an ``id`` would silently overwrite each other in these maps
    and mis-grade every cell of the shadowed task — so it is a hard error.
    """
    ids = [task.id for task in tasks]
    dups = sorted({tid for tid in ids if ids.count(tid) > 1})
    if dups:
        raise ValueError(f"duplicate task ids: {dups} — each task id must be unique")
    graders = {task.id: get_grader(task.grader) for task in tasks}
    prepared = {task.id: prepare_task(task) for task in tasks}
    return graders, prepared


def _regrade_row(
    prior: LedgerRow, grader: Grader, output: str, gold: object, ts: str
) -> tuple[LedgerRow, Score]:
    """Re-score a stored run's ``output`` with ``grader``; cost/latency zeroed.

    The re-grade incurs no model cost, so ``cost_usd``/``latency_s`` are zeroed
    (a naive ``SUM(cost_usd)`` over the ledger then stays correct); ``run_id`` and
    a ``regrade_of`` marker are kept for audit. A run-level ``artifact_missing``
    marker is carried forward (the new grade details would otherwise drop it).
    """
    score = _safe_grade(grader, output, gold)
    details: dict[str, object] = dict(score.details)
    if prior.details.get("artifact_missing"):
        details["artifact_missing"] = True
    details["regrade_of"] = prior.run_id
    row = replace(
        prior,
        grader_version=grader.version,
        grade_status=score.status,
        value=score.value,
        subscores=dict(score.subscores),
        details=details,
        cost_usd=0.0,
        latency_s=0.0,
        ts=ts,
    )
    return row, score


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
def run_sweep(
    tasks: Sequence[Task],
    grid: Grid,
    *,
    runner: Runner,
    ledger_path: Path | str,
    outputs_dir: Path | str | None = None,
    worktree_base: Path = wt_mod.DEFAULT_BASE,
    neutral_cwd: Path | str | None = None,
    max_retries: int = 4,
    backoff_base_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], str] | None = None,
    harness_repo: Path | None = None,
    cleanup: bool = True,
) -> SweepSummary:
    """Execute the grid sweep, appending one ledger row per cell. Resumable.

    Parameters
    ----------
    tasks, grid:
        The suite and its sweep axes; :func:`expand_grid` drops invalid combos.
    runner:
        The substrate (``ClaudeCodeRunner`` in v1).
    ledger_path:
        Append-only JSONL ledger; also read up-front for resume.
    outputs_dir:
        Where the exact gradeable output of each ``ok`` run is stored (default
        ``<ledger dir>/outputs``) — the basis for a later re-grade.
    neutral_cwd:
        cwd for ``none``-variant (infra-agnostic) cells; a temp dir by default
        (created and removed by the sweep).
    sleep, now:
        Injected for tests (back-off sleeps; row timestamps).

    Returns
    -------
    SweepSummary
        Per-cell disposition counts; ``halted`` is set if a usage cap stopped it.
    """
    timestamp = now or (lambda: datetime.now(UTC).isoformat())
    ledger_path = Path(ledger_path)
    out_dir = Path(outputs_dir) if outputs_dir else ledger_path.parent / "outputs"

    cells = expand_grid(grid, list(tasks))
    graders, prepared = _resolve_tasks(tasks)  # raises on duplicate task id
    provenance = gather_provenance(harness_repo=harness_repo)

    rows = load_rows(ledger_path)
    ok_ledger = ok_row_by_ledger_key(rows)
    ok_rows = ok_row_by_run_key(rows)

    worktrees: dict[str, wt_mod.Worktree] = {}
    bad_variants: set[str] = set()
    neutral_dir = Path(neutral_cwd) if neutral_cwd else None
    owns_neutral = neutral_cwd is None

    tally = _Tally()
    halted = False
    halt_reason: str | None = None
    try:
        for cell in cells:
            grader = graders[cell.task_id]
            prep = prepared[cell.task_id]
            run_key: RunKey = (cell.task_id, cell.model, cell.effort, cell.variant, cell.epoch)
            ledger_key = (*run_key, grader.version)

            # Path 1: already graded at this exact (spec, grader_version) → skip.
            done_row = ok_ledger.get(ledger_key)
            if done_row is not None and done_row.spec_sha == prep.spec_sha:
                tally.skipped += 1
                continue

            # Path 2: a stored ok run for the SAME spec → re-grade offline (no Claude).
            prior = ok_rows.get(run_key)
            prior_output = _reusable_output(prior, prep.spec_sha)
            if prior is not None and prior_output is not None:
                row, score = _regrade_row(prior, grader, prior_output, prep.gold, timestamp())
                append_row(ledger_path, row)
                ok_ledger[ledger_key] = row
                ok_rows[run_key] = row
                tally.regraded += 1
                tally.bump_grade(score.status)
                continue

            # Path 3: a real run. Resolve cwd / worktree first.
            if cell.variant in bad_variants:
                tally.failed += 1
                continue
            try:
                cwd, infra_repo, infra_sha, neutral_dir = _resolve_cwd(
                    cell.variant, worktrees, worktree_base, neutral_dir
                )
            except (RuntimeError, ValueError, OSError) as exc:
                logger.error("variant %s unusable, skipping its cells: %s", cell.variant, exc)
                bad_variants.add(cell.variant)
                tally.failed += 1
                continue

            before = time.time()  # only files written after this count as the artifact
            try:
                run_result = run_with_backoff(
                    runner,
                    prep,
                    cell,
                    cwd,
                    max_retries=max_retries,
                    base_s=backoff_base_s,
                    sleep=sleep,
                )
            except SweepHaltedError as exc:
                halted = True
                halt_reason = str(exc)
                logger.error("sweep halted: %s", halt_reason)
                break

            output, artifact_missing = _capture_output(prep, run_result, cwd, since=before)
            output_path = (
                _persist_output(out_dir, run_result.run_id, output)
                if run_result.status == "ok"
                else None
            )
            score = _grade(grader, run_result.status, output, prep.gold)
            row = _build_row(
                cell,
                grader.version,
                prep.spec_sha,
                run_result,
                score,
                output_path=output_path,
                output_preview=output[:_PREVIEW_CHARS],
                provenance=provenance,
                infra_repo=infra_repo,
                infra_sha=infra_sha,
                ts=timestamp(),
                extra_details={"artifact_missing": True} if artifact_missing else None,
            )
            append_row(ledger_path, row)
            if run_result.status == "ok":
                ok_ledger[ledger_key] = row
                ok_rows[run_key] = row
                tally.ran += 1
                tally.bump_grade(score.status)
            else:
                tally.failed += 1
    finally:
        if cleanup:
            _cleanup(worktrees, neutral_dir if owns_neutral else None)

    return SweepSummary(
        total=len(cells),
        ran=tally.ran,
        regraded=tally.regraded,
        skipped=tally.skipped,
        failed=tally.failed,
        graded_ok=tally.graded_ok,
        unparseable=tally.unparseable,
        grader_error=tally.grader_error,
        halted=halted,
        halt_reason=halt_reason,
    )


def _reusable_output(prior: LedgerRow | None, spec_sha: str) -> str | None:
    """Stored output to re-grade, iff ``prior`` matches ``spec_sha`` and its file exists.

    Returns ``None`` (→ a fresh run) when there is no prior ok run, the spec
    changed (prompt/schema/gold differ → the stored output is for a different
    cell), or the output file is gone — never re-grades stale output.
    """
    if prior is None or prior.spec_sha != spec_sha or not prior.output_path:
        return None
    path = Path(prior.output_path)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.warning("stored output %s unreadable: %s", path, exc)
        return None


def _resolve_cwd(
    variant: str,
    worktrees: dict[str, wt_mod.Worktree],
    worktree_base: Path,
    neutral_dir: Path | None,
) -> tuple[Path, str | None, str | None, Path | None]:
    """Resolve a cell's cwd. ``none`` → a neutral dir; else a reset worktree.

    Returns ``(cwd, infra_repo, infra_sha, neutral_dir)`` — ``neutral_dir`` is
    threaded back so it is created at most once and reused across ``none`` cells.
    """
    if variant == NONE_VARIANT:
        if neutral_dir is None:
            neutral_dir = Path(tempfile.mkdtemp(prefix="ablation-neutral-"))
        return neutral_dir, None, None, neutral_dir
    repo, ref = parse_variant(variant)  # type: ignore[misc]  # not None for non-'none'
    worktree = worktrees.get(variant)
    if worktree is None:
        worktree = wt_mod.ensure_worktree(Path(os.path.expanduser(repo)), ref, base=worktree_base)
        worktrees[variant] = worktree
    wt_mod.reset_clean(worktree)  # pristine before this cell
    return worktree.path, str(worktree.repo), worktree.sha, neutral_dir


def _cleanup(worktrees: dict[str, wt_mod.Worktree], neutral_dir: Path | None) -> None:
    """Remove sweep-created worktrees and the owned neutral dir (best-effort)."""
    import shutil

    for worktree in worktrees.values():
        try:
            wt_mod.remove_worktree(worktree)
        except RuntimeError as exc:
            logger.warning("could not remove worktree %s: %s", worktree.path, exc)
    if neutral_dir is not None:
        shutil.rmtree(neutral_dir, ignore_errors=True)


def regrade_ledger(
    tasks: Sequence[Task],
    *,
    ledger_path: Path | str,
    now: Callable[[], str] | None = None,
) -> SweepSummary:
    """Re-score every stored ``ok`` run with the *current* graders — no Claude calls.

    The decoupling payoff: after fixing a grader and bumping its ``version``, this
    appends fresh rows for the new ``grader_version`` by reading each run's stored
    output. Rows already at the current version are skipped; a run whose output is
    missing — or whose stored ``spec_sha`` no longer matches the task's current
    prompt/gold — is counted as ``failed`` (re-grading it would score stale output
    against a changed spec; it needs a fresh run instead).
    """
    timestamp = now or (lambda: datetime.now(UTC).isoformat())
    ledger_path = Path(ledger_path)
    rows = load_rows(ledger_path)
    ok_ledger = ok_row_by_ledger_key(rows)
    ok_rows = ok_row_by_run_key(rows)
    graders, prepared = _resolve_tasks(tasks)

    tally = _Tally()
    for run_key, prior in ok_rows.items():
        grader = graders.get(prior.task_id)
        prep = prepared.get(prior.task_id)
        if grader is None or prep is None:
            continue  # a task not in this suite — leave its rows untouched
        if ok_ledger.get((*run_key, grader.version)) is not None:
            tally.skipped += 1
            continue
        output = _reusable_output(prior, prep.spec_sha)
        if output is None:  # missing output or a changed spec — cannot re-grade offline
            tally.failed += 1
            continue
        row, score = _regrade_row(prior, grader, output, prep.gold, timestamp())
        append_row(ledger_path, row)
        ok_ledger[row.ledger_key] = row
        tally.regraded += 1
        tally.bump_grade(score.status)
    return SweepSummary(
        total=len(ok_rows),
        ran=0,
        regraded=tally.regraded,
        skipped=tally.skipped,
        failed=tally.failed,
        graded_ok=tally.graded_ok,
        unparseable=tally.unparseable,
        grader_error=tally.grader_error,
    )
