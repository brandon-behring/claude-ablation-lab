# Pressure-testing "default sonnet": does opus ever earn its keep?

**Status: concluded (2026-07-04).** Follows PR #15, where `ablation advise` found opus/max *ties*
sonnet/high on hard MDX authoring (0.978 = 0.978) at 3.6× cost. That settled the reflex on authoring —
but the honest open question remained: does the opus/max reflex earn its keep on genuinely **hard**
work, where a real quality gradient could exist? Rather than trust the downgrade rule, we adversarially
**pressure-tested** it: build the tasks *most likely to break* "sonnet is enough" and see whether sonnet
keeps holding.

**Answer: across three cleanly-checkable domains — authoring, debugging, hard math — there is zero opus
edge.** The only gradient ever observed is *haiku* occasionally slipping; opus never pulls ahead of
sonnet, and higher reasoning effort never helps. More important than the number, we found the
*structural* reason the search came up empty.

## The probes
Grid throughout: 3 models × {low, high, max} effort × 5 epochs = 45 cells per task. Multi-item tasks
score a smooth `k/N` fraction (far less noisy at low n than a single binary answer).

| Domain | Task | Result |
|---|---|---|
| **Authoring** | `books-validate` — fix a seeded-broken MDX chapter, graded by a 15-item validator (PR #14) | opus/max = sonnet/high = **0.978**, at 3.6× cost + ~200s latency; discriminates haiku (~0.88). No opus edge. |
| **Debugging** | `t7_find_bug` — name the single buggy line in each of 6 functions (easy→subtle) | **Saturates** — every tier ~1.0, haiku included. No opus edge. |
| **Hard math** | `t8_hard_math` — 6 hard problems solved **by hand, no tools** (e.g. digit sum of 2¹⁰⁰; 3²⁰⁰ mod 1000) | **Saturates** — sonnet = opus = 1.000, haiku 0.978 (its only slip: 3²⁰⁰ mod 1000, and at high/max effort, not low). No opus edge; effort useless. |

## The structural finding
We never found a task where opus beats sonnet — and it is not for lack of looking. There is a tension
baked into the method:

> A task's answer being **cleanly gradable** ⟺ it has a **determinate** right answer ⟺ it is within
> reach of **every** current model tier. The tasks where opus might plausibly have an edge — open-ended
> generation, judgment, taste, novel design, voice — are precisely those *without* a determinate answer,
> hence **un-gradable** objectively (you would need an LLM-judge or a human, both fuzzy and biased).
>
> **Checkable ⟹ easy ⟹ no opus edge. Opus's putative edge ⟹ open-ended ⟹ un-gradable by this harness.**

So **"default sonnet" is robustly safe for any verifiable work** — extraction, classification,
debugging, math, format-bound editing: no evidence opus earns its keep, and reserving `max` effort is
never justified on these shapes. Whether opus is worth its cost on *un-gradable* open-ended work is a
real question this harness structurally cannot answer; it would need a different, fuzzier instrument
(LLM-judge or pairwise preference) — recorded as future work, not a gap in the present result.

## Why the verdicts are trustworthy — the grader saga
Getting a *trustworthy* "no edge" was harder than getting the number. Single-turn free-form
checkable-answer grading proved a persistent minefield: **three separate grading confounds**, each of
which mis-scored a *correct verbose* answer as 0 and would have manufactured a false "downgrade to
haiku" verdict — each caught only by inspecting transcripts before trusting the score.

1. **Spurious JSON array** in the reasoning shadowed the real answer object (`lenient_json` returned the
   *first* JSON value it found).
2. **Markdown backticks** — `` `answer` `` — weren't stripped, so a wrapped-but-correct answer scored 0.
3. **First-number-on-the-line** (numeric) — `ANSWER 4: 3^200 mod 1000 = 1` parsed `3`, not `1`.

All three shared a **direction**: they penalized *verbose / high-effort* output (which opus produces
more of), biasing the very A/B under test. #3 was caught by a **pre-quota 3-voice adversarial review**
(codex + gemini + a blind Claude reviewer that ran the grader) — the discipline paid for itself: the
blind reviewer *proved* a fully-correct verbose block scored 2/6 and an all-wrong-finals block 6/6.

**The fix — a strict contract, unbiased by construction.** Rather than pile on extraction heuristics
(which breed the next confound), the numeric grader now requires a **bare integer** per answer; anything
else makes the whole cell `unparseable` — *excluded* from the mean, never a silent zero. Non-compliance
surfaces as a visible unparseable count, not a corrupted score. The t8 run confirmed it: **0
unparseable** (the strict prompt worked across every config), so the "no edge" is real, not a fourth
artifact.

## Methodology lessons (reusable)
- **Multi-item tasks (smooth `k/N`) discriminate far better than a single binary answer** at low epoch
  counts — the binary 0/1 is too coarse and too noisy (books-validate discriminated at n=3 *because* it
  had 15 items).
- **Free-form answer extraction is a minefield.** Every model formats differently; each quirk silently
  zeros correct answers, biased by *style*, not skill. Prefer a **strict contract** (bare answer;
  deviation → `unparseable`/excluded) over lenient heuristics — it is unbiased by construction.
- **Always inspect the score distribution + a few transcripts before trusting `advise`.** An
  all-or-nothing or suspiciously clean pattern is the tell for a grading artifact.
- **Run the adversarial review *before* spending quota**, not just before merge.

## Artifacts
`graders/exact_match.py`, `graders/exact_match_set.py`; tasks `t7_find_bug`, `t8_hard_math`; grid
`grids/pressure-test.yaml`; ledgers `results/pressure-test*.jsonl` (gitignored). Prior authoring probe:
`docs/design/2026-07-03_spend-audit.md` + PR #14.
