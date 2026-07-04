"""graders.get_grader — the lazy registry that resolves a task's grader ref."""

from __future__ import annotations

import pytest

from claude_ablation_lab.grade import Grader
from claude_ablation_lab.graders import GRADER_NAMES, get_grader


@pytest.mark.unit
@pytest.mark.parametrize("name", ["anchor", "anchor_strict", "validator", "exact_match"])
def test_get_grader_returns_a_grader(name: str) -> None:
    grader = get_grader(name)
    assert isinstance(grader, Grader)
    assert grader.version


@pytest.mark.unit
def test_get_grader_classification_when_available() -> None:
    pytest.importorskip("eval_toolkit")
    assert get_grader("classification").version == "t1-clf-v1"


@pytest.mark.unit
def test_unknown_grader_raises() -> None:
    with pytest.raises(ValueError, match="unknown grader"):
        get_grader("nope")


@pytest.mark.unit
def test_grader_names_cover_seed_graders_and_anchor_strict() -> None:
    assert set(GRADER_NAMES) == {
        "classification",
        "validator",
        "anchor",
        "anchor_strict",
        "books_validate",
        "exact_match",
    }


@pytest.mark.unit
def test_every_registered_grader_has_a_preparer() -> None:
    # The registry and prepare._PREPARERS are parallel enumerations; a mismatch today
    # surfaces only at sweep start ("no preparer"), i.e. at live-run time.
    from claude_ablation_lab.prepare import _PREPARERS

    assert set(_PREPARERS) == set(GRADER_NAMES)
