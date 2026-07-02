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
    #: Tools this task needs beyond the always-allowed ``Skill`` (e.g. an agentic task's
    #: ``[Bash, Read, Write, Edit, Glob, Grep]``). Empty for every non-agentic task today —
    #: the hermetic default (deny all but Skill) already covers them. See :mod:`prepare`.
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
        tools=tuple(str(tool) for tool in (raw.get("tools") or ())),
    )


def load_all(directory: Path | str) -> list[Task]:
    """Load every ``*.yaml`` task spec in ``directory`` (sorted by filename)."""
    return [load_task(path) for path in sorted(Path(directory).glob("*.yaml"))]


def _render(prompt: str, params: Mapping[str, Any]) -> str:
    """Substitute ``{key}`` with each string param value (brace-safe; no format)."""
    for key, value in params.items():
        if isinstance(value, str):
            prompt = prompt.replace("{" + key + "}", value)
    return prompt
