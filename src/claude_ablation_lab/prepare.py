"""Turn a static :class:`~claude_ablation_lab.task.Task` into a runnable cell.

A :class:`Prepared` bundles everything the orchestrator needs to *run* a cell and
then *grade* it: the live ``prompt``, the ``gold`` the grader expects, an optional
``json_schema`` (structured output), an optional ``artifact`` (a file the agentic
run produces, captured as the gradeable output instead of stdout), and an optional
``permission_mode`` (agentic tasks need a non-interactive mode).

Preparation dispatches on ``task.grader`` — in v1 the grader and the task type are
1:1 (``classification``→T1, ``validator``→T2, ``anchor``→T3). T1 is built *live*
and *deterministically* (seeded subsample of the holdout parquet) so a later
re-grade reproduces the identical gold without storing it.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from claude_ablation_lab.runner import HERMETIC_DISALLOWED_TOOLS
from claude_ablation_lab.task import Task

__all__ = [
    "Prepared",
    "prepare_task",
    "spec_sha",
    "DEFAULT_ARTIFACT",
    "DEFAULT_AGENT_PERMISSION_MODE",
]

#: Where an agentic task is expected to write its artifact (overridable per task).
DEFAULT_ARTIFACT = "research_plan.md"
#: Non-interactive permission mode for agentic tasks (so file writes don't block).
DEFAULT_AGENT_PERMISSION_MODE = "acceptEdits"


@dataclass(frozen=True, slots=True)
class Prepared:
    """A task made concrete for one run+grade cycle.

    ``spec_sha`` fingerprints the *gradeable identity* (prompt + json_schema + gold
    + declared tools). It is stamped on every ledger row and gates resume/re-grade:
    if a task's prompt, schema, seed/subsample, gold, or tool policy changes, the
    fingerprint changes, so a stored run is never silently reused for — nor graded
    against — a different spec (the Phase-3 analog of the run/grade honesty split).
    Tool policy joined this fingerprint in D6 (review finding): unlike
    ``permission_mode`` (execution friction only), a task's tool set changes *what
    the cell can even do* — reusing a run from before a ``tools:`` change would
    silently compare results measured under different tool boundaries.
    """

    prompt: str
    gold: Mapping[str, Any] = field(default_factory=dict)
    json_schema: dict[str, Any] | None = None
    artifact: str | None = None
    permission_mode: str | None = None
    #: Per-cell ``--disallowedTools`` override. ``None`` → the runner's own default
    #: (the hermetic tool-minimal set). Not itself hashed into ``spec_sha`` — it's a
    #: pure function of ``Task.tools`` (+ the catalog), and ``Task.tools`` is what's
    #: hashed instead, as the actual declared source of intent.
    disallowed_tools: tuple[str, ...] | None = None
    spec_sha: str = ""


def spec_sha(
    prompt: str,
    json_schema: dict[str, Any] | None,
    gold: Mapping[str, Any],
    tools: tuple[str, ...] = (),
) -> str:
    """16-hex fingerprint of a cell's gradeable inputs (prompt + schema + gold + tools)."""
    blob = json.dumps(
        {"prompt": prompt, "schema": json_schema, "gold": gold, "tools": tools},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _prepare_classification(task: Task) -> Prepared:
    """T1: seeded balanced subsample → batched prompt + verdict schema + idx→label gold.

    Holdout resolution order: ``$T1_HOLDOUT_PATH`` (the documented escape hatch — it
    must beat a task-pinned path, or the override is dead exactly when the pinned
    default doesn't exist on this machine), then ``params.gold_parquet``, then the
    package default.
    """
    from claude_ablation_lab import t1_dataset

    params = task.params
    env_path = os.environ.get("T1_HOLDOUT_PATH")
    raw_path = env_path or params.get("gold_parquet")
    path = Path(os.path.expanduser(str(raw_path))) if raw_path else t1_dataset.DEFAULT_HOLDOUT_PATH
    frame = t1_dataset.subsample(
        t1_dataset.load_holdout(path),
        n=int(params.get("subsample_n", 60)),
        seed=int(params.get("seed", 42)),
    )
    return Prepared(
        prompt=t1_dataset.build_prompt(frame),
        gold={"labels": t1_dataset.build_gold(frame)},
        json_schema=t1_dataset.VERDICT_JSON_SCHEMA,
    )


def _prepare_validator(task: Task) -> Prepared:
    """T2: static ``/research-plan`` prompt; grade the captured ``research_plan.md``.

    Agentic tasks need real tools, so this is the one preparer that relaxes the
    hermetic default: ``disallowed_tools`` = the base catalog *minus* whatever the
    task declares via ``tools:`` (e.g. Bash/Read/Write/Edit for a file-writing skill).
    A task declaring no ``tools`` gets the unmodified hermetic default back — agentic
    but tool-minimal, which the CLI warns about (see ``cli/main.py``).
    """
    needed = set(task.tools)
    effective = tuple(t for t in HERMETIC_DISALLOWED_TOOLS if t not in needed)
    return Prepared(
        prompt=task.prompt,
        gold=task.gold,
        artifact=str(task.params.get("artifact", DEFAULT_ARTIFACT)),
        permission_mode=str(task.params.get("permission_mode", DEFAULT_AGENT_PERMISSION_MODE)),
        disallowed_tools=effective,
    )


def _prepare_anchor(task: Task) -> Prepared:
    """T3: fully static — the loaded task already carries prompt + gold."""
    return Prepared(prompt=task.prompt, gold=task.gold)


#: Fixture files the MODEL is allowed to see (the agent-visible whitelist). The answer key
#: (expected.json, check.py) is excluded — it is grader-only. Hashing exactly these into gold means
#: a change to task *material* changes spec_sha (→ re-run), parallel to the grader ``version``
#: hashing the *rubric* (→ re-grade). Kept in sync with examples/books-validate/setup.sh.
_BOOKS_VISIBLE = (
    "chapter.mdx",
    "labels.json",
    "references.json",
    "files.json",
    "CLAUDE.md",
    "validate_fixture.py",
)


def _books_fixture_sha(root: Path) -> str:
    """16-hex hash of the agent-visible fixture files (missing file → its name only, still stable)."""
    h = hashlib.sha256()
    for name in _BOOKS_VISIBLE:
        h.update(name.encode("utf-8"))
        p = root / name
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _prepare_books_validate(task: Task) -> Prepared:
    """t5/t6: fix a seeded-broken MDX chapter to pass the editorial conventions.

    ``mode: single`` embeds the chapter + conventions + registries in the prompt and grades the
    returned MDX; ``mode: agent`` gives a short instruction and grades the chapter edited in a
    worktree (the fixture repo built by ``examples/books-validate/setup.sh``). The fixture root is
    read from ``params.fixture_root`` (default: the packaged ``examples/books-validate``). Its
    content hash goes into ``gold`` so a fixture edit changes ``spec_sha`` and never silently reuses
    a stored run.
    """
    from claude_ablation_lab.graders.books_validate import DEFAULT_FIXTURE_ROOT

    root = Path(os.path.expanduser(str(task.params.get("fixture_root", DEFAULT_FIXTURE_ROOT))))
    gold = {"fixture_root": str(root), "fixture_sha": _books_fixture_sha(root)}
    if task.mode == "agent":
        needed = set(task.tools)
        effective = tuple(t for t in HERMETIC_DISALLOWED_TOOLS if t not in needed)
        return Prepared(
            prompt=_books_agent_prompt(),
            gold=gold,
            artifact=str(task.params.get("artifact", "chapter.mdx")),
            permission_mode=str(task.params.get("permission_mode", DEFAULT_AGENT_PERMISSION_MODE)),
            disallowed_tools=effective,
        )
    return Prepared(prompt=_books_single_prompt(root), gold=gold)


def _books_single_prompt(root: Path) -> str:
    """Assemble the single-turn prompt: conventions + registries + the chapter, inline."""

    def read(name: str) -> str:
        return (root / name).read_text(encoding="utf-8")

    return (
        f"{read('CLAUDE.md')}\n\n"
        "## Registries\n\n"
        f"labels.json (valid XRef ids):\n{read('labels.json')}\n\n"
        f"references.json (valid Cite keys):\n{read('references.json')}\n\n"
        f"files.json (source files and their line counts):\n{read('files.json')}\n\n"
        "## The chapter to correct\n\n"
        "Apply every convention above to the chapter below. Preserve all prose and headings; change "
        "only tags. Return the COMPLETE corrected chapter and nothing else — no diff, no "
        "commentary.\n\n"
        f"{read('chapter.mdx')}"
    )


def _books_agent_prompt() -> str:
    """The agentic instruction — the chapter + rules live in the worktree, not the prompt."""
    return (
        "This project's chapter.mdx has editorial-convention violations. Read CLAUDE.md for the "
        "conventions and labels.json / references.json / files.json for what is valid. Edit "
        "chapter.mdx IN PLACE to satisfy every convention — do not create a copy or a new file, and "
        "do not add, remove, or retitle headings. `python3 validate_fixture.py chapter.mdx` reports "
        "structural errors but does not check every convention, so read the chapter carefully too. "
        "Stop when the chapter satisfies every convention in CLAUDE.md."
    )


_PREPARERS = {
    "classification": _prepare_classification,
    "validator": _prepare_validator,
    "anchor": _prepare_anchor,
    "anchor_strict": _prepare_anchor,  # same static prep; only the grader differs
    "books_validate": _prepare_books_validate,
}


def prepare_task(task: Task) -> Prepared:
    """Build the :class:`Prepared` cell for ``task`` (dispatch on its grader)."""
    try:
        preparer = _PREPARERS[task.grader]
    except KeyError:
        raise ValueError(
            f"no preparer for grader {task.grader!r} (known: {', '.join(_PREPARERS)})"
        ) from None
    prep = preparer(task)
    return replace(prep, spec_sha=spec_sha(prep.prompt, prep.json_schema, prep.gold, task.tools))
