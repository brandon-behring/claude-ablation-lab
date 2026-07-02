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
    "parse_stream_json",
    "AUTH_ENV_STRIP",
    "KNOWN_BUILTIN_TOOLS",
    "CATALOG_VERIFIED_CLAUDE_VERSION",
    "HERMETIC_DISALLOWED_TOOLS",
]

RunStatus = Literal["ok", "rate_limited", "infra_error", "timeout", "parse_fail"]

# Env vars removed from the subprocess so `claude` uses the subscription login.
AUTH_ENV_STRIP: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: The CLI's built-in tool names, as of the version below — there is no ``claude
#: --list-tools`` (confirmed: ``--help`` documents only name-accepting flags). Sourced
#: two ways, both dated 2026-07-02 against v2.1.198:
#:
#: 1. **Live-confirmed** (42 names): passing a name to ``--disallowedTools`` that the
#:    CLI doesn't recognize prints ``Permission deny rule "<X>" matches no known
#:    tool`` to stderr but still runs (exit 0) — so a real probe with ~40 candidates
#:    cleanly separates real names from typos/renames at zero risk (the invocation
#:    itself is never broken by an unrecognized name). ``DesignSync`` (1 name) came
#:    from a live ``--output-format stream-json`` session's ``system/init`` event,
#:    which reports a ``tools`` array — a second, independent live source (see the
#:    D6.2 backlog note in ``docs/design/2026-07-01_phase6-deferrals.md`` for why
#:    that array isn't used as the primary catalog: it disagrees with the probe on a
#:    few names, e.g. it omits ``Grep``/``Glob``).
#: 2. **Docs-only, not individually live-tested** (2 names): ``Agent``,
#:    ``AskUserQuestion`` — confirmed real by Anthropic's tools-reference docs.
#:
#: **Important nuance, found via ``StructuredOutput`` (see below): "matches no known
#: tool" is a validator/linting message, not proof a deny rule is inert.** A follow-up
#: probe denied ``StructuredOutput`` specifically — same "unknown tool" warning as a
#: deliberately-fake control name — yet the model's structured-output tool call was
#: then actually rejected at runtime ("permission was denied"). So an unrecognized
#: name can still functionally match at call time; "unknown to the validator" only
#: means it's safe to *include* (never breaks the invocation itself), not that
#: denying it has no effect. Two consequences:
#:
#: - ``StructuredOutput`` is real and load-bearing: ``--json-schema`` (T1's batched
#:   verdict schema) is implemented as a synthetic ``StructuredOutput`` tool call
#:   (confirmed live: the transcript shows ``tool_use`` with ``name: "StructuredOutput"``).
#:   Denying it silently breaks every ``json_schema`` cell. It is excluded from
#:   ``HERMETIC_DISALLOWED_TOOLS`` below, the same treatment as ``Skill`` — a
#:   response-shaping mechanism the harness itself requested, not an escape-surface
#:   tool a cell could use to read/write/exfiltrate.
#: - ``"SlashCommand"``, previously in this tuple, is a confirmed-fake name — but *not*
#:   solely on the "unknown tool" evidence above (which, per this nuance, wouldn't be
#:   dispositive on its own). The real evidence: the published showcase's 54-session
#:   harvest (`docs/design/2026-07-02_pr11-review.md`) shows all 18 with-skill cells
#:   invoked ``Skill`` successfully *while `"SlashCommand"` sat in this exact deny
#:   list* — so whatever it does or doesn't match, it demonstrably never blocked the
#:   one thing it might have been guarding. It is dead weight, removed. It is *not*
#:   replaced 1:1: ``--help`` confirms ``--disable-slash-commands`` — the only flag
#:   governing that surface — also disables **all Skills** ("Skills still resolve via
#:   /skill-name" per ``--bare``'s own description; a live ``system/init`` event lists
#:   ``research-plan`` in both ``skills`` and ``slash_commands``, confirming they share
#:   one resolution path in this CLI version). Using it here would silently zero out
#:   the with-skill treatment arm. There is currently **no way to block user-level
#:   ``~/.claude/commands`` injection without also disabling Skill** — recorded as a
#:   residual gap, not silently dropped (D6.1 in the deferrals doc).
KNOWN_BUILTIN_TOOLS: tuple[str, ...] = (
    "Agent",
    "Artifact",
    "AskUserQuestion",
    "Bash",
    "BashOutput",
    "CronCreate",
    "CronDelete",
    "CronList",
    "DesignSync",
    "Edit",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitPlanMode",
    "ExitWorktree",
    "Glob",
    "Grep",
    "KillShell",
    "ListMcpResourcesTool",
    "LSP",
    "Monitor",
    "NotebookEdit",
    "PowerShell",
    "PushNotification",
    "Read",
    "ReadMcpResourceTool",
    "RemoteTrigger",
    "ReportFindings",
    "ScheduleWakeup",
    "SendMessage",
    "SendUserFile",
    "ShareOnboardingGuide",
    "Skill",  # the one tool a hermetic cell keeps — never in HERMETIC_DISALLOWED_TOOLS
    "StructuredOutput",
    "Task",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "TodoWrite",
    "ToolSearch",
    "WaitForMcpServers",
    "WebFetch",
    "WebSearch",
    "Workflow",
    "Write",
)

#: The exact ``claude --version`` this catalog was verified against. ``cli/main.py``
#: hard-stops a real sweep if the installed CLI has moved past this pin (a version
#: bump may have added/renamed a tool the catalog doesn't know about — fail closed,
#: don't silently trust a stale list) unless ``--allow-unverified-tools`` is passed.
CATALOG_VERIFIED_CLAUDE_VERSION = "2.1.198"

#: Tools kept out of every cell's deny list — not the escape surface, but mechanisms
#: the harness itself relies on. ``Skill`` is the treatment mechanism under test.
#: ``StructuredOutput`` is how ``--json-schema`` is actually implemented (a synthetic
#: tool call, confirmed live — see the catalog docstring above); denying it silently
#: breaks every schema-based cell (T1). Neither can read/write/exec/network on its
#: own, so excluding them doesn't reopen the escape surface the rest of the deny
#: list closes.
_ALWAYS_ALLOWED: frozenset[str] = frozenset({"Skill", "StructuredOutput"})

#: Tools disallowed in every cell by default: the exec / filesystem-read / network /
#: delegation escape surface — every known built-in tool except `_ALWAYS_ALLOWED`. A
#: live probe (2026-07-02 extended pilot) showed headless cells CAN run Bash — a
#: control-arm cell grepped beyond its worktree and located files containing its own
#: gold (prior sessions' transcripts; the public repo is one `curl` away). The
#: current task suite needs at most the Skill tool (t1 is JSON-only via
#: StructuredOutput, t3's source is embedded in the prompt, t4's reference arrives
#: via Skill), so cells run tool-minimal by default; an agentic task relaxes this
#: per-task via ``Task.tools``/``Prepared.disallowed_tools`` (see ``prepare.py``).
HERMETIC_DISALLOWED_TOOLS: tuple[str, ...] = tuple(
    t for t in KNOWN_BUILTIN_TOOLS if t not in _ALWAYS_ALLOWED
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
    #: Ordered tool names from ``tool_use`` content blocks — mechanism evidence.
    #: ``None`` means *not measured* (plain ``json`` format has no per-tool events —
    #: ``ClaudeCodeRunner.capture_mechanism=False``, or the run failed before any
    #: events parsed); ``()`` means *measured, zero tool calls*. Collapsing these
    #: (review finding, D6) would make "we didn't look" indistinguishable from "we
    #: looked and saw nothing" — exactly the kind of overclaim this project has been
    #: burned by before (PR #11's "control cells make zero tool calls" needed the
    #: full session harvest to state precisely). Supersedes the now-impossible
    #: post-hoc ``~/.claude/projects`` harvest, since cells run
    #: ``--no-session-persistence``.
    tools_used: tuple[str, ...] | None = None


@runtime_checkable
class Runner(Protocol):
    """A substrate that runs a prompt at a given model+effort and reports cost/latency.

    ``json_schema`` requests structured output (T1's batched verdict array);
    ``permission_mode`` overrides the runner default per call (agentic T2 needs a
    non-interactive mode so the skill's file writes don't block); ``disallowed_tools``
    overrides the runner's default deny-list per call (agentic T2 needs real tools —
    see ``prepare.py``'s ``Prepared.disallowed_tools``). ``None`` for either means
    "use the runner's own default", not "allow everything".
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
        disallowed_tools: tuple[str, ...] | None = None,
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


def parse_stream_json(text: str) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Parse ``--output-format stream-json --verbose`` NDJSON output.

    Returns ``(result_payload, tools_used)``. The terminal ``type: "result"`` event
    is structurally identical to the single-shot ``json`` payload (same fields
    :func:`result_from_payload` already reads: ``is_error``/``result``/
    ``total_cost_usd``/``usage``/``session_id``/``num_turns``/``modelUsage``) —
    verified against a live capture, 2026-07-02. ``tools_used`` is every
    ``tool_use`` content-block ``name`` from ``type: "assistant"`` events, in
    invocation order (an assistant turn may mix ``thinking``/``text``/``tool_use``
    blocks — only the latter are collected). Tolerant of stray non-JSON or
    non-dict lines, same defensive posture as :func:`extract_json`; a stream that
    never reaches its terminal event (e.g. cut off mid-run) returns
    ``(None, tools_used-so-far)`` rather than raising.
    """
    payload: dict[str, Any] | None = None
    tools_used: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            payload = event
        elif event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name")
                        if isinstance(name, str):
                            tools_used.append(name)
    return payload, tuple(tools_used)


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
    tools_used: tuple[str, ...] | None = None,
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
        tools_used=tools_used,
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
        capture_mechanism: use `--output-format stream-json --verbose` and populate
            `RunResult.tools_used` instead of the default single-shot `json` format.
            A sweep-level choice (not per-call, unlike `disallowed_tools`): either the
            whole sweep wants mechanism evidence or it doesn't.
    """

    transcript_dir: Path | None = None
    timeout_s: float = 900.0
    max_budget_usd: float | None = None
    permission_mode: str | None = None
    disallowed_tools: tuple[str, ...] = HERMETIC_DISALLOWED_TOOLS
    capture_mechanism: bool = False

    def _argv(
        self,
        prompt: str,
        model: str,
        effort: str,
        *,
        json_schema: dict[str, Any] | None = None,
        permission_mode: str | None = None,
        disallowed_tools: tuple[str, ...] | None = None,
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
            "stream-json" if self.capture_mechanism else "json",
            # Hermetic cells (2026-07-02 checkpoint review + extended-pilot probe): no
            # user MCP servers, and the escape-surface tools are disallowed so a cell
            # can only see its prompt, its cwd-loaded memory/skills, and the Skill tool.
            # The A/B control arm must have no path to its gold — which is public in
            # this repo and present in prior sessions' transcripts on the host.
            # --no-session-persistence stops NEW gold-bearing session files accumulating
            # under ~/.claude/projects (the source the probe's grep actually found);
            # mechanism evidence comes from the runner's own transcript sidecars.
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if self.capture_mechanism:
            argv.append("--verbose")  # required for stream-json to emit tool_use blocks
        tools = disallowed_tools if disallowed_tools is not None else self.disallowed_tools
        if tools:
            argv += ["--disallowedTools", *tools]
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
        disallowed_tools: tuple[str, ...] | None = None,
    ) -> RunResult:
        run_id = uuid.uuid4().hex
        argv = self._argv(
            prompt,
            model,
            effort,
            json_schema=json_schema,
            permission_mode=permission_mode,
            disallowed_tools=disallowed_tools,
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
        if self.capture_mechanism:
            payload, tools_used = parse_stream_json(proc.stdout)
        else:
            payload, tools_used = extract_json(proc.stdout), None  # not measured, not "zero"
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
            tools_used=tools_used,
        )
