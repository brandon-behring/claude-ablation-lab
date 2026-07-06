"""OpenAI Codex CLI judge transport.

Argv shape proven by the lever-plugin scripts (reimplemented here — this repo is
self-contained): ``codex exec -s read-only --ephemeral --skip-git-repo-check`` in
a throwaway cwd, final answer written to a ``-o`` tempfile (stdout is a fallback).
Model AND reasoning effort are pinned explicitly — never inherited from
``~/.codex/config.toml`` (whose default is xhigh: documented-slow, and silent
config drift would change the judge without changing ``version``). The
subscription CLI reports no token usage or cost — those stay ``None`` (not
measured) on judge rows; latency and output bytes are what is real.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from claude_ablation_lab.judge import PROMPT_TEMPLATE_VERSION, JudgeCall
from claude_ablation_lab.judges._parse import PARSER_VERSION, extract_verdict
from claude_ablation_lab.judges._transport import run_cli

__all__ = ["CodexJudge"]


@dataclass(frozen=True, slots=True)
class CodexJudge:
    """Pairwise judge over ``codex exec`` (pinned model + effort)."""

    model: str = "gpt-5.5"
    effort: str = "medium"

    @property
    def judge_id(self) -> str:
        return "codex"

    @property
    def version(self) -> str:
        return f"{PROMPT_TEMPLATE_VERSION}+{PARSER_VERSION}/codex:{self.model}:{self.effort}"

    def judge(self, prompt: str, *, timeout_s: float = 240.0) -> JudgeCall:
        with tempfile.TemporaryDirectory(prefix="judge-codex-") as tmp:
            out_path = Path(tmp) / "last_message.txt"
            argv = [
                "codex",
                "exec",
                "-s",
                "read-only",
                "--ephemeral",
                "--skip-git-repo-check",  # the throwaway cwd is not a git repo
                "-c",
                f"model={self.model}",
                "-c",
                f"model_reasoning_effort={self.effort}",
                "-o",
                str(out_path),
                "--",  # end-of-options: a '-'-leading prompt is never reparsed
                prompt,
            ]
            outcome = run_cli(argv, timeout_s=timeout_s, cwd=Path(tmp))
            answer = ""
            if out_path.is_file():
                answer = out_path.read_text(encoding="utf-8", errors="replace")

        if outcome.status != "ok":
            detail = (outcome.stderr or outcome.stdout)[-500:]
            return JudgeCall(
                status=outcome.status,  # type: ignore[arg-type]
                reason=detail,
                latency_s=outcome.latency_s,
                raw_text=outcome.stdout,
            )
        text = answer.strip() or outcome.stdout  # -o file wins; stdout is the fallback
        parsed = extract_verdict(text)
        if parsed is None:
            return JudgeCall(
                status="unparsed",
                latency_s=outcome.latency_s,
                output_bytes=len(text.encode("utf-8")),
                raw_text=text,
            )
        verdict, reason = parsed
        return JudgeCall(
            status="ok",
            verdict=verdict,
            reason=reason,
            latency_s=outcome.latency_s,
            output_bytes=len(text.encode("utf-8")),
            raw_text=text,
        )
