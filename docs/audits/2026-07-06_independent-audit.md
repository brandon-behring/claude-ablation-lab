# Independent audit — goals, progress, and the Pareto north star

> **Status: concluded (2026-07-06).** An independent audit of the whole repo
> (two fresh-eyes exploration passes + a Codex cross-review of every claim below
> + a prior-art web survey), commissioned to answer three questions: *what is this
> lab actually for, is the progress real, and what is missing for the north-star
> deliverable — Pareto curves over model × thinking-effort?* Line references are
> to the tree at `ecce9b2` (pre-audit main).

## 1. Mission, clarified

The README leads with infra A/B regression ("prove whether a change to your
`CLAUDE.md`/skills/MCP actually helps"), but every post-showcase effort — `advise`,
the spend audit, `books-validate`, the pressure test — is **model/effort selection
economics**: stop overpaying for opus/max, prove where cheaper configs are safe.
This audit records the owner's resolution: **economics is the mission; the infra
A/B machinery matters exactly insofar as it lets the economics question be answered
correctly** (hermetic cells, controls, honest verdicts). The README now says so.

The practical north star: **Pareto curves** — quality vs cost per (model, effort)
cell, per task, with honest uncertainty — as a personal decision tool, a public
portfolio artifact, and a release-over-release regression instrument.

## 2. Progress: the claims hold

- **Phases 0–6 are real and unusually well-reviewed.** Every phase carries a
  multi-voice adversarial review with tool-grounded findings (17 docs in
  `docs/design/`); the reviews caught substantive methodology bugs (the
  bootstrap-CI verdict tautology, Type-I ≈ 21% at n=4 → exact sign-flip test) —
  self-critical, not celebratory.
- **The Phase C showcase shipped 2026-07-02** (54/54 cells ok; the t4 skill A/B
  moved 6/6 pairs 0.0→1.0, exact p = 0.0312; sanitized ledger + figures
  committed). CLAUDE.md still said "pending" — fixed by this audit.
- **Post-phase work outgrew the phase list**: `advise` (cost frontier verdicts),
  t5/t6 `books-validate` (the first discriminating task), t7/t8 + two exact-match
  graders (the pressure test, PR #16). CLAUDE.md listed 4 tasks and 3 grids; the
  tree has 8 and 5 (now 6). Fixed.
- **375 tests collected cleanly pre-audit; `make ci` green.** The "~93% coverage"
  claim was not verifiable from any committed artifact — the enforceable claim is
  the CI floor of 90, and CLAUDE.md now says exactly that.

**The one structural gap:** every committed result **saturates at the top** — it
is *not* true that every task saturates (t5 discriminates haiku, ~0.10 below the
field), but **no task yet separates sonnet from opus**; the only quality gradient
ever observed is haiku slipping. So the committed Pareto figures are top-flat:
cost varies, quality doesn't, and "cheapest wins" trivially. The pressure test
(2026-07-04) says why, honestly: the determinate-answer zone that is also
tier-discriminating looks narrow, and opus's value on open-ended work is
**unmeasured** because objective graders can't score it.

## 3. Claims vs reality (fixed by this audit)

| Claim (pre-audit) | Reality | Disposition |
|---|---|---|
| CLAUDE.md: "Phase C showcase pending" | shipped 2026-07-02 | CLAUDE.md refreshed |
| CLAUDE.md: 4 tasks, 3 grids, 4 graders | 8 tasks, 5 grids, 6 graders | refreshed |
| CLAUDE.md: "`max` effort is Opus-only" | falsified in-repo (`grids/books-pilot.yaml` live-verified max on all models); re-probed today — see §5 | replaced with the measured matrix |
| CLAUDE.md: "coverage ~93%" | unverifiable from committed artifacts | restated as "CI floor 90" |
| `experiments/log.txt` current | stopped 2026-07-02; work ran through 07-04 | backfilled |
| — | two dead scratch files (`test_regex*.py`) at repo root | deleted |
| README quickstart claims Pareto/`advise` | true — but tokens were parsed and then **dropped** before the ledger (`orchestrate._build_row`), cost/latency had no uncertainty, and the frontier axis was hardcoded to USD | fixed — see §5 |

Also recorded, not fixed (self-disclosed in the docs, worth keeping visible):
- **Reproducibility gap:** only the showcase ledger is committed; the
  books-validate and pressure-test numbers — including the load-bearing
  "0 unparseable" that licenses the strict-exclude numeric grader — live in
  gitignored ledgers. Inherent to the subscription-run design; flagged so nobody
  mistakes the committed tree for a full reproduction kit.
- **CLI effort footgun (new finding, probe 2026-07-06):** `claude --effort
  <unknown-value>` **warns and silently runs at the default effort** rather than
  erroring — a typo'd effort in a grid would produce cells mislabeled with the
  requested effort while measuring the default. The harness's `effort_support`
  matrix masks this today; a grid-load validation against the CLI's accepted set
  (`low|medium|high|xhigh|max`) is the cheap hardening. **Recommendation, not yet
  implemented.**

## 4. Prior art — what others do that this lab should absorb

Survey run 2026-07-06 (WebSearch/WebFetch; links inline).

- **Anthropic effort docs**
  ([platform.claude.com/docs/en/build-with-claude/effort](https://platform.claude.com/docs/en/build-with-claude/effort)):
  the valid effort set is now `low|medium|high|xhigh|max`, with `xhigh` on
  Opus 4.7/4.8, Sonnet 5, and Fable/Mythos 5, and explicit guidance to *step down
  only when your own evals show the lower level holds quality* — precisely this
  harness's use case. Also a directly testable frontier claim: **lower effort on
  Fable 5 often exceeds `xhigh` on prior models.**
- **HAL — Holistic Agent Leaderboard** ([arXiv:2510.11977](https://arxiv.org/abs/2510.11977)):
  21,730 agent rollouts; **higher reasoning effort reduced accuracy in the
  majority of runs** — independent, large-N corroboration of this lab's "max is
  never justified" finding. Practice to absorb: LLM-aided transcript inspection as
  a first-class step (this lab already reads transcripts by hand; HAL scales it).
- **Overthinking in test-time compute** ([arXiv:2604.10739](https://arxiv.org/abs/2604.10739)):
  the token-budget → accuracy curve is an **inverted U** (negative marginal
  utility past a task-difficulty-dependent knee; easy problems peak earliest).
  Matches t5's observation that `max` was haiku's *worst* tier. Implication: treat
  "more effort" as a tunable with a peak, not a monotone ladder — effort curves
  (already in `ablation plot`) are the right visualization.
- **Compute-accuracy Pareto frontiers** ([arXiv:2512.24776](https://arxiv.org/html/2512.24776v1)):
  frontier plots use a **log compute axis**; notably they run single-pass with *no
  uncertainty at all* — this lab's bootstrap intervals are ahead of the academic
  baseline. One adoptable finding: models spend **more compute on wrong answers
  than right ones** — report cost-of-failure separately before averaging it in.
- **Economic evaluation of LLMs** ([arXiv:2507.03834](https://arxiv.org/pdf/2507.03834)):
  argues for jointly reported cost-quality (cost-of-pass-style metrics) over
  naive accuracy tables; the `advise` verdict ("cheapest within margin of best,
  vs your reflex") is a per-task instance of exactly this.
- **Aider polyglot leaderboard** ([aider.chat/docs/leaderboards](https://aider.chat/docs/leaderboards/)):
  the community-standard cost-vs-score scatter draws the frontier as a **dashed
  staircase** (the achievable envelope), not a point-to-point line. Adopted (see
  §5). Their tracker's open feature request asks for the interactive version of
  what `ablation plot` already renders.
- **Artificial Analysis methodology**
  ([artificialanalysis.ai/methodology/intelligence-benchmarking](https://artificialanalysis.ai/methodology/intelligence-benchmarking)):
  cost is reported as *the tokens/dollars to run the full eval suite* using
  provider-reported token counts, 1–5 repeats per eval. Same design pressure that
  motivated persisting provider token counts to the ledger (see §5).

**Net assessment:** nothing in the survey invalidates the lab's approach; on
uncertainty honesty it is ahead of most published frontier work. The absorbable
deltas were: log-x axes, staircase frontiers, token-denominated cost, cost-of-
failure asymmetry (future), and large-N corroboration for the "default sonnet"
rule.

## 5. What this audit changed (shipped with it)

1. **Token persistence** — `input/output/cache_read/cache_creation_tokens` now on
   every new ledger row (native scalars; `None` = not measured on old rows —
   the `tool_calls` rule). Cache-read matters: the spend audit measured it as the
   single largest component of real spend.
2. **Uncertainty on the cost axes** — cost/latency/token across-epoch intervals
   from the same estimator and gates as the quality CI (≥3 epochs to compute;
   labeled "epoch range", never "95% CI", below 5).
3. **Selectable frontier** — `report(x_axis=cost|latency|tokens)` and
   `ablation plot --x-axis …`; the `pareto` flag is axis-specific; unmeasured x
   never counts as free. On the pressure-test-math ledger the USD frontier
   (haiku/low) and the latency frontier (sonnet/low) already disagree — the
   selectable axis is not cosmetic.
4. **Plot polish** — staircase frontier, x error bars, log-x when the range spans
   ≥10×, `medium`/`xhigh` in the effort orderings (plot + advisor fallback).
5. **Effort-matrix re-probe + Claude-5 refresh sweep** — see below.

### The re-probed model × effort matrix (CLI 2.1.201, 2026-07-06)

20 minimal cells (one trivial prompt each, neutral cwd, key-strip subscription
auth), ≈ $1.42 total equivalent:

| alias | resolves to | low | medium | high | xhigh | max |
|---|---|---|---|---|---|---|
| `haiku` | `claude-haiku-4-5-20251001` | ok | ok | ok | ok | ok |
| `sonnet` | `claude-sonnet-5` | ok | ok | ok | ok | ok |
| `opus` | `claude-opus-4-8` | ok | ok | ok | ok | ok |
| `claude-fable-5` | `claude-fable-5` | ok | ok | ok | ok | ok |

**"`max` effort is Opus-only" is retired** — every alias accepts every effort, so
`effort_support` in grids is now a *budget* tool, not a validity one. Two bonus
observations worth keeping: (a) on the trivial "reply with exactly: ok" probe,
haiku emitted 38–40 output tokens (it did not obey the exactness instruction)
while sonnet/opus emitted 4 — instruction-following differs at the tier floor
even on trivia; (b) fable's output grew with effort on the *same* prompt
(4 → 4 → 17 → 21 → 68 tokens across the ladder) — adaptive thinking visibly
engages from `high` upward, i.e. the effort lever is behaviorally live even when
the answer is one word.

### The Claude-5 refresh (first cross-release data point)

`grids/claude5-refresh.yaml` × t8 hard-math: **39/39 cells ran, 0 infra
failures, ≈$4.7 equivalent** (floor estimate was $3.06 — the 1.5× is the usual
mixed-grid overhead), 1 unparseable (haiku/low epoch 2 — transcript inspected
per the pressure-test gate: haiku wandered off-task narrating a repo exploration
and answered nothing; a genuine tier-floor failure counted as its honest 0.0 and
flagged `⚠1unp`, not a formatting artifact of a correct answer).

Quality: 12/13 configs at 1.000 (t8 stays saturated for sonnet/opus/fable, as
predicted — this grid tracks the cost axes); haiku/low 0.667 is the sweep's only
quality signal. The economics, per axis:

| config | qual | $ | lat s | out-tok |
|---|---|---|---|---|
| `haiku/high` | 1.000 | **$0.051** ★$ | 64.2 | 9,599 |
| `sonnet/low` | 1.000 | $0.057 | **16.2** ★lat | 1,553 |
| `haiku/low` | 0.667 | $0.066 | 54.8 | 10,110 |
| `opus/low` | 1.000 | $0.071 | 19.3 | 1,258 |
| `opus/xhigh` | 1.000 | $0.116 | 34.4 | 3,091 |
| `claude-fable-5/low` | 1.000 | $0.138 | 17.4 | **998** ★tok |
| `claude-fable-5/max` | 1.000 | $0.306 | 48.5 | 4,365 |

(7 of 13 rows shown; full ledger local.) **Each axis crowns a different
winner** — `--x-axis` is not cosmetic:

- **USD frontier: `haiku/high`.** But haiku burned 6–10× the output tokens and
  3–4× the wall-clock of `sonnet/low` for the same quality — on a flat
  subscription (where the real budgets are time and rate-limit headroom),
  haiku's cheapness is a **pricing illusion**. This materially revises the
  earlier USD-only "haiku wins" frontier readings.
- **Latency frontier: `sonnet/low`** (16.2 s, $0.057) — the all-round pick, and
  further support for the spend-audit "default sonnet" rule.
- **Token frontier: `claude-fable-5/low`** — the fewest output tokens of *any*
  config (998), consistent with Anthropic's "lower effort on Claude 5 rivals
  prior models" claim on the efficiency side (per-dollar it is pricier).
- **Effort helps at the floor, not the top:** `haiku/low → high` = 0.667 →
  1.000, while `fable/max` = 2.2× `fable/low` cost for +0.000 — the
  overthinking-paper shape, reproduced in-house.
- `ablation advise --reflex opus/max` correctly fell back to `opus/xhigh` (the
  new effort ordering, exercised live) and recommends `haiku/high` for 2.3×
  saving — *if dollars are your budget*; on latency it costs you 30 s/cell.
  The axis choice is now an explicit, first-class decision.

## 6. Roadmap (economics-first order)

1. ~~Pareto plumbing~~ — shipped with this audit.
2. ~~Effort re-probe + Claude-5 refresh~~ — shipped with this audit.
3. **LLM-judge pairwise-preference phase** — the instrument for the unmeasured
   open-ended frontier, where the remaining opus/fable question actually lives.
   Planned with pilot: `docs/plans/active/2026-07-06_llm-judge-phase.md`.
4. **Release-tracking cadence** — re-run `grids/claude5-refresh.yaml` per model
   generation; the frontier's movement (not any single point) is the deliverable.
5. Deferred, recorded: grid-load effort validation (§3), cost-of-failure
   asymmetry reporting (§4), and the T2 runway (unchanged, still blocked on the
   upstream flat-skill conversion).
