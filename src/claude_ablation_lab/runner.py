"""Substrate runners: execute one task cell and return a structured RunResult.

v1 substrate is Claude Code headless (`claude -p ... --output-format json`).
Auth strategy: the runner strips ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN from the
subprocess environment so `claude` falls back to the claude.ai/subscription login
(in this environment a rate-limited API key would otherwise take precedence).

Status taxonomy keeps infra failure separate from model quality (the talk's
failure-mode #2): `ok | rate_limited | infra_error | timeout | parse_fail`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "RunStatus",
    "RunResult",
    "Runner",
    "ClaudeCodeRunner",
    "classify_status",
    "result_from_payload",
    "AUTH_ENV_STRIP",
]

RunStatus = Literal["ok", "rate_limited", "infra_error", "timeout", "parse_fail"]

# Env vars removed from the subprocess so `claude` uses the subscription login.
AUTH_ENV_STRIP: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@dataclass(frozen=True, slots=True)
class RunResult:
    """The outcome of a single headless cell, ready to append to the ledger."""

    run_id: str
    status: RunStatus
    output: str
    cost_usd: float
    latency_s: float
    model_resolved: str | None
    num_turns: int
    session_id: str | None
    usage: dict[str, Any]
    transcript_path: str | None
    raw: dict[str, Any] | None


@runtime_checkable
class Runner(Protocol):
    """A substrate that runs a prompt at a given model+effort and reports cost/latency."""

    def run(
        self,
        prompt: str,
        *,
        model: str,
        effort: str,
        cwd: Path,
    ) -> RunResult: ...


def classify_status(payload: dict[str, Any]) -> RunStatus:
    """Map a parsed `claude --output-format json` payload to a RunStatus.

    Usage/rate-limit errors are `rate_limited` (back off, don't blame the model);
    any other `is_error` is `infra_error`; otherwise `ok`.
    """
    if payload.get("is_error"):
        message = str(payload.get("result", "")).lower()
        api_status = payload.get("api_error_status")
        if api_status in (400, 429) and ("usage limit" in message or "rate limit" in message):
            return "rate_limited"
        return "infra_error"
    return "ok"


def _resolve_model(payload: dict[str, Any]) -> str | None:
    """Recover the concrete model id Claude reported (alias → full id)."""
    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        return str(next(iter(model_usage)))
    return None


def result_from_payload(
    payload: dict[str, Any],
    *,
    run_id: str,
    latency_s: float,
    transcript_path: str | None,
) -> RunResult:
    """Build a RunResult from a parsed JSON payload (pure; unit-tested on fixtures)."""
    usage = payload.get("usage")
    return RunResult(
        run_id=run_id,
        status=classify_status(payload),
        output=str(payload.get("result", "")),
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        latency_s=latency_s,
        model_resolved=_resolve_model(payload),
        num_turns=int(payload.get("num_turns") or 0),
        session_id=payload.get("session_id"),
        usage=usage if isinstance(usage, dict) else {},
        transcript_path=transcript_path,
        raw=payload,
    )


@dataclass(frozen=True, slots=True)
class ClaudeCodeRunner:
    """Runs `claude -p` headless, parsing the JSON result into a RunResult.

    Args:
        transcript_dir: where full per-run JSON traces are written (the talk's
            "read your transcripts"). None disables sidecar dumps.
        timeout_s: hard wall-clock cap per cell (→ status `timeout`).
        max_budget_usd: soft per-call runaway-loop stop passed to `--max-budget-usd`.
        permission_mode: `--permission-mode` for agentic tasks so tools don't block.
    """

    transcript_dir: Path | None = None
    timeout_s: float = 900.0
    max_budget_usd: float | None = None
    permission_mode: str | None = None

    def _argv(self, prompt: str, model: str, effort: str) -> list[str]:
        argv = [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--effort",
            effort,
            "--output-format",
            "json",
        ]
        if self.max_budget_usd is not None:
            argv += ["--max-budget-usd", str(self.max_budget_usd)]
        if self.permission_mode is not None:
            argv += ["--permission-mode", self.permission_mode]
        return argv

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in AUTH_ENV_STRIP:
            env.pop(key, None)
        return env

    def _write_transcript(self, run_id: str, text: str) -> str | None:
        if self.transcript_dir is None:
            return None
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"{run_id}.json"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def run(self, prompt: str, *, model: str, effort: str, cwd: Path) -> RunResult:
        run_id = uuid.uuid4().hex
        argv = self._argv(prompt, model, effort)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            latency_s = time.monotonic() - start
            return RunResult(
                run_id=run_id,
                status="timeout",
                output=f"timeout after {self.timeout_s}s",
                cost_usd=0.0,
                latency_s=latency_s,
                model_resolved=None,
                num_turns=0,
                session_id=None,
                usage={},
                transcript_path=None,
                raw=None,
            )
        latency_s = time.monotonic() - start
        transcript_path = self._write_transcript(run_id, proc.stdout)

        try:
            payload: dict[str, Any] = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            return RunResult(
                run_id=run_id,
                status="parse_fail",
                output=(proc.stdout or proc.stderr)[:2000],
                cost_usd=0.0,
                latency_s=latency_s,
                model_resolved=None,
                num_turns=0,
                session_id=None,
                usage={},
                transcript_path=transcript_path,
                raw=None,
            )
        return result_from_payload(
            payload,
            run_id=run_id,
            latency_s=latency_s,
            transcript_path=transcript_path,
        )
