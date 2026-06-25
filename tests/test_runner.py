"""Runner parser + argv/env tests against captured `claude --output-format json` fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_ablation_lab.runner import (
    AUTH_ENV_STRIP,
    ClaudeCodeRunner,
    classify_status,
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


@pytest.mark.unit
def test_env_strips_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in AUTH_ENV_STRIP:
        monkeypatch.setenv(key, "sk-should-be-removed")
    env = ClaudeCodeRunner()._env()
    for key in AUTH_ENV_STRIP:
        assert key not in env
