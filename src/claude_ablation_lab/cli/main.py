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
    from claude_ablation_lab.analyze import CompareRow
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


def _verify_tool_catalog(allow_unverified: bool) -> None:
    """Fail closed on CLI version drift (D6) before any real Claude call.

    ``HERMETIC_DISALLOWED_TOOLS`` is a hand-pinned catalog, verified once against a
    specific ``claude`` version — a later version may add/rename a tool the catalog
    doesn't know about, silently widening the escape surface. Shared by ``run`` and
    ``estimate`` (both make real calls; a stale catalog is just as unsafe for a
    single calibration cell as for a full sweep).
    """
    from claude_ablation_lab.provenance import claude_version
    from claude_ablation_lab.runner import CATALOG_VERIFIED_CLAUDE_VERSION

    installed = claude_version()
    if installed != CATALOG_VERIFIED_CLAUDE_VERSION and not allow_unverified:
        console.print(
            f"[red]installed claude {installed!r} != the catalog's verified "
            f"{CATALOG_VERIFIED_CLAUDE_VERSION!r}[/red] — HERMETIC_DISALLOWED_TOOLS in "
            "runner.py may be missing a tool the new version added. Re-verify the "
            "catalog (see its docstring for the probe method), bump "
            "CATALOG_VERIFIED_CLAUDE_VERSION, or pass --allow-unverified-tools to "
            "proceed anyway."
        )
        raise typer.Exit(2)


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
    worktree_base: Annotated[
        Path | None,
        typer.Option(
            help="Directory variant worktrees materialize under (default: "
            "~/.cache/claude-ablation-lab/worktrees — deliberately OUTSIDE any repo, so "
            "cells cannot see the harness's own CLAUDE.md as ancestor memory)"
        ),
    ] = None,
    capture_mechanism: Annotated[
        bool,
        typer.Option(
            "--capture-mechanism/--no-capture-mechanism",
            help="Use --output-format stream-json to record which tools each cell "
            "invoked (RunResult.tools_used → ledger tool_calls). On by default.",
        ),
    ] = True,
    allow_unverified_tools: Annotated[
        bool,
        typer.Option(
            "--allow-unverified-tools",
            help="Skip the installed-CLI-version gate (see KNOWN_BUILTIN_TOOLS in "
            "runner.py). Use only after re-verifying the tool catalog by hand against "
            "the new claude version.",
        ),
    ] = False,
) -> None:
    """Execute the sweep sequentially, appending each cell to the ledger (resumable)."""
    tasks = _load_suite(suite, task)
    parsed_grid = load_grid(grid)
    cells = expand_grid(parsed_grid, tasks)
    if not cells:
        console.print("[yellow]grid expands to 0 valid cells[/yellow]")
        raise typer.Exit(1)
    covered = {c.task_id for c in cells}
    for selected in tasks:
        if selected.id not in covered:  # never fail silently: a dropped task is loud
            console.print(
                f"[yellow]{selected.id}: 0 cells — its infra_repo matches none of the "
                "grid's variants (check the strings, run from the repo root)[/yellow]"
            )

    if dry_run:
        _print_cells(cells)
        return

    # Imported lazily so `--dry-run` / `--help` need no runner/grader dependencies.
    from claude_ablation_lab.orchestrate import run_sweep
    from claude_ablation_lab.runner import ClaudeCodeRunner

    _verify_tool_catalog(allow_unverified_tools)

    runner = ClaudeCodeRunner(
        transcript_dir=ledger.parent / "transcripts",
        timeout_s=timeout_s,
        max_budget_usd=max_budget_usd,
        capture_mechanism=capture_mechanism,
    )
    from claude_ablation_lab.worktree import DEFAULT_BASE

    # Cells run tool-minimal by default (HERMETIC_DISALLOWED_TOOLS). An agent-mode
    # task that declares `tools:` gets exactly those relaxed (prepare.py); one that
    # doesn't would spend its expensive cells scoring ~0 for harness reasons — warn.
    for selected in tasks:
        if selected.mode != "agent" or selected.id not in covered:
            continue
        if selected.tools:
            console.print(f"{selected.id}: agent-mode, tools relaxed → {list(selected.tools)}")
        else:
            console.print(
                f"[red]{selected.id} is an agent-mode task with no `tools:` declared — "
                "cells run tool-minimal and it will likely score ~0 for harness reasons. "
                "Add a `tools:` list to its task YAML.[/red]"
            )

    console.print(f"running {len(cells)} cells → {ledger}")
    summary = run_sweep(
        tasks,
        parsed_grid,
        runner=runner,
        ledger_path=ledger,
        worktree_base=worktree_base if worktree_base is not None else DEFAULT_BASE,
    )
    _print_summary(summary)
    if summary.failed:
        console.print(
            f"[red]{summary.failed} cell(s) failed[/red] — inspect the transcripts before "
            "trusting this sweep (exit stays 0: the ledger is resumable)"
        )
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
    capture_mechanism: Annotated[
        bool,
        typer.Option(
            "--capture-mechanism/--no-capture-mechanism",
            help="Match `run`'s default execution mode so the calibration cell's "
            "latency/cost reflects the real sweep path. On by default.",
        ),
    ] = True,
    allow_unverified_tools: Annotated[
        bool, typer.Option("--allow-unverified-tools", help="See `run --help`.")
    ] = False,
) -> None:
    """Run one cell and project the full sweep's tokens/turns/cost/wall-clock."""
    tasks = _load_suite(suite, task)
    parsed_grid = load_grid(grid)
    from claude_ablation_lab.orchestrate import estimate_sweep
    from claude_ablation_lab.runner import ClaudeCodeRunner

    _verify_tool_catalog(allow_unverified_tools)

    runner = ClaudeCodeRunner(timeout_s=timeout_s, capture_mechanism=capture_mechanism)
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
    x_axis: Annotated[
        str,
        typer.Option(
            "--x-axis",
            help="Pareto cost axis for the ★ flag: cost ($) / latency (s) / tokens (output tokens)",
        ),
    ] = "cost",
) -> None:
    """DuckDB aggregates per task×model×effort: score±CI, cost, latency, Pareto."""
    from claude_ablation_lab.analyze import X_AXES
    from claude_ablation_lab.analyze import report as run_report

    if x_axis not in X_AXES:
        console.print(f"[red]unsupported --x-axis {x_axis!r}[/red] (choose {'/'.join(X_AXES)})")
        raise typer.Exit(2)
    cells = run_report(ledger, x_axis=x_axis)
    if not cells:
        console.print(f"[yellow]no graded rows in {ledger}[/yellow]")
        return
    _print_report(cells, x_axis=x_axis)


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


@app.command()
def advise(
    ledger: Annotated[Path, typer.Argument(help="JSONL ledger to analyze")],
    reflex: Annotated[
        str,
        typer.Option("--reflex", help="Your expensive default as 'model/effort' (e.g. opus/max)"),
    ] = "opus/max",
    margin: Annotated[
        float,
        typer.Option(
            "--margin",
            help="Quality tolerance on [0,1] within which a cheaper config still counts as safe",
        ),
    ] = 0.02,
) -> None:
    """Where the reflex config overpays: cheapest non-inferior config + $ and seconds saved."""
    from claude_ablation_lab.analyze import cost_advisor
    from claude_ablation_lab.analyze import report as run_report

    cells = run_report(ledger)
    if not cells:
        console.print(f"[yellow]no graded rows in {ledger}[/yellow]")
        return
    try:
        advice = cost_advisor(cells, reflex=reflex, margin=margin)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    _print_advice(advice, reflex, margin)


@app.command()
def plot(
    ledger: Annotated[Path, typer.Argument(help="JSONL ledger to visualize")],
    out: Annotated[Path, typer.Option(help="Directory to write figures to")] = Path(
        "results/plots"
    ),
    task: Annotated[list[str] | None, typer.Option(help="Only plot these task ids")] = None,
    a: Annotated[
        str | None, typer.Option("--a", help="Baseline variant for the A/B forest")
    ] = None,
    b: Annotated[
        str | None, typer.Option("--b", help="Candidate variant for the A/B forest")
    ] = None,
    fmt: Annotated[str, typer.Option("--format", help="Figure format: png / svg / pdf")] = "png",
    x_axis: Annotated[
        str,
        typer.Option(
            "--x-axis",
            help="Pareto cost axis: cost ($) / latency (s) / tokens (output tokens)",
        ),
    ] = "cost",
) -> None:
    """Render Pareto / effort-curve / A→B-forest figures from a ledger (needs the ``plot`` extra)."""
    if fmt not in ("png", "svg", "pdf"):
        console.print(f"[red]unsupported --format {fmt!r}[/red] (choose png / svg / pdf)")
        raise typer.Exit(2)
    if bool(a) != bool(b):
        console.print("[red]--a and --b must be given together[/red] (each names a variant)")
        raise typer.Exit(2)
    from claude_ablation_lab.analyze import X_AXES
    from claude_ablation_lab.analyze import report as run_report

    if x_axis not in X_AXES:
        console.print(f"[red]unsupported --x-axis {x_axis!r}[/red] (choose {'/'.join(X_AXES)})")
        raise typer.Exit(2)
    # Pareto marking is axis-specific — report() must mark against the plotted axis.
    cells = run_report(ledger, x_axis=x_axis)
    wanted = set(task) if task else None
    if wanted is not None:
        cells = [c for c in cells if c.task_id in wanted]
    if not cells:
        console.print(f"[yellow]no graded rows to plot in {ledger}[/yellow]")
        return
    try:
        from claude_ablation_lab import plot as plot_mod
    except ImportError:
        console.print('[red]matplotlib not installed[/red] — run: pip install -e ".[plot]"')
        raise typer.Exit(1) from None

    compare_rows: list[CompareRow] = []
    if a and b:
        from claude_ablation_lab.analyze import compare as run_compare

        compare_rows = run_compare(ledger, a, b)
        if wanted is not None:  # honour --task on the forest too, not only the per-task figures
            compare_rows = [r for r in compare_rows if r.task_id in wanted]
        if not compare_rows:  # a requested A/B with nothing to show must say so
            console.print(
                f"[yellow]no A/B forest: no task ran under both {a} and {b}"
                f"{' for the selected tasks' if wanted is not None else ''}[/yellow]"
            )
    written = plot_mod.render_all(
        cells, compare_rows, out, fmt=fmt, a=a or "A", b=b or "B", x_axis=x_axis
    )
    console.print(f"wrote {len(written)} figure(s) → {out}")
    for path in written:
        console.print(f"  {path.name}")


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


def _with_interval(mean: float, lo: float | None, hi: float | None, places: int) -> str:
    """``mean [lo,hi]`` when an across-epoch interval exists, else the bare mean."""
    base = _fmt(mean, places)
    if lo is None or hi is None:
        return base
    return f"{base} [{_fmt(lo, places)},{_fmt(hi, places)}]"


def _fmt_tokens(c) -> str:  # type: ignore[no-untyped-def]
    """Mean output tokens with its epoch interval; mixed-era shows the denominator."""
    if c.mean_output_tokens is None:
        return "—"
    base = f"{c.mean_output_tokens:.0f}"
    if c.tokens_ci_low is not None and c.tokens_ci_high is not None:
        # Same interval treatment as cost/latency — the tokens axis must not be the
        # one column whose computed uncertainty is silently dropped (round-3 review).
        base += f" [{c.tokens_ci_low:.0f},{c.tokens_ci_high:.0f}]"
    if c.n_token_epochs < c.n_epochs:  # partial coverage must be visible
        base += f" ({c.n_token_epochs}/{c.n_epochs})"
    return base


def _print_report(cells: list, *, x_axis: str = "cost") -> None:  # type: ignore[type-arg]
    """Render report cells: quality (±epoch CI), cost, latency, tokens, Pareto, leakage."""
    table = Table(title=f"report — quality vs {x_axis} (epochs = exploratory run-variance)")
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
        "out-tok",
        "",
    ):
        table.add_column(col)
    for c in cells:
        flags = " ".join(
            f
            for f, on in (
                ("★", c.pareto),
                ("⚠LEAK", c.leakage),
                ("⚠SPEC", c.n_spec > 1),
                ("⚠VER", c.n_grader_versions > 1),
                (f"⚠{c.n_unparseable}unp", c.n_unparseable > 0),
            )
            if on
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
            _with_interval(c.mean_cost, c.cost_ci_low, c.cost_ci_high, 4),
            _with_interval(c.mean_latency, c.latency_ci_low, c.latency_ci_high, 1),
            _fmt_tokens(c),
            flags,
        )
    console.print(table)
    console.print(
        f"★ = Pareto-optimal (quality vs {x_axis}) · ⚠LEAK = shuffled-label self-test off 0.5 · "
        "⚠SPEC = cell mixes task specs · ⚠VER = cell mixes grader versions · "
        "⚠Nunp = N unparseable epochs counted as honest 0.0 · "
        "every [lo,hi] at <5 epochs is the min–max epoch range, not a 95% CI · "
        "out-tok (n/N) = tokens measured on n of N epochs"
    )


def _print_advice(advice: list, reflex: str, margin: float) -> None:  # type: ignore[type-arg]
    """Render per-(task, variant) cost recommendations (biggest overpay first)."""
    table = Table(
        title=f"advise — cheapest config within margin {margin:g} of the best (vs {reflex})"
    )
    for col in ("task", "variant", "reflex→use", "save$", "×", "qual", "n", "Δlat s", "why"):
        table.add_column(col)
    total = 0.0
    any_fallback = any_suspect = any_vacuous = False
    for a in advice:
        if not a.vacuous:  # a vacuous row (best ≤ margin) is not a real overpay
            total += max(0.0, a.cost_saving)
        any_fallback = any_fallback or a.reflex_fallback
        any_suspect = any_suspect or a.suspect
        any_vacuous = any_vacuous or a.vacuous
        star = "*" if a.reflex_fallback else ""
        table.add_row(
            a.task_id,
            a.variant,
            f"{a.reflex_model}/{a.reflex_effort}{star}→{a.rec_model}/{a.rec_effort}",
            _fmt(a.cost_saving, 4),
            "—" if a.cost_multiple is None else f"{a.cost_multiple:.1f}×",
            _fmt(a.rec_value),
            str(a.n_epochs),
            _fmt(a.latency_saving, 1),
            a.note,
        )
    console.print(table)
    legend = [
        f"Σ per-run overpay (excl. n/a rows): [bold]${total:.4f}[/bold]",
        "qual = recommended config's absolute mean quality",
        "Δlat s = reflex − use (negative = cheaper yet slower)",
        "a point estimate at n epochs (run-variance, not a population; `report` has the CIs)",
    ]
    if any_fallback:
        legend.append("* exact reflex absent; measured vs the nearest config that ran")
    if any_suspect:
        legend.append(
            "⚠suspect = a report validity flag (leakage / mixed spec / grader-version / unparseable)"
        )
    if any_vacuous:
        legend.append("n/a = best config scores ≤ margin (nothing meaningfully works)")
    console.print(" · ".join(legend))


def _print_compare(deltas: list, a: str, b: str) -> None:  # type: ignore[type-arg]
    """Render variant A→B deltas with the paired-bootstrap verdict."""
    table = Table(title=f"compare  A={a}  →  B={b}")
    for col in (
        "task",
        "pairs",
        "mean A",
        "mean B",
        "Δ (B−A)",
        "CI (context)",
        "exact p",
        "real?",
        "note",
    ):
        table.add_column(col)
    for d in deltas:
        ci = f"[{_fmt(d.ci_low)},{_fmt(d.ci_high)}]" if d.ci_low is not None else "—"
        p = "—" if d.p_value is None else f"{d.p_value:.3g} (n≠0: {d.n_nonzero})"
        table.add_row(
            d.task_id,
            str(d.n_pairs),
            _fmt(d.mean_a),
            _fmt(d.mean_b),
            _fmt(d.delta),
            ci,
            p,
            "[green]yes[/green]" if d.real else "no",
            d.note,
        )
    console.print(table)
    console.print(
        "[dim]real? = exact sign-flip permutation test at α=0.05 (needs ≥6 nonzero pairs); "
        "the bootstrap CI is effect-size context, never the verdict.[/dim]"
    )


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
        "[dim]rough FLOOR: one cheapest-cell calibration extrapolated to all cells — a mixed "
        "grid (pricier models, higher efforts, agentic tasks) commonly runs 2–5× this. "
        "Rate-limit headroom — not dollars — is the real budget.[/dim]"
    )


# --- the pairwise-judge phase (t9) --------------------------------------------------

_JUDGE_CONTROLS_ROOT = Path(__file__).resolve().parents[3] / "examples" / "judge-controls"


def _judge_instances() -> list:  # type: ignore[type-arg]
    from claude_ablation_lab.judges import JUDGE_NAMES, get_judge

    return [get_judge(name) for name in JUDGE_NAMES]


def _print_controls(report: object) -> None:
    from claude_ablation_lab.judge_orchestrate import ControlsReport

    if not isinstance(report, ControlsReport):  # pragma: no cover — narrowing only
        raise TypeError("expected a ControlsReport")
    table = Table(title="judge validity controls")
    table.add_column("judge")
    table.add_column("control")
    table.add_column("pass")
    table.add_column("detail")
    for judge_id, outcomes in sorted(report.per_judge.items()):
        for outcome in outcomes:
            table.add_row(
                judge_id,
                outcome.name,
                "[green]yes[/green]" if outcome.passed else "[red]NO[/red]",
                outcome.detail,
            )
    console.print(table)


@app.command()
def judge(
    suite: Annotated[Path, typer.Argument(help="Task-suite dir or a single task YAML (t9)")],
    ledger: Annotated[
        Path, typer.Option(help="CONTESTANT ledger the outputs are read from")
    ] = Path("results/judge-pilot.jsonl"),
    judge_ledger: Annotated[Path, typer.Option(help="Judge ledger (JSONL) to append to")] = Path(
        "results/judge.jsonl"
    ),
    controls_only: Annotated[
        bool,
        typer.Option("--controls-only", help="Run/score the validity controls, then stop"),
    ] = False,
    baseline: Annotated[
        str | None,
        typer.Option(
            help="Override the measured-cheapest baseline as 'model/effort' "
            "(record the reason in the design doc — the default is deterministic and "
            "quality-blind on purpose)"
        ),
    ] = None,
    pairs: Annotated[
        str, typer.Option(help="Pairing scheme: baseline (success criterion) / all")
    ] = "baseline",
    max_workers: Annotated[int, typer.Option(help="Concurrent judge CLI calls")] = 4,
    timeout_s: Annotated[float, typer.Option(help="Per-call judge timeout")] = 240.0,
    task: Annotated[list[str] | None, typer.Option(help="Only judge these task ids")] = None,
) -> None:
    """Pairwise-judge stored contestant outputs (codex + gemini; controls gate first)."""
    from claude_ablation_lab.judge_ledger import load_judge_rows
    from claude_ablation_lab.judge_orchestrate import (
        JudgePassHaltedError,
        enumerate_pairs,
        evaluate_controls,
        load_control_pairs,
        pick_baseline,
        run_judge_pass,
    )
    from claude_ablation_lab.ledger import load_rows
    from claude_ablation_lab.provenance import gather_provenance

    judges = _judge_instances()
    versions = {j.judge_id: j.version for j in judges}
    transcripts = judge_ledger.parent / "judge_transcripts"
    harness_sha = gather_provenance().harness_sha

    if controls_only:
        control_pairs = load_control_pairs(_JUDGE_CONTROLS_ROOT)
        console.print(
            f"[bold]controls gate[/bold] — {len(control_pairs)} pairs × 2 orders × "
            f"{len(judges)} judges (resumable)"
        )
        try:
            summary = run_judge_pass(
                control_pairs,
                judges,
                ledger_path=judge_ledger,
                transcripts_dir=transcripts,
                timeout_s=timeout_s,
                max_workers=max_workers,
                harness_sha=harness_sha,
            )
        except JudgePassHaltedError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from None
        console.print(
            f"controls pass: {summary.n_ok} ok, {summary.n_failed_final} failed, "
            f"{summary.n_skipped_resume} resumed"
        )
        report = evaluate_controls(load_judge_rows(judge_ledger), versions)
        _print_controls(report)
        if not report.passed:
            console.print(
                "[red]controls FAILED — do not judge real pairs.[/red] Inspect "
                f"{transcripts}, revise the template (bump pj-v*), re-run --controls-only."
            )
            raise typer.Exit(2)
        console.print("[green]controls passed for every judge — real judging is unlocked.[/green]")
        return

    # The gate: real pairs refuse to run on unpassed controls (stored rows only).
    report = evaluate_controls(load_judge_rows(judge_ledger), versions)
    if not report.passed:
        _print_controls(report)
        console.print(
            "[red]validity controls have not passed for every judge at its current "
            "judge_version[/red] — run `ablation judge ... --controls-only` first."
        )
        raise typer.Exit(2)

    tasks = _load_suite(suite, task)
    contestant_rows = load_rows(ledger)
    chosen = baseline or pick_baseline(contestant_rows, {t.id for t in tasks})
    origin = "override" if baseline else "measured cheapest (cost-only, frozen pre-judging)"
    console.print(f"baseline: [bold]{chosen}[/bold] ({origin})")

    pair_specs, dropped = enumerate_pairs(tasks, contestant_rows, baseline=chosen, pairs=pairs)
    for reason in dropped:
        console.print(f"[yellow]dropped pair:[/yellow] {reason}")
    if not pair_specs:
        console.print("[red]no judgeable pairs[/red]")
        raise typer.Exit(2)
    console.print(
        f"[bold]privacy:[/bold] contestant outputs + reference excerpts in "
        f"{len(pair_specs)} pairs will be sent to OpenAI (codex) and Google (gemini)."
    )
    try:
        summary = run_judge_pass(
            pair_specs,
            judges,
            ledger_path=judge_ledger,
            transcripts_dir=transcripts,
            timeout_s=timeout_s,
            max_workers=max_workers,
            harness_sha=harness_sha,
        )
    except JudgePassHaltedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    console.print(
        f"judge pass: {summary.n_ok} ok, {summary.n_failed_final} failed, "
        f"{summary.n_skipped_resume} resumed of {summary.n_calls_planned} calls"
    )
    _print_judge_report(load_judge_rows(judge_ledger), contestant_rows, baseline=chosen)


@app.command("judge-report")
def judge_report_cmd(
    judge_ledger: Annotated[Path, typer.Argument(help="Judge ledger (JSONL)")],
    ledger: Annotated[
        Path, typer.Option(help="CONTESTANT ledger for the cost/latency/token joins")
    ] = Path("results/judge-pilot.jsonl"),
    baseline: Annotated[
        str | None, typer.Option(help="Baseline config (default: measured cheapest)")
    ] = None,
    primary: Annotated[
        str | None, typer.Option(help="Predeclared primary contrast candidate")
    ] = None,
) -> None:
    """Preference verdicts per contrast: W/L/T, sign-flip p, cost× and length× context."""
    from claude_ablation_lab.judge_analyze import DEFAULT_PRIMARY
    from claude_ablation_lab.judge_ledger import REAL_PAIR, load_judge_rows
    from claude_ablation_lab.judge_orchestrate import pick_baseline
    from claude_ablation_lab.ledger import load_rows

    judge_rows = load_judge_rows(judge_ledger)
    contestant_rows = load_rows(ledger)
    if not any(r.control == REAL_PAIR for r in judge_rows):
        console.print(f"[yellow]no real judged pairs in {judge_ledger}[/yellow]")
        raise typer.Exit(1)
    task_ids = {r.task_id for r in judge_rows if r.control == REAL_PAIR}
    chosen = baseline or pick_baseline(contestant_rows, task_ids)
    _print_judge_report(
        judge_rows, contestant_rows, baseline=chosen, primary=primary or DEFAULT_PRIMARY
    )


@app.command("judge-spotcheck")
def judge_spotcheck(
    suite: Annotated[Path, typer.Argument(help="Task-suite dir (to rebuild pair texts)")],
    judge_ledger: Annotated[Path, typer.Option(help="Judge ledger (JSONL)")] = Path(
        "results/judge.jsonl"
    ),
    ledger: Annotated[Path, typer.Option(help="CONTESTANT ledger")] = Path(
        "results/judge-pilot.jsonl"
    ),
    out: Annotated[Path, typer.Option(help="Blinded spot-check file to write")] = Path(
        "results/judge_spotcheck.md"
    ),
    n: Annotated[int, typer.Option(help="Pairs to sample")] = 10,
    seed: Annotated[int, typer.Option(help="Sampling seed")] = 42,
    baseline: Annotated[str | None, typer.Option(help="Baseline config")] = None,
    decisive_only: Annotated[
        bool,
        typer.Option(
            "--decisive-only/--all-pairs",
            help="Sample only decisive-consensus pairs (tie-excluded gate; default)",
        ),
    ] = True,
    stratify: Annotated[
        list[str] | None,
        typer.Option(
            "--stratify", help="Contestant config to guarantee in the sample (repeatable)"
        ),
    ] = None,
    score: Annotated[
        Path | None,
        typer.Option("--score", help="Score a FILLED spot-check file instead of writing one"),
    ] = None,
) -> None:
    """Write a blinded ~10-pair human spot-check file, or score the filled one (>=80% to headline)."""
    from claude_ablation_lab.judge_ledger import load_judge_rows
    from claude_ablation_lab.judge_orchestrate import (
        enumerate_pairs,
        pick_baseline,
        sample_spotcheck,
        score_spotcheck,
    )
    from claude_ablation_lab.ledger import load_rows

    judge_rows = load_judge_rows(judge_ledger)
    if score is not None:
        report = score_spotcheck(score, judge_rows)
        if report.n_strict_scored == 0:
            console.print("[yellow]no filled verdicts found in the spot-check file[/yellow]")
            raise typer.Exit(1)
        gate = report.agreement
        if gate is None:
            console.print(
                "[yellow]no decisive-consensus pairs among the filled verdicts — the gate "
                "cannot be scored (every answered pair was a consensus tie)[/yellow]"
            )
            raise typer.Exit(1)
        color = "green" if gate >= 0.8 else "red"
        console.print(
            f"spot-check agreement (decisive, tie-excluded — the gate): "
            f"[{color}]{report.n_agree}/{report.n_scored} ({gate:.0%})[/{color}] "
            "— ≥80% required to headline the judge verdicts"
        )
        strict = report.strict_agreement or 0.0
        console.print(
            f"  context: strict 3-way {report.n_strict_agree}/{report.n_strict_scored} "
            f"({strict:.0%}); human called tie on {report.n_human_tie_on_decisive} "
            "decisive pair(s)"
        )
        return

    tasks = _load_suite(suite, None)
    contestant_rows = load_rows(ledger)
    chosen = baseline or pick_baseline(contestant_rows, {t.id for t in tasks})
    pair_specs, _dropped = enumerate_pairs(tasks, contestant_rows, baseline=chosen, pairs="all")
    path = sample_spotcheck(
        judge_rows,
        pair_specs,
        n=n,
        seed=seed,
        out_path=out,
        decisive_only=decisive_only,
        stratify=tuple(stratify or ()),
    )
    console.print(
        f"wrote [bold]{path}[/bold] — fill each `your_verdict:` blind, then re-run "
        "with --score <file>"
    )


def _print_judge_report(
    judge_rows: list,  # type: ignore[type-arg]
    contestant_rows: list,  # type: ignore[type-arg]
    *,
    baseline: str,
    primary: str | None = None,
) -> None:
    from claude_ablation_lab.judge_analyze import DEFAULT_PRIMARY, judge_report

    summaries = judge_report(
        judge_rows, contestant_rows, baseline=baseline, primary=primary or DEFAULT_PRIMARY
    )
    if not summaries:
        console.print("[yellow]no judged contrasts against the baseline[/yellow]")
        return
    table = Table(title=f"pairwise-judge verdicts vs {baseline} (dr-v1)")
    for col in ("contrast", "W/L/T", "score", "p", "real?", "cost×", "len×", "noise", "note"):
        table.add_column(col)
    for s in summaries:
        marker = " ★" if s.primary else ""
        p_txt = "—" if s.p_value is None else f"{s.p_value:.3g} (n≠0: {s.n_nonzero})"
        if s.p_adjusted is not None:
            p_txt += f" → {s.p_adjusted:.3g} adj"
        disagree = (
            "—" if s.cross_judge_disagree_rate is None else f"{s.cross_judge_disagree_rate:.0%}"
        )
        order_txt = " ".join(f"{j}:{r:.0%}" for j, r in sorted(s.order_disagree_rate.items()))
        table.add_row(
            f"{s.config}{marker}",
            f"{s.wins}/{s.losses}/{s.ties} of {s.n_scored}",
            "—" if s.mean_score is None else f"{s.mean_score:+.2f}",
            p_txt,
            "[green]yes[/green]" if s.real else "no",
            "—" if s.cost_multiple is None else f"{s.cost_multiple:.1f}×",
            "—" if s.mean_length_ratio is None else f"{s.mean_length_ratio:.2f}×",
            f"flip {order_txt} | xjudge {disagree}",
            s.note,
        )
    console.print(table)
    console.print(
        "[dim]★ = predeclared primary contrast (others are Holm-corrected, exploratory); "
        "score = mean per-prompt preference in [-1,+1] (+ favors the contrast config); "
        "p = exact sign-flip over per-prompt scores; len× > 1 with a win is the verbosity "
        "tripwire — re-read length-stratified before believing it. Preference, not "
        "correctness.[/dim]"
    )


if __name__ == "__main__":
    app()
