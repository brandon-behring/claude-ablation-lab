# 2026-07-11 — Adversarial audit findings (fix-list)

> Companion to [`2026-07-11_big-picture-mission-and-roadmap.md`](2026-07-11_big-picture-mission-and-roadmap.md).
> This is the line-level ledger: verified bug + methodology findings, plus the construct-validity
> findings, each with an opinionated verdict. The strategy doc is *why*; this is *what to fix*.

**Legend.** Severity `High/Med/Low`. Confidence: `CONFIRMED` (verified against source/ledger this
session) · `PLAUSIBLE` (code-consistent, not reproduced) · `FRAMING` (a judgment call, not a defect).
Voices: **C**laude · Code**X** · **G**emini. All `file:line` anchors were checked against the tree
this session unless a finding is tagged `PLAUSIBLE`.

---

## Tier A — how to read the headline numbers (methodology)

| ID | Sev | Conf | Voices | Finding (where) | Verdict |
|----|-----|------|--------|-----------------|---------|
| A1 | High | CONFIRMED | X+G | `unparseable` runs are **included at `0.0`** in the mean (`analyze.py:122`, docstring `:28-30`), directly contradicting `graders/exact_match_set.py:17,22-25` + `docs/design/2026-07-04_pressure-test.md`, which claim they are *excluded*. The strict grader's "biased zero" was **relabeled, not removed** — a verbose Opus answer failing the bare-integer parser is scored `0.0` and drags Opus down. | Fix the stale docstrings to match code; surface **per-config unparseable rate** in `report`/`compare`; do **not** ship "opus no-edge on hard math" until that rate is ~0 and not config-correlated. |
| A2 | High | CONFIRMED | C+X+G | "No opus edge" is near-**unfalsifiable**: checkable tasks saturate → paired diffs ≈ exactly 0 → `n_nonzero < MIN_PAIRS_FOR_REAL(6)` → `real` mechanically unreachable (`analyze.py:499-519,561-565`); n≤10 sign-flip power ≈ 0.38 @ 80% true win-rate. | Every checkable "no edge" is "not detected at this power," not "absent." Scope it in the headline; pre-register an MDE. |
| A3 | High | CONFIRMED | C+X+G | **External validity**: `t5` = a single-author 15-item MDX checklist; the judge = 10 single-author prompts; `t1`/`t8` are never contamination-screened. (See CV3/CV4.) | Anchor to an external benchmark; diversify the judge; contamination-screen. |
| A4 | Med | CONFIRMED | C+X | T1 "**AUROC**" = *balanced accuracy* on hard 0/1 labels — the docstring admits it (`graders/classification.py:5-9,115-119`). | Rename; it cannot support ranking / calibration / separability claims. |
| A5 | Med | CONFIRMED | C+X | `compare` applies **no across-task multiple-comparison correction** (`analyze.py:466-467`) while the judge phase does (Holm). | Add the MC caveat; the less-corrected checkable "no edge" is the more-cited. |
| A6 | Med | CONFIRMED | C | Epoch interval fields (`ci_low/ci_high`, cost/latency/tokens) are **~74%-coverage min–max ranges** at n=3, not CIs, and **don't self-label** (`analyze.py:354-368`; fields `:139-155`). | Rename/annotate the fields so a consumer can't read them as 95% CIs. |
| A7 | Low-Med | CONFIRMED (relabel) | X | The judge p-value **is** the exact sign-flip test (`judge_analyze.py:155-157`, on branch) — correct. But `9/1/0 → p=0.0039` reads like a binomial sign test (which is 0.0215). | Present as "sign-flip p (magnitude-weighted), not a sign test"; conclusion is robust to either. |
| A8 | Low | CONFIRMED | C+X+G | The shuffled-label "**leakage**" gate cannot detect real prompt contamination (permutes at grade time over fixed predictions) — `graders/classification.py:139-150`. | Already disclosed as a metric self-test; the `LEAK`/`leakage` naming still oversells. |

## Tier B — infra masquerading as signal (design + implementation bugs)

| ID | Sev | Conf | Voices | Finding (where) | Verdict |
|----|-----|------|--------|-----------------|---------|
| B1 | High | CONFIRMED | C+X+G | **Silent effort clamping**: `effort_ok` is fail-open (`grid.py:77-80`); Haiku 4.5 has no effort parameter (documented), so every `haiku/{low..max}` cell is the same default config. (See CV2.) | Reject/annotate unsupported model×effort pairs at grid-load; retract Haiku effort findings; record effective config. |
| B2 | High | CONFIRMED | C-verified (X) | **Cost means blend cold + warm cache epochs.** Verified in `results/claude5-refresh-2026-07-06.jsonl`: epoch 0 (cache *creation*) ≈ 1.5–1.8× epochs 1-2 (warm reads) — fable/high $0.234 vs $0.145/$0.133; opus/high $0.122 vs $0.083/$0.080. `analyze.py:371`. | Report cold vs warm separately (or mark the frontier "cache-regime-mixed"); a cost frontier that depends on epoch order isn't an economic property. |
| B3 | High | CONFIRMED | C+X | **Unguarded post-run writes crash a PAID sweep / lose the row.** `_write_transcript` (`runner.py:429-435`), `_persist_output` (`orchestrate.py:281-286`), `append_row` (`ledger.py:146-152`) do bare writes; an error after a paid `ok` escapes `run_sweep`, and if it's the transcript, **no ledger row → resume re-pays**. | Wrap all three best-effort; a paid `ok` must **always** yield a ledger row. **(P1.)** |
| B4 | High | CONFIRMED | C | **Infra breaker conflates "env broken" with "one cell-type broken"** (`orchestrate.py:68,608-619`): 5 contiguous `infra_error`s halt the whole sweep; a systematic `parse_fail` is invisible to the breaker and burns the grid without halting. | Budget infra failures per `(task,variant,model,effort)`; make `parse_fail` visible to the breaker. |
| B5 | Med | CONFIRMED | C | `estimate_sweep` **re-introduces the NaN crash `_usage_token` exists to prevent**: raw `int(usage.get(...))` at `orchestrate.py:813-814` (hardened guard at `:289-308`); also calibrates on `cells[0]` trusting a *comment* (`:793`). | Route through `_usage_token`; stop calibrating on `cells[0]` by comment. **(P1.)** |
| B6 | Med | CONFIRMED | C | `grade_run` is a **dead, divergent public seam** — grades `run_result.output` (stdout, `grade.py:70-80`) while the live path grades the captured artifact (`orchestrate.py:580,586`). Nothing calls it. | Delete or fix; a future caller would silently mis-grade every agentic task. |
| B7 | Med | CONFIRMED | G-verified | `_mark_pareto` filters only `task_id`, **not `variant`** (`analyze.py:441`) → control (without-skill) and treatment (with-skill) arms compete on one frontier. | Group the frontier by `(task, variant)`. |
| B8 | Med | CONFIRMED | C | `Score.value` has **no `[0,1]` range validation** at the boundary or on ledger write (`grade.py:29-51`; `orchestrate.py:357`). | Validate at the `Score` boundary — a buggy grader's `5.0`/`-1` currently flows into every mean/CI. |
| B9 | Med | CONFIRMED | C | **Provenance staleness/blind spots**: sampled once per sweep (`orchestrate.py:499`); `harness_sha` ignores a dirty tree (`provenance.py:87-89`); `global_layer` digest excludes `~/.claude/skills`+`commands` (`:112-133`) — the exact injection surface `runner.py:84-91` flags. | Flag a dirty tree; widen the digest; sample per-row or detect mid-sweep version drift. |
| B10 | Med | CONFIRMED | X | `git clean -fdx` **leaves nested git repos behind** (`worktree.py:146`); a scratch repo an agentic cell created survives and contaminates the next cell. | `-ffdx` (double `-f`). One-char fix. **(P1.)** |
| B11 | Low | CONFIRMED | C+X | Brittle `rate_limited` classification (needs `api_error_status ∈ {400,429}` **and** a substring, `runner.py:303-308`): a 529 / reworded 429 → `infra_error` → no back-off + trips the breaker. | Widen; feeds B4. |
| B12 | Low | CONFIRMED | C | `model_resolved` = first `modelUsage` key (`runner.py:312-317`) — a mid-run Opus→Sonnet fallback is mis-stamped. | Record all of `modelUsage`. |
| B13 | Low | CONFIRMED (platform) | X | `_modified_since` uses `mtime >= since` (`orchestrate.py:255`): a same-tick race on coarse-granularity filesystems (negligible on APFS). | Note as a known edge; optional `>` + sentinel-stat. |
| B14 | Low | CONFIRMED | G | `latency_s` wraps the whole subprocess (`runner.py:481-516`), so in-CLI back-offs / soft-throttle stalls inflate it. | The latency axis penalizes background throttling as if it were generation speed — note it. |

## Tier C — framing / provenance / reproducibility

| ID | Sev | Conf | Voices | Finding (where) | Verdict |
|----|-----|------|--------|-----------------|---------|
| C1 | High | CONFIRMED (git) | C | **Judge-phase status contradiction on `main`.** The merged `docs/audits/2026-07-07_literature-gap-analysis.md:21,71` cites "fable > sonnet — the lab's first REAL positive separation" as fact, but the plan doc says **"Status: planned"** (`docs/plans/active/2026-07-06_llm-judge-phase.md:3`) and CLAUDE.md:27 says **"Next scheduled / (planned)."** Judge code + `t9` + grids are on unmerged `feat/llm-judge-phase`; all judge ledgers are gitignored; the human spot-check gate (`results/judge_spotcheck.md`) is **blank**. | Reconcile the status prose; fill/run the spot-check gate before any headline; decide whether to merge the judge code + a sanitized snapshot — **user gate; ledgers stay local**. |
| C2 | High | FRAMING | G | `cost_advisor`'s marquee is **USD saving** (`analyze.py:684`) — a phantom constraint on a flat subscription; the "overpay $" prose can steer to 3-4× slower Haiku. | Lead `advise`/README with **latency + tokens**; make the flat-fee caveat load-bearing. (Folded into the mission reframe.) |
| C3 | Med | CONFIRMED | C+G | **Survivorship**: only the 2 *saturated* ledgers (`showcase`, `claude5-refresh`) are committed; every *discriminating* ledger (books, pressure, judge) is gitignored — incl. the "0 unparseable" rate that licenses A1's strict grader. | The committed tree is not a reproduction kit for **any** discriminating claim; the two-lane split (strategy §5) addresses this. |
| C4 | Med | CONFIRMED | C+G | **`t6` ships an unsafe eval**: it grants Bash and `docs/METHODOLOGY.md:90-94` says a sandbox is *required* to stop the model reading the in-repo answer key, but the runner shells `claude` natively. | Give `t6` a real sandbox (Docker) or make the runner refuse it. |

## Construct-validity findings (CV1–CV6)

The deepest findings — that the lab measures single-turn saturated tasks and concludes about models,
never running the agentic regime where the edge lives. Full detail + grounding is in the strategy doc,
[§2](2026-07-11_big-picture-mission-and-roadmap.md#2-the-convergent-verdict--construct-validity).
Summary: **CV1** no agentic cell ever ran (`mode: single` everywhere; `t2`/`t6` dormant); **CV2** Haiku
effort inert (documented); **CV3** saturation + `t8` contamination-canonical; **CV4** insular
single-author validity; **CV5** USD ≠ the cost you pay + recovery unmeasured; **CV6** headline claims
outrun evidence (`t5` mislabel, ungated judge headline, no stopping rule).

---

## Remediation ordering (matches the build plan)

- **P1 — believe the numbers / don't lose paid work:** A1, B3, C1, B5, B10, CV2, CV6 (the honesty pass
  + paid-sweep crash).
- **P2 — honest numbers / robustness:** B2, B4, B7, A5, A6, A7, C2, C4, B1.
- **P3 — hygiene / honesty:** B6, B8, B9, B11–B14, A4, A8, and the CV1/CV3–CV5 items that become the
  roadmap (agentic regime, contamination screen, two-lane split).

These map onto the repo's `tracked` + P1/P2/P3 GitHub-issue convention.
