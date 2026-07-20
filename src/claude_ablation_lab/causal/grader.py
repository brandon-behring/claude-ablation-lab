"""Item-level grader for causal identification answers.

Consumes the ``--json-schema`` answer shape (:data:`~claude_ablation_lab.causal.dgp.
ANSWER_SCHEMA`) and grades by **checking**, not matching (decision 13): a proposed
adjustment set scores 1.0 iff it satisfies the backdoor criterion in the item's own DAG
*and* uses only observed variables. Every valid alternative is admitted; minimality is a
subscore, never the grade.
"""

from __future__ import annotations

from typing import Any

from claude_ablation_lab.causal.backdoor import (
    is_minimal_adjustment_set,
    is_valid_adjustment_set,
)
from claude_ablation_lab.causal.dgp import CausalItem
from claude_ablation_lab.grade import Score

__all__ = ["GRADER_VERSION", "grade_identification"]

GRADER_VERSION = "causal-backdoor-v1"


def grade_identification(answer: Any, item: CausalItem) -> Score:
    """Grade one structured answer against one item's generated truth.

    Parameters
    ----------
    answer:
        The decoded ``structured_output`` — expected shape
        ``{"identified": bool, "adjustment_set": [str, ...]}``. Anything else is
        ``unparseable``, never a crash: with ``--json-schema`` enforcing the shape at
        generation time this path should be rare, and rare is the point (a parse
        failure correlated with effort tier would bend the curve for non-quality
        reasons).
    item:
        The generated item carrying its DAG, observability, and identifiability gold.

    Notes
    -----
    Scoring is binary with diagnostic subscores:

    - ``identified`` wrong → 0.0 (saying "not identifiable" when a valid observed set
      exists is a miss; claiming identifiability where none exists likewise).
    - ``identified`` correctly ``False`` → 1.0; the ``adjustment_set`` field is
      ignored, as the schema documents.
    - ``identified`` correctly ``True`` → 1.0 iff the proposed set uses only observed
      variables and satisfies the backdoor criterion in the item's DAG.
    - ``minimality`` subscore: 1.0 if the valid set is also minimal — efficiency
      evidence, deliberately not part of the grade.
    """
    if not isinstance(answer, dict):
        return Score(0.0, status="unparseable", details={"reason": "answer not an object"})
    identified = answer.get("identified")
    proposed_raw = answer.get("adjustment_set")
    if not isinstance(identified, bool) or not isinstance(proposed_raw, list):
        return Score(0.0, status="unparseable", details={"reason": "schema shape mismatch"})
    if not all(isinstance(name, str) for name in proposed_raw):
        return Score(0.0, status="unparseable", details={"reason": "non-string set member"})

    if identified != item.backdoor_identifiable:
        return Score(
            0.0,
            details={
                "reason": "identifiability wrong",
                "said": identified,
                "truth": item.backdoor_identifiable,
            },
        )

    if not identified:
        # Correctly recognised that no observed set works (front-door stratum).
        return Score(1.0, subscores={"minimality": 1.0})

    proposed = frozenset(proposed_raw)
    unknown = sorted(proposed - item.dag.nodes)
    if unknown:
        return Score(0.0, details={"reason": "unknown variable(s)", "unknown": unknown})
    unobserved = sorted(proposed - item.observed)
    if unobserved:
        # Graph-valid or not, the story said these cannot be adjusted for.
        return Score(
            0.0, details={"reason": "uses unmeasured variable(s)", "unobserved": unobserved}
        )
    if item.treatment in proposed or item.outcome in proposed:
        return Score(0.0, details={"reason": "set contains treatment or outcome"})

    valid = is_valid_adjustment_set(
        item.dag, treatment=item.treatment, outcome=item.outcome, adjustment=proposed
    )
    if not valid:
        return Score(
            0.0, details={"reason": "fails backdoor criterion", "proposed": sorted(proposed)}
        )

    minimal = is_minimal_adjustment_set(
        item.dag, treatment=item.treatment, outcome=item.outcome, adjustment=proposed
    )
    return Score(1.0, subscores={"minimality": 1.0 if minimal else 0.0})
