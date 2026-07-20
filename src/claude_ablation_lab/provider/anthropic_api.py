"""Anthropic provider — the routing arm, via the API rather than the CLI.

This is the backend whose answers you actually act on day to day, and it is also the one
that structurally cannot support the article's x-axis: Claude bills thinking inside
``output_tokens`` and never returns the raw trace, so :attr:`Completion.reasoning_tokens`
is always ``None`` here. Curves for this backend are plotted against *total output
tokens* and must be labelled as such — never pooled onto a shared axis with a backend
that reports a true reasoning split.

The single most important behaviour in this module is :meth:`AnthropicProvider.capabilities`.
It asks the Models API which effort tiers a model actually honors, replacing the
hand-maintained ``grid._EFFORT_CAPABILITY`` table. That table encoded capability as one
bool per model *family*, which cannot express the real matrix — verified live on
2026-07-18:

===========================  ===  ======  ====  =====  ===
model                        low  medium  high  xhigh  max
===========================  ===  ======  ====  =====  ===
``claude-haiku-4-5``          no    no     no    no     no
``claude-opus-4-5``          yes   yes    yes    no     no
``claude-opus-4-6``          yes   yes    yes    no    yes
``claude-sonnet-4-6``        yes   yes    yes    no    yes
``claude-opus-4-7`` / ``4-8``  yes   yes    yes   yes    yes
``claude-sonnet-5``          yes   yes    yes   yes    yes
``claude-fable-5``           yes   yes    yes   yes    yes
===========================  ===  ======  ====  =====  ===

Haiku 4.5 has *no effort parameter at all*. The previous CLI substrate accepted
``--effort`` for it regardless and discarded the value, so every haiku x effort cell in
the historical ledger is one configuration wearing five labels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from claude_ablation_lab.provider import EFFORT_TIERS, Completion, Effort, ModelCaps

if TYPE_CHECKING:  # pragma: no cover - typing only
    import anthropic

__all__ = ["AnthropicProvider", "PRICING_USD_PER_MTOK", "PRICING_AS_OF"]

#: Static list price, USD per million tokens, as ``(input, output)``. Dated because it
#: goes stale: this is a *comparability axis*, not the constraint that binds on a flat
#: subscription, and the 2026-07-03 spend audit is explicit that ratios are meaningful
#: while absolute dollars are a proxy. Cache reads bill at ~0.1x input and cache writes
#: at 1.25x (5-minute TTL), applied separately below.
PRICING_AS_OF = "2026-06-24"
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _price_for(model: str) -> tuple[float, float] | None:
    """Look up list price for *model*, tolerating dated-snapshot suffixes."""
    if model in PRICING_USD_PER_MTOK:
        return PRICING_USD_PER_MTOK[model]
    for known, price in PRICING_USD_PER_MTOK.items():
        if model.startswith(known):
            return price
    return None


@dataclass
class AnthropicProvider:
    """Bare inference against the Anthropic Messages API.

    Parameters
    ----------
    client:
        An ``anthropic.Anthropic`` instance. Constructed lazily from the environment
        when omitted, so importing this module never requires credentials.
    """

    client: anthropic.Anthropic | None = None
    _caps_cache: dict[str, ModelCaps] = field(default_factory=dict, repr=False)
    #: model -> whether adaptive thinking is supported, populated by :meth:`capabilities`.
    _adaptive_cache: dict[str, bool] = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        return "anthropic"

    def _ensure_client(self) -> anthropic.Anthropic:
        if self.client is None:
            import anthropic as _anthropic

            self.client = _anthropic.Anthropic()
        return self.client

    # ------------------------------------------------------------------ capabilities

    def capabilities(self, model: str) -> ModelCaps:
        """Ask the Models API what *model* honors. Cached per instance.

        Raises
        ------
        RuntimeError
            If the model is unknown to the API. A typo must fail loudly rather than
            resolve to some default and produce a mislabelled cell.
        """
        if model in self._caps_cache:
            return self._caps_cache[model]

        client = self._ensure_client()
        try:
            info = client.models.retrieve(model)
        except Exception as exc:  # noqa: BLE001 - surfaced as a loud RuntimeError
            raise RuntimeError(f"cannot retrieve capabilities for model {model!r}: {exc}") from exc

        # model_dump() rather than attribute access: the SDK returns nested pydantic
        # models, and only the dump gives plain dicts the whole way down.
        dumped: dict[str, Any] = info.model_dump()
        raw: dict[str, Any] = dumped.get("capabilities") or {}
        effort_block: dict[str, Any] = raw.get("effort") or {}
        tiers = frozenset(
            tier
            for tier in EFFORT_TIERS
            if isinstance(effort_block.get(tier), dict)
            and bool(effort_block[tier].get("supported"))
        )

        thinking_types: dict[str, Any] = (raw.get("thinking") or {}).get("types") or {}
        supports_budget = bool((thinking_types.get("enabled") or {}).get("supported"))
        supports_adaptive = bool((thinking_types.get("adaptive") or {}).get("supported"))

        caps = ModelCaps(
            model=str(dumped.get("id") or model),
            effort_tiers=tiers,
            supports_token_budget=supports_budget,
            # Anthropic never separates thinking from answer tokens, on any model.
            reports_reasoning_tokens=False,
            max_output_tokens=dumped.get("max_tokens"),
            context_window=dumped.get("max_input_tokens"),
        )
        # Whether to send thinking={"type":"adaptive"} is a per-model fact from the API,
        # not something to infer from the effort ladder: on Opus 4.7/4.8 omitting the
        # parameter runs *without* thinking, so it must be set explicitly.
        self._adaptive_cache[model] = supports_adaptive
        self._caps_cache[model] = caps
        return caps

    # ---------------------------------------------------------------------- generate

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
        """Run one prompt at the requested effort.

        Effort is validated against :meth:`capabilities` first, so an unsupported tier
        raises before a request is paid for — the guarantee the CLI substrate lacked.

        Raises
        ------
        ValueError
            If *json_schema* is passed. Structured output via ``output_config.format``
            is not yet wired on this arm (unverifiable without API credits), and
            silently dropping the schema would produce free-text answers under a
            schema-assuming grader — the exact treatment-correlated parse bias
            decision 15 exists to prevent.
        """
        import anthropic as _anthropic

        if json_schema is not None:
            raise ValueError(
                "json_schema is not yet wired for the direct-API arm; use CliProvider "
                "or OllamaProvider, or wire output_config.format once credits exist"
            )
        caps = self.capabilities(model)
        caps.validate(effort)

        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        # Adaptive thinking where the API says it is supported. On Opus 4.7/4.8 omitting
        # the parameter runs *without* thinking, so it must be set explicitly; Fable 5
        # and Sonnet 5 accept the same explicit form. Older budget-style models take an
        # explicit budget only when one was requested.
        if self._adaptive_cache.get(model, False):
            kwargs["thinking"] = {"type": "adaptive"}
        elif effort.token_budget is not None and caps.supports_token_budget:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": effort.token_budget}

        if effort.tier is not None:
            kwargs["output_config"] = {"effort": effort.tier}

        started = time.monotonic()
        try:
            with client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except _anthropic.RateLimitError as exc:
            return Completion(
                text="",
                status="rate_limited",
                latency_s=time.monotonic() - started,
                raw={"error": str(exc)},
            )
        except _anthropic.APITimeoutError as exc:
            return Completion(
                text="",
                status="timeout",
                latency_s=time.monotonic() - started,
                raw={"error": str(exc)},
            )
        except _anthropic.APIStatusError as exc:
            return Completion(
                text="",
                status="infra_error",
                latency_s=time.monotonic() - started,
                raw={"error": str(exc), "status_code": exc.status_code},
            )
        except _anthropic.APIConnectionError as exc:
            return Completion(
                text="",
                status="infra_error",
                latency_s=time.monotonic() - started,
                raw={"error": str(exc)},
            )
        latency_s = time.monotonic() - started

        # Discriminate on ``.type`` rather than getattr: the content union includes
        # thinking and tool blocks that carry no ``.text``, and the literal check is what
        # narrows the type. Thinking blocks are empty here anyway (``display`` defaults to
        # ``omitted``), which is the same structural fact that makes reasoning_tokens None.
        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage

        return Completion(
            text=text.strip(),
            # A policy refusal is a model outcome, not an infrastructure failure, but it
            # is also not a gradeable answer — parse_fail keeps it out of quality
            # aggregation while leaving it visible in the ledger.
            status="parse_fail" if message.stop_reason == "refusal" else "ok",
            latency_s=latency_s,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            # Structural, not an oversight: thinking is billed inside output_tokens and
            # the raw trace is never returned on any Claude model.
            reasoning_tokens=None,
            reasoning_text=None,
            cost_usd=self._cost_usd(model, usage),
            stop_reason=message.stop_reason,
            model_resolved=getattr(message, "model", None),
            # The API does not echo an applied effort, which is why application can only
            # be established behaviourally by the Control pre-flight.
            effort_applied=None,
            raw={
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
                "pricing_as_of": PRICING_AS_OF,
            },
        )

    @staticmethod
    def _cost_usd(model: str, usage: Any) -> float | None:
        """API-equivalent list cost, or ``None`` when the model is not in the table.

        ``None`` rather than ``0.0``: an unpriced model is unmeasured, and a measured
        zero would quietly crown it on any cost frontier.
        """
        price = _price_for(model)
        if price is None:
            return None
        input_price, output_price = price
        fresh = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return (
            fresh * input_price
            + cache_read * input_price * 0.1
            + cache_write * input_price * 1.25
            + out * output_price
        ) / 1_000_000
