# books-validate — a *discriminating* constrained-MDX-repair benchmark

Where `t3`/`t4` are **saturated** (every config scores ~1.0, proving the harness plumbing), this
fixture is built to **discriminate**: fix a seeded-broken MDX chapter so it satisfies book-scaffold
editorial conventions, on a difficulty ladder that a stronger model should climb further. It exists
to answer the question the [spend audit](../../docs/design/2026-07-03_spend-audit.md) left open —
does the opus/max reflex actually earn its keep on *hard constrained-repair* work (not open-ended authoring), or does the cheaper config
tie it here too?

## Two task shapes, one fixture

| Task | Mode | How the chapter reaches the model | How it is graded |
|------|------|-----------------------------------|------------------|
| `t5_books_validate` | single-turn | chapter + conventions + registries embedded in the prompt | model returns the corrected MDX on stdout |
| `t6_books_validate_agent` | agentic | a `.books-validate@v1` worktree (`setup.sh`); the agent edits `chapter.mdx` and can run `validate_fixture.py` | the edited file is read back |

Both are scored identically by the **`books_validate` grader**, which runs `check.py` against the
returned/edited chapter from the canonical copy of this directory.

## The two checkers (do not confuse them)

- **`validate_fixture.py`** — *agent-visible*. A faithful, self-contained subset of book-scaffold's
  `validate.mjs`: unknown `<XRef>` ids / `<Cite>` keys, out-of-range `<CodeRef>`, malformed
  `<BookLink>`. Exit code = violation count. It is a genuine helper — and, like the real tool, it is
  **blind to semantics**: a well-formed id pointing at the wrong section, or a prose citation with no
  tag, passes here.
- **`check.py` + `expected.json`** — *grader-only, never shipped to the model*. The 15-item checklist
  (`labels`/`references`/`files` validity **plus** correct referents, a required citation addition,
  and tripwires that must survive). `<XRef>` items give partial credit (0 / ½ / 1) for a
  valid-but-wrong-family id; census items are excess-only (anti-spray). The gap between "makes
  `validate_fixture.py` pass" (~0.77) and "understands the chapter" (1.0) is the discrimination
  signal.

## Scoring gradient (verified)

`empty / delete-all → 0.0` · `do-nothing (seeded) → 0.5` · `fix only what the fidelity validator
sees → 0.77` · `full understanding (gold) → 1.0`. The gold fix lives at
`tests/fixtures/books_validate_gold.mdx` (never shipped to the model).

## Anti-gaming (from a pre-build adversarial design review)

Deletion can't beat honest work (checklist, not violation-count); an empty artifact scores 0
(census floored at ≥1); spraying valid ids fails the census; the grader trusts only the final
`CHECK PASSED` line cross-checked against the exit code (no echo-injection); the fixture's content
hash enters `spec_sha` and the grader `version` (no silent metric-mixing on a fixture edit).

## Running (build + dry-run only — the model×effort run is a separate, explicit quota go)

```
examples/books-validate/setup.sh                              # materialise .books-validate@v1 (t6)
ablation run tasks/ grids/books-pilot.yaml --task t5_books_validate --dry-run
```
