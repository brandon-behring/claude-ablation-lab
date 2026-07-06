"""Gemini judge transport via the ``agy`` CLI (Antigravity).

The standalone ``gemini`` CLI lost free-tier eligibility (2026-06-18), so the
gemini voice runs through ``agy`` — the same substitution the lever-plugin
scripts made. Two agy-specific constraints shape the argv (both proven there):
the prompt is bound as ``--prompt=<value>`` (flag-injection guard; agy does not
read stdin reliably in print mode), and there is no read-only flag — isolation is
structural via a throwaway temp cwd. The subprocess timeout gets +15 s kill
headroom beyond ``--print-timeout`` so a gracefully-exiting agy is not
misreported as a harness timeout.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from claude_ablation_lab.judge import PROMPT_TEMPLATE_VERSION, JudgeCall
from claude_ablation_lab.judges._parse import PARSER_VERSION, extract_verdict
from claude_ablation_lab.judges._transport import run_cli

__all__ = ["GeminiJudge"]

#: Seconds past ``--print-timeout`` before SIGKILL — lets agy flush and exit.
AGY_KILL_HEADROOM_S = 15.0


@dataclass(frozen=True, slots=True)
class GeminiJudge:
    """Pairwise judge over ``agy`` (pinned model display string)."""

    model: str = "Gemini 3.1 Pro (High)"

    @property
    def judge_id(self) -> str:
        return "gemini"

    @property
    def version(self) -> str:
        slug = self.model.lower().replace(" ", "-").replace("(", "").replace(")", "")
        return f"{PROMPT_TEMPLATE_VERSION}+{PARSER_VERSION}/gemini:{slug}"

    def judge(self, prompt: str, *, timeout_s: float = 240.0) -> JudgeCall:
        argv = [
            "agy",
            f"--prompt={prompt}",
            "--model",
            self.model,
            "--print-timeout",
            f"{int(timeout_s)}s",
        ]
        with tempfile.TemporaryDirectory(prefix="judge-gemini-") as tmp:
            outcome = run_cli(argv, timeout_s=timeout_s + AGY_KILL_HEADROOM_S, cwd=Path(tmp))

        if outcome.status != "ok":
            detail = (outcome.stderr or outcome.stdout)[-500:]
            return JudgeCall(
                status=outcome.status,  # type: ignore[arg-type]
                reason=detail,
                latency_s=outcome.latency_s,
                raw_text=outcome.stdout,
            )
        text = outcome.stdout.strip()
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
