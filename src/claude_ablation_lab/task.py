"""Task spec model + YAML loader.

A :class:`Task` is the static description of one gradeable unit: an ``id``, a
``grader`` ref (resolved via :func:`claude_ablation_lab.graders.get_grader`), a
``mode`` (single-turn or agent-loop), an optional ``infra_repo`` (whose config
the task exercises; ``null`` for infra-agnostic tasks like T1), a ``gold``
reference for grading, and free-form ``params`` for task-type-specific prep
(e.g. T1's parquet path / subsample size; T3's source text).

``prompt`` may embed ``{param}`` placeholders that are filled from ``params`` at
load time via plain substitution — *not* :meth:`str.format` — so JSON braces in
a prompt (e.g. ``{"claims": …}``) are left untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from claude_ablation_lab.runner import KNOWN_BUILTIN_TOOLS

__all__ = ["Task", "TaskMode", "REQUIRED_KEYS", "load_task", "load_all"]

TaskMode = Literal["single", "agent"]
REQUIRED_KEYS = ("id", "domain", "grader", "mode")


@dataclass(frozen=True, slots=True)
class Task:
    """Static spec for one gradeable task (loaded from ``tasks/*.yaml``)."""

    id: str
    domain: str
    grader: str
    mode: TaskMode
    prompt: str = ""
    infra_repo: str | None = None
    gold: Mapping[str, Any] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)
    timeout_s: float = 900.0
    tags: tuple[str, ...] = ()
    #: Tools this task needs beyond the always-allowed ``Skill``/``StructuredOutput``
    #: (e.g. an agentic task's ``[Bash, Read, Write]``). Empty for every non-agentic
    #: task today — the hermetic default already covers them. Validated at load time
    #: against ``KNOWN_BUILTIN_TOOLS``: an unrecognized name here would silently
    #: relax nothing (`prepare.py`'s subtraction just never matches it) while the CLI
    #: reports the tools as relaxed — a fail-open the loader closes instead. See
    #: :mod:`prepare`.
    tools: tuple[str, ...] = ()


def load_task(path: Path | str) -> Task:
    """Load and validate a single task spec from a YAML file."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"task spec {path} is not a mapping")
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ValueError(f"task spec {path} missing required keys: {missing}")
    mode = raw["mode"]
    if mode not in ("single", "agent"):
        raise ValueError(f"task spec {path}: mode must be 'single' or 'agent', got {mode!r}")

    params = dict(raw.get("params") or {})
    return Task(
        id=str(raw["id"]),
        domain=str(raw["domain"]),
        grader=str(raw["grader"]),
        mode=mode,
        prompt=_render(str(raw.get("prompt", "")), params),
        infra_repo=str(raw["infra_repo"]) if raw.get("infra_repo") else None,
        gold=dict(raw.get("gold") or {}),
        params=params,
        timeout_s=float(raw.get("timeout_s", 900.0)),
        tags=tuple(str(tag) for tag in (raw.get("tags") or ())),
        tools=_load_tools(path, raw.get("tools")),
    )


def _load_tools(path: Path, raw_tools: object) -> tuple[str, ...]:
    """Validate + parse a task's ``tools:`` list (fail loud, never a silent no-op).

    Two footguns this closes: a bare YAML scalar (``tools: Bash`` parses as the
    string ``"Bash"``, and iterating a string yields its *characters* — a real,
    reproduced failure mode) must be a list; and an unrecognized tool name would
    otherwise silently relax nothing (``prepare.py``'s subtraction never matches
    it) while the CLI still reports the tools as relaxed.
    """
    if raw_tools is None:
        return ()
    if not isinstance(raw_tools, list):
        raise ValueError(
            f"task spec {path}: 'tools' must be a YAML list of tool names, got {raw_tools!r}"
        )
    tools = tuple(str(tool) for tool in raw_tools)
    unknown = sorted(set(tools) - set(KNOWN_BUILTIN_TOOLS))
    if unknown:
        raise ValueError(
            f"task spec {path}: unknown tool(s) in 'tools': {unknown} "
            f"(known: {sorted(KNOWN_BUILTIN_TOOLS)})"
        )
    return tools


def load_all(directory: Path | str) -> list[Task]:
    """Load every ``*.yaml`` task spec in ``directory`` (sorted by filename)."""
    return [load_task(path) for path in sorted(Path(directory).glob("*.yaml"))]


def _render(prompt: str, params: Mapping[str, Any]) -> str:
    """Substitute ``{key}`` with each string param value (brace-safe; no format)."""
    for key, value in params.items():
        if isinstance(value, str):
            prompt = prompt.replace("{" + key + "}", value)
    return prompt
