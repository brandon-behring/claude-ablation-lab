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
| Open-ended authoring with voice, novel design, hard debugging, adversarial review | **opus** | high | **Untested** — opus *may* earn it here; this is assumption, and the one slice `books-validate` would verify |
| "Reach for `max` effort" | — | treat like the opus reflex | **No tested case** where max earned its keep; default med/high, reserve max for genuinely hard reasoning |

**One line:** *start every task on sonnet/medium; escalate to opus/high only when the task is genuinely open-ended-hard or sonnet visibly underperforms.* Because opus is 96–99% of current spend, flipping the default likely captures most of the savings on its own.

## What this does to the roadmap

- **The audit is the deliverable, not a new benchmark.** The highest-leverage action is behavioral (the rule above), not more measurement.
- **`books-validate` stays designed and parked** (`2026-07-02_cost-benchmark-map.md` + the session plan): it's the *depth*-probe for the untested bottom row, worth building on a real trigger, but it addresses an 8% slice — not the headline.
- **A broader cross-work "downgrade audit"** is blocked by the fact that the top task types (guides, job-apps, interview-prep) aren't cheaply auto-gradable — which is *why* the synthetic fixture exists. Scope separately if the rule alone proves insufficient.
