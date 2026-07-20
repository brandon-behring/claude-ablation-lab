"""`claude -p` as a bare inference endpoint — the routing arm.

This measures the surface you actually deploy on: the real Claude Code system prompt is
deliberately kept (decision 9), so every figure built on this backend describes *Claude
Code at effort E*, not the model in isolation. What is stripped is everything that would
vary between cells or add tool-loop variance:

- ``--tools ""`` disables every built-in tool (decision 10) — retiring the hand-curated
  tool catalog and its CLI-version pin, exactly as the repo's D6.3 deferral predicted.
- ``--exclude-dynamic-system-prompt-sections`` moves per-machine content (cwd, env, git
  status, memory paths) out of the system prompt so it cannot differ between cells.
- ``--strict-mcp-config`` / ``--no-session-persistence`` keep the cell hermetic, as in
  the original runner.

Auth: the subprocess env strips ``ANTHROPIC_API_KEY``/``ANTHROPIC_AUTH_TOKEN``
(:data:`~claude_ablation_lab.runner.AUTH_ENV_STRIP`) so the CLI uses the flat-rate
subscription login. Probed live on CLI 2.1.214 (2026-07-20): with the key exported, the
CLI routes to the credit-less API and every call fails with "Credit balance is too low";
with it stripped, the same call succeeds on the subscription and returns full usage.

The original bug this module exists to prevent: the CLI *accepts* ``--effort`` for every
model and silently discards it where unsupported. Effort is therefore validated against
the **Models API capability matrix** (a free read that needs no credits) before any
subprocess runs — an inert or unsupported tier raises instead of producing a mislabelled
cell.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from claude_ablation_lab.provider import Completion, Effort, ModelCaps
from claude_ablation_lab.provider.anthropic_api import AnthropicProvider
from claude_ablation_lab.runner import AUTH_ENV_STRIP, classify_status, extract_json

__all__ = ["CliProvider"]


@dataclass
class CliProvider:
    """Bare inference over headless ``claude -p``, funded by the subscription.

    Parameters
    ----------
    caps_source:
        Where capability truth comes from. Defaults to :class:`AnthropicProvider`,
        whose :meth:`~AnthropicProvider.capabilities` reads the Models API — the CLI
        itself has no capability surface, which is how the original silent-clamp bug
        happened. Injectable for tests.
    claude_bin:
        The CLI executable.
    """

    caps_source: AnthropicProvider = field(default_factory=AnthropicProvider)
    claude_bin: str = "claude"

    @property
    def name(self) -> str:
        return "claude-cli"

    # ------------------------------------------------------------------ capabilities

    def capabilities(self, model: str) -> ModelCaps:
        """Capability matrix for *model*, from the Models API — with one CLI override.

        The API reports ``supports_token_budget=True`` for budget-era models
        (``thinking.enabled``), but the CLI exposes no flag to pass ``budget_tokens``,
        so on this backend a token budget is not enforceable and requesting one must
        raise rather than be quietly dropped.
        """
        api_caps = self.caps_source.capabilities(model)
        return replace(api_caps, supports_token_budget=False)

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
        """Run one prompt through ``claude -p`` at a validated effort tier.

        ``max_tokens`` is accepted for protocol compatibility but **not enforceable**:
        the CLI exposes no output-cap flag, so the CLI manages its own limits. This is
        recorded here rather than silently ignored — callers sizing budgets should use
        the ollama arm, where ``num_predict`` is a real cap.

        ``json_schema`` maps to ``--json-schema`` (decision 15): constraining the answer
        shape at generation time keeps parse failure rare and — critically —
        uncorrelated with effort tier, closing the verbose-output grading bias the
        2026-07-11 audit flagged.
        """
        del max_tokens  # documented above: no CLI flag exists to enforce it
        caps = self.capabilities(model)
        caps.validate(effort)

        argv = [self.claude_bin, "-p", prompt, "--model", model]
        if effort.tier is not None:
            argv += ["--effort", effort.tier]
        argv += [
            "--tools",
            "",
            "--output-format",
            "json",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--exclude-dynamic-system-prompt-sections",
        ]
        if json_schema is not None:
            argv += ["--json-schema", json.dumps(json_schema)]

        env = dict(os.environ)
        for key in AUTH_ENV_STRIP:
            env.pop(key, None)

        run_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 - fixed binary, no shell
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return Completion(
                text="",
                status="timeout",
                latency_s=time.monotonic() - started,
                raw={"run_id": run_id, "argv_flags": argv[3:]},
            )
        except OSError as exc:
            return Completion(
                text="",
                status="infra_error",
                latency_s=time.monotonic() - started,
                raw={"run_id": run_id, "error": str(exc)},
            )
        latency_s = time.monotonic() - started

        payload = extract_json(proc.stdout)
        if payload is None:
            return Completion(
                text="",
                status="parse_fail",
                latency_s=latency_s,
                raw={
                    "run_id": run_id,
                    "returncode": proc.returncode,
                    "stdout_head": proc.stdout[:500],
                    "stderr_head": proc.stderr[:500],
                },
            )

        status = classify_status(payload)
        if proc.returncode != 0 and status == "ok":
            # The CLI signalled failure even though the JSON looks clean — suspect run.
            status = "infra_error"

        usage_raw = payload.get("usage")
        usage: dict[str, Any] = usage_raw if isinstance(usage_raw, dict) else {}
        model_usage = payload.get("modelUsage")
        model_resolved = (
            str(next(iter(model_usage))) if isinstance(model_usage, dict) and model_usage else None
        )

        return Completion(
            text=str(payload.get("result", "")).strip(),
            status=status,
            latency_s=latency_s,
            input_tokens=_opt_int(usage.get("input_tokens")),
            output_tokens=_opt_int(usage.get("output_tokens")),
            # Structural on every Claude surface: thinking is billed inside
            # output_tokens and the raw trace is never returned. Figures built on this
            # backend use total output tokens as the proxy axis, and say so.
            reasoning_tokens=None,
            reasoning_text=None,
            cost_usd=_opt_float(payload.get("total_cost_usd")),
            stop_reason=payload.get("stop_reason"),
            model_resolved=model_resolved,
            # The CLI does not echo an applied effort; application is established
            # behaviourally by the Control pre-flight, never assumed from the flag.
            effort_applied=None,
            raw={
                "run_id": run_id,
                "structured_output": payload.get("structured_output"),
                "cache_read_tokens": _opt_int(usage.get("cache_read_input_tokens")),
                "cache_creation_tokens": _opt_int(usage.get("cache_creation_input_tokens")),
                "num_turns": payload.get("num_turns"),
                "session_id": payload.get("session_id"),
                "is_error": payload.get("is_error"),
                "api_error_status": payload.get("api_error_status"),
            },
        )


def _opt_int(value: object) -> int | None:
    """A non-negative int, or ``None`` for absent/malformed — never a fabricated zero.

    The ledger's None-vs-zero rule: an absent usage key means *not measured*, and
    coercing it to 0 would make "we could not look" indistinguishable from "we looked
    and saw nothing".
    """
    if value is None:
        return None
    try:
        result = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _opt_float(value: object) -> float | None:
    """A finite float, or ``None`` for absent/malformed — same rule as :func:`_opt_int`."""
    import math

    if value is None:
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None
