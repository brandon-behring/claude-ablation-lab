"""Property-based invariants (hypothesis) for the two pure, sweep-critical
functions — ``t1_dataset.subsample`` (balanced / exact / seed-stable) and
``grid.expand_grid`` (cells = valid (model, effort) combos × variants × epochs).
Fuzzed inputs catch the tail cases hand-picked examples miss.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from claude_ablation_lab.grid import Grid, expand_grid
from claude_ablation_lab.t1_dataset import build_gold, subsample
from claude_ablation_lab.task import Task

# even n in [2, 200] (the synthetic_holdout fixture has 100 rows per class).
_EVEN_N = st.integers(min_value=1, max_value=100).map(lambda k: 2 * k)
_NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=4)


@pytest.mark.property
@given(n=_EVEN_N, seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_subsample_balanced_exact_and_seed_stable(synthetic_holdout, n: int, seed: int) -> None:
    picked = subsample(synthetic_holdout, n=n, seed=seed)
    assert len(picked) == n  # exactly n rows
    counts = picked["label"].value_counts().to_dict()
    assert counts.get(1, 0) == n // 2 and counts.get(0, 0) == n // 2  # balanced classes
    assert picked.equals(subsample(synthetic_holdout, n=n, seed=seed))  # seed-stable
    assert set(build_gold(picked)) == set(range(n))  # positional idx 0..n-1


@pytest.mark.property
@given(
    models=st.lists(_NAMES, min_size=1, max_size=4, unique=True),
    efforts=st.lists(_NAMES, min_size=1, max_size=3, unique=True),
    epochs=st.integers(min_value=1, max_value=3),
    restrict=st.booleans(),
    data=st.data(),
)
def test_expand_grid_count_equals_valid_combos_times_epochs(
    models: list[str],
    efforts: list[str],
    epochs: int,
    restrict: bool,
    data: st.DataObject,
) -> None:
    support: dict[str, tuple[str, ...]] = {}
    if restrict:  # optionally restrict the first model to a subset of efforts (incl. none)
        keep = data.draw(st.lists(st.sampled_from(efforts), max_size=len(efforts), unique=True))
        support[models[0]] = tuple(keep)
    grid = Grid(
        models=tuple(models),
        efforts=tuple(efforts),
        variants=("none",),  # one compatible variant for an infra-agnostic task
        epochs=epochs,
        effort_support=support,
    )
    task = Task(id="t", domain="d", grader="anchor", mode="single", infra_repo=None)
    cells = expand_grid(grid, [task])
    valid = [(m, e) for m in models for e in efforts if grid.effort_ok(m, e)]
    assert len(cells) == len(valid) * epochs
    assert {(c.model, c.effort) for c in cells} <= set(valid)
    assert all(c.variant == "none" and 0 <= c.epoch < epochs for c in cells)
