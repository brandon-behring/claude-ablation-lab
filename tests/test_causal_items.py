"""Generator + grader: determinism, self-consistency, and the trap actually trapping."""

from __future__ import annotations

from itertools import combinations

import pytest

from claude_ablation_lab.causal.backdoor import is_valid_adjustment_set
from claude_ablation_lab.causal.dgp import generate_items
from claude_ablation_lab.causal.grader import grade_identification

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def items():
    return generate_items(seed=7, per_structure=3)


class TestGeneratorDeterminism:
    def test_same_seed_same_items(self, items) -> None:
        again = generate_items(seed=7, per_structure=3)
        assert [i.item_id for i in again] == [i.item_id for i in items]
        assert [i.prompt for i in again] == [i.prompt for i in items]

    def test_different_seed_different_stories(self) -> None:
        a = generate_items(seed=1, per_structure=2)
        b = generate_items(seed=2, per_structure=2)
        assert [i.prompt for i in a] != [i.prompt for i in b]

    def test_unknown_structure_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown structure"):
            generate_items(seed=1, structures=("nonexistent",))


class TestItemSelfConsistency:
    """Every generated item's gold must be reproducible from its own DAG."""

    def test_identifiability_gold_matches_brute_force(self, items) -> None:
        for item in items:
            candidates = sorted(item.observed - {item.treatment, item.outcome})
            any_valid = any(
                is_valid_adjustment_set(
                    item.dag,
                    treatment=item.treatment,
                    outcome=item.outcome,
                    adjustment=frozenset(combo),
                )
                for size in range(len(candidates) + 1)
                for combo in combinations(candidates, size)
            )
            assert any_valid == item.backdoor_identifiable, item.item_id

    def test_prompt_names_every_node(self, items) -> None:
        """A node absent from the prompt would make its role unguessable-by-reading."""
        for item in items:
            for node in item.dag.nodes:
                assert node in item.prompt, f"{item.item_id}: {node} missing from prompt"

    def test_role_names_never_leak(self, items) -> None:
        """Variable names must not announce their graph role."""
        for item in items:
            for node in item.dag.nodes:
                assert not any(
                    hint in node.lower() for hint in ("confound", "collider", "mediat")
                ), f"{item.item_id}: leaky name {node}"


class TestTrapActuallyTraps:
    """The design property that makes the family discriminate (decision 12):
    'adjust for every observed covariate' must FAIL on the trap strata."""

    def test_reflex_answer_fails_on_m_bias_and_collider(self, items) -> None:
        for item in items:
            if item.structure not in ("m_bias", "collider"):
                continue
            reflex = {
                "identified": True,
                "adjustment_set": sorted(item.observed - {item.treatment, item.outcome}),
            }
            assert grade_identification(reflex, item).value == 0.0, item.item_id

    def test_front_door_items_are_not_backdoor_identifiable(self, items) -> None:
        for item in items:
            if item.structure == "front_door":
                assert not item.backdoor_identifiable, item.item_id

    def test_easy_stratum_rewards_the_textbook_answer(self, items) -> None:
        for item in items:
            if item.structure == "confounder":
                confounder = next(iter(item.observed - {item.treatment, item.outcome}))
                answer = {"identified": True, "adjustment_set": [confounder]}
                assert grade_identification(answer, item).value == 1.0, item.item_id


class TestGraderRules:
    def test_valid_nonminimal_superset_scores_full_with_minimality_zero(self, items) -> None:
        """The non-uniqueness fix in action: a valid superset is CORRECT, just flagged."""
        for item in items:
            if item.structure != "butterfly":
                continue
            # {Z, U1, U2} is a valid (super)set for the butterfly; minimal sets are pairs.
            full = sorted(item.observed - {item.treatment, item.outcome})
            score = grade_identification({"identified": True, "adjustment_set": full}, item)
            assert score.value == 1.0, item.item_id
            assert score.subscores["minimality"] == 0.0, item.item_id

    def test_unmeasured_variable_is_rejected(self, items) -> None:
        for item in items:
            if item.structure != "front_door":
                continue
            latent = next(iter(item.dag.nodes - item.observed))
            answer = {"identified": True, "adjustment_set": [latent]}
            score = grade_identification(answer, item)
            assert score.value == 0.0
            assert score.details["reason"] == "identifiability wrong"

    def test_correct_not_identifiable_scores_full(self, items) -> None:
        for item in items:
            if not item.backdoor_identifiable:
                answer = {"identified": False, "adjustment_set": []}
                assert grade_identification(answer, item).value == 1.0, item.item_id

    def test_malformed_answers_are_unparseable_not_zero_quality(self, items) -> None:
        item = items[0]
        for bad in (
            None,
            [],
            {"identified": "yes", "adjustment_set": []},
            {"identified": True, "adjustment_set": [1, 2]},
        ):
            score = grade_identification(bad, item)
            assert score.status == "unparseable", bad

    def test_treatment_in_set_is_wrong_answer_not_crash(self, items) -> None:
        """The grader must never crash on a model proposing the treatment itself."""
        item = next(i for i in items if i.structure == "confounder")
        answer = {"identified": True, "adjustment_set": [item.treatment]}
        score = grade_identification(answer, item)
        assert score.value == 0.0
        assert score.status == "ok"
