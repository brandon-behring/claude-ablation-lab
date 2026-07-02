"""Runner parser + argv/env tests against captured `claude --output-format json` fixtures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from claude_ablation_lab.runner import (
    AUTH_ENV_STRIP,
    CATALOG_VERIFIED_CLAUDE_VERSION,
    HERMETIC_DISALLOWED_TOOLS,
    KNOWN_BUILTIN_TOOLS,
    ClaudeCodeRunner,
    classify_status,
    extract_json,
    parse_stream_json,
    result_from_payload,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.unit
def test_success_payload_classifies_ok() -> None:
    assert classify_status(_load("claude_json_success.json")) == "ok"


@pytest.mark.unit
def test_api_usage_limit_classifies_rate_limited() -> None:
    # The 400 "usage limit" error must be rate_limited, NOT model failure.
    assert classify_status(_load("claude_json_api_limit.json")) == "rate_limited"


@pytest.mark.unit
def test_generic_error_is_infra_error() -> None:
    assert classify_status(
        {"is_error": True, "result": "tool crashed", "api_error_status": 500}
    ) == ("infra_error")


@pytest.mark.unit
def test_result_from_success_payload() -> None:
    res = result_from_payload(
        _load("claude_json_success.json"),
        run_id="r1",
        latency_s=2.39,
        transcript_path="results/transcripts/r1.json",
    )
    assert res.status == "ok"
    assert res.output == "ok"
    assert res.cost_usd == pytest.approx(0.0355077)
    assert res.model_resolved == "claude-haiku-4-5-20251001"  # alias resolved
    assert res.num_turns == 1
    assert res.session_id == "0bec25f8-6239-4498-9673-f54b562767b0"
    assert res.usage["output_tokens"] == 40
    assert res.transcript_path == "results/transcripts/r1.json"


@pytest.mark.unit
def test_result_from_rate_limited_payload() -> None:
    res = result_from_payload(
        _load("claude_json_api_limit.json"), run_id="r2", latency_s=0.65, transcript_path=None
    )
    assert res.status == "rate_limited"
    assert res.cost_usd == 0.0


@pytest.mark.unit
def test_argv_includes_core_flags_and_optionals() -> None:
    runner = ClaudeCodeRunner(max_budget_usd=1.0, permission_mode="acceptEdits")
    argv = runner._argv("do x", "haiku", "low")
    assert argv[:2] == ["claude", "-p"]
    assert "do x" in argv
    for flag in ("--model", "--effort", "--output-format", "--max-budget-usd", "--permission-mode"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    # Hermeticity flags ride on EVERY cell: no user MCP servers, no session files left
    # on the host (they accumulate gold), and the full escape surface
    # (exec/fs-read/net/delegation) disallowed — a live probe showed a control
    # cell running Bash and locating its own gold outside the worktree.
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    i = argv.index("--disallowedTools")
    assert tuple(argv[i + 1 : i + 1 + len(HERMETIC_DISALLOWED_TOOLS)]) == HERMETIC_DISALLOWED_TOOLS
    for escape in ("Bash", "Read", "Grep", "Glob", "Task", "WebSearch", "WebFetch"):
        assert escape in HERMETIC_DISALLOWED_TOOLS
    assert "Skill" not in HERMETIC_DISALLOWED_TOOLS  # the treatment arm's mechanism stays


@pytest.mark.unit
def test_argv_pairs_flags_with_their_values() -> None:
    # Presence alone would pass with --model and --effort transposed, shipping every
    # cell to the wrong model with green tests (review finding) — assert the pairing.
    argv = ClaudeCodeRunner(max_budget_usd=2.5)._argv("p", "sonnet", "high")
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--max-budget-usd") + 1] == "2.5"


@pytest.mark.unit
def test_env_strips_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in AUTH_ENV_STRIP:
        monkeypatch.setenv(key, "sk-should-be-removed")
    env = ClaudeCodeRunner()._env()
    for key in AUTH_ENV_STRIP:
        assert key not in env


# --- Phase 1.5: extract_json robustness -------------------------------------


@pytest.mark.unit
def test_extract_json_clean() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


@pytest.mark.unit
def test_extract_json_with_preamble() -> None:
    text = "⚠ claude.ai connectors disabled\n" + '{"result": "ok", "is_error": false}'
    assert extract_json(text) == {"result": "ok", "is_error": False}


@pytest.mark.unit
def test_extract_json_trailing_after_json() -> None:
    parsed = extract_json('{"x": 1}\n[done]')
    assert parsed is not None
    assert parsed["x"] == 1


@pytest.mark.unit
def test_extract_json_garbage_returns_none() -> None:
    assert extract_json("not json at all") is None


@pytest.mark.unit
def test_returncode_override_ok_to_infra_error() -> None:
    res = result_from_payload(
        _load("claude_json_success.json"),
        run_id="r",
        latency_s=1.0,
        transcript_path=None,
        returncode=1,
    )
    assert res.status == "infra_error"  # nonzero exit + ok payload = suspect
    assert res.returncode == 1


# --- Phase 1.5: run() envelope + status paths (monkeypatched subprocess) -----


def _fake_proc(
    stdout: str, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.mark.unit
def test_run_preamble_stdout_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    success = (FIXTURES / "claude_json_success.json").read_text()
    monkeypatch.setattr(
        "claude_ablation_lab.runner.subprocess.run",
        lambda *a, **k: _fake_proc("⚠ notice line\n" + success),
    )
    res = ClaudeCodeRunner(transcript_dir=tmp_path).run(
        "hi", model="haiku", effort="low", cwd=tmp_path
    )
    assert res.status == "ok"
    assert res.output == "ok"
    assert res.returncode == 0
    assert res.transcript_path is not None
    envelope = json.loads(Path(res.transcript_path).read_text())
    assert {"argv", "cwd", "returncode", "stdout", "stderr"} <= envelope.keys()


@pytest.mark.unit
def test_run_missing_binary_is_infra_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*a: object, **k: object) -> object:
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr("claude_ablation_lab.runner.subprocess.run", boom)
    res = ClaudeCodeRunner(transcript_dir=tmp_path).run(
        "hi", model="haiku", effort="low", cwd=tmp_path
    )
    assert res.status == "infra_error"  # did not crash the sweep


@pytest.mark.unit
def test_run_timeout_captures_streams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def timeout(*a: object, **k: object) -> object:
        raise subprocess.TimeoutExpired(
            cmd=["claude"], timeout=1.0, output="partial out", stderr="err out"
        )

    monkeypatch.setattr("claude_ablation_lab.runner.subprocess.run", timeout)
    res = ClaudeCodeRunner(transcript_dir=tmp_path).run(
        "hi", model="haiku", effort="low", cwd=tmp_path
    )
    assert res.status == "timeout"
    assert res.transcript_path is not None
    envelope = json.loads(Path(res.transcript_path).read_text())
    assert envelope["stdout"] == "partial out"
    assert envelope["stderr"] == "err out"


@pytest.mark.unit
def test_run_nonzero_rate_limit_stays_rate_limited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    limit = (FIXTURES / "claude_json_api_limit.json").read_text()
    monkeypatch.setattr(
        "claude_ablation_lab.runner.subprocess.run",
        lambda *a, **k: _fake_proc(limit, returncode=1),
    )
    res = ClaudeCodeRunner(transcript_dir=tmp_path).run(
        "hi", model="haiku", effort="low", cwd=tmp_path
    )
    assert res.status == "rate_limited"  # classification wins over the nonzero override


# --- Phase 3: structured output + per-call permission override ---------------


@pytest.mark.unit
def test_argv_includes_json_schema_when_requested() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    argv = ClaudeCodeRunner()._argv("p", "haiku", "low", json_schema=schema)
    assert "--json-schema" in argv
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema  # serialized inline


@pytest.mark.unit
def test_argv_per_call_permission_mode_overrides_instance() -> None:
    runner = ClaudeCodeRunner(permission_mode="default")
    argv = runner._argv("p", "haiku", "low", permission_mode="acceptEdits")
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


@pytest.mark.unit
def test_argv_omits_json_schema_by_default() -> None:
    assert "--json-schema" not in ClaudeCodeRunner()._argv("p", "haiku", "low")


@pytest.mark.unit
def test_run_passes_json_schema_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}
    success = (FIXTURES / "claude_json_success.json").read_text()

    def fake(argv, *a, **k):  # noqa: ANN001, ANN002, ANN003
        captured["argv"] = argv
        return _fake_proc(success)

    monkeypatch.setattr("claude_ablation_lab.runner.subprocess.run", fake)
    ClaudeCodeRunner(transcript_dir=tmp_path).run(
        "hi", model="haiku", effort="low", cwd=tmp_path, json_schema={"type": "object"}
    )
    assert "--json-schema" in captured["argv"]


# --- D6: task-scoped tool policy + tool catalog + mechanism capture ----------


@pytest.mark.unit
def test_argv_per_call_disallowed_tools_overrides_instance_default() -> None:
    runner = ClaudeCodeRunner()  # instance default = HERMETIC_DISALLOWED_TOOLS
    argv = runner._argv("p", "haiku", "low", disallowed_tools=("Bash",))
    i = argv.index("--disallowedTools")
    assert argv[i + 1 :] == ["Bash"]  # the override wins, not the full hermetic set


@pytest.mark.unit
def test_argv_empty_disallowed_tools_override_omits_the_flag() -> None:
    # An agentic task whose declared `tools:` covers the whole catalog (or that
    # explicitly wants no restriction) can pass () to omit --disallowedTools entirely.
    argv = ClaudeCodeRunner()._argv("p", "haiku", "low", disallowed_tools=())
    assert "--disallowedTools" not in argv


@pytest.mark.unit
def test_argv_none_disallowed_tools_falls_back_to_instance_default() -> None:
    runner = ClaudeCodeRunner(disallowed_tools=("Bash", "Read"))
    argv = runner._argv("p", "haiku", "low", disallowed_tools=None)
    i = argv.index("--disallowedTools")
    assert argv[i + 1 :] == ["Bash", "Read"]


@pytest.mark.unit
def test_known_builtin_tools_catalog_covers_hermetic_deny_list() -> None:
    # Fail-closed regression guard (D6): every known tool except the always-allowed
    # set (Skill — the treatment mechanism; StructuredOutput — how --json-schema is
    # actually implemented, confirmed live) must be denied by default. Structurally
    # guaranteed today (HERMETIC_DISALLOWED_TOOLS is DERIVED from KNOWN_BUILTIN_TOOLS)
    # — this test exists so a future refactor that hardcodes either tuple
    # independently breaks loudly instead of silently drifting.
    always_allowed = {"Skill", "StructuredOutput"}
    assert set(KNOWN_BUILTIN_TOOLS) - always_allowed <= set(HERMETIC_DISALLOWED_TOOLS)
    assert always_allowed <= set(KNOWN_BUILTIN_TOOLS)
    assert not always_allowed & set(HERMETIC_DISALLOWED_TOOLS)


@pytest.mark.unit
def test_structured_output_denial_would_break_json_schema_regression_guard() -> None:
    # A live probe (2026-07-02) found --json-schema is implemented as a synthetic
    # StructuredOutput tool call — denying it breaks T1 silently (confirmed: the
    # model's structured-output call gets rejected, "permission was denied", even
    # though the invocation itself doesn't crash). This is the regression guard for
    # that fix: the hermetic default must never deny it.
    assert "StructuredOutput" not in HERMETIC_DISALLOWED_TOOLS


@pytest.mark.unit
def test_argv_json_schema_and_disallowed_tools_never_deny_structured_output() -> None:
    # Even a caller-supplied disallowed_tools override should never accidentally
    # break schema cells — the default catalog is the source of truth for this, but
    # assert the actual argv a schema-bearing cell gets never carries the denial.
    argv = ClaudeCodeRunner()._argv("p", "haiku", "low", json_schema={"type": "object"})
    i = argv.index("--disallowedTools")
    tools = argv[i + 1 : argv.index("--json-schema")]
    assert "StructuredOutput" not in tools
    assert "Skill" not in tools  # sanity: still not the treatment mechanism itself


@pytest.mark.unit
def test_slash_command_is_not_a_real_tool_name_regression_guard() -> None:
    # A live probe (2026-07-02, v2.1.198) found "SlashCommand" — previously in
    # HERMETIC_DISALLOWED_TOOLS — matches no known tool per the CLI's own deny-rule
    # validator. It was dead code providing zero actual protection; must not return
    # silently (see runner.py's KNOWN_BUILTIN_TOOLS docstring for the full story).
    assert "SlashCommand" not in KNOWN_BUILTIN_TOOLS
    assert "SlashCommand" not in HERMETIC_DISALLOWED_TOOLS


@pytest.mark.unit
def test_catalog_verified_version_is_pinned() -> None:
    assert CATALOG_VERIFIED_CLAUDE_VERSION == "2.1.198"


@pytest.mark.unit
def test_argv_capture_mechanism_uses_stream_json_and_verbose() -> None:
    runner = ClaudeCodeRunner(capture_mechanism=True)
    argv = runner._argv("p", "haiku", "low")
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv


@pytest.mark.unit
def test_argv_default_capture_mechanism_off_uses_json_no_verbose() -> None:
    argv = ClaudeCodeRunner()._argv("p", "haiku", "low")
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--verbose" not in argv


@pytest.mark.unit
def test_parse_stream_json_on_real_capture() -> None:
    # A genuine `claude -p ... --output-format stream-json --verbose` capture
    # (2026-07-02, v2.1.198) — not hand-authored. See the fixture file's own
    # provenance note. Trimmed only to drop a leaking system/init preamble that
    # parse_stream_json doesn't read anyway (see runner.py's module docstring).
    text = (FIXTURES / "claude_stream_json_tool_use.txt").read_text()
    payload, tools_used = parse_stream_json(text)
    assert tools_used == ("Bash",)
    assert payload is not None and payload["type"] == "result" and payload["is_error"] is False
    res = result_from_payload(
        payload,
        run_id="r",
        latency_s=1.0,
        transcript_path=None,
        returncode=0,
        tools_used=tools_used,
    )
    assert res.status == "ok"
    assert res.tools_used == ("Bash",)
    assert res.cost_usd > 0


@pytest.mark.unit
def test_parse_stream_json_tolerates_unknown_events_and_stray_lines() -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "hook_started"}),
        "not json at all",
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "..."},
                        {"type": "tool_use", "name": "Skill", "id": "x", "input": {}},
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Skill", "id": "y", "input": {}}]
                },
            }
        ),
        json.dumps({"type": "result", "is_error": False, "result": "done"}),
    ]
    payload, tools_used = parse_stream_json("\n".join(lines))
    assert tools_used == ("Skill", "Skill")  # ordered, not deduped — counted downstream
    assert payload == {"type": "result", "is_error": False, "result": "done"}


@pytest.mark.unit
def test_parse_stream_json_no_terminal_result_returns_none_payload() -> None:
    line = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}
    )
    payload, tools_used = parse_stream_json(line)
    assert payload is None
    assert tools_used == ()


@pytest.mark.unit
def test_run_with_capture_mechanism_populates_tools_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream_text = (FIXTURES / "claude_stream_json_tool_use.txt").read_text()
    monkeypatch.setattr(
        "claude_ablation_lab.runner.subprocess.run",
        lambda *a, **k: _fake_proc(stream_text),
    )
    res = ClaudeCodeRunner(transcript_dir=tmp_path, capture_mechanism=True).run(
        "hi", model="haiku", effort="low", cwd=tmp_path
    )
    assert res.status == "ok"
    assert res.tools_used == ("Bash",)


@pytest.mark.unit
def test_run_without_capture_mechanism_leaves_tools_used_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    success = (FIXTURES / "claude_json_success.json").read_text()
    monkeypatch.setattr(
        "claude_ablation_lab.runner.subprocess.run", lambda *a, **k: _fake_proc(success)
    )
    res = ClaudeCodeRunner(transcript_dir=tmp_path).run(
        "hi", model="haiku", effort="low", cwd=tmp_path
    )
    assert res.status == "ok"
    # None ("not measured"), never () — plain json format has no per-tool events to
    # report, and that must not be misread as "measured, zero tool calls" (D6 fix).
    assert res.tools_used is None


@pytest.mark.unit
def test_argv_capture_mechanism_and_json_schema_together_never_deny_structured_output() -> None:
    # T1 is the only json_schema task, and a real sweep now defaults to
    # capture_mechanism=True — this exact combination (review finding, confirmed by
    # execution: --json-schema is implemented as a synthetic StructuredOutput tool
    # call, and denying it makes the model's structured-output attempt fail with
    # "permission was denied") must never regress.
    argv = ClaudeCodeRunner(capture_mechanism=True)._argv(
        "p", "haiku", "low", json_schema={"type": "object"}
    )
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    i = argv.index("--disallowedTools")
    tools = argv[i + 1 : argv.index("--json-schema")]
    assert "StructuredOutput" not in tools


@pytest.mark.unit
def test_run_capture_mechanism_and_json_schema_together_extracts_structured_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A synthetic stream-json capture modeled on two real, directly-observed probes
    # (2026-07-02, v2.1.198): one where StructuredOutput fires cleanly (result
    # carries the schema-shaped JSON), one where denying it produces a
    # "permission was denied" text response instead of the tool_use block below —
    # this fixture is the FIRST (allowed) case, proving the parse path stays correct
    # once StructuredOutput is excluded from the deny list.
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "StructuredOutput",
                        "input": {"classifications": [{"idx": 0, "label": 1}]},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "tool_use_id": "toolu_1",
                        "type": "tool_result",
                        "content": "Structured output provided successfully",
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"classifications": [{"idx": 0, "label": 1}]}',
            "session_id": "s",
            "total_cost_usd": 0.01,
            "num_turns": 2,
            "usage": {},
            "structured_output": {"classifications": [{"idx": 0, "label": 1}]},
        },
    ]
    stream_text = "\n".join(json.dumps(e) for e in events)
    monkeypatch.setattr(
        "claude_ablation_lab.runner.subprocess.run",
        lambda *a, **k: _fake_proc(stream_text),
    )
    res = ClaudeCodeRunner(transcript_dir=tmp_path, capture_mechanism=True).run(
        "classify",
        model="haiku",
        effort="low",
        cwd=tmp_path,
        json_schema={"type": "object"},
    )
    assert res.status == "ok"
    assert res.tools_used == ("StructuredOutput",)
    assert json.loads(res.output) == {"classifications": [{"idx": 0, "label": 1}]}
