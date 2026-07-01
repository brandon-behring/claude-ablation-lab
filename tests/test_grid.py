"""Grid spec loading + cell expansion (validity drops, ordering, compatibility)."""

from __future__ import annotations

import pytest

from claude_ablation_lab.grid import (
    NONE_VARIANT,
    Cell,
    Grid,
    expand_grid,
    load_grid,
    parse_variant,
)
from claude_ablation_lab.task import Task


def _task(task_id: str, *, infra_repo: str | None) -> Task:
    return Task(id=task_id, domain="d", grader="anchor", mode="single", infra_repo=infra_repo)


@pytest.mark.unit
def test_parse_variant_none_and_repo_ref() -> None:
    assert parse_variant(NONE_VARIANT) is None
    assert parse_variant("~/r@HEAD") == ("~/r", "HEAD")
    # ref may itself contain '@' — only the first '@' splits.
    assert parse_variant("repo@feat@v2") == ("repo", "feat@v2")


@pytest.mark.unit
def test_parse_variant_malformed_raises() -> None:
    for bad in ("@HEAD", "repo@", "norefnoat"):
        with pytest.raises(ValueError, match="variant"):
            parse_variant(bad)


@pytest.mark.unit
def test_effort_ok_unlisted_model_allows_all() -> None:
    grid = Grid(("haiku",), ("low", "high"), ("none",), 1, {"opus": ("low", "high", "max")})
    assert grid.effort_ok("haiku", "max")  # unlisted → unrestricted
    assert grid.effort_ok("opus", "max")
    assert not grid.effort_ok("opus", "weird")


@pytest.mark.unit
def test_expand_drops_unsupported_effort() -> None:
    grid = Grid(
        ("haiku", "opus"), ("low", "max"), ("none",), 1, {"haiku": ("low",), "opus": ("low", "max")}
    )
    cells = expand_grid(grid, [_task("t", infra_repo=None)])
    pairs = {(c.model, c.effort) for c in cells}
    assert ("haiku", "max") not in pairs  # dropped
    assert pairs == {("haiku", "low"), ("opus", "low"), ("opus", "max")}


@pytest.mark.unit
def test_expand_task_variant_compatibility() -> None:
    grid = Grid(("haiku",), ("low",), ("none", "~/repo@HEAD"), 1)
    agnostic = _task("t1", infra_repo=None)
    infra = _task("t2", infra_repo="~/repo")
    cells = expand_grid(grid, [agnostic, infra])
    variants = {(c.task_id, c.variant) for c in cells}
    # infra-agnostic only under 'none'; infra-sensitive only under a worktree variant.
    assert variants == {("t1", "none"), ("t2", "~/repo@HEAD")}


@pytest.mark.unit
def test_expand_drops_malformed_variant_without_aborting() -> None:
    # A malformed variant spec must drop-and-log like every other invalid combo — one bad
    # entry must not abort the whole expansion (and with it dry-run/estimate/run).
    grid = Grid(("haiku",), ("low",), ("repoA@v1", "MALFORMED-NO-AT"), 1)
    cells = expand_grid(grid, [_task("t", infra_repo="repoA")])
    assert {c.variant for c in cells} == {"repoA@v1"}


@pytest.mark.unit
def test_expand_drops_task_under_mismatched_infra_repo() -> None:
    # A grid may list several infra repos; a task runs only under ITS repo's refs, never
    # an unrelated one (which would load the wrong project's config and measure nothing).
    grid = Grid(("haiku",), ("low",), ("repoA@v1", "repoB@v1"), 1)
    variants = {c.variant for c in expand_grid(grid, [_task("t", infra_repo="repoA")])}
    assert variants == {"repoA@v1"}  # repoB@v1 dropped — wrong repo


@pytest.mark.unit
def test_expand_epochs_and_order() -> None:
    grid = Grid(("haiku",), ("low",), ("none",), 3)
    cells = expand_grid(grid, [_task("t", infra_repo=None)])
    assert [c.epoch for c in cells] == [0, 1, 2]  # deterministic, contiguous
    assert all(isinstance(c, Cell) for c in cells)


@pytest.mark.unit
def test_load_grid_roundtrip(tmp_path) -> None:
    spec = tmp_path / "g.yaml"
    spec.write_text(
        "models: [haiku, opus]\nefforts: [low, max]\nvariants: [none]\nepochs: 2\n"
        "effort_support:\n  haiku: [low]\n  opus: [low, max]\n",
        encoding="utf-8",
    )
    grid = load_grid(spec)
    assert grid.models == ("haiku", "opus")
    assert grid.epochs == 2
    assert grid.effort_support["haiku"] == ("low",)


@pytest.mark.unit
def test_load_grid_rejects_bad_epochs_and_missing_axes(tmp_path) -> None:
    bad_epochs = tmp_path / "e.yaml"
    bad_epochs.write_text("models: [h]\nefforts: [low]\nepochs: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="epochs"):
        load_grid(bad_epochs)
    no_models = tmp_path / "m.yaml"
    no_models.write_text("efforts: [low]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="models"):
        load_grid(no_models)
