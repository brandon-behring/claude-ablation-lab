"""Grid spec loading + cell expansion (validity drops, ordering, compatibility)."""

from __future__ import annotations

import pytest

from claude_ablation_lab.grid import (
    NONE_VARIANT,
    Cell,
    Grid,
    expand_grid,
    load_grid,
    model_effort_inert,
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
def test_effort_ok_unlisted_capable_model_allows_all() -> None:
    # An effort-CAPABLE model absent from effort_support supports every effort.
    grid = Grid(("sonnet",), ("low", "high", "max"), ("none",), 1, {"opus": ("low", "high", "max")})
    assert grid.effort_ok("sonnet", "max")  # unlisted capable → unrestricted
    assert grid.effort_ok("opus", "max")
    assert not grid.effort_ok("opus", "weird")


@pytest.mark.unit
def test_model_effort_inert_matrix() -> None:
    # Haiku 4.5 has no effort parameter (CV2); the other families do; unknown → capable.
    assert model_effort_inert("haiku")
    assert model_effort_inert("claude-haiku-4-5")
    assert not model_effort_inert("sonnet")
    assert not model_effort_inert("opus")
    assert not model_effort_inert("claude-fable-5")
    assert not model_effort_inert("some-future-model")  # unknown → assumed effort-capable


@pytest.mark.unit
def test_effort_inert_model_collapses_to_one_canonical_cell() -> None:
    # An effort-inert model (Haiku) is valid only for the single canonical effort — its
    # other efforts are provider-identical duplicates, dropped at expansion (CV2).
    grid = Grid(("haiku", "opus"), ("low", "high", "max"), ("none",), 2)
    assert grid.canonical_effort("haiku") == "low"  # first listed
    assert grid.effort_ok("haiku", "low")  # canonical — kept
    assert not grid.effort_ok("haiku", "high")  # inert duplicate — dropped
    assert not grid.effort_ok("haiku", "max")  # inert duplicate — dropped
    cells = expand_grid(grid, [_task("t", infra_repo=None)])
    haiku = {(c.model, c.effort) for c in cells if c.model == "haiku"}
    assert haiku == {("haiku", "low")}  # one config, not three
    assert sum(c.model == "haiku" for c in cells) == 2  # 1 config × 2 epochs
    assert sum(c.model == "opus" for c in cells) == 6  # 3 efforts × 2 epochs (capable)


@pytest.mark.unit
def test_canonical_effort_honours_effort_support_narrowing() -> None:
    # If a grid narrows the inert model via effort_support, the canonical cell is the
    # first listed effort that support permits (author intent respected).
    grid = Grid(("haiku",), ("low", "high"), ("none",), 1, {"haiku": ("high",)})
    assert grid.canonical_effort("haiku") == "high"
    assert grid.effort_ok("haiku", "high")
    assert not grid.effort_ok("haiku", "low")


@pytest.mark.unit
def test_effort_inert_model_with_no_permitted_effort_is_dropped() -> None:
    # If a grid's effort_support permits NONE of the listed efforts for an inert model, it
    # is dropped ENTIRELY (never silently run at an effort the author excluded) — matching
    # how a capable model with no permitted effort is dropped.
    grid = Grid(("haiku", "opus"), ("low", "high"), ("none",), 1, {"haiku": ("max",)})
    assert grid.canonical_effort("haiku") is None
    assert not grid.effort_ok("haiku", "low")
    assert not grid.effort_ok("haiku", "high")
    cells = expand_grid(grid, [_task("t", infra_repo=None)])
    assert not any(c.model == "haiku" for c in cells)  # dropped, not silently run at low
    assert any(c.model == "opus" for c in cells)  # capable model unaffected


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
