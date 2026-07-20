"""Local open-weight provider (ollama) — the arm where the effort knob is observable.

This is the scientific half of the two-backend design. Unlike every commercial API it
returns the raw reasoning trace as a field distinct from the answer, and its budget lever
is genuinely *enforced* rather than requested: generation stops at ``num_predict`` even
mid-thought. That makes it the only backend where "quality vs. reasoning tokens" is a
measurement rather than a proxy.

Token attribution uses **streaming**. The non-streaming response reports only
``eval_count`` (total generated), which conflates thinking with answer. Streaming emits
one chunk per token carrying either a ``thinking`` or a ``content`` delta, so counting
chunks by kind recovers the split exactly. Measured against ``qwen3:8b``: 636 thinking +
3 content chunks vs. an ``eval_count`` of 644 — the ~0.6% gap is control tokens
(``</think>``, EOS) that are never emitted as message deltas. Both numbers are recorded
and the residual is exposed as ``control_tokens`` rather than silently absorbed into
either side.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from claude_ablation_lab.provider import Completion, Effort, ModelCaps

__all__ = ["OllamaProvider", "DEFAULT_HOST"]

DEFAULT_HOST = "http://localhost:11434"

#: Models known to emit a separate reasoning trace via the ``think`` flag. Membership is
#: verified at :meth:`OllamaProvider.capabilities` time by consulting the local server's
#: model metadata, never assumed from the name alone — the previous harness's central
#: mistake was trusting a name-keyed capability table over the provider's own answer.
_THINKING_FAMILIES = frozenset({"qwen3", "deepseek-r1", "gpt-oss", "magistral", "phi4-reasoning"})


@dataclass(frozen=True, slots=True)
class OllamaProvider:
    """Bare inference against a local ollama server.

    Parameters
    ----------
    host:
        Base URL of the ollama server.
    num_gpu:
        Layers to offload to GPU. ``0`` forces CPU. Relevant here because an 8 GB card
        shared with a desktop session will fail to load an 8B model with an opaque
        ``load request: EOF``; forcing CPU trades ~5 tok/s for reliability.
    num_thread:
        CPU threads. Measured to be near-flat above ~32 on this host (CPU inference is
        memory-bandwidth bound), so raising it is not a throughput lever.
    temperature:
        Held fixed across the sweep. Deliberately *not* a knob: lowering temperature to
        shrink error bars converts conditional variance into variance of conditional
        means and can triple the minimum achievable variance (Miller, arXiv:2411.00640).
    """

    host: str = DEFAULT_HOST
    num_gpu: int = 0
    num_thread: int = 32
    temperature: float = 0.6
    seed: int | None = None

    @property
    def name(self) -> str:
        return "ollama"

    # ------------------------------------------------------------------ capabilities

    def capabilities(self, model: str) -> ModelCaps:
        """Report what *model* honors, from the local server's own metadata.

        Raises
        ------
        RuntimeError
            If the server is unreachable or does not know *model*. Failing loudly here
            is deliberate: a missing model must not degrade into a mislabelled cell.
        """
        try:
            payload = self._post("/api/show", {"model": model}, timeout_s=30.0)
        except OSError as exc:
            raise RuntimeError(f"ollama unreachable at {self.host}: {exc}") from exc
        if "error" in payload:
            raise RuntimeError(f"ollama does not have model {model!r}: {payload['error']}")

        family = str(model).split(":", 1)[0].lower()
        caps_reported = {str(c).lower() for c in (payload.get("capabilities") or [])}
        thinks = "thinking" in caps_reported or family in _THINKING_FAMILIES

        info: dict[str, Any] = payload.get("model_info") or {}
        context_window = next(
            (int(v) for k, v in info.items() if k.endswith(".context_length")), None
        )

        return ModelCaps(
            model=model,
            # Local models expose no ordinal tier; the lever is the enforced budget.
            # Empty is the honest answer and makes ModelCaps.validate reject a tier
            # request rather than accept and ignore it.
            effort_tiers=frozenset(),
            supports_token_budget=True,
            reports_reasoning_tokens=thinks,
            max_output_tokens=None,
            context_window=context_window,
        )

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
        """Run one prompt, counting reasoning and answer tokens separately.

        The effective generation cap is ``effort.token_budget`` when set, else
        ``max_tokens``. Because the cap covers thinking *and* answer, a tight budget on a
        reasoning model yields ``stop_reason="length"`` with an empty answer — a
        truncation, not a wrong answer, and the caller must not grade it as one.

        ``json_schema`` maps to ollama's ``format`` parameter, which constrains the
        *answer* (thinking is unaffected) — the local analogue of ``--json-schema``.
        """
        caps = self.capabilities(model)
        caps.validate(effort)

        budget = effort.token_budget if effort.token_budget is not None else max_tokens
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "think": caps.reports_reasoning_tokens,
            "options": {
                "num_predict": budget,
                "temperature": self.temperature,
                "num_gpu": self.num_gpu,
                "num_thread": self.num_thread,
            },
        }
        if json_schema is not None:
            body["format"] = json_schema
        if self.seed is not None:
            body["options"]["seed"] = self.seed

        started = time.monotonic()
        try:
            chunks = self._post_stream("/api/chat", body, timeout_s=timeout_s)
        except TimeoutError:
            return Completion(text="", status="timeout", latency_s=time.monotonic() - started)
        except OSError as exc:
            return Completion(
                text="",
                status="infra_error",
                latency_s=time.monotonic() - started,
                raw={"error": str(exc)},
            )
        latency_s = time.monotonic() - started

        return self._assemble(chunks, latency_s=latency_s, thinking_enabled=body["think"])

    def _assemble(
        self, chunks: list[dict[str, Any]], *, latency_s: float, thinking_enabled: bool
    ) -> Completion:
        """Fold streamed chunks into a :class:`Completion` with an exact token split."""
        think_parts: list[str] = []
        answer_parts: list[str] = []
        reasoning_tokens = 0
        answer_tokens = 0
        final: dict[str, Any] = {}

        for chunk in chunks:
            if chunk.get("error"):
                return Completion(text="", status="infra_error", latency_s=latency_s, raw=chunk)
            message = chunk.get("message") or {}
            # One chunk carries one token, so chunk counts *are* token counts.
            if message.get("thinking"):
                think_parts.append(message["thinking"])
                reasoning_tokens += 1
            elif message.get("content"):
                answer_parts.append(message["content"])
                answer_tokens += 1
            if chunk.get("done"):
                final = chunk

        if not final:
            return Completion(
                text="".join(answer_parts),
                status="parse_fail",
                latency_s=latency_s,
                raw={"reason": "stream ended without a done chunk"},
            )

        eval_count = final.get("eval_count")
        # Residual between the vendor's own total and what the deltas carried: control
        # tokens (</think>, EOS). Surfaced rather than folded into either bucket so the
        # reconciliation stays auditable.
        control_tokens = (
            eval_count - (reasoning_tokens + answer_tokens) if eval_count is not None else None
        )

        return Completion(
            text="".join(answer_parts).strip(),
            status="ok",
            latency_s=latency_s,
            input_tokens=final.get("prompt_eval_count"),
            output_tokens=eval_count,
            reasoning_tokens=reasoning_tokens if thinking_enabled else None,
            reasoning_text="".join(think_parts) if thinking_enabled else None,
            # Local inference has no dollar price. 0.0 would assert a measured zero;
            # None correctly says the axis does not apply on this backend.
            cost_usd=None,
            stop_reason=final.get("done_reason"),
            model_resolved=final.get("model"),
            # ollama does not report an applied effort; that is exactly why the Control
            # pre-flight must establish application behaviourally.
            effort_applied=None,
            raw={
                "streamed_reasoning_tokens": reasoning_tokens,
                "streamed_answer_tokens": answer_tokens,
                "control_tokens": control_tokens,
                "total_duration_ns": final.get("total_duration"),
                "eval_duration_ns": final.get("eval_duration"),
            },
        )

    # ------------------------------------------------------------------------ transport

    def _post(self, path: str, body: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.host}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return dict(json.loads(response.read().decode()))
        except urllib.error.HTTPError as exc:
            return {"error": exc.read().decode(errors="replace")[:500]}

    def _post_stream(
        self, path: str, body: dict[str, Any], *, timeout_s: float
    ) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"{self.host}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        chunks: list[dict[str, Any]] = []
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            for raw_line in response:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    # A malformed line is evidence, not a crash: keep it for forensics
                    # and let _assemble decide the status.
                    chunks.append({"error": f"undecodable stream line: {line[:200]}"})
        return chunks
