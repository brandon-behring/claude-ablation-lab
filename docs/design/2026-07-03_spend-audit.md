# Where the spend actually goes — a read-only audit of local session logs

**Status: finding (2026-07-03).** Phase 1 (`ablation advise`) proved the *method* — cheapest config within a quality margin — and, on the committed showcase, that opus→haiku is 11–15× cheaper for +0.000 quality on *saturated* tasks. Before building a harder discriminating task (`books-validate`), I asked the prior question directly: **across all my real Claude Code work, where does the spend actually go?** This doc answers it from data, and the answer reframes the roadmap.

## Method

Aggregated **all 3312 local session logs** under `~/.claude/projects/**/*.jsonl` (`message.usage` per record), split by:
- **model tier** — opus / sonnet / haiku (substring of `message.model`);
- **layer** — main-loop vs subagent fan-out (path contains `/subagents/`);
- **recency** — record `timestamp` bucketed ≤14d / 15–45d (newest record = 2026-07-03).

Scripts: `spend_split.py`, `spend_recency.py` (scratchpad; stdlib-only, re-runnable). The account is a **flat subscription** — nothing here is billed per token — so **token volume is the honest currency** (it maps to both effort and latency). An API-list-equivalent `$` is included only as a *proxy* for relative weight; treat the ratios, not the absolute dollars, as real.

## Findings (pricing-robust — hold on raw token volume)

1. **The opus reflex is real and total: opus ≈ 99% of spend.** Main-loop output tokens: **209M opus vs <2M sonnet vs ~0 haiku**. Across both layers, opus is **99.2%** of the API-equivalent proxy. This is not a subtle overpay — opus is essentially *all* of the work.
2. **It's current, not historical.** Last 14 days: **95.7% opus** (98.5M of 103M output). 15–45 days: 97.4%. Every window with data is ~96–99% opus. The reflex is live *today*.
3. **Layer: the main loop dominates cost, not fan-out.** Main-loop **73%** / subagent **27%** by proxy-$ (86% / 14% by output tokens). Subagent overhead is real but secondary.
4. **Books authoring is only ~8% of opus output** (18M of ~240M). The larger sinks are a spread of other authoring- and learning-heavy projects (technical guides, courses, long-form writing) — no single one dominates. A *books-specific* benchmark therefore probes a narrow slice of where the reflex actually costs.

## Findings (pricing-dependent — proxy only)

5. **Within opus, cache-read from long sessions is the single biggest `$` component** (~$44K of the $74K main-opus proxy; 29.7B cache-read tokens). This is *conversation length × context size* — a lever orthogonal to both model choice and subagents. Under a flat subscription it costs no dollars, but it is the clearest "takes longer" signal in the data.

## Honest non-finding

6. **The latency split by layer is NOT reconstructable here.** A per-session-span sum came out 78% subagent, but that double-counts *concurrent* subagents (N parallel agents summing to N× their real wall-clock). These logs cannot honestly separate the "takes longer" axis by layer; only the token/cost axis is solid. Recorded so the misleading 78% is never cited.

## The decision rule (what this session actually set out to answer)

The confidence axis is **task shape, not task importance**. Flip the default from opus→sonnet and escalate *deliberately*:

| Task shape | Model | Effort | Evidence grade |
|---|---|---|---|
| Extraction / classification / verbatim / format-bound (a checkable answer exists) | **haiku** | low | **Tested** — Phase-1: haiku = opus quality at 11–15× less cost; low=high on saturated work |
| Mechanical edits, refactor-with-tests, lookups, summarize, boilerplate | **sonnet** | medium | *Strongly implied* — low downside (tests/checks catch regressions) |
| Open-ended authoring with voice, novel design, hard debugging, adversarial review | **sonnet** | high | **Tested (t5, 2026-07-03) — reflex falsified for authoring.** On the `books-validate` hard-authoring probe, opus/max (0.978) *tied* sonnet/high (0.978) to four decimals at 3.6× cost + ~200s latency; opus did not separate from sonnet, only haiku fell behind. (Novel design / hard debugging still unmeasured — but the specific "opus authoring" assumption that motivated this row is disproven on authoring.) |
| "Reach for `max` effort" | — | **avoid** | **Tested (t5) — waste.** sonnet/max == sonnet/high and opus/max == opus/low (both to the decimal), haiku/max is haiku's *worst* tier. Zero cases where max beat high/low; default med/high. |

**One line:** *start every task on sonnet — open-ended authoring included — and escalate to opus only when sonnet visibly underperforms, not by task-hardness alone.* The `books-validate` run removed the last "maybe opus earns it on hard authoring" hedge: it didn't. Because opus is 96–99% of current spend, flipping the default likely captures most of the savings on its own.

## The depth-probe result (t5 `books-validate`, 2026-07-03)

The bottom-row assumption finally got its test — a 27-cell sweep (3 models × {low, high, max} × 3 epochs) fixing a seeded-broken MDX chapter that an anti-gaming checklist grader scores 0→1. The task **discriminates** (haiku lands ~0.10 below the field — genuinely not saturated), but within the field opus does **not** separate from sonnet:

| model | low | high | max |
|---|---|---|---|
| haiku | 0.878 | 0.867 | 0.822 |
| sonnet | 0.956 | **0.978** | 0.978 |
| opus | 0.978 | 0.878 | **0.978** |

**opus/max = sonnet/high = 0.9778** — identical epoch scores `[0.933, 1.0, 1.0]`, Δ +0.0000. `ablation advise --reflex opus/max` → **use `sonnet/high`: same quality, 3.6× cheaper, ~200s faster per run.** `max` effort was pure waste on every model (the whole top tier — sonnet/high, sonnet/max, opus/low, opus/max — ties at 0.978). And the reflex tier is the one that hit the weekly session cap mid-run before it was resumed: the priciest config exhausted quota for *zero* quality gain over one 3.6× cheaper. (`t6` agentic shape stays sandbox-gated — not run.)

## What this does to the roadmap

- **The audit is the deliverable, not a new benchmark.** The highest-leverage action is behavioral (the rule above), not more measurement.
- **`books-validate` was built *and* run** (2026-07-03; `2026-07-02_cost-benchmark-map.md`): the depth-probe for the (formerly) untested bottom row is done, and it *falsified* the opus-earns-it-on-hard-authoring assumption — opus/max tied sonnet/high at 3.6× the cost (see the result section above). Still only an 8% slice, but it's the one slice that turns the bottom row from assumption into measurement.
- **A broader cross-work "downgrade audit"** is blocked by the fact that the top task types (guides, job-apps, interview-prep) aren't cheaply auto-gradable — which is *why* the synthetic fixture exists. Scope separately if the rule alone proves insufficient.
