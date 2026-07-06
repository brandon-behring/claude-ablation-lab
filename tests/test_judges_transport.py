"""Judge transports (codex/agy) against a MOCKED subprocess — argv pins, fallback
order, failure taxonomy. No live CLI is ever invoked in unit tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from claude_ablation_lab.judge import Judge
from claude_ablation_lab.judges import JUDGE_NAMES, get_judge
from claude_ablation_lab.judges.codex import CodexJudge
from claude_ablation_lab.judges.gemini import GeminiJudge

_VERDICT = '{"winner": "A", "reason": "clearer"}'


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch subprocess.run inside the shared transport; capture the call."""
    seen: dict[str, Any] = {"proc": _Proc(stdout=_VERDICT)}

    def fake_run(argv: list[str], **kwargs: Any) -> _Proc:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        effect = seen.get("effect")
        if effect is not None:
            raise effect
        proc = seen["proc"]
        write = seen.get("write_out_file")
        if write is not None:
            out_idx = argv.index("-o") + 1
            Path(argv[out_idx]).write_text(write, encoding="utf-8")
        return proc

    monkeypatch.setattr("claude_ablation_lab.judges._transport.subprocess.run", fake_run)
    return seen


@pytest.mark.unit
def test_registry_resolves_protocol_instances() -> None:
    for name in JUDGE_NAMES:
        judge = get_judge(name)
        assert isinstance(judge, Judge)
        assert judge.judge_id == name
        assert judge.version
    with pytest.raises(ValueError, match="unknown judge"):
        get_judge("claude")  # an Anthropic judge would be a contestant — never valid


@pytest.mark.unit
def test_codex_argv_pins_model_and_effort(captured: dict[str, Any]) -> None:
    call = CodexJudge().judge("PROMPT", timeout_s=240.0)
    argv = captured["argv"]
    assert argv[:6] == ["codex", "exec", "-s", "read-only", "--ephemeral", "--skip-git-repo-check"]
    # Both pins present — silent ~/.codex config drift must be impossible.
    assert "model=gpt-5.5" in argv
    assert "model_reasoning_effort=medium" in argv
    # End-of-options guard: the prompt is the final element, after "--".
    assert argv[-2:] == ["--", "PROMPT"]
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL  # codex stdin deadlock guard
    assert captured["kwargs"]["timeout"] == 240.0
    assert call.status == "ok"
    assert call.verdict == "A"


@pytest.mark.unit
def test_codex_prefers_output_file_over_stdout(captured: dict[str, Any]) -> None:
    captured["proc"] = _Proc(stdout='{"winner": "B"}')
    captured["write_out_file"] = _VERDICT  # the -o file carries the real answer
    call = CodexJudge().judge("p")
    assert call.verdict == "A"  # file wins
    captured["write_out_file"] = None
    assert CodexJudge().judge("p").verdict == "B"  # empty file -> stdout fallback


@pytest.mark.unit
def test_gemini_argv_shape(captured: dict[str, Any]) -> None:
    call = GeminiJudge().judge("PROMPT", timeout_s=200.0)
    argv = captured["argv"]
    assert argv[0] == "agy"
    assert argv[1] == "--prompt=PROMPT"  # bound form: flag-injection guard
    assert argv[2:4] == ["--model", "Gemini 3.1 Pro (High)"]
    assert argv[4:6] == ["--print-timeout", "200s"]
    assert captured["kwargs"]["timeout"] == 200.0 + 15.0  # kill headroom
    assert call.status == "ok"


@pytest.mark.unit
def test_nonzero_exit_is_error_and_never_parsed(captured: dict[str, Any]) -> None:
    # stdout carries a perfectly valid verdict — it must NOT be trusted on exit 1.
    captured["proc"] = _Proc(returncode=1, stdout=_VERDICT, stderr="quota exceeded")
    call = GeminiJudge().judge("p")
    assert call.status == "error"
    assert call.verdict is None
    assert "quota exceeded" in call.reason


@pytest.mark.unit
def test_timeout_and_missing_binary_taxonomy(captured: dict[str, Any]) -> None:
    captured["effect"] = subprocess.TimeoutExpired(cmd="codex", timeout=1)
    assert CodexJudge().judge("p").status == "timeout"
    captured["effect"] = FileNotFoundError("no agy")
    assert GeminiJudge().judge("p").status == "missing"


@pytest.mark.unit
def test_narration_without_schema_json_is_unparsed(captured: dict[str, Any]) -> None:
    captured["proc"] = _Proc(stdout="Response A is better in my opinion.")
    call = CodexJudge().judge("p")
    assert call.status == "unparsed"
    assert call.verdict is None
    assert call.raw_text  # persisted for transcript inspection


@pytest.mark.unit
def test_versions_fingerprint_template_parser_and_pins() -> None:
    assert CodexJudge().version == "pj-v1+vp-v1/codex:gpt-5.5:medium"
    assert GeminiJudge().version == "pj-v1+vp-v1/gemini:gemini-3.1-pro-high"
    # A different pin is a different version -> re-judges automatically.
    assert CodexJudge(effort="low").version != CodexJudge().version
