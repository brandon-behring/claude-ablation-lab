# `advise` (cost-advisor) — 3-voice adversarial review

Branch `feat/cost-advisor` (Phase 1: the cost-frontier verdict). Voices: **codex** (gpt-5.5 @ xhigh),
**gemini** (3.1 Pro), and a **blind Claude subagent** (independent, ran the suite + synthetic repros).
Every finding was tool-grounded before acceptance. Unusually high signal: the *selection* logic was
sound and well-tested; the defects clustered in the **presentation/aggregation** layer, which the
first cut under-tested — and one was a selection bug the voices didn't reach but self-review did.

## Confirmed & fixed (before merge)

| # | Finding | Voice(s) | Fix |
|---|---|---|---|
| 1 | **Best-floor selection.** A reflex that itself fails (0.0) drops the non-inferiority floor to ~0, so `advise` recommends the cheapest *failing* config over a working pricier one. | self-review (verified; voices didn't test the 3-config case) | Floor at `best − margin`, not `reflex − margin`. Reflex is used only for the saving. |
| 2 | **Σ overpay banks vacuous rows** — on the *shipped* ledger the all-zero without-skill control contributed $0.099 = **37%** of the $0.269 headline; README hand-picked 2 rows to get $0.17, so tool ≠ doc. | subagent (HIGH, confirmed on shipped data), codex C5 | `vacuous = best ≤ margin`; excluded from Σ. Σ now = **$0.1704**, matching the (corrected) README exactly. Golden test pins it. |
| 3 | **`_advice_why` mislabel** "all configs score 0" fired on `reflex_value` alone — false when a cheaper config succeeded. | gemini (critical, 100), codex (91), subagent | Deleted `_advice_why`; the (correct) explanation is built once in `cost_advisor.note`. |
| 4 | **Dead `note`** — carefully built, never rendered; the terse renderer duplicated a buggy subset. | gemini (100) | `note` is now the single source of truth and is rendered. |
| 5 | "cheaper" claimed even at **equal cost** (tie-break picks a same-cost config). | gemini (95) | `note` says "equal cost" when `cost_saving == 0`. |
| 6 | "−0.010 ≤ δ" is **trivially true** (negative ≤ positive). | gemini (95) | Shows the drop *magnitude*: "0.010 quality drop (≤ margin 0.02)". |
| 7 | **Reflex parsing** accepted `opus/`, `/high`, `opus/high/typo` → silent wrong reflex. | codex (92) | Require exactly two non-empty parts; else `ValueError`. |
| 8 | `advise` ignored the **validity flags** `report` surfaces (leakage / mixed spec / grader-version / unparseable) → a suspect cell could read as a clean downgrade. | codex (88) | `suspect` flag on the row + `⚠suspect` in the note + legend. |
| 9 | **Overclaim** in the docstring/METHODOLOGY: "different-config epochs aren't matched pairs, a p-value would be theatre." The configs **do** share task examples — it's a data-plumbing + low-power limit, not a matched-pairs impossibility. | subagent (2a) | Rewrote both to the honest framing (`report` carries the CI; per-example scores aren't plumbed to this layer). |
| 10 | **Absolute quality hidden** — only Δ vs reflex was shown, so a near-failing recommendation could hide behind a 0.000 delta. | subagent (2b) | Added a `qual` column (recommendation's absolute mean) + an `n` (epochs) column. |
| 11 | "$ + latency **saved**" overstates a signed quantity (`latency_saving` can be negative). | subagent (#4), README:9 | Legend: "Δlat s = reflex − use (negative = cheaper yet slower)"; shown signed. |
| 12 | **Tests didn't pin the buggy surface** — `_advice_why` untested, Σ never asserted numerically. | subagent (#5) | Added: best-floor/inverted-gradient, vacuous + numeric Σ (CLI), suspect, equal-cost, absolute-quality, stricter-parsing, and a **golden `advise`-on-showcase** test. |
| 13 | README table/`$0.17` didn't match `advise` output. | codex (C5) | README now shows the verbatim 3-row output incl. the `n/a` control + Σ $0.1704. |
| 14 | Quickstart "loses no quality" vs. a 0.02 default margin. | codex (94) | "within the quality margin". |

## Deferred (recorded, not silently dropped)
- **Per-group unequal-epoch flagging** (codex C2, conf 78): the `n` column is now shown, but a warning
  when configs in a group have *different* epoch counts is not — it doesn't occur on current
  uniform-`n=3` data. Backlog.
- **A real paired test** (the deeper form of #9): would need per-example/per-epoch scores plumbed into
  `advise`; a larger change. For now the margin is honestly labeled a point estimate and `report`
  carries the bootstrap CI.
- **CI columns in `advise`**: `ci_low/ci_high` stay in `report`; `advise` shows absolute quality and
  points to `report` for uncertainty (table-width trade-off).

## Refuted
None outright — the batch was almost entirely legitimate. The nearest was the subagent's "selection
logic is correct," which was *incomplete* (it didn't test the 3-config inverted gradient) rather than
wrong; self-review caught the gap and finding #1 addresses it.

## Tally
14 confirmed & fixed · 3 deferred with reasons · 0 refuted · `make ci` green (**283 tests**, ruff +
black + mypy clean, coverage floor held). Zero new quota.
