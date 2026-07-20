"""Causal item generator: DAG structures with truth known by construction.

Items are generated, never mined (decision 2): the author's repos are largely
Claude-authored, so mining them would grade candidates against Claude's own prior
output. Here the gold is the *graph* — the grader checks a proposed adjustment set
against the backdoor criterion in the very DAG the item was built from, so every valid
answer is admitted and every invalid one rejected regardless of phrasing.

The discriminating property is deliberate (decision 12): **the plausible-but-wrong
answer must fail often.** Each structure is chosen so that "adjust for every observed
covariate" — the reflex answer — is *invalid* on the harder strata (M-bias, butterfly,
front-door), while remaining valid on the easy stratum (plain confounding). If the
reflex were always right, every config would score 1.0 and this family would be ``t8``
again.

Difficulty is dialed by graph structure, not arithmetic:

=========  ===========================  ====================================
stratum    structures                   why it separates
=========  ===========================  ====================================
easy       confounder, mediation        textbook; reflex answer works or the
                                        empty set is obviously right
medium     collider, M-bias             the reflex answer ("adjust Z") is
                                        *wrong*; requires seeing the collider
hard       front-door, butterfly        no valid observable set exists /
                                        validity requires a non-obvious pair
=========  ===========================  ====================================

Determinism: everything derives from an integer seed via ``random.Random`` — no global
state, no wall clock — so an item set is exactly reproducible from ``(seed, counts)``
and the frozen ladder can be re-rendered byte-identically.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from claude_ablation_lab.causal.backdoor import Dag

__all__ = ["CausalItem", "ANSWER_SCHEMA", "generate_items", "render_prompt"]

#: The --json-schema contract (decision 15). ``adjustment_set`` uses the story-facing
#: variable names; ``identified`` covers the front-door stratum, where the correct
#: answer is "no valid backdoor adjustment exists among the observed variables".
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "identified": {
            "type": "boolean",
            "description": (
                "Whether the causal effect is identifiable by adjusting for a set of "
                "OBSERVED variables (backdoor criterion)."
            ),
        },
        "adjustment_set": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "A valid backdoor adjustment set of observed variable names; empty if "
                "no adjustment is needed; ignored when identified is false."
            ),
        },
    },
    "required": ["identified", "adjustment_set"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class CausalItem:
    """One identification item, carrying its own grading truth.

    Parameters
    ----------
    item_id:
        Stable id — the pairing key across configurations.
    cluster_id:
        The randomization unit: structure × story seed. Items sharing it are not
        independent draws, and clustered standard errors group on it.
    difficulty_stratum:
        ``easy`` / ``medium`` / ``hard`` from the structure table above.
    structure:
        The template name (``confounder``, ``m_bias``, ...), kept for analysis.
    dag:
        The generated graph over *story-facing* variable names — the grading truth.
    treatment, outcome:
        The causal query, in story-facing names.
    observed:
        Variables the story presents as measured. A proposed set containing an
        unobserved variable is *invalid regardless of graph validity* — the story
        said it cannot be adjusted for.
    backdoor_identifiable:
        Whether any valid adjustment set exists within ``observed`` — the gold for
        the ``identified`` field.
    prompt:
        The rendered question (story + variable list + instructions).
    """

    item_id: str
    cluster_id: str
    difficulty_stratum: str
    structure: str
    dag: Dag
    treatment: str
    outcome: str
    observed: frozenset[str]
    backdoor_identifiable: bool
    prompt: str


# --------------------------------------------------------------------------- stories
#
# Variable vocabularies. Names are deliberately domain-flavoured but structurally
# neutral: nothing in a name may leak the graph role (calling a node "Confounder"
# would grade prior knowledge of the template, not reading of the described DGP).

_DOMAINS: tuple[dict[str, Any], ...] = (
    {
        "setting": "an observational study of a workplace wellness program",
        "treatment": "ProgramParticipation",
        "outcome": "SickDaysTaken",
        "pool": (
            "BaselineHealth",
            "JobSeniority",
            "TeamCulture",
            "CommuteTime",
            "GymAccess",
            "ManagerSupport",
            "PriorBurnout",
            "ShiftType",
        ),
    },
    {
        "setting": "a study of an online tutoring platform and exam performance",
        "treatment": "TutoringHours",
        "outcome": "ExamScore",
        "pool": (
            "PriorGPA",
            "ParentalIncome",
            "SchoolDistrict",
            "StudyMotivation",
            "InternetQuality",
            "ClassSize",
            "SleepQuality",
            "PeerStudyGroup",
        ),
    },
    {
        "setting": "an observational pricing experiment on a subscription service",
        "treatment": "DiscountOffered",
        "outcome": "TwelveMonthRetention",
        "pool": (
            "PastEngagement",
            "AcquisitionChannel",
            "PlanTier",
            "SupportTickets",
            "RegionIncome",
            "DeviceType",
            "ReferralStatus",
            "EmailOpenRate",
        ),
    },
)


def _edge_lines(dag: Dag, rng: random.Random) -> list[str]:
    """Human-readable causal statements, order-shuffled so edge order carries no hint."""
    lines = [
        f"- {parent} directly affects {child}."
        for child in sorted(dag.parents)
        for parent in dag.parents[child]
    ]
    rng.shuffle(lines)
    return lines


# ------------------------------------------------------------------------ structures
#
# Each builder returns (dag, observed, extra_note). Node roles are mapped onto
# story-variable names drawn by the caller; U-nodes may be marked unobserved.


def _build_confounder(t: str, y: str, names: list[str]) -> tuple[Dag, set[str], str]:
    z = names[0]
    dag = Dag({t: (z,), y: (z, t)})
    return dag, {t, y, z}, ""


def _build_mediation(t: str, y: str, names: list[str]) -> tuple[Dag, set[str], str]:
    m = names[0]
    dag = Dag({m: (t,), y: (m,)})
    return dag, {t, y, m}, ""


def _build_collider(t: str, y: str, names: list[str]) -> tuple[Dag, set[str], str]:
    c = names[0]
    dag = Dag({c: (t, y), y: (t,)})
    return dag, {t, y, c}, ""


def _build_m_bias(t: str, y: str, names: list[str]) -> tuple[Dag, set[str], str]:
    z, u1, u2 = names[0], names[1], names[2]
    dag = Dag({z: (u1, u2), t: (u1,), y: (u2, t)})
    # The trap: Z is observed and correlated with both T and Y; U1/U2 are not measured.
    note = f"{u1} and {u2} were not measured in this study."
    return dag, {t, y, z}, note


def _build_front_door(t: str, y: str, names: list[str]) -> tuple[Dag, set[str], str]:
    m, u = names[0], names[1]
    dag = Dag({t: (u,), m: (t,), y: (m, u)})
    note = f"{u} was not measured in this study."
    return dag, {t, y, m}, note


def _build_butterfly(t: str, y: str, names: list[str]) -> tuple[Dag, set[str], str]:
    z, u1, u2 = names[0], names[1], names[2]
    dag = Dag({z: (u1, u2), t: (u1, z), y: (u2, z, t)})
    return dag, {t, y, z, u1, u2}, ""


_STRUCTURES: dict[str, tuple[str, Any, int]] = {
    # name -> (stratum, builder, names_needed)
    "confounder": ("easy", _build_confounder, 1),
    "mediation": ("easy", _build_mediation, 1),
    "collider": ("medium", _build_collider, 1),
    "m_bias": ("medium", _build_m_bias, 3),
    "front_door": ("hard", _build_front_door, 2),
    "butterfly": ("hard", _build_butterfly, 3),
}


def _has_valid_observed_set(
    dag: Dag, *, treatment: str, outcome: str, observed: frozenset[str]
) -> bool:
    """Brute-force gold for ``identified``: does ANY observed subset satisfy backdoor?

    Exponential in |observed candidates|, which is fine at generator scale (≤ ~6) and
    has the virtue of being obviously correct — this is the gold for a gold-producing
    module, so transparency beats cleverness.
    """
    from itertools import combinations

    from claude_ablation_lab.causal.backdoor import is_valid_adjustment_set

    candidates = sorted(observed - {treatment, outcome})
    for size in range(len(candidates) + 1):
        for combo in combinations(candidates, size):
            if is_valid_adjustment_set(
                dag, treatment=treatment, outcome=outcome, adjustment=frozenset(combo)
            ):
                return True
    return False


def render_prompt(
    item_dag: Dag,
    *,
    setting: str,
    treatment: str,
    outcome: str,
    observed: frozenset[str],
    note: str,
    rng: random.Random,
) -> str:
    """Render the question text. Distractor-free, single-question, schema-shaped."""
    unobserved = sorted(item_dag.nodes - observed)
    lines = [
        f"You are analysing {setting}.",
        "",
        "The data-generating process is known to be exactly the following "
        "(a directed edge means direct causation; there are no other causal "
        "relationships and no other variables):",
        "",
        *_edge_lines(item_dag, rng),
        "",
        f"Observed (measured) variables: {', '.join(sorted(observed))}.",
    ]
    if unobserved:
        lines.append(f"Unmeasured variables: {', '.join(unobserved)}.")
    if note:
        lines.append(note)
    lines += [
        "",
        f"Question: to estimate the causal effect of {treatment} on {outcome} by "
        "covariate adjustment, is the effect identifiable by adjusting for a set of "
        "observed variables (backdoor criterion)? If yes, give ONE valid adjustment "
        "set of observed variables (the empty set is a valid answer if no adjustment "
        "is needed). If no observed set is valid, say so.",
    ]
    return "\n".join(lines)


def generate_items(
    *, seed: int, per_structure: int = 4, structures: tuple[str, ...] | None = None
) -> list[CausalItem]:
    """Generate a deterministic item set.

    Parameters
    ----------
    seed:
        Master seed; the full item set is a pure function of ``(seed, per_structure,
        structures)``.
    per_structure:
        Items per structure template. Each gets a distinct story draw; items from the
        same template+domain share a ``cluster_id``.
    structures:
        Subset of template names to generate (default: all six).

    Raises
    ------
    ValueError
        For an unknown structure name.
    """
    chosen = structures if structures is not None else tuple(_STRUCTURES)
    unknown = [name for name in chosen if name not in _STRUCTURES]
    if unknown:
        raise ValueError(f"unknown structure(s) {unknown!r}; have {sorted(_STRUCTURES)}")

    rng = random.Random(seed)
    items: list[CausalItem] = []
    for structure in chosen:
        stratum, builder, needed = _STRUCTURES[structure]
        for index in range(per_structure):
            domain = _DOMAINS[rng.randrange(len(_DOMAINS))]
            treatment, outcome = domain["treatment"], domain["outcome"]
            names = rng.sample(list(domain["pool"]), needed)
            dag, observed_set, note = builder(treatment, outcome, names)
            observed = frozenset(observed_set)
            identifiable = _has_valid_observed_set(
                dag, treatment=treatment, outcome=outcome, observed=observed
            )
            prompt = render_prompt(
                dag,
                setting=domain["setting"],
                treatment=treatment,
                outcome=outcome,
                observed=observed,
                note=note,
                rng=rng,
            )
            items.append(
                CausalItem(
                    item_id=f"causal-{structure}-{seed}-{index:02d}",
                    cluster_id=f"{structure}:{domain['treatment']}",
                    difficulty_stratum=stratum,
                    structure=structure,
                    dag=dag,
                    treatment=treatment,
                    outcome=outcome,
                    observed=observed,
                    backdoor_identifiable=identifiable,
                    prompt=prompt,
                )
            )
    return items
