"""Substrate runners: execute one task cell and return a structured RunResult.

v1 substrate is Claude Code headless (`claude -p ... --output-format json`).
Auth strategy: the runner strips ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN from the
subprocess environment so `claude` falls back to the claude.ai/subscription login
(in this environment a rate-limited API key would otherwise take precedence).

Status taxonomy keeps infra failure separate from model quality (the talk's
failure-mode #2): `ok | rate_limited | infra_error | timeout | parse_fail`.
Every run writes a full diagnostic envelope to a transcript sidecar.
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
    "extract_json",
    "AUTH_ENV_STRIP",
    "HERMETIC_DISALLOWED_TOOLS",
]

RunStatus = Literal["ok", "rate_limited", "infra_error", "timeout", "parse_fail"]

# Env vars removed from the subprocess so `claude` uses the subscription login.
AUTH_ENV_STRIP: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: Tools disallowed in every cell by default: the exec / filesystem-read / network /
#: delegation escape surface. A live probe (2026-07-02 extended pilot) showed headless
#: cells CAN run Bash — a control-arm cell grepped beyond its worktree and located
#: files containing its own gold (prior sessions' transcripts; the public repo is one
#: `curl` away). Disallowing WebSearch/WebFetch alone is not a boundary. The current
#: task suite needs at most the Skill tool (t1 is JSON-only, t3's source is embedded
#: in the prompt, t4's reference arrives via Skill), so cells run tool-minimal;
#: a future agentic task must explicitly relax this via ``disallowed_tools``.
HERMETIC_DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash",
    "Read",
    "Grep",
    "Glob",
    "Task",
    "WebSearch",
    "WebFetch",
    "Write",
    "Edit",
    "NotebookEdit",
)


@dataclass(frozen=True, slots=True)
class RunResult:
    """The outcome of a single headless cell, ready to append to the ledger."""

    run_id: str
    status: RunStatus
    output: str
    cost_usd: float
    latency_s: float
    returncode: int | None
    model_resolved: str | None
    num_turns: int
    session_id: str | None
    usage: dict[str, Any]
    transcript_path: str | None
    raw: dict[str, Any] | None


@runtime_checkable
class Runner(Protocol):
    """A substrate that runs a prompt at a given model+effort and reports cost/latency.

    ``json_schema`` requests structured output (T1's batched verdict array);
    ``permission_mode`` overrides the runner default per call (agentic T2 needs a
    non-interactive mode so the skill's file writes don't block).
    """

    def run(
        self,
        prompt: str,
        *,
        model: str,
        effort: str,
        cwd: Path,
        json_schema: dict[str, Any] | None = None,
        permission_mode: str | None = None,
    ) -> RunResult: ...


def extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of the JSON result object from CLI stdout.

    Tolerates preamble/warning lines that some `claude` invocations print before the
    JSON: try the whole string, then each line bottom-up, then a first-`{`…last-`}`
    slice. Returns None if nothing parses to a dict.
    """
    candidates: list[str] = [text]
    candidates += [ln for ln in reversed(text.splitlines()) if ln.strip().startswith("{")]
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def classify_status(payload: dict[str, Any]) -> RunStatus:
    """Map a parsed payload to a RunStatus (payload-only; returncode applied later).

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
    returncode: int | None = None,
) -> RunResult:
    """Build a RunResult from a parsed JSON payload (pure; unit-tested on fixtures).

    A nonzero exit with an otherwise-`ok` payload is overridden to `infra_error`:
    the CLI signalled failure even though the JSON looks clean, so the run is suspect.
    """
    status = classify_status(payload)
    if returncode not in (0, None) and status == "ok":
        status = "infra_error"
    usage = payload.get("usage")
    return RunResult(
        run_id=run_id,
        status=status,
        output=str(payload.get("result", "")),
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        latency_s=latency_s,
        returncode=returncode,
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
        transcript_dir: where full per-run JSON envelopes are written (the talk's
            "read your transcripts"). None disables sidecar dumps.
        timeout_s: hard wall-clock cap per cell (→ status `timeout`).
        max_budget_usd: soft per-call runaway-loop stop passed to `--max-budget-usd`.
        permission_mode: `--permission-mode` for agentic tasks so tools don't block.
    """

    transcript_dir: Path | None = None
    timeout_s: float = 900.0
    max_budget_usd: float | None = None
    permission_mode: str | None = None
    disallowed_tools: tuple[str, ...] = HERMETIC_DISALLOWED_TOOLS

    def _argv(
        self,
        prompt: str,
        model: str,
        effort: str,
        *,
        json_schema: dict[str, Any] | None = None,
        permission_mode: str | None = None,
    ) -> list[str]:
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
            # Hermetic cells (2026-07-02 checkpoint review + extended-pilot probe): no
            # user MCP servers, and the escape-surface tools are disallowed so a cell
            # can only see its prompt, its cwd-loaded memory/skills, and the Skill tool.
            # The A/B control arm must have no path to its gold — which is public in
            # this repo and present in prior sessions' transcripts on the host.
            "--strict-mcp-config",
        ]
        if self.disallowed_tools:
            argv += ["--disallowedTools", *self.disallowed_tools]
        if json_schema is not None:
            argv += ["--json-schema", json.dumps(json_schema)]
        if self.max_budget_usd is not None:
            argv += ["--max-budget-usd", str(self.max_budget_usd)]
        mode = permission_mode or self.permission_mode
        if mode is not None:
            argv += ["--permission-mode", mode]
        return argv

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in AUTH_ENV_STRIP:
            env.pop(key, None)
        return env

    def _write_transcript(self, run_id: str, envelope: dict[str, Any]) -> str | None:
        if self.transcript_dir is None:
            return None
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"{run_id}.json"
        path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        return str(path)

    def _failure(
        self,
        run_id: str,
        status: RunStatus,
        output: str,
        latency_s: float,
        transcript_path: str | None,
    ) -> RunResult:
        return RunResult(
            run_id=run_id,
            status=status,
            output=output,
            cost_usd=0.0,
            latency_s=latency_s,
            returncode=None,
            model_resolved=None,
            num_turns=0,
            session_id=None,
            usage={},
            transcript_path=transcript_path,
            raw=None,
        )

    def run(
        self,
        prompt: str,
        *,
        model: str,
        effort: str,
        cwd: Path,
        json_schema: dict[str, Any] | None = None,
        permission_mode: str | None = None,
    ) -> RunResult:
        run_id = uuid.uuid4().hex
        argv = self._argv(
            prompt, model, effort, json_schema=json_schema, permission_mode=permission_mode
        )
        base_envelope: dict[str, Any] = {"argv": argv, "cwd": str(cwd)}
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
        except subprocess.TimeoutExpired as e:
            latency_s = time.monotonic() - start
            tp = self._write_transcript(
                run_id,
                {
                    **base_envelope,
                    "returncode": None,
                    "stdout": e.stdout or "",
                    "stderr": e.stderr or "",
                    "timeout": True,
                    "parsed_ok": False,
                },
            )
            return self._failure(
                run_id, "timeout", f"timeout after {self.timeout_s}s", latency_s, tp
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as e:
            # Missing `claude`, bad cwd, argv too large, etc. — never crash the sweep.
            latency_s = time.monotonic() - start
            tp = self._write_transcript(
                run_id, {**base_envelope, "error": str(e), "parsed_ok": False}
            )
            return self._failure(run_id, "infra_error", str(e), latency_s, tp)

        latency_s = time.monotonic() - start
        payload = extract_json(proc.stdout)
        tp = self._write_transcript(
            run_id,
            {
                **base_envelope,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "parsed_ok": payload is not None,
            },
        )
        if payload is None:
            status: RunStatus = "infra_error" if proc.returncode != 0 else "parse_fail"
            return self._failure(run_id, status, (proc.stdout or proc.stderr)[:2000], latency_s, tp)
        return result_from_payload(
            payload,
            run_id=run_id,
            latency_s=latency_s,
            transcript_path=tp,
            returncode=proc.returncode,
        )
