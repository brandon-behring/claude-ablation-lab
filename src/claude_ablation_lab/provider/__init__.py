"""Inference-provider seam: one bare model call, one :class:`Completion`.

This replaces the ``claude -p`` subprocess substrate. The distinction is the whole point
of the rebuild: a provider is an *inference endpoint*, not an agent harness. No system
prompt, no tool loop, no multi-turn recovery, no memory — because every one of those
sits between the knob being set and the number being recorded, and the previous substrate
lost the independent variable in exactly that gap (``--effort`` was accepted and silently
discarded on models with no effort parameter).

Two invariants everything here exists to enforce:

1. **An un-honorable knob raises.** If a provider is asked for an effort tier or token
   budget it cannot apply, it raises :class:`ValueError` rather than falling back to a
   default. Silent clamping is the failure mode being designed out; a loud error is the
   only acceptable substitute.
2. **``None`` means "not measured", never "measured zero".** ``reasoning_tokens=None``
   says the vendor does not report a reasoning/thinking split (every Anthropic model);
   ``reasoning_tokens=0`` says it reported one and it was zero. Collapsing these would
   make "we could not look" indistinguishable from "we looked and saw nothing" — the
   same rule the ledger already applies to ``tool_calls``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from claude_ablation_lab.runner import RunStatus

__all__ = [
    "EFFORT_TIERS",
    "Completion",
    "Effort",
    "ModelCaps",
    "Provider",
]

#: Canonical ordering of the ordinal effort tiers. Ordering matters: the Control
#: pre-flight compares each tier against the one below it, so "adjacent" must be
#: well defined. Providers expose which subset they actually honor via
#: :attr:`ModelCaps.effort_tiers`; membership here implies nothing about support.
EFFORT_TIERS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True, slots=True)
class Effort:
    """A request for how much inference compute to spend.

    Vendors expose fundamentally different levers, and conflating them is how the
    previous harness ended up plotting a label it had not applied. Two are modelled
    here and they are *not* interchangeable:

    Parameters
    ----------
    tier:
        An ordinal label from :data:`EFFORT_TIERS` (Anthropic ``output_config.effort``).
        A *ceiling on* rather than a *dial for* spend — the model still allocates
        adaptively beneath it. ``None`` means "do not send an effort tier".
    token_budget:
        A hard cap on total generated tokens, thinking included (local runtimes'
        ``num_predict``). Genuinely enforced: generation stops when it is reached,
        even mid-thought. ``None`` means "no explicit budget".

    Raises
    ------
    ValueError
        If ``tier`` is not in :data:`EFFORT_TIERS`, or ``token_budget`` is not positive.
    """

    tier: str | None = None
    token_budget: int | None = None

    def __post_init__(self) -> None:
        if self.tier is not None and self.tier not in EFFORT_TIERS:
            raise ValueError(
                f"unknown effort tier {self.tier!r}; expected one of {list(EFFORT_TIERS)}"
            )
        if self.token_budget is not None and self.token_budget <= 0:
            raise ValueError(f"token_budget must be positive, got {self.token_budget!r}")

    @property
    def label(self) -> str:
        """A stable identifier for this setting, for ledger keys and plot facets."""
        if self.tier is not None and self.token_budget is not None:
            return f"{self.tier}@{self.token_budget}"
        if self.tier is not None:
            return self.tier
        if self.token_budget is not None:
            return f"budget={self.token_budget}"
        return "default"


@dataclass(frozen=True, slots=True)
class ModelCaps:
    """What a provider will actually honor for one model.

    This is queried from the vendor, not hardcoded. It is the direct replacement for
    ``grid._EFFORT_CAPABILITY`` — which encoded capability as one bool per model family
    and so could not express that ``claude-opus-4-5`` has ``low|medium|high`` but no
    ``xhigh``/``max``, or that ``claude-opus-4-6`` has ``max`` but no ``xhigh``.

    Parameters
    ----------
    model:
        The model identifier as the provider resolves it.
    effort_tiers:
        Tiers this model honors. **Empty means the model has no effort lever at all**
        (e.g. ``claude-haiku-4-5``), in which case every tier would collapse to one
        config and asking for one is an error rather than a redundant paid cell.
    supports_token_budget:
        Whether a hard generation cap can be enforced.
    reports_reasoning_tokens:
        Whether usage separates thinking from answer tokens. ``False`` for every
        Anthropic model — thinking is billed inside ``output_tokens`` and the raw
        trace is never returned — which is why curves for such models must be plotted
        against total output tokens and labelled as such, never pooled with a true
        reasoning-token axis.
    max_output_tokens, context_window:
        Reported limits; ``None`` when the provider does not publish them.
    """

    model: str
    effort_tiers: frozenset[str]
    supports_token_budget: bool
    reports_reasoning_tokens: bool
    max_output_tokens: int | None = None
    context_window: int | None = None

    @property
    def has_effort_lever(self) -> bool:
        """True iff at least one ordinal effort tier is honored."""
        return bool(self.effort_tiers)

    def validate(self, effort: Effort) -> None:
        """Raise if *effort* asks for something this model will not apply.

        Raises
        ------
        ValueError
            If an effort tier is requested on a model with no effort lever, if the
            requested tier is unsupported, or if a token budget is requested where
            none can be enforced.
        """
        if effort.tier is not None:
            if not self.effort_tiers:
                raise ValueError(
                    f"{self.model!r} has no effort parameter, so effort={effort.tier!r} "
                    "would be silently ignored; omit the tier instead of mislabelling "
                    "a default-config cell"
                )
            if effort.tier not in self.effort_tiers:
                raise ValueError(
                    f"{self.model!r} does not support effort={effort.tier!r}; "
                    f"supported: {sorted(self.effort_tiers)}"
                )
        if effort.token_budget is not None and not self.supports_token_budget:
            raise ValueError(
                f"{self.model!r} cannot enforce a token budget "
                f"(requested {effort.token_budget})"
            )


@dataclass(frozen=True, slots=True)
class Completion:
    """The outcome of one bare inference call.

    Parameters
    ----------
    text:
        The answer text, with any reasoning trace excluded (see ``reasoning_text``).
    status:
        Reuses the harness-wide taxonomy so infrastructure failure is never counted as
        model quality. Anything other than ``ok`` short-circuits grading.
    input_tokens, output_tokens:
        Prompt and total generated tokens. ``output_tokens`` **includes** reasoning
        tokens wherever the vendor bills them together (all Anthropic models).
    reasoning_tokens:
        Tokens spent thinking, when the vendor separates them. ``None`` means the
        vendor does not report the split — not that it was zero.
    reasoning_text:
        The raw reasoning trace when returned (local open-weight models). ``None``
        for every commercial API. Retained because counting tokens in it is the only
        way to verify ``reasoning_tokens`` against the vendor's own accounting.
    cost_usd:
        API-equivalent cost. ``None`` for local models, where the honest cost is
        wall-clock and tokens, not dollars — ``0.0`` would imply a measured zero price.
    latency_s:
        Wall-clock for the call.
    stop_reason:
        Why generation ended. Load-bearing for local budget sweeps: a budget-truncated
        call stops mid-thought with an empty answer, which must be distinguished from
        a genuine wrong answer rather than scored as one.
    model_resolved:
        The concrete model the provider says served the request, when reported.
    effort_applied:
        The effort the provider reports having applied, when it reports one at all.
        ``None`` is the common case and is precisely why the Control pre-flight exists:
        with no provider-side confirmation, application can only be established
        behaviourally, from realized token distributions.
    raw:
        The undecoded payload, for post-hoc forensics.
    """

    text: str
    status: RunStatus
    latency_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    reasoning_text: str | None = None
    cost_usd: float | None = None
    stop_reason: str | None = None
    model_resolved: str | None = None
    effort_applied: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def answer_tokens(self) -> int | None:
        """Generated tokens excluding reasoning, or ``None`` if the split is unknown."""
        if self.output_tokens is None or self.reasoning_tokens is None:
            return None
        return max(0, self.output_tokens - self.reasoning_tokens)

    @property
    def truncated(self) -> bool:
        """True iff generation stopped by hitting a length/budget cap."""
        return self.stop_reason in {"length", "max_tokens"}


@runtime_checkable
class Provider(Protocol):
    """A bare inference endpoint.

    Deliberately *not* satisfied by ``codex``, ``agy``, ``claude -p``, or Claude Code
    subagents: each wraps the model in an agent harness that confounds the measurement
    and reports no per-call token accounting.
    """

    @property
    def name(self) -> str:
        """Short provider identifier, used as a ledger field and a plot facet."""
        ...

    def capabilities(self, model: str) -> ModelCaps:
        """Report what *model* will actually honor, queried from the provider."""
        ...

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        effort: Effort,
        max_tokens: int,
        timeout_s: float,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        """Run one prompt and return the completion.

        Implementations must validate *effort* against :meth:`capabilities` and raise
        rather than silently degrading, and must never raise for a *runtime* failure —
        network, throttle, timeout — which is reported as a non-``ok``
        :attr:`Completion.status` so a paid attempt is still recorded.

        ``json_schema`` constrains the answer shape at generation time (decision 15).
        A provider that cannot enforce it must raise :class:`ValueError` rather than
        drop it silently — an unconstrained answer under a schema-assuming grader
        would fail to parse at a rate correlated with effort tier.
        """
        ...
