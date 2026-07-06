"""Judge transports + a lazy registry (mirrors :mod:`claude_ablation_lab.graders`).

:func:`get_judge` resolves a judge name to an instance, importing the concrete
transport on demand. Both transports shell out to external subscription CLIs
(OpenAI ``codex``, Gemini via ``agy``) — cross-vendor by design, so no Anthropic
contestant is ever judged by itself or a sibling (self-preference bias).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_ablation_lab.judge import Judge

__all__ = ["get_judge", "JUDGE_NAMES"]

JUDGE_NAMES = ("codex", "gemini")


def get_judge(name: str) -> Judge:
    """Return a judge instance by name (``codex`` / ``gemini``)."""
    if name == "codex":
        from claude_ablation_lab.judges.codex import CodexJudge

        return CodexJudge()
    if name == "gemini":
        from claude_ablation_lab.judges.gemini import GeminiJudge

        return GeminiJudge()
    raise ValueError(f"unknown judge: {name!r} (known: {', '.join(JUDGE_NAMES)})")
