"""Pairwise LLM-judge protocol — the quality instrument for open-ended authoring.

This is a seam PARALLEL to :mod:`claude_ablation_lab.grade`, not a ``Grader``: a
judge compares TWO stored contestant outputs (A/B/tie) and calls an external CLI,
where a grader scores one output purely. The design contract lives in
``docs/plans/active/2026-07-06_llm-judge-phase.md``:

- **Pairwise, never absolute** — the judge picks A / B / tie, nothing else.
- **Blinded** — the judge prompt carries the assignment + the two outputs only;
  no model names, efforts, or cost hints.
- **Position-debiased** — every pair is judged in both orders; order-flip
  disagreement records a *tie* (and is reported as judge noise, never absorbed).
- **Version-keyed** — ``version`` fingerprints the call-time surface (prompt
  template + parser + pinned judge model/effort); a bump re-judges stored outputs
  for free. The decision rule (:data:`DECISION_RULE_VERSION`) is analysis-time
  and deliberately NOT part of ``version``: re-analysing with a new rule must not
  force re-judging.

This module is pure (no subprocess): transports live in
:mod:`claude_ablation_lab.judges`, orchestration in
:mod:`claude_ablation_lab.judge_orchestrate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "PROMPT_TEMPLATE_VERSION",
    "DECISION_RULE_VERSION",
    "JudgeStatus",
    "RawVerdict",
    "PairVerdict",
    "JudgeCall",
    "Judge",
    "build_judge_prompt",
    "canonical_verdict",
    "debias",
    "pair_score",
]

#: Fingerprint of the blinded prompt template below. Bump on ANY wording change —
#: it is part of every judge's ``version``, so a bump re-judges stored outputs.
PROMPT_TEMPLATE_VERSION = "pj-v1"
#: Fingerprint of the analysis-time aggregation rule (debias + cross-judge mean).
#: Stamped on analysis output, never on judge rows (see module docstring).
DECISION_RULE_VERSION = "dr-v1"

JudgeStatus = Literal["ok", "unparsed", "error", "timeout", "missing"]
#: The presentation frame: what the judge literally answered about "Response A/B".
RawVerdict = Literal["A", "B", "tie"]
#: The canonical frame: which CONFIG (``config_a``/``config_b``) was preferred.
PairVerdict = Literal["a", "b", "tie"]


@dataclass(frozen=True, slots=True)
class JudgeCall:
    """The outcome of one judge CLI invocation (one order of one pair).

    Parameters
    ----------
    status:
        ``ok`` (a parsed verdict), ``unparsed`` (the CLI answered but no
        schema-matching JSON was found — reported, never treated as clean),
        ``error`` / ``timeout`` / ``missing`` (transport failures).
    verdict:
        The raw A/B/tie answer; ``None`` unless ``status == "ok"``.
    reason:
        The judge's one-sentence justification (spot-check fodder).
    raw_text:
        The full CLI output — persisted as the judge transcript by the
        orchestrator, never stored on the ledger row itself.
    """

    status: JudgeStatus
    verdict: RawVerdict | None = None
    reason: str = ""
    latency_s: float = 0.0
    output_bytes: int = 0
    raw_text: str = ""


@runtime_checkable
class Judge(Protocol):
    """An external pairwise judge (cross-vendor CLI transport).

    ``judge_id`` names the judge family (``codex`` / ``gemini``); ``version`` is
    the call-time fingerprint that keys judge ledger rows (template + parser +
    pinned model + effort). Both are read-only properties so frozen dataclasses
    satisfy the protocol.
    """

    @property
    def judge_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    def judge(self, prompt: str, *, timeout_s: float) -> JudgeCall: ...


def build_judge_prompt(*, assignment: str, first: str, second: str) -> str:
    """The blinded pairwise prompt: assignment + two responses + rubric.

    ``first``/``second`` are the two outputs in PRESENTATION order (the caller
    decides which config is shown as "Response A"). Nothing in the prompt hints
    at which model, effort tier, or cost produced either response, and an
    explicit anti-length clause counters verbosity bias (the single most likely
    way this instrument lies — the bias points toward expensive configs).
    """
    return (
        "You are judging two candidate responses to the same writing assignment.\n"
        "Judge which response is the better piece of work, using this rubric:\n"
        "- Technical correctness: are the claims and mechanics right?\n"
        "- Coverage: does it deliver everything the assignment asked for?\n"
        "- Convention adherence: does it follow the voice, structure, and markup\n"
        "  conventions demonstrated in the assignment's reference material?\n"
        "- Craft: is it clear, well-organized writing a demanding editor would accept?\n"
        "Do NOT reward length. A shorter response that covers the material equally\n"
        "well must not lose for being shorter; padding, repetition, and filler are\n"
        "defects, not effort.\n"
        "If the responses are equally good overall, answer tie.\n\n"
        "## The assignment\n\n"
        f"{assignment}\n\n"
        "## Response A\n\n"
        f"{first}\n\n"
        "## Response B\n\n"
        f"{second}\n\n"
        "## Your verdict\n\n"
        "Output a SINGLE JSON object and nothing else — no prose, no markdown\n"
        'fences: {"winner": "A" | "B" | "tie", "reason": "<one sentence>"}\n'
    )


def canonical_verdict(raw: RawVerdict, order: Literal["ab", "ba"]) -> PairVerdict:
    """Map a presentation-frame verdict to the canonical config frame.

    ``order == "ab"`` means ``config_a`` was shown as "Response A"; ``"ba"`` means
    the pair was swapped, so the judge's "A" is ``config_b``. Getting this mapping
    wrong would silently invert half of all verdicts — it is unit-tested for every
    combination.
    """
    if raw == "tie":
        return "tie"
    if order == "ab":
        return "a" if raw == "A" else "b"
    return "b" if raw == "A" else "a"


def debias(first: PairVerdict | None, second: PairVerdict | None) -> PairVerdict | None:
    """One judge's order-debiased verdict from its two canonical order verdicts.

    Both orders agree → that verdict. Both answered but disagree → ``tie`` (the
    design rule: order-flip disagreement is judge noise, counted by the caller and
    reported, never absorbed). Either order missing (a non-``ok`` call) → ``None``
    — informative missingness, excluded rather than guessed.
    """
    if first is None or second is None:
        return None
    if first == second:
        return first
    return "tie"


def pair_score(verdicts: Sequence[PairVerdict | None]) -> float | None:
    """Cross-judge score for one (prompt, epoch) pair: mean of ±1/0 over judges.

    Maps ``a`` → +1, ``tie`` → 0, ``b`` → −1 and averages the judges that
    produced a debiased verdict. Full cross-judge disagreement → 0 (a tie for the
    headline), tie+win → ±0.5 (the half-signal is kept, not discarded); the exact
    sign-flip test downstream excludes exact zeros, so this plugs straight in.
    ``None`` when no judge produced a verdict (the pair is missing, reported).
    """
    scored = [v for v in verdicts if v is not None]
    if not scored:
        return None
    numeric = {"a": 1.0, "tie": 0.0, "b": -1.0}
    return sum(numeric[v] for v in scored) / len(scored)
