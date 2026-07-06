"""Analysis over the judge ledger: per-contrast preference summaries.

The unit of evidence is the PROMPT (task), per the phase design: epochs of one
prompt measure run variance, not independent evidence about the task, so the
sign-flip test runs over per-prompt scores (mean over epochs of the cross-judge
score). Aggregation is :data:`~claude_ablation_lab.judge.DECISION_RULE_VERSION`
(``dr-v1``): per judge, order-flip disagreement → tie; across judges, ±1/0
averaged (tie+win keeps its ±0.5 half-signal). Judge cost never joins contestant
cost — the ``*_multiple`` columns are contestant-ledger joins.

Multiple comparisons: the PRIMARY contrast is predeclared (default
``claude-fable-5/high`` vs baseline — the config the user would actually switch
to); the other contrasts are Holm-corrected and labeled exploratory, matching
``compare``'s exploratory-caveat convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from claude_ablation_lab.analyze import ALPHA, MIN_PAIRS_FOR_REAL, _sign_flip_p
from claude_ablation_lab.judge import DECISION_RULE_VERSION, PairVerdict, debias
from claude_ablation_lab.judge_ledger import REAL_PAIR, latest_rows_by_judge_key
from claude_ablation_lab.ledger import ok_row_by_run_key

if TYPE_CHECKING:
    from collections.abc import Sequence

    from claude_ablation_lab.judge_ledger import JudgeRow
    from claude_ablation_lab.ledger import LedgerRow

__all__ = ["JudgePairSummary", "judge_report", "DEFAULT_PRIMARY"]

#: The predeclared primary contrast candidate (vs the measured-cheapest baseline).
DEFAULT_PRIMARY = "claude-fable-5/high"


@dataclass(frozen=True, slots=True)
class JudgePairSummary:
    """One candidate-vs-baseline preference verdict, fully caveated.

    ``wins``/``losses``/``ties`` are per-PROMPT signs of the per-prompt score
    (the sign-flip test's own input). ``p_value`` is the exact sign-flip test
    over per-prompt scores; ``real`` requires ``p <= ALPHA`` with at least
    ``MIN_PAIRS_FOR_REAL`` nonzero prompts (else the note says underpowered).
    ``p_adjusted`` is the Holm-corrected p for non-primary (exploratory)
    contrasts, ``None`` for the primary. ``mean_length_ratio`` (candidate chars /
    baseline chars) is the verbosity tripwire printed on every verdict line.
    """

    config: str
    baseline: str
    n_prompts: int
    n_scored: int
    wins: int
    losses: int
    ties: int
    mean_score: float | None
    p_value: float | None
    n_nonzero: int
    real: bool
    primary: bool
    p_adjusted: float | None
    order_disagree_rate: dict[str, float]
    cross_judge_disagree_rate: float | None
    missing_rate: float
    mean_length_ratio: float | None
    cost_multiple: float | None
    latency_multiple: float | None
    token_multiple: float | None
    decision_rule: str = DECISION_RULE_VERSION
    note: str = ""


def judge_report(
    judge_rows: Sequence[JudgeRow],
    contestant_rows: Sequence[LedgerRow],
    *,
    baseline: str,
    primary: str = DEFAULT_PRIMARY,
) -> list[JudgePairSummary]:
    """Summarize every candidate-vs-``baseline`` contrast in the judge ledger.

    Only REAL pairs (``control == "none"``) whose config pair includes
    ``baseline`` are summarized; latest row per judge key wins (retries
    supersede). Holm correction is applied across the non-primary contrasts.
    """
    latest = [
        r
        for r in latest_rows_by_judge_key(list(judge_rows)).values()
        if r.control == REAL_PAIR and baseline in (r.config_a, r.config_b)
    ]
    candidates = sorted({r.config_a if r.config_b == baseline else r.config_b for r in latest})
    summaries = [
        _summarize(candidate, latest, contestant_rows, baseline=baseline, primary=primary)
        for candidate in candidates
    ]
    return _holm_adjust(summaries)


def _summarize(
    candidate: str,
    rows: list[JudgeRow],
    contestant_rows: Sequence[LedgerRow],
    *,
    baseline: str,
    primary: str,
) -> JudgePairSummary:
    config_a, config_b = sorted((candidate, baseline))
    pair_rows = [r for r in rows if (r.config_a, r.config_b) == (config_a, config_b)]
    candidate_side: PairVerdict = "a" if config_a == candidate else "b"
    baseline_side: PairVerdict = "b" if candidate_side == "a" else "a"

    # (task, epoch, judge) -> canonical verdict per order -> debiased verdict.
    per_judge: dict[tuple[str, int, str], dict[str, PairVerdict | None]] = {}
    length_ratio: dict[tuple[str, int], float] = {}
    for r in pair_rows:
        key = (r.task_id, r.epoch, r.judge_id)
        verdict = r.verdict if r.status == "ok" else None
        per_judge.setdefault(key, {})[r.order] = verdict  # type: ignore[assignment]
        chars = {config_a: r.output_chars_a, config_b: r.output_chars_b}
        if chars[baseline] > 0:
            length_ratio[(r.task_id, r.epoch)] = chars[candidate] / chars[baseline]

    judge_ids = sorted({j for (_, _, j) in per_judge})
    order_flips: dict[str, list[bool]] = {j: [] for j in judge_ids}
    debiased: dict[tuple[str, int], dict[str, PairVerdict | None]] = {}
    for (task_id, epoch, judge_id), orders in per_judge.items():
        v_ab, v_ba = orders.get("ab"), orders.get("ba")
        if v_ab is not None and v_ba is not None:
            order_flips[judge_id].append(v_ab != v_ba)
        debiased.setdefault((task_id, epoch), {})[judge_id] = debias(v_ab, v_ba)

    # Cross-judge score per (prompt, epoch), in the CANDIDATE frame (+1 = candidate).
    numeric = {candidate_side: 1.0, "tie": 0.0, baseline_side: -1.0}
    epoch_scores: dict[tuple[str, int], float | None] = {}
    cross_disagreements: list[bool] = []
    for cell, by_judge in debiased.items():
        verdicts = [v for v in by_judge.values() if v is not None]
        epoch_scores[cell] = sum(numeric[v] for v in verdicts) / len(verdicts) if verdicts else None
        if len(verdicts) >= 2:
            cross_disagreements.append(len(set(verdicts)) > 1)

    # Per-prompt score = mean over scored epochs; the sign-flip test's input.
    by_prompt: dict[str, list[float]] = {}
    for (task_id, _epoch), score in epoch_scores.items():
        if score is not None:
            by_prompt.setdefault(task_id, []).append(score)
    all_prompts = sorted({task_id for task_id, _ in epoch_scores})
    prompt_scores = {t: sum(v) / len(v) for t, v in by_prompt.items()}

    diffs = np.array(list(prompt_scores.values()), dtype=float)
    p_value, n_nonzero = _sign_flip_p(diffs) if diffs.size else (None, 0)
    real = p_value is not None and p_value <= ALPHA and n_nonzero >= MIN_PAIRS_FOR_REAL

    notes: list[str] = []
    if diffs.size and n_nonzero < MIN_PAIRS_FOR_REAL:
        notes.append(
            f"underpowered: {n_nonzero} nonzero prompts < {MIN_PAIRS_FOR_REAL} "
            "needed for p<=0.05"
        )
    if not diffs.size:
        notes.append("no scored prompts")
    missing = sum(1 for s in epoch_scores.values() if s is None)
    if missing:
        notes.append(f"{missing} judged (prompt,epoch) cells have no verdict")

    ratios = list(length_ratio.values())
    multiples = _cost_multiples(candidate, baseline, contestant_rows, set(all_prompts))
    return JudgePairSummary(
        config=candidate,
        baseline=baseline,
        n_prompts=len(all_prompts),
        n_scored=len(prompt_scores),
        wins=sum(1 for s in prompt_scores.values() if s > 0),
        losses=sum(1 for s in prompt_scores.values() if s < 0),
        ties=sum(1 for s in prompt_scores.values() if s == 0),
        mean_score=float(diffs.mean()) if diffs.size else None,
        p_value=p_value,
        n_nonzero=n_nonzero,
        real=real,
        primary=candidate == primary,
        p_adjusted=None,
        order_disagree_rate={
            j: (sum(flips) / len(flips) if flips else 0.0) for j, flips in order_flips.items()
        },
        cross_judge_disagree_rate=(
            sum(cross_disagreements) / len(cross_disagreements) if cross_disagreements else None
        ),
        missing_rate=missing / len(epoch_scores) if epoch_scores else 1.0,
        mean_length_ratio=sum(ratios) / len(ratios) if ratios else None,
        cost_multiple=multiples["cost_multiple"],
        latency_multiple=multiples["latency_multiple"],
        token_multiple=multiples["token_multiple"],
        note="; ".join(notes),
    )


def _cost_multiples(
    candidate: str,
    baseline: str,
    contestant_rows: Sequence[LedgerRow],
    task_ids: set[str],
) -> dict[str, float | None]:
    """Candidate/baseline ratios of mean cost, latency, and output tokens.

    Joined from the CONTESTANT ledger over the judged tasks — judge-side latency
    and bytes never enter these numbers. A ratio is ``None`` when either side
    lacks the measurement (tokens on pre-token rows): unknown is never 1.0.
    """
    latest = [r for r in ok_row_by_run_key(list(contestant_rows)).values() if r.task_id in task_ids]

    def mean_of(config: str, attr: str) -> float | None:
        values = [getattr(r, attr) for r in latest if f"{r.model}/{r.effort}" == config]
        if not values or any(v is None for v in values):
            return None
        return float(sum(values) / len(values))

    def ratio(attr: str) -> float | None:
        top, bottom = mean_of(candidate, attr), mean_of(baseline, attr)
        if top is None or bottom is None or bottom == 0:
            return None
        return top / bottom

    return {
        "cost_multiple": ratio("cost_usd"),
        "latency_multiple": ratio("latency_s"),
        "token_multiple": ratio("output_tokens"),
    }


def _holm_adjust(summaries: list[JudgePairSummary]) -> list[JudgePairSummary]:
    """Holm–Bonferroni over the NON-primary contrasts (the primary is predeclared)."""
    from dataclasses import replace

    exploratory = [
        (i, s, s.p_value)
        for i, s in enumerate(summaries)
        if not s.primary and s.p_value is not None
    ]
    if not exploratory:
        return summaries
    ordered = sorted(exploratory, key=lambda item: item[2])
    m = len(ordered)
    out = list(summaries)
    running_max = 0.0
    for rank, (idx, _summary, p_value) in enumerate(ordered):
        adjusted = min(1.0, (m - rank) * p_value)
        running_max = max(running_max, adjusted)  # enforce monotonicity
        out[idx] = replace(
            out[idx],
            p_adjusted=running_max,
            real=out[idx].real and running_max <= ALPHA,
            note=(out[idx].note + "; " if out[idx].note else "") + "exploratory (Holm-corrected)",
        )
    return out
