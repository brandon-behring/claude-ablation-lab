# 2026-07-11 — Big-picture audit, mission reframe & roadmap

> **Scope.** A strategic assessment of whether `claude-ablation-lab` is aimed at the right question,
> grounded against official Anthropic guidance, with a mission reframe, a model/effort/subagent
> decision guide, a phased roadmap, a measurement-rigor layer, and a sequenced build plan.
> Companion (line-level bugs + methodology): [`2026-07-11_adversarial-audit-findings.md`](2026-07-11_adversarial-audit-findings.md).

> **Method.** Multi-voice review: 3 Claude Explore agents + my own re-verification against the tree
> and ledgers; **Codex** (design, methodology, big-picture, and a meta-review of this plan) and
> **Gemini** (methodology, big-picture, measurement-science) adversarial passes; a deep
> methodology-auditor; three official-guidance research agents (docs, Claude Code subagent/best-
> practice posts, the `claude-api` reference); and a browser agent that retrieved **real transcripts**
> of four official Anthropic talks. Every quantitative claim below was verified against the committed
> tree/ledgers this session. Official claims carry source URLs. The one talk whose captions were
> gated ("The Thinking Lever") is flagged **not grounded** and never asserted.

---

## TL;DR

The lab is unusually honest and well-engineered, but it commits a **construct-validity inversion**:
it measures short, saturated, exact-match, **single-turn** micro-tasks and reports conclusions about
**models** ("no opus edge," "higher effort rarely helps"). Anthropic's own guidance locates the
model-tier and effort payoff in a **different regime** — long-horizon agentic, complex-reasoning,
tool-heavy, large-codebase work — which the lab has **never run** (every committed cell is
`mode: single`; the two agentic tasks `t2`/`t6` are shipped but never executed). So the null findings
are *predicted by* official guidance for this task design, not discoveries about the models. That
untested regime is also, precisely, the shape of real Claude Code work — so **fixing the lab's biggest
flaw and turning it into a personal learning + regression harness are the same move.**

---

## 1. Mission reframe

From **"model × effort selection *economics*"** → **"which Claude configuration is worth it for which
real work — across *quality, latency, efficiency, and context hygiene*, with dollars as just one
axis."**

The lab's identity becomes a **personal + shareable harness with three uses**:

1. **Config selection** — model × effort per task (the original mission, re-scoped to real work).
2. **Multi-agent workflow design** — role → model assignment (executive/planner vs writer vs
   auditor), subagent **context hygiene**, and **disagreement arbitration**.
3. **Regression harness** — re-run the workflow suite when a new model ships and flag regressions
   (the long-dormant "regression" half of the repo's own name).

**Four axes, not one:** capability/quality (including recovery from failure), **latency** (wall-clock
to a good outcome), **efficiency** (total token + context burn incl. input/cache — the real rate-limit
headroom cost), and **context hygiene**. USD is retained only as a comparability lens, never the
headline.

**Grounding.**
- Official guidance already *is* the config-selection mission: *"Tuning effort is often a better lever
  than switching models"*; *"start on Haiku … upgrade only if necessary for specific capability gaps"*
  — [choosing-a-model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model).
- USD is a "pricing illusion" on a flat subscription (the repo's own words). The 2026-07-03 spend
  audit found **cache-read/input dominates both $ and latency**, yet the token axis is *output-only*
  and `advise`/README headline **USD** ("11–15× cheaper", "$0.1704 overpay"). Retire USD as the
  headline; denominate on **latency + total-throughput (incl. input/cache) per successful outcome**.
- The multi-agent pillar is Anthropic's own recommended shape — an **Opus lead + Sonnet subagents**
  system beat single-agent Opus by ~90% on internal evals
  ([multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)).

---

## 2. The convergent verdict — construct validity

All six external voices plus the official guidance converged on the same structural gaps.

| # | Structural gap | Grounding | Verdict |
|---|---|---|---|
| **CV1** | **No agentic/long-horizon cell has ever run** — every committed ledger is `mode: single`; `t2`/`t6` are dormant; even `t9` (the "frontier" judge task) is single-turn. | Opus/effort payoff is "multi-hour autonomous coding, large-scale refactoring… long-horizon agentic" (choosing-a-model; Opus 4.8 launch). | "No opus edge / effort rarely helps" is a fact about **single-turn saturated tasks**, not models. Scope it in the *headline*; build the missing regime. |
| **CV2** | **Haiku effort cells are inert** — `haiku/{low..max}` are all the same default config. | **Documented:** Haiku 4.5 is absent from every effort-support list on [effort](https://platform.claude.com/docs/en/build-with-claude/effort); "the values documented on this page are the complete set the API accepts." Haiku uses `budget_tokens`, not effort — a substrate asymmetry vs the adaptive-thinking Opus/Sonnet/Fable family. | Retract all Haiku effort findings; reject/annotate unsupported model×effort pairs at grid-load; record *effective* provider config. |
| **CV3** | **Saturation makes the null structurally unfalsifiable**; `t8` math is contamination-canonical (Project-Euler/textbook staples). | Official: capability gaps *widen with difficulty*; the lab tests where all frontier models saturate. | Drop "genuinely hard"; contamination-screen or fresh-author; don't track releases on a memorized task. |
| **CV4** | **Insular single-author validity** — a self-authored MDX checklist + a 10-prompt single-author judge over the author's own voice. | Eval guidance: "realistic tasks grounded in actual workflows"; calibrate judges to a human baseline ([demystifying-evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)). | Anchor to an external benchmark (SWE-bench Lite / τ-bench) + a diverse judge panel; complete the **blank** human spot-check gate before any "fable > sonnet" headline. |
| **CV5** | **USD optimizes a constraint you don't pay**; failure-recovery (the real value of top-tier models) is never measured. | Official: agents "trade latency and cost for better task performance"; value is resilience/recovery. | Re-denominate on latency + headroom; add a **recovery-rate** metric on a seeded/perturbed failure. |
| **CV6** | **Headline claims outrun evidence** — `t5` (a constrained MDX repair) is labeled "authoring falsified" while the judge plan calls authoring *unmeasured*; the "REAL separation" doc states `fable > sonnet` as fact on `main` with the spot-check gate unfilled; no kill-criterion/stopping rule. | — | Relabel `t5`; gate the judge headline; pre-register a minimum detectable effect + a stopping rule so a persistent null becomes a *stated-power* conclusion, not open-ended task-building. |

---

## 3. Decision guide (the personal-learning artifact + regression baseline)

**Which config for which task** — evidence + official guidance + honest gaps. Each row is re-measured
whenever a new model ships (see §6, the regression harness).

| Task shape | Guidance-backed pick | Lab evidence | Confidence |
|---|---|---|---|
| Simple / high-volume / latency-sensitive | Haiku or Sonnet, **low** effort | saturated probes agree | High (matches Anthropic's default) |
| Daily coding / analysis | **Sonnet**, high effort | not directly tested | Medium (official positioning) |
| Complex reasoning / hard coding | Sonnet→Opus, **high/xhigh** | **untested** | Low — the lab's gap |
| Long-horizon agentic / large-codebase | **Opus/Fable**, high+, or advisor/executor | **untested** | Low — the lab's gap |
| Open-ended authoring | **Fable** (judge pilot) | real but single-author, gate unfilled | Provisional |

### What Anthropic says **on the record** (real transcripts retrieved this session)

- **Default Sonnet, escalate to Opus when needed.** *"slash-model … I'm on default, which happens to
  be Sonnet. We can jump over to Opus."* — Cal Rueb, *Claude Code best practices* (~19:01,
  [gv0WHhKelSE](https://www.youtube.com/watch?v=gv0WHhKelSE)).
- **Thinking "matters most between tool calls," and you prompt the thinking, not just toggle it.**
  *"most of the time you will get out-of-the-box better performance, but you can squeeze even more … if
  you just prompt the agent to use its thinking well."* — *Prompting for Agents* (~11:38,
  [XSZP9GhhuAc](https://www.youtube.com/watch?v=XSZP9GhhuAc)). *"starting with 4 they can now think
  between tool calls … throw a 'think hard' in there."* — Cal Rueb (~19:25/20:08).
- **Don't build agents for everything.** *"Agents really thrive in ambiguous problem spaces. And if
  you can map out the entire decision tree pretty easily, just build that explicitly … more
  cost-effective."* / *"the task really needs to justify the cost. If your budget per task is around 10
  cents … that only affords you 30 to 50,000 tokens … just use a workflow."* — Barry Zhang, *How We
  Build Effective Agents* (~3:01/3:16, [D7_ipDqhtwk](https://www.youtube.com/watch?v=D7_ipDqhtwk)).
- **Orchestrator + worker-subagent is the on-record role pattern, for context economy.** *"one agent
  be the lead agent and then sub-agents do the actual searching … the sub-agents can compress the
  results to the lead agent in a really dense form … the lead agent can give the final report … We
  actually use this process in our research system."* — *Prompting for Agents* (~14:47). *"having a sub
  agent … will really protect the main agent's context window."* — Barry Zhang (~12:36).
- **Counterweight to an elaborate multi-agent design.** Anthropic's own Claude Code stance is *"the
  simple thing that works … just one agent that's great at coding and does everything,"* with power
  users running *independent* Claudes that *"write to a shared markdown file"* + git-worktree isolation
  (Cal Rueb ~24:37; Boris Cherny ~22:43, [6eBSHbLKuN0](https://www.youtube.com/watch?v=6eBSHbLKuN0)).
  **So role assignment / arbitration is a hypothesis to test against a strong single-agent baseline,
  not an assumption.**

> **Honest gap.** The flagship *model × effort cost/quality frontier with numbers* and an explicit
> *per-role Opus/Sonnet/Haiku/Fable rule* live in the one talk I could not retrieve — *"The Thinking
> Lever"* (Matt Bleifer, Code with Claude 2026), whose captions are POToken-gated/throttled. Those are
> **not grounded** here and are not asserted.

### Multi-agent role guide (starting hypotheses to *test*, per your example)

| Role | Candidate model | Why (to validate) | Arbitration open question |
|---|---|---|---|
| Executive / planner | Fable or Opus, high+ | long-horizon planning; holds the goal | — |
| Writer / implementer | Opus, xhigh | strongest coding + long-horizon execution | — |
| Auditor / reviewer | Opus, **or a different family for independence** | 4.8 finds real bugs; independence reduces shared blind spots | **who decides on writer↔auditor disagreement?** 3rd arbiter / executive / majority-of-N / human gate — measure which scheme yields the best *verified* outcome (§5) |
| Subagent (explore/fan-out) | Haiku or Sonnet, low | cheap, parallel, context-isolated | how much context to hand back (hygiene metric, §5) |

---

## 4. Roadmap — the two goals, unified

The single move that serves **both** goals (shareable + personal-learning) is to pivot the task suite
from single-turn checkable micro-tasks to **real long-horizon agentic work** — the missing regime
(CV1) *and* your own Claude Code workload. **Every phase adopts the rigor layer (§5).**

- **Phase 0 — honesty + cheap fixes (do first).** Scope every null to "single-turn"; retract Haiku
  effort (CV2); add a model×effort capability matrix that rejects/annotates unsupported pairs;
  re-denominate `advise` off USD onto latency + throughput; complete or un-headline the judge gate;
  land the P1 bug fixes.
- **Phase 1 — the missing regime (personal-learning harness).** Build ≥1 real multi-turn, multi-file
  agentic task with a **checkable outcome** (a repo change verified by tests) and a **recovery-rate**
  metric on a perturbed failure; **harvest seed tasks from your own Claude Code sessions** — you learn
  which config wins on *your* work, and the lab gains real coverage.
- **Phase 2 — shareable validity.** Anchor with a slice of an external benchmark (SWE-bench Lite /
  τ-bench); contamination-screen the public probes; commit a *sanitized* reproducible snapshot (your
  gate); add outcome/rubric graders (not exact-match) with transcript spot-checks.
- **Phase 3 — the frontier + orchestration questions, answered honestly.** **Sequential-race** configs
  (default + a few challengers — not a full grid) on the agentic suite, scored by **final-state
  oracles** against pre-registered SLOs, on all four axes. Treat **multi-agent as a protocol**
  (equalized budget + same-budget single-agent baseline); test role→model assignments; a **functional**
  context-hygiene metric; and an **arbitration experiment** with **cross-provider** auditors, schemes
  compared and scored by *verified* outcome.
- **Phase 4 — the regression harness.** Freeze the suite as a versioned **baseline**; add
  `ablation regress <new-model>` that re-runs it and diffs quality/latency/efficiency/hygiene per task
  and per role — so a new Claude release is vetted against *your* workflow. Provenance already stamps
  `claude_version`, which makes the baseline diff honest.

---

## 5. The rigor layer (north-star — start pragmatic)

Agentic tasks break the current scalar/exact-match measurement model. Codex and Gemini converged
(independently, citing τ-bench, Terminal-Bench, HCAST, GDPval, "Lost in the Middle," Bradley-Terry
juries, causal replay) on the requirements below. **Treat these as a north-star, not a checklist:**
the build plan (§6) starts with a pragmatic MVP subset — **final-state oracle + tail-risk stats +
two-lane split** — and grows into the rest.

| Requirement | Why |
|---|---|
| **Sealed final-state oracles**, not trajectory scoring | once trajectories diverge, transcript scoring is confounded by style/path/luck. Score the final repo/DB/file **state** + `pass^k` reliability. Each task = `initial_state + reset + frozen tool/user simulator + budget + final-state scorer + forbidden-action policy + event log`. |
| **Two lanes, not one** | a *private personal-utility harness* (utility-weighted, workflow-specific) and a *research-grade benchmark* (sanitized, frozen holdout, contamination controls, external anchors, preregistered rules). No universal headline unless it survives **both**. This resolves the personal↔shareable tension. |
| **Sequential racing, not a full grid** | model×effort×role×arbitration×task explodes into underpowered cells. Default + 2-3 challengers, promote on gates; fractional-factorial screening; a small **sentinel** suite for releases. |
| **Stochastic-aware stats + tail risk** | n=3 is meaningless for chaotic trajectories. Report severe-failure rate, recovery rate, **p90 latency**, handoff-F1 — not just means; sequential/SPRT + mixed-effects/Bayesian hierarchical; call a regression only when `P(Δ < −MDE)` clears a threshold or a control-chart breach reproduces across run-days. |
| **Functional context-hygiene metric** | tidy ≠ clean; a concise handoff can omit the load-bearing fact or leak stale state ("Lost in the Middle" position effects). Measure hygiene as **downstream blinded-executor success per handoff token**, with hidden-fact probes (required / decoy / stale / forbidden / canary). |
| **Arbitration validated by outcome, not another arbiter** | a same-family auditor rubber-stamps the writer (self-preference / homophily). Use **cross-provider** auditors, **reliability-weighted** (Bradley-Terry) juries, and score by *verified outcome* / a human-calibrated set — comparing schemes. |
| **Multi-agent = a protocol, not a cell** | "Fable-exec + Opus-writer wins" may just mean more budget/retries. Version protocols; equalize budget/tool access; include **same-budget single-agent baselines**; ablate models-vs-protocol separately. (Matches Anthropic's on-record "one simple agent" default — that baseline is the one to beat.) |
| **Agentic + local contamination controls** | agents can read tests/keys/mirrors and infer rubric shape ("passes tests ≠ correct"); and *your own transcripts building this lab may leak into future training*. Keys off the FS/network, Docker snapshots, hidden differential/metamorphic tests, canaries, authoring provenance, an obfuscation pass on personal seeds, a never-committed private set. |
| **Utility calibration + blast radius** | quality points aren't equal (a 2% drop on a risky refactor ≠ 20% on a toy). Attach value metadata (recurrence, human-baseline time, review burden, failure cost) → **expected-utility** score; penalize **unsafe** failures (wrong-dir delete, `git reset --hard`) far above graceful aborts. |
| **Interactive, not headless** | Claude Code is a copilot; "regression" includes asking dumb clarifying questions, failing to hand back control, silent destructive assumptions. Add an **interruption-quality** metric via a deterministic simulated-user oracle. |
| **Judge-drift + Goodhart guards** | pin a **frozen** judge for longitudinal grading, track Cohen's Kappa vs baseline; hold out a locked test-set of your own tasks so tuning your `CLAUDE.md` doesn't just overfit the graders. |

---

## 6. Sequenced build plan (ranked runway)

Start with the **honesty pass**; hold §5 as a north-star; nothing is built until you say go.

1. **Honesty pass on the current lab (START HERE — days, not weeks, no new tasks).** Scope every null
   to "single-turn checkable" in README/CLAUDE.md prose; **retract the inert Haiku effort cells** +
   add a model×effort **capability matrix** rejecting/annotating unsupported pairs at grid-load;
   **re-denominate `advise`** off USD onto latency + total-throughput; **complete or un-headline the
   judge spot-check gate**; relabel `t5` "constrained MDX repair." → what you already have becomes
   trustworthy. *(Findings A1/B1/C1/CV2/CV6.)*
2. **Land the P1 bug fixes.** B3 (best-effort post-run writes — stop the paid-sweep crash), A1
   (unparseable include-vs-exclude), B5 (`estimate` NaN guard), B10 (`git clean -ffdx`).
3. **Stand up the MVP-rigor spine (3 essentials of §5):** a **final-state-oracle** task harness;
   **tail-risk stats**; the **two-lane split** (private personal-utility vs sanitized research lane
   with a frozen holdout).
4. **First real agentic task, seeded from your Claude Code work** (obfuscated, private lane), run as a
   **sequential race** (default + 2-3 challengers) on the four axes — the first datapoint in the regime
   that actually answers the mission, and your personal-learning payoff.
5. **Regression harness + multi-agent experiments:** `ablation regress <model>` vs a frozen sentinel
   baseline; then multi-agent **as a protocol** (vs a same-budget single-agent baseline), cross-provider
   arbitration, functional context-hygiene probe.

**North-star (added as the lab matures, not blockers for 1–4):** SPRT / Bayesian-hierarchical stats,
Bradley-Terry reliability juries, blast-radius sandboxing, interruption-quality oracle, judge-drift
tracking, external-benchmark anchors.

---

## Method, honesty & sources

- **Grounded vs not.** Four official videos were transcribed from real captions this session
  (browser-driven, with timestamps): Barry Zhang *How We Build Effective Agents*; *Prompting for
  Agents*; Cal Rueb *Claude Code best practices*; Boris Cherny *Mastering Claude Code*. *"The Thinking
  Lever"* was caption-gated and is **not grounded**. Docs/API facts are primary
  (platform.claude.com). The off-`main` judge stats were not line-audited beyond the gate + provenance.
- **Confidence tags** in the companion findings doc: `CONFIRMED` (verified against source/ledger this
  session) · `PLAUSIBLE` (code-consistent, not reproduced) · `FRAMING` (a judgment call).
- **Local-only constraint honored.** The judge/books/pressure ledgers are local-only; this doc
  references findings *about* them and proposes no publishing without a per-artifact go-ahead.
- **Key official sources:** choosing-a-model, effort, adaptive-thinking, models/overview
  (platform.claude.com/docs); multi-agent-research-system, building-effective-agents,
  effective-context-engineering-for-ai-agents, writing-tools-for-agents, demystifying-evals-for-ai-agents
  (anthropic.com/engineering); the four talks above; the `claude-api` skill reference.
