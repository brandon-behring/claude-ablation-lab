# LLM-judge phase — pairwise preference for open-ended work

> Status: **pilot complete** (2026-07-06; results in
> `docs/design/2026-07-06_llm-judge-implementation.md` — fable/high ★ and
> fable/low REAL over the sonnet/high baseline; opus/high not; verdicts pending
> the human spot-check gate). Scheduled by the
> independent audit — `docs/audits/2026-07-06_independent-audit.md`. Build
> decisions: `docs/design/2026-07-06_llm-judge-implementation.md`. Two design
> deltas from this doc, both user-decided at build time: the judges are
> CROSS-VENDOR (OpenAI codex + Gemini via agy — dissolves the judge≠contestant
> conflict, so opus/high STAYS a contestant), and the prompts are drawn from the
> author's real local corpora via prepare-time reference assembly.

## Why this phase exists

The pressure test (`docs/design/2026-07-04_pressure-test.md`) closed the checkable
frontier: across authoring, debugging, and hard math, opus never separated from
sonnet, and the discriminating zone for determinate-answer tasks looks narrow and
hard to hit. The one question the harness *cannot* currently answer is the one the
spend audit says matters most: does a bigger model/effort earn its cost on
**open-ended** work (guides, courses, long-form writing — the real spend sinks),
where no objective grader exists? That needs a fuzzier instrument, built with the
same statistical honesty as the rest of the lab: a **pairwise-preference LLM
judge**.

## Instrument design

- **Pairwise, never absolute.** The judge sees two outputs for the same prompt and
  must pick A / B / tie. Absolute 1–10 scoring is unanchored and drifts; pairwise
  preference is the standard remedy (Chatbot-Arena-style), and it plugs directly
  into the harness's existing paired machinery (`compare`'s exact sign-flip test).
- **Judge ≠ contestant.** The judge model must not be any model under test
  (self-preference bias). Fix one judge model + effort for the whole run.
- **Position-debiased.** Every pair is judged twice, A-first and B-first. The two
  verdicts must agree, else the pair records a **tie**. Order-flip disagreement is
  a real judge-noise signal and is *reported*, never silently absorbed.
- **Blinded.** Judge prompts contain the outputs only — no model names, no effort
  labels, no cost hints.
- **`judge_version` keyed like `grader_version`.** Judge model + prompt template +
  decision rule are fingerprinted; re-judging stored outputs is free (no contestant
  re-runs), exactly like re-grading.
- **Judge cost is measurement cost.** Judge tokens are stamped on the judge rows,
  never added to a contestant's `cost_usd`/token fields — the frontier must show
  what the *contestant* costs.

## Validity controls (run before trusting any verdict)

| Control | Expectation | Catches |
|---|---|---|
| **Same-output null** — both sides are the identical text | tie (both orders) | trigger-happy judges, broken plumbing |
| **Verbosity-only pair** — same content, one side padded/expanded ~2× | tie or no consistent preference for the long side | verbosity bias — the style-not-skill confound that mis-scored verbose answers in the pressure-test grader saga; effort/model correlate with length, so this bias would rig the exact A/B under test |
| **Designed positive control** — a known-good vs deliberately-degraded output (facts removed, structure broken) | the good side wins at both orders | a judge that can't detect real quality gaps (the t4 skill-A/B pattern, applied to judging) |
| **Human spot-check** — ~10 randomly sampled pairs re-judged by the author, blind | ≥80% agreement with the judge | systematic judge-taste divergence |

Tie and order-disagreement **rates are reported per config pair** — informative
missingness, the unparseable-rate lesson: a nonzero rate is a gating signal to
inspect, not neutral noise to drop.

## Power (why 8–10 prompts, not epochs)

The exact sign-flip test needs **≥ 6 nonzero pairs** to reach p ≤ 0.05
(`analyze.MIN_PAIRS_FOR_REAL`; min two-sided p = 2/2^n). Five epochs of one prompt
cannot get there — and epochs of the same prompt are not independent evidence about
the *task*, only about run variance. So the pilot uses **8–10 distinct authoring
prompts** (multi-item, the harness's own lesson: k/N discriminates at low n).
Pairs = prompts. Epochs (2–3) add within-prompt stability and feed the tie rate.

## Pilot pick

Per the spend audit (2026-07-03): guides/courses/long-form are ~90%+ of the real
opus spend; books is only ~8%. So:

- **Task**: open-ended **guide-section authoring** — 8–10 prompts drawn from the
  author's real corpus (e.g. "write the section on X for the Y guide, in the
  established voice"), each with the reference materials in-context so the judge
  can check groundedness, not just fluency.
- **Contestants**: `sonnet/high` (the current default), `fable/high`, `opus/high`,
  and `fable/low` as the economics dark horse (Anthropic claims low-effort Fable 5
  often exceeds prior models at xhigh — directly testable here).
- **Judge**: one fixed non-contestant configuration at low effort, order-swapped.
  (If every current-generation model is a contestant, use the strongest
  prior-generation model as judge and record the limitation.)
- **Success criterion**: a contestant beats the **cheapest** contestant with
  sign-flip p ≤ 0.05 over the prompt pairs → it earns its cost; otherwise
  `advise` recommends the cheapest. Win-rates with tie/disagreement rates and the
  four validity controls are reported alongside.

## Risks stated up front

1. **Verbosity bias** is the single most likely way this instrument lies — it
   biases *for* expensive configs, i.e. against the lab's own prior finding, so a
   surprising "opus earns it" result must first survive the verbosity control and
   a length-stratified re-read.
2. **Judge taste ≠ user taste.** The human spot-check anchors this; a divergence
   means the judge prompt needs the author's actual rubric, not generic "quality".
3. **Preference is not correctness.** This instrument answers "which do I prefer",
   not "which is right" — scope every conclusion accordingly (the pressure-test
   ceiling-effect discipline, applied to taste).

## Build outline (when picked up)

1. `graders/pairwise_judge.py` — judge protocol + position-swap + verdict record
   (tested against canned judge responses; 90%+ tier, it is a grader).
2. Judge transport reuses `ClaudeCodeRunner` (hermetic, tool-minimal, key-strip).
3. `tasks/t9_guide_authoring.yaml` — the 8–10 prompts + reference context.
4. Controls run first as a gate (`--controls-only` mode); pilot second;
   `experiments/log.txt` + a design doc record both.
