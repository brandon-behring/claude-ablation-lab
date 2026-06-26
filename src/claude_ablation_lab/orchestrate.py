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
    completed_ledger_keys,
    load_rows,
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
    """Counts of how each cell was handled (``total`` = expanded valid cells)."""

    total: int
    ran: int
    regraded: int
    skipped: int
    failed: int
    halted: bool = False
    halt_reason: str | None = None


# --------------------------------------------------------------------------- #
# Rate-limit handling
# --------------------------------------------------------------------------- #
def _is_hard_limit(run_result: RunResult) -> bool:
    """True for the dated account usage cap (vs a transient, retryable throttle)."""
    message = (run_result.output or "").lower()
    return "usage limit" in message and "regain access" in message


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
def _capture_output(prepared: Prepared, run_result: RunResult, cwd: Path) -> tuple[str, bool]:
    """Return ``(gradeable_output, artifact_missing)``.

    Single-turn tasks grade stdout. An agentic task grades the file it was meant to
    produce (``prepared.artifact``): preferred at ``cwd/<artifact>``, else the most
    recently modified match anywhere under ``cwd`` (the skill may nest it). A
    successful run that produced no artifact yields ``("", True)`` — a *quality*
    failure (the task was not completed), graded honestly, not an infra error.
    """
    if run_result.status != "ok" or prepared.artifact is None:
        return run_result.output, False
    direct = cwd / prepared.artifact
    if direct.is_file():
        return direct.read_text(encoding="utf-8"), False
    name = Path(prepared.artifact).name
    matches = sorted(cwd.glob(f"**/{name}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        return matches[0].read_text(encoding="utf-8"), False
    return "", True


def _grade(grader: Grader, run_status: str, output: str, gold: object) -> Score:
    """Grade an ``ok`` run's output; short-circuit a failed run to ``grader_error``."""
    if run_status != "ok":
        return Score(0.0, status="grader_error", details={"run_status": run_status})
    return grader.grade(output=output, gold=gold)  # type: ignore[arg-type]


def _persist_output(outputs_dir: Path, run_id: str, output: str) -> str:
    """Write the exact gradeable output to ``outputs/<run_id>.txt`` (enables re-grade)."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / f"{run_id}.txt"
    path.write_text(output, encoding="utf-8")
    return str(path)


def _build_row(
    cell: Cell,
    grader_version: str,
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
    graders: dict[str, Grader] = {task.id: get_grader(task.grader) for task in tasks}
    prepared: dict[str, Prepared] = {task.id: prepare_task(task) for task in tasks}
    provenance = gather_provenance(harness_repo=harness_repo)

    rows = load_rows(ledger_path)
    done = completed_ledger_keys(rows)
    ok_rows = ok_row_by_run_key(rows)

    worktrees: dict[str, wt_mod.Worktree] = {}
    bad_variants: set[str] = set()
    neutral_dir = Path(neutral_cwd) if neutral_cwd else None
    owns_neutral = neutral_cwd is None

    ran = regraded = skipped = failed = 0
    halted = False
    halt_reason: str | None = None
    try:
        for cell in cells:
            grader = graders[cell.task_id]
            prep = prepared[cell.task_id]
            run_key: RunKey = (cell.task_id, cell.model, cell.effort, cell.variant, cell.epoch)
            ledger_key = (*run_key, grader.version)

            if ledger_key in done:
                skipped += 1
                continue

            # Path 2: re-grade a stored ok run at a new grader_version (no Claude call).
            prior = ok_rows.get(run_key)
            if prior is not None and prior.output_path and Path(prior.output_path).is_file():
                output = Path(prior.output_path).read_text(encoding="utf-8")
                score = grader.grade(output=output, gold=prep.gold)
                row = replace(
                    prior,
                    grader_version=grader.version,
                    grade_status=score.status,
                    value=score.value,
                    subscores=dict(score.subscores),
                    details=dict(score.details),
                    ts=timestamp(),
                )
                append_row(ledger_path, row)
                done.add(ledger_key)
                ok_rows[run_key] = row
                regraded += 1
                continue

            # Path 3: a real run. Resolve cwd / worktree first.
            if cell.variant in bad_variants:
                failed += 1
                continue
            try:
                cwd, infra_repo, infra_sha, neutral_dir = _resolve_cwd(
                    cell.variant, worktrees, worktree_base, neutral_dir
                )
            except (RuntimeError, ValueError, OSError) as exc:
                logger.error("variant %s unusable, skipping its cells: %s", cell.variant, exc)
                bad_variants.add(cell.variant)
                failed += 1
                continue

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

            output, artifact_missing = _capture_output(prep, run_result, cwd)
            output_path = (
                _persist_output(out_dir, run_result.run_id, output)
                if run_result.status == "ok"
                else None
            )
            score = _grade(grader, run_result.status, output, prep.gold)
            row = _build_row(
                cell,
                grader.version,
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
                done.add(ledger_key)
                ok_rows[run_key] = row
                ran += 1
            else:
                failed += 1
    finally:
        if cleanup:
            _cleanup(worktrees, neutral_dir if owns_neutral else None)

    return SweepSummary(
        total=len(cells),
        ran=ran,
        regraded=regraded,
        skipped=skipped,
        failed=failed,
        halted=halted,
        halt_reason=halt_reason,
    )


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
    output. Rows already at the current version are skipped; a run whose output
    file is missing is counted as ``failed``.
    """
    timestamp = now or (lambda: datetime.now(UTC).isoformat())
    ledger_path = Path(ledger_path)
    rows = load_rows(ledger_path)
    done = completed_ledger_keys(rows)
    ok_rows = ok_row_by_run_key(rows)
    graders: dict[str, Grader] = {task.id: get_grader(task.grader) for task in tasks}
    prepared: dict[str, Prepared] = {task.id: prepare_task(task) for task in tasks}

    regraded = skipped = failed = 0
    for run_key, prior in ok_rows.items():
        grader = graders.get(prior.task_id)
        prep = prepared.get(prior.task_id)
        if grader is None or prep is None:
            failed += 1
            continue
        if (*run_key, grader.version) in done:
            skipped += 1
            continue
        if not prior.output_path or not Path(prior.output_path).is_file():
            failed += 1
            continue
        output = Path(prior.output_path).read_text(encoding="utf-8")
        score = grader.grade(output=output, gold=prep.gold)
        append_row(
            ledger_path,
            replace(
                prior,
                grader_version=grader.version,
                grade_status=score.status,
                value=score.value,
                subscores=dict(score.subscores),
                details=dict(score.details),
                ts=timestamp(),
            ),
        )
        regraded += 1
    return SweepSummary(
        total=len(ok_rows), ran=0, regraded=regraded, skipped=skipped, failed=failed
    )
