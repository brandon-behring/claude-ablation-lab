# Cost-benchmark map — extending the overpay verdict to *discriminating* work

**Status: BUILT + RUN DONE (t5, 2026-07-03).** The pilot task below shipped as `t5_books_validate`
(single-turn) + `t6_books_validate_agent` (agentic) with the hardened checklist grader, the
`examples/books-validate/` fixture, `grids/books-pilot.yaml`, an adversarial test battery, and a
blind-solve fairness pass (two ambiguous items caught + fixed). **The t5 quota run (27/27 cells)
settled the question: the task discriminates — haiku sits ~0.10 below the field, so it is genuinely
not saturated — but the opus/max reflex does NOT earn its keep. opus/max (0.978) ties sonnet/high
(0.978) to four decimals while costing 3.6× more and running ~200s slower per run; `advise` → use
sonnet/high. Higher `max` effort added no gain on the effort-capable models (sonnet/max == sonnet/high,
opus/max == opus/low); Haiku has no effort parameter, so its low/high/max spread is n=3 noise.** (t6 stays sandbox-gated — not run.) Full rationale in
`docs/METHODOLOGY.md` (Discriminating tasks) and `docs/design/2026-07-03_spend-audit.md`. Original
design notes preserved below.

**Status: design + grounding (2026-07-02).** Phase 1 (`ablation advise`) shipped the cost verdict and,
on the committed showcase ledger, a real finding: **opus→haiku is 11–15× cheaper by API-equivalent
proxy for +0.000 quality** on `t3`/`t4`. But those tasks are *saturated single-turn* probes — every
config already scores 1.000 — so they prove the overpay on **easy** work only. Whether the Opus/max reflex is wasteful on **hard** work (where a real
quality gradient exists) needs a task that **discriminates** configs. This doc maps the candidates and
records what grounding settled, so the build is a decision, not a discovery.

## Why saturation, not the method, is the gap
`advise` already answers "cheapest config within `--margin` of the reflex." On a task with a genuine
gradient it will instead say "opus/high earns its keep here — the cheaper configs fall outside the
margin." We just have no such task yet. The map's job: add one, cheaply, and feed its ledger to `advise`.

## Candidate discriminating tasks

| # | Real work | Task shape | Grader | Separates? | Build cost | Notes |
|---|---|---|---|---|---|---|
| **1** | **Constrained MDX repair** | fix seeded MDX convention violations until a validator passes | exit-code=error-count (`books_validate`, new) | *plausible* — id-matching against `labels.json`/`references.json` has a difficulty gradient | LOW (single-turn) / MED (agentic) | **the pilot** — see below |
| 2 | Engineering | make a seeded-broken test green (`book-scaffold tests/*.test.mjs` or a pytest holdout) | exit-code / TAP-parse | *plausible-high* — classic SWE gradient | MED | reuses the same exit-code grader as #1 → the consolidation trigger |
| 3 | Editorial verification | classify which claims need a `<Tag kind="official">` source URL | existing `classification` | depends on label difficulty | LOW | cheapest; single-turn, no new grader |

Separability was honestly **unknown** for every row until a quota run — that was the whole point of building one.
**Row 1 has now run (t5):** it *does* separate (haiku ~0.10 below the field), confirming the instrument measures
real quality on constrained MDX repair — but the separation is *haiku-vs-the-rest*, not *opus-above-sonnet*: sonnet/high,
sonnet/max, opus/low and opus/max all tie at 0.978. Rows 2–3 remain unrun.

## Grounding that settled the pilot design

- **Agentic file-editing tasks are gradable with no new grader capability.** `orchestrate._extract_artifact`
  (orchestrate.py:205) reads a produced/edited file back from the cell's `cwd` as the graded `output`
  (prefers `cwd/<prepared.artifact>`, else the most-recently-modified match). This is exactly how T2's
  agent-written `research_plan.md` reaches its validator. So an agentic "edit the chapter in a worktree,
  run validate, iterate" task grades identically to a single-turn "return the corrected chapter" one —
  the grader validates a returned MDX string either way. The choice is about faithfulness and cost, not
  feasibility.

- **Single-turn vs agentic — the pilot's one real fork.**
  - *Single-turn* (`mode: single`, T3's shape): the broken chapter + the valid ids live in the prompt;
    the model returns the corrected MDX; the grader validates it. No worktree, no `setup.sh`, no grid
    variant, cheap cells. De-risks "does authoring discriminate?" at minimum cost.
  - *Agentic* (`mode: agent`, T4's shape): a `.books-validate@HEAD` worktree fixture built by a
    ~35-line `setup.sh` (mirroring `examples/demo-infra/setup.sh`, 54 lines); `tools: [Read, Edit, Write,
    Bash]`; the agent runs the validator itself and iterates; 600 s cells. More faithful to real
    authoring and exercises the D6 tool-policy + `stream-json` mechanism capture — but more machinery and
    more quota, for the **same discrimination signal**.
  - **Recommendation:** pilot single-turn first (it answers the question); promote to agentic only if the
    single-turn result is interesting enough to warrant the richer, costlier version.

- **Validator fidelity vs. hermeticity.** The real `book-scaffold validate`
  (`~/book-scaffold-astro/package/scripts/validate.mjs:476` → `process.exit(errors.length)`) is a Node
  CLI needing the scaffold installed — not CI-portable (GitHub Actions installs neither `claude` nor the
  scaffold). A vendored **`validate.py`** faithful to its `<XRef id>`∈`labels.json`, `<Cite bibkey>`∈
  `references.json`, and `<BookLink>` requires-both checks — same exit-code=count contract — keeps the
  fixture self-contained and the grader test hermetic. It is an honest **subset** of the real validator's
  checks; a drop-in swap to real `book-scaffold validate` is a later option once the fixture ships a
  pinned scaffold.

## The pilot, ready to build (gated on a discriminating-run decision)
`t5_books_validate` (single-turn): `examples/books-validate/` ships `labels.json`, `references.json`, a
seeded-broken `chapter.mdx` (typo'd XRef id, nonexistent Cite key, `<BookLink>` missing `to=`, out-of-range
`<CodeRef>`), and `validate.py`. Grader `books_validate` writes the returned MDX to a temp file and runs
`validate.py` against it (labels/references beside it), scoring `max(0, 1 − errors/CAP)`. **The build
produces no finding by itself** — the payoff is a model×effort run (real quota, a separate explicit go)
whose ledger goes straight into `ablation advise`.

## Out of scope / deferred
- Engineering (#2) triggers the exit-code grader's generalization (run any checker in the cell, score by
  count) — deferred to its second real use, not pre-built.
- Editorial (#3) is the cheap single-turn follow-up on the existing `classification` grader.
- Real `book-scaffold validate` (vs. the vendored subset) — a fidelity upgrade once the fixture pins the
  scaffold.
