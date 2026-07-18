# Pressure-testing "default sonnet": does opus ever earn its keep?

**Status: concluded (2026-07-04).** Follows PR #15, where `ablation advise` found opus/max *ties*
sonnet/high on constrained MDX repair (0.978 = 0.978) at 3.6× cost. That addressed the reflex on
*bounded repair* (not open-ended authoring) — but the honest open question remained: does the opus/max
reflex earn its keep on genuinely **hard** work, where a real quality gradient could exist? Rather than trust the downgrade rule, we adversarially
**pressure-tested** it: build the tasks *most likely to break* "sonnet is enough" and see whether sonnet
keeps holding.

**Answer: across three cleanly-checkable probes — constrained MDX repair, debugging, hard math — none showed an opus
edge.** The only gradient ever observed is *haiku* occasionally slipping; opus never pulls ahead of
sonnet, and higher reasoning effort never helps. But note the important caveat below: all three probes
**saturated or tied**, so this is a strong prior for the shapes tested, not a universal proof.

## The probes
Grid throughout: 3 models × {low, high, max} effort × 5 epochs = 45 cells per task. Multi-item tasks
score a smooth `k/N` fraction (far less noisy at low n than a single binary answer).

| Domain | Task | Result |
|---|---|---|
| **Constrained MDX repair** | `books-validate` — fix a seeded-broken MDX chapter, graded by a 15-item validator (PR #14) | opus/max = sonnet/high = **0.978**, at 3.6× cost + ~200s latency; discriminates haiku (~0.88). No opus edge *detected* (single-turn; not open-ended authoring). |
| **Debugging** | `t7_find_bug` — name the single buggy line in each of 6 functions (easy→subtle) | **Saturates** — every tier ~1.0, haiku included. No opus edge *detected* (single-turn, saturated). |
| **Hard math** | `t8_hard_math` — 6 hard problems solved **by hand, no tools** (e.g. digit sum of 2¹⁰⁰; 3²⁰⁰ mod 1000) | **Saturates** — sonnet = opus = 1.000, haiku 0.978 (its only misses: two subanswers, both on 3²⁰⁰ mod 1000; Haiku has no effort parameter, so which effort-labeled cell they fell in is noise, not a tier effect). No opus edge *detected*; higher effort didn't help — all single-turn, saturated. |

## What we can and cannot conclude — the ceiling effect
We never found a task where opus beats sonnet, but note *why*: all three probes **saturated or tied**
(haiku ~0.88 on constrained MDX repair, ~1.0 everywhere on debug, sonnet = opus = 1.0 on math). A saturated task has
**no headroom** to measure a tier difference, so the honest reading is deliberately narrow:

- **Supported:** on the specific probes built — constrained MDX repair, textbook debugging, hard-but-standard
  hand-math — opus shows **no edge** over sonnet, and reserving `max` effort is never justified. The only
  gradient observed is *haiku* slipping slightly; opus never pulls ahead.
- **NOT supported:** the stronger claim that *checkable tasks structurally cannot* discriminate opus from
  sonnet. A checkable task can be objectively hard and tier-discriminating (competition-grade math that
  weaker models fail). We simply did not build one that is both (a) determinate-answer *and* (b) hard
  enough that sonnet fails where opus succeeds. That discriminating zone looks **narrow and hard to
  hit** — even digit-sum-of-2¹⁰⁰-by-hand saturated — but "narrow" is not "impossible."

The open question — does opus earn its cost on **open-ended, un-gradable** work (judgment, taste, novel
design, voice)? — remains **unmeasured**: those tasks lack a determinate answer, so this harness
(objective, checkable graders) cannot score them without a fuzzier instrument (an LLM-judge or
pairwise-preference setup), recorded as future work.

**Practical takeaway:** default sonnet is well-supported for the verifiable task shapes probed here; no
evidence yet justifies the opus/max reflex on checkable work. Treat it as a strong prior, not a proof.

## Why the verdicts are trustworthy — the grader saga
Getting a *trustworthy* "no edge" was harder than getting the number. Single-turn free-form
checkable-answer grading proved a persistent minefield: **three separate grading confounds**, each of
which mis-scored a *correct verbose* answer as 0 and would have manufactured a false "downgrade to
haiku" — each caught only by inspecting transcripts before trusting the score.

1. **Spurious JSON array** in the reasoning shadowed the real answer object (`lenient_json` returned the
   *first* JSON value it found).
2. **Markdown backticks** — `` `answer` `` — weren't stripped, so a wrapped-but-correct answer scored 0.
3. **First-number-on-the-line** (numeric) — `ANSWER 4: 3^200 mod 1000 = 1` parsed `3`, not `1`.

All three shared a **direction**: they penalized *verbose / high-effort* output (which opus produces
more of), biasing the very A/B under test. #3 was caught by a **pre-quota 3-voice adversarial review**
— the blind reviewer *proved* a fully-correct verbose block scored 2/6.

**The fix — a strict contract that makes the biased zero *visible and countable*.** The numeric grader
(`exact_match_set` numeric mode) now requires a **bare integer** per answer; any non-bare answer makes
the whole cell `unparseable` — scored an honest **0.0 that is still included in the mean** (a quality
failure), but **labelled and counted** so the failure surfaces as an `unparseable` rate rather than
hiding inside an `ok` score. This is **not free**: the strict parser does *not* remove the biased zero
(the 0 still enters the mean), so a **verbose / high-effort answer that fails the bare-integer parser is
scored 0** and biases the A/B whenever the unparseable rate is nonzero and correlates with model/effort.
It is safe **only when the unparseable rate is ~0** — which the t8 run confirmed: **0 unparseable across
all 9 configs**, verified per-config. Going forward, treat a nonzero unparseable rate as an
**invalid-run / gating signal**, and always report the parseable denominator. (Note the asymmetry: both
*numeric* and *string* mode score a residual mismatch as an in-mean 0; numeric mode additionally *labels*
non-bare answers `unparseable` so the rate is measurable — safe here only because t8 held it at 0.)

## Methodology lessons (reusable)
- **Multi-item tasks (smooth `k/N`) discriminate far better than a single binary answer** at low epoch
  counts (books-validate discriminated at n=3 *because* it had 15 items).
- **Free-form answer extraction is a minefield.** Every model formats differently; each quirk silently
  zeros correct answers, biased by *style*, not skill. Prefer a **strict contract** (bare answer;
  deviation → `unparseable`) over lenient heuristics — but watch the resulting **biased zero** (the
  `unparseable` 0.0 still enters the mean, penalising verbose/high-effort output) and gate on the rate.
- **Always inspect the score distribution + a few transcripts before trusting `advise`.** An
  all-or-nothing or suspiciously clean pattern is the tell for a grading artifact.
- **Run the adversarial review *before* spending quota**, not just before merge — and let it review the
  *conclusions*, not only the code (this doc's first draft overclaimed a structural result; the review
  caught it).

## Artifacts & reproducibility caveat
`graders/exact_match.py`, `graders/exact_match_set.py`; tasks `t7_find_bug`, `t8_hard_math`; grid
`grids/pressure-test.yaml`. **The run ledgers `results/pressure-test*.jsonl` are gitignored** (only the
frozen showcase ledger is committed), so the numbers here — including the load-bearing *0 unparseable* —
live in those ledgers and are not reproducible from the committed tree alone; this is inherent to the
harness's subscription-run design. Prior constrained repair probe: `docs/design/2026-07-03_spend-audit.md` + PR #14.
