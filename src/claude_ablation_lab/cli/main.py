"""CLI for claude-ablation-lab.

Commands (built out across phases):
    ablation run <suite> <grid>         # execute the model × effort × variant sweep
    ablation regrade <suite>            # re-score stored runs with current graders
    ablation estimate <suite> <grid>    # project rate-limit usage before a sweep (Phase 5)
    ablation report <ledger>            # DuckDB aggregates: score±CI, cost, latency (Phase 4)
    ablation compare <ledger> --a --b   # paired-bootstrap delta, "is it real" (Phase 4)

The v1 substrate is the `claude` CLI run headless; see CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from claude_ablation_lab._version import __version__
from claude_ablation_lab.grid import expand_grid, load_grid
from claude_ablation_lab.task import Task, load_all, load_task

if TYPE_CHECKING:
    from claude_ablation_lab.orchestrate import Estimate, SweepSummary

app = typer.Typer(
    name="ablation",
    help="Model × effort × config ablation/regression harness for Claude Code.",
    no_args_is_help=True,
)
console = Console()


def _load_suite(suite: Path, only: list[str] | None) -> list[Task]:
    """Load tasks from a dir (all ``*.yaml``) or a single YAML file, optional id filter."""
    tasks = load_all(suite) if suite.is_dir() else [load_task(suite)]
    if only:
        wanted = set(only)
        tasks = [task for task in tasks if task.id in wanted]
    if not tasks:
        console.print("[red]no tasks selected[/red]", style="bold")
        raise typer.Exit(1)
    return tasks


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def run(
    suite: Annotated[Path, typer.Argument(help="Task-suite dir or a single task YAML")],
    grid: Annotated[Path, typer.Argument(help="Grid spec YAML")],
    ledger: Annotated[Path, typer.Option(help="JSONL ledger to append to")] = Path(
        "results/ledger.jsonl"
    ),
    task: Annotated[list[str] | None, typer.Option(help="Only run these task ids")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Expand the grid and exit")] = False,
    max_budget_usd: Annotated[
        float | None, typer.Option(help="Soft per-call --max-budget-usd runaway stop")
    ] = None,
    timeout_s: Annotated[float, typer.Option(help="Per-cell wall-clock cap")] = 900.0,
) -> None:
    """Execute the sweep sequentially, appending each cell to the ledger (resumable)."""
    tasks = _load_suite(suite, task)
    parsed_grid = load_grid(grid)
    cells = expand_grid(parsed_grid, tasks)
    if not cells:
        console.print("[yellow]grid expands to 0 valid cells[/yellow]")
        raise typer.Exit(1)

    if dry_run:
        _print_cells(cells)
        return

    # Imported lazily so `--dry-run` / `--help` need no runner/grader dependencies.
    from claude_ablation_lab.orchestrate import run_sweep
    from claude_ablation_lab.runner import ClaudeCodeRunner

    runner = ClaudeCodeRunner(
        transcript_dir=ledger.parent / "transcripts",
        timeout_s=timeout_s,
        max_budget_usd=max_budget_usd,
    )
    console.print(f"running {len(cells)} cells → {ledger}")
    summary = run_sweep(tasks, parsed_grid, runner=runner, ledger_path=ledger)
    _print_summary(summary)
    if summary.halted:
        console.print(f"[red]halted:[/red] {summary.halt_reason}")
        raise typer.Exit(2)


@app.command()
def regrade(
    suite: Annotated[Path, typer.Argument(help="Task-suite dir or a single task YAML")],
    ledger: Annotated[Path, typer.Option(help="JSONL ledger to re-grade in place")] = Path(
        "results/ledger.jsonl"
    ),
    task: Annotated[list[str] | None, typer.Option(help="Only re-grade these task ids")] = None,
) -> None:
    """Re-score stored ``ok`` runs with the current graders (no Claude calls)."""
    tasks = _load_suite(suite, task)
    from claude_ablation_lab.orchestrate import regrade_ledger

    summary = regrade_ledger(tasks, ledger_path=ledger)
    _print_summary(summary)


@app.command()
def estimate(
    suite: Annotated[Path, typer.Argument(help="Task-suite dir or a single task YAML")],
    grid: Annotated[Path, typer.Argument(help="Grid spec YAML")],
    task: Annotated[list[str] | None, typer.Option(help="Only consider these task ids")] = None,
    timeout_s: Annotated[float, typer.Option(help="Per-cell wall-clock cap")] = 900.0,
) -> None:
    """Run one cell and project the full sweep's tokens/turns/cost/wall-clock."""
    tasks = _load_suite(suite, task)
    parsed_grid = load_grid(grid)
    from claude_ablation_lab.orchestrate import estimate_sweep
    from claude_ablation_lab.runner import ClaudeCodeRunner

    runner = ClaudeCodeRunner(timeout_s=timeout_s)
    console.print("calibrating on one cell…")
    est = estimate_sweep(tasks, parsed_grid, runner=runner)
    _print_estimate(est)
    if est.calibration_status != "ok":
        console.print(
            f"[red]calibration cell status = {est.calibration_status}[/red] — projection unreliable"
        )
        raise typer.Exit(2)


@app.command()
def report(
    ledger: Annotated[Path, typer.Argument(help="JSONL ledger to analyze")],
) -> None:
    """DuckDB aggregates per task×model×effort: score±CI, cost, latency, Pareto."""
    from claude_ablation_lab.analyze import report as run_report

    cells = run_report(ledger)
    if not cells:
        console.print(f"[yellow]no graded rows in {ledger}[/yellow]")
        return
    _print_report(cells)


@app.command()
def compare(
    ledger: Annotated[Path, typer.Argument(help="JSONL ledger")],
    a: Annotated[str, typer.Option("--a", help="Baseline variant (infra_repo@ref)")],
    b: Annotated[str, typer.Option("--b", help="Candidate variant (infra_repo@ref)")],
) -> None:
    """Per-task delta between two variants with a paired bootstrap — is it real?"""
    from claude_ablation_lab.analyze import compare as run_compare

    deltas = run_compare(ledger, a, b)
    if not deltas:
        console.print(f"[yellow]no task ran under both {a} and {b} in {ledger}[/yellow]")
        return
    _print_compare(deltas, a, b)


def _print_cells(cells: list) -> None:  # type: ignore[type-arg]
    """Dry-run: show the per-(task, model, effort) cell counts."""
    table = Table(title=f"{len(cells)} cells")
    for col in ("task", "model", "effort", "variant", "epochs"):
        table.add_column(col)
    counts: dict[tuple[str, str, str, str], int] = {}
    for cell in cells:
        key = (cell.task_id, cell.model, cell.effort, cell.variant)
        counts[key] = counts.get(key, 0) + 1
    for (task_id, model, effort, variant), n in counts.items():
        table.add_row(task_id, model, effort, variant, str(n))
    console.print(table)


def _print_summary(summary: SweepSummary) -> None:
    """Render a SweepSummary as a compact table (+ a grade-status breakdown)."""
    table = Table(title="sweep summary")
    fields = (
        "total",
        "ran",
        "regraded",
        "skipped",
        "failed",
        "graded_ok",
        "unparseable",
        "grader_error",
    )
    for field_name in fields:
        table.add_column(field_name)
    table.add_row(*(str(getattr(summary, field_name)) for field_name in fields))
    console.print(table)
    # A sweep can "succeed" (ran>0, failed=0) yet grade nothing useful — surface it.
    if summary.grader_error or summary.unparseable:
        console.print(
            f"[yellow]note:[/yellow] {summary.grader_error} grader_error / "
            f"{summary.unparseable} unparseable graded rows — inspect before trusting scores"
        )


def _fmt(value: float | None, places: int = 3) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _print_report(cells: list) -> None:  # type: ignore[type-arg]
    """Render report cells: quality (±epoch CI), cost, latency, Pareto, leakage."""
    table = Table(title="report — quality vs cost (epochs = exploratory run-variance)")
    for col in (
        "task",
        "model",
        "effort",
        "variant",
        "n",
        "mean",
        "CI(epoch)",
        "cost$",
        "lat s",
        "",
    ):
        table.add_column(col)
    for c in cells:
        flags = " ".join(
            f for f, on in (("★", c.pareto), ("⚠LEAK", c.leakage), ("⚠SPEC", c.n_spec > 1)) if on
        )
        ci = f"[{_fmt(c.ci_low)},{_fmt(c.ci_high)}]" if c.ci_low is not None else "—"
        table.add_row(
            c.task_id,
            c.model,
            c.effort,
            c.variant,
            str(c.n_epochs),
            _fmt(c.mean_value),
            ci,
            _fmt(c.mean_cost, 4),
            _fmt(c.mean_latency, 1),
            flags,
        )
    console.print(table)
    console.print(
        "★ = Pareto-optimal (quality vs cost) · ⚠LEAK = shuffled-label control off 0.5 · "
        "CI(epoch) shown at ≥3 epochs · ⚠SPEC = cell mixes task specs"
    )


def _print_compare(deltas: list, a: str, b: str) -> None:  # type: ignore[type-arg]
    """Render variant A→B deltas with the paired-bootstrap verdict."""
    table = Table(title=f"compare  A={a}  →  B={b}")
    for col in ("task", "pairs", "mean A", "mean B", "Δ (B−A)", "95% CI", "real?", "note"):
        table.add_column(col)
    for d in deltas:
        ci = f"[{_fmt(d.ci_low)},{_fmt(d.ci_high)}]" if d.ci_low is not None else "—"
        table.add_row(
            d.task_id,
            str(d.n_pairs),
            _fmt(d.mean_a),
            _fmt(d.mean_b),
            _fmt(d.delta),
            ci,
            "[green]yes[/green]" if d.real else "no",
            d.note,
        )
    console.print(table)


def _print_estimate(est: Estimate) -> None:
    """Render an Estimate: per-cell calibration → projected totals for the grid."""
    table = Table(title=f"estimate — {est.n_cells} cells (calibrated on {est.calibration_label})")
    table.add_column("metric")
    table.add_column("per cell")
    table.add_column(f"× {est.n_cells} cells")
    rows: list[tuple[str, object, object]] = [
        ("input tokens", est.cell_input_tokens, est.projected_input_tokens),
        ("output tokens", est.cell_output_tokens, est.projected_output_tokens),
        ("turns", est.cell_turns, est.projected_turns),
        ("cost $", f"{est.cell_cost_usd:.4f}", f"{est.projected_cost_usd:.2f}"),
        ("wall-clock s", f"{est.cell_latency_s:.1f}", f"{est.projected_wall_clock_s:.0f}"),
    ]
    for name, per, total in rows:
        table.add_row(name, str(per), str(total))
    console.print(table)
    console.print(
        "[dim]rough: one calibration cell extrapolated to all cells; per-model cost varies. "
        "Rate-limit headroom — not dollars — is the real budget.[/dim]"
    )


if __name__ == "__main__":
    app()
