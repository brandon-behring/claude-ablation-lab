# LLM-judge pairwise phase — implementation notes (2026-07-06)

> Companion to the instrument design in
> `docs/plans/active/2026-07-06_llm-judge-phase.md`. This doc records the build
> decisions; the plan doc records the methodology. Plan was independently
> Codex-reviewed pre-build (10 findings, all folded in — the critical one:
> `spec_sha` joined the judge-row identity key).

## What was built

A judge seam **parallel to graders** (a pairwise judge needs two outputs +
external CLI calls; `Grader.grade(output, gold)` structurally cannot express it):

| Module | Role |
|---|---|
| `judge.py` | Protocol, blinded prompt template `pj-v1`, canonical A/B→config mapping, order debias, cross-judge `pair_score` |
| `judges/` | Lazy registry; `_parse.py` span-scanner (`vp-v1`); `codex.py` + `gemini.py` transports |
| `judge_ledger.py` | One CLI call = one JSONL row (`results/judge.jsonl`); resume on the full judge key |
| `judge_orchestrate.py` | `pick_baseline`, `enumerate_pairs`, `run_judge_pass` (retry + circuit breaker), `evaluate_controls`, spot-check |
| `judge_analyze.py` | `JudgePairSummary` per contrast; sign-flip over per-prompt scores; Holm on exploratory contrasts |
| `graders/authoring_conventions.py` | SECONDARY conventions check giving contestant rows an honest `value` |
| `tasks/t9_authoring/` | 10 prompts-as-tasks (5 LaTeX guide-fleet voice, 5 Astro/MDX book voice) |
| `examples/judge-controls/` | Committed control fixtures (neutral content, never from the private corpora) |

## Key decisions

- **Cross-vendor judges** (user decision): OpenAI `codex exec` (gpt-5.5 pinned,
  effort `medium` pinned — never inherited from `~/.codex`, whose xhigh default
  is documented-slow and would drift silently) and Gemini via `agy`
  ("Gemini 3.1 Pro (High)"). No Anthropic contestant is judged by a sibling, so
  all four contestants stay: sonnet/high, opus/high, fable/low, fable/high.
- **Judge key** = (task, epoch, config_a, config_b, order, judge_id,
  judge_version, **spec_sha**, output_sha_a, output_sha_b, control). Everything
  a verdict depends on is identity; template/parser/pin bumps and corpus edits
  re-judge automatically; re-judging costs zero contestant runs.
- **`judge_version` vs `dr-v1`**: `judge_version` fingerprints the call-time
  surface (template `pj-v1` + parser `vp-v1` + model + effort). The decision
  rule `dr-v1` (debias: order-flip → tie; cross-judge: ±1/0 mean, tie+win keeps
  ±0.5) is analysis-time and stamped on summaries — re-analysis never forces
  re-judging.
- **Prompts as tasks**: 10 `t9_*` YAMLs (paths + prompt text only; reference
  excerpts assembled at prepare time from the LOCAL corpora, full contents
  hashed into `gold.reference_sha` → `spec_sha`). Every topic was grep-verified
  ABSENT from its reference files — references anchor voice, never answers.
  Sign-flip power comes from prompts (n=10 > MIN_PAIRS_FOR_REAL=6); epochs (2)
  feed within-prompt stability and the tie rate.
- **Baseline**: deterministic, cost-only, full-coverage configs only, frozen
  before any judging. Override requires a reason recorded here.
- **Primary contrast**: `claude-fable-5/high` vs baseline, predeclared. The
  other contrasts are Holm-corrected and labeled exploratory.
- **Tokens/cost on judge rows are `None`** — the subscription CLIs report
  neither; latency + output bytes are what is measured. Judge cost never joins
  contestant cost.

## Controls gate (numeric, per judge, at current judge_version)

| Control | Pass rule |
|---|---|
| Same-output null (4 texts × 2 calls) | ≥ 7/8 `tie`; 0 texts with a consistent side preference |
| Verbosity (6 pairs, padded ≈ 2.0–2.4×, same facts) | padded wins ≤ 1/6 debiased pairs |
| Positive (6 pairs, degraded length-MATCHED 0.98–1.18×) | good wins ≥ 5/6; degraded wins 0 |
| Call health | latest-row-per-key non-ok ≤ 10 % |

`ablation judge` refuses real pairs until every judge passes at its current
version; `--controls-only` runs/scores the gate. The gate is a **coarse
preflight** — the standing defenses are the length-ratio column on every
verdict, the order-disagreement rate, and the human spot-check (≥ 80 % blind
agreement to headline — scored **tie-excluded** on decisive-consensus pairs, the
Zheng et al. 2023 without-tie convention).

## Privacy

Judging sends contestant outputs + reference excerpts (drawn from the author's
local corpora) to OpenAI and Google. The CLI prints this notice before the
first real call. Control fixtures are neutral content, committed.

## Budget (pilot)

80 contestant runs (4 configs × 10 prompts × 2 epochs, resumable) + 64 control
calls + 240 real judge calls (3 baseline pairings × 10 prompts × 2 epochs × 2
orders × 2 judges) at `max_workers=4`, 240 s/call, 1 retry, circuit breaker at
5-consecutive or >20 % failures.

## Results (pilot, 2026-07-06 — pending the human spot-check)

Sweep: 80/80 cells `ok`, $20.70, conventions 0.88–0.93 per config, output
lengths tightly matched (9.1–9.4 k chars — the 900–1400-word prompt target
defused the verbosity confound at the source). Controls gate: **clean pass,
both judges** (64/64 parsed; 8/8 same-output ties; padded won 0/6; degraded won
0/6). Judge pass: **240/240 ok, 0 unparsed** — no embargo.

Baseline (measured cheapest by `cost_usd`, frozen pre-judging): **sonnet/high**
($0.134/cell) — not fable/low as the plan guessed; fable is the *expensive*
model here. Every contrast therefore reads "does the pricier config beat the
current default":

| contrast vs sonnet/high | W/L/T | score | p (sign-flip) | real? | cost× | tok× | len× |
|---|---|---|---|---|---|---|---|
| **fable/high** ★ primary | 9/1/0 | +0.80 | **0.0039** (n≠0: 10) | **yes** | 3.25× | 1.07× | 1.04× |
| fable/low (Holm) | 9/0/1 | +0.72 | 0.0039 → **0.0078 adj** | **yes** | 2.17× | **0.59×** | 1.03× |
| opus/high (Holm) | 5/3/2 | +0.20 | 0.42 | no | 1.30× | 0.68× | 0.99× |

Judge noise: order-flip 5–25 % per judge, cross-judge disagreement 15–20 %,
0 % missing. **Length-stratified re-read** (the mandatory check): fable/high
wins *more* decisively on pairs where its output is SHORTER than sonnet's
(+1.00 vs +0.64) — the opposite of a verbosity artifact; fable/low wins in both
strata (+0.38 shorter / +0.96 longer).

Findings, scoped as **preference, not correctness**:

1. **The lab's first REAL positive separation.** After t5–t8 found no opus edge
   on any checkable probe, the open-ended instrument finds both Fable configs
   decisively preferred over the sonnet/high default on the author's own
   guide-section work.
2. **The economics dark horse confirmed.** fable/low beats the default 9/0/1 at
   0.77× latency and **0.59× output tokens** — on the token axis (the honest
   flat-subscription spend currency per the 2026-07-03 audit) it is *cheaper
   than the baseline and better*. On the USD axis it costs 2.17×. The
   axis-disagreement lesson from the Pareto work recurs at the quality frontier.
3. **Opus still never separates.** The pressure-test conclusion now extends to
   open-ended authoring: opus/high is preferred on only 5/10 prompts (p = 0.42)
   at 1.30× cost. The opus reflex remains unearned on every instrument the lab
   has.

Gate on headline use: `results/judge_spotcheck.md` (10 blinded pairs, sampled
**decisive-consensus-only** and stratified to the headline Fable contrasts) awaits
the author's verdicts; ≥ 80 % agreement — scored **tie-excluded** on decisive pairs
(the Zheng et al. 2023 without-tie convention: a consensus tie, often mere
cross-judge disagreement, is nothing a human can match) via
`ablation judge-spotcheck --score` — is required before these verdicts headline.
The bar's calibration at n = 10 (exact binomial: an 85 %-agreement human passes
≥ 8/10 with P ≈ 0.82; the old ties-included sample made even a 95 % human fail more
often than not) is recorded in the 2026-07 literature gap-analysis. Judge-side
measurement cost: 307 ledger rows (64 control + 240 real settled calls + 3 retried
transient failures, all recovered), mean 24 s/call, $0 marginal (subscription CLIs).
