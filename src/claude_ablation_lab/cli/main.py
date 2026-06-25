"""CLI for claude-ablation-lab.

Commands (built out across phases):
    ablation estimate <suite> <grid>   # project rate-limit usage before a sweep
    ablation run <suite> <grid>         # execute the model × effort × variant sweep
    ablation report <ledger>            # DuckDB aggregates: score±CI, cost, latency, Pareto
    ablation compare <ledger> --a <variant> --b <variant>   # paired-bootstrap delta, "is it real"

The v1 substrate is the `claude` CLI run headless; see CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from claude_ablation_lab._version import __version__

app = typer.Typer(
    name="ablation",
    help="Model × effort × config ablation/regression harness for Claude Code.",
    no_args_is_help=True,
)

_NOT_YET = "Command scaffolded but not yet implemented — see CLAUDE.md build phases."


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def estimate(
    suite: Annotated[Path, typer.Argument(help="Task-suite YAML or dir")],
    grid: Annotated[
        Path, typer.Argument(help="Grid spec YAML (models × efforts × variants × epochs)")
    ],
) -> None:
    """Calibrate on one cell, then project total rate-limit usage and warn (Phase 5)."""
    raise typer.Exit(_fail(_NOT_YET))


@app.command()
def run(
    suite: Annotated[Path, typer.Argument(help="Task-suite YAML or dir")],
    grid: Annotated[Path, typer.Argument(help="Grid spec YAML")],
    ledger: Annotated[Path, typer.Option(help="JSONL ledger to append to")] = Path(
        "results/ledger.jsonl"
    ),
) -> None:
    """Execute the sweep sequentially, appending each cell to the ledger (Phase 3/5)."""
    raise typer.Exit(_fail(_NOT_YET))


@app.command()
def report(
    ledger: Annotated[Path, typer.Argument(help="JSONL ledger to analyze")],
) -> None:
    """DuckDB aggregates per task×model×effort: score±CI, cost, latency, Pareto (Phase 4)."""
    raise typer.Exit(_fail(_NOT_YET))


@app.command()
def compare(
    ledger: Annotated[Path, typer.Argument(help="JSONL ledger")],
    a: Annotated[str, typer.Option("--a", help="Baseline variant (infra_repo@ref)")],
    b: Annotated[str, typer.Option("--b", help="Candidate variant (infra_repo@ref)")],
) -> None:
    """Per-task delta between two variants with paired bootstrap — is the difference real? (Phase 4)."""
    raise typer.Exit(_fail(_NOT_YET))


def _fail(msg: str) -> int:
    typer.echo(msg, err=True)
    return 1


if __name__ == "__main__":
    app()
