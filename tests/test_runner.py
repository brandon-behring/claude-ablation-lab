"""Runner parser + argv/env tests against captured `claude --output-format json` fixtures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from claude_ablation_lab.runner import (
    AUTH_ENV_STRIP,
    ClaudeCodeRunner,
    classify_status,
    extract_json,
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
    from claude_ablation_lab.runner import HERMETIC_DISALLOWED_TOOLS

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
