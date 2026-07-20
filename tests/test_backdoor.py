"""Known-answer fixtures for the backdoor-criterion grader core.

Every graph here is a textbook case with a hand-derivable answer — this module is the
"a buggy grader poisons every number" defense for the causal family. The fixtures cover
exactly the structures the item generator dials difficulty with: confounder triangle,
mediation chain, collider, M-bias, front-door, butterfly.
"""

from __future__ import annotations

import pytest

from claude_ablation_lab.causal.backdoor import (
    Dag,
    is_minimal_adjustment_set,
    is_valid_adjustment_set,
)

pytestmark = pytest.mark.unit


def _valid(dag: Dag, adjustment: set[str], treatment: str = "X", outcome: str = "Y") -> bool:
    return is_valid_adjustment_set(
        dag, treatment=treatment, outcome=outcome, adjustment=frozenset(adjustment)
    )


class TestConfounderTriangle:
    """Z→X, Z→Y, X→Y — the canonical confounder."""

    dag = Dag({"X": ("Z",), "Y": ("Z", "X")})

    def test_empty_set_is_invalid(self) -> None:
        assert not _valid(self.dag, set())

    def test_confounder_is_valid_and_minimal(self) -> None:
        assert _valid(self.dag, {"Z"})
        assert is_minimal_adjustment_set(
            self.dag, treatment="X", outcome="Y", adjustment=frozenset({"Z"})
        )


class TestMediationChain:
    """X→M→Y — no backdoor path exists; the mediator must not be adjusted."""

    dag = Dag({"M": ("X",), "Y": ("M",)})

    def test_empty_set_is_valid(self) -> None:
        assert _valid(self.dag, set())

    def test_mediator_is_invalid_descendant(self) -> None:
        """Adjusting M violates the no-descendant condition (and blocks the effect)."""
        assert not _valid(self.dag, {"M"})


class TestCollider:
    """X→C←Y with X→Y — conditioning on the collider *creates* bias."""

    dag = Dag({"C": ("X", "Y"), "Y": ("X",)})

    def test_empty_set_is_valid(self) -> None:
        assert _valid(self.dag, set())

    def test_conditioning_on_collider_is_invalid(self) -> None:
        assert not _valid(self.dag, {"C"})


class TestMBias:
    """X←U1→Z←U2→Y with X→Y — the M-structure.

    Z looks like a confounder (associated with both X and Y) but is a collider on the
    only backdoor path; adjusting it OPENS the path. This is the fixture that separates
    'adjust for everything observed' from understanding — the item generator's core
    trap, so the grader must get it exactly right.
    """

    dag = Dag({"Z": ("U1", "U2"), "X": ("U1",), "Y": ("U2", "X")})

    def test_empty_set_is_valid(self) -> None:
        assert _valid(self.dag, set())

    def test_adjusting_the_collider_is_invalid(self) -> None:
        assert not _valid(self.dag, {"Z"})

    def test_either_latent_parent_is_valid(self) -> None:
        assert _valid(self.dag, {"U1"})
        assert _valid(self.dag, {"U2"})

    def test_collider_plus_one_parent_is_valid_again(self) -> None:
        """{Z, U1} re-blocks what conditioning on Z opened — supersets can rescue."""
        assert _valid(self.dag, {"Z", "U1"})
        assert _valid(self.dag, {"Z", "U2"})

    def test_valid_non_minimal_superset_is_valid_but_not_minimal(self) -> None:
        """The non-uniqueness that kills exact-match grading, in one assertion."""
        assert _valid(self.dag, {"U1", "U2"})
        assert not is_minimal_adjustment_set(
            self.dag, treatment="X", outcome="Y", adjustment=frozenset({"U1", "U2"})
        )


class TestFrontDoor:
    """U→X→M→Y with U→Y — identification exists only via the front door.

    No *backdoor-valid* observable set exists: {} leaves X←U→Y open, and M is a
    descendant of X. The grader must reject both — 'is the effect backdoor-identifiable
    at all' is itself an item form.
    """

    dag = Dag({"X": ("U",), "M": ("X",), "Y": ("M", "U")})

    def test_empty_set_is_invalid(self) -> None:
        assert not _valid(self.dag, set())

    def test_mediator_is_invalid(self) -> None:
        assert not _valid(self.dag, {"M"})

    def test_latent_confounder_would_be_valid_if_observable(self) -> None:
        assert _valid(self.dag, {"U"})


class TestButterfly:
    """U1→Z←U2, U1→X, U2→Y, Z→X, Z→Y, X→Y — Z is confounder AND collider at once."""

    dag = Dag({"Z": ("U1", "U2"), "X": ("U1", "Z"), "Y": ("U2", "Z", "X")})

    def test_z_alone_is_invalid(self) -> None:
        """Adjusting Z blocks its confounding paths but opens U1→Z←U2."""
        assert not _valid(self.dag, {"Z"})

    def test_z_plus_either_parent_is_valid(self) -> None:
        assert _valid(self.dag, {"Z", "U1"})
        assert _valid(self.dag, {"Z", "U2"})

    def test_both_parents_without_z_is_invalid(self) -> None:
        """{U1, U2} leaves the Z→X / Z→Y confounding paths unblocked."""
        assert not _valid(self.dag, {"U1", "U2"})


class TestDSeparationDirect:
    def test_chain_blocked_by_middle(self) -> None:
        dag = Dag({"B": ("A",), "C": ("B",)})
        assert dag.d_separated("A", "C", frozenset({"B"}))
        assert not dag.d_separated("A", "C", frozenset())

    def test_collider_opened_by_descendant(self) -> None:
        """Conditioning on a collider's descendant opens it — the subtle rule."""
        dag = Dag({"C": ("A", "B"), "D": ("C",)})
        assert dag.d_separated("A", "B", frozenset())
        assert not dag.d_separated("A", "B", frozenset({"C"}))
        assert not dag.d_separated("A", "B", frozenset({"D"}))


class TestConstructionAndRefusals:
    def test_cycle_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            Dag({"A": ("B",), "B": ("A",)})

    def test_self_loop_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="self-loop"):
            Dag({"A": ("A",)})

    def test_unknown_node_in_adjustment_raises(self) -> None:
        dag = Dag({"Y": ("X",)})
        with pytest.raises(ValueError, match="unknown node"):
            _valid(dag, {"GHOST"})

    def test_treatment_in_adjustment_raises(self) -> None:
        dag = Dag({"Y": ("X", "Z"), "X": ("Z",)})
        with pytest.raises(ValueError, match="may not contain"):
            _valid(dag, {"X"})

    def test_same_treatment_outcome_raises(self) -> None:
        dag = Dag({"Y": ("X",)})
        with pytest.raises(ValueError, match="distinct"):
            is_valid_adjustment_set(dag, treatment="X", outcome="X", adjustment=frozenset())
