# Phase 3 review — grid + ledger + orchestrator (2026-06-25)

Two specialist reviewers ran on commit `5d64e71` (branch `feat/phases-3-5-sweep`):
a **silent-failure hunter** and a **general code reviewer**. Both converged on the
solid parts (cross-validated below) and surfaced **10 findings — 0 false positives**
(each cross-checked against the code before folding). Theme: the Phase-2 lesson
("never convert a failure into a score") reappears in the orchestration layer.

All 10 are fixed in the follow-up commit; each has a regression test.

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| P1 | HIGH | Resume/re-grade keyed only on `(cell, grader_version)` — a changed prompt/seed/gold would **stale-skip** or **grade old output against new gold** (a confident *wrong* score). | `spec_sha` (prompt+schema+gold fingerprint) on every row; skip requires a matching `spec_sha`; a stored run is reused only when `spec_sha` matches, else a fresh run. |
| P2 | HIGH | A grader that raises crashed the whole sweep **and** discarded the just-paid run (row appended after grading). | `_safe_grade` wraps `grade()` → `grader_error`; output persisted *before* grading, so the paid run is always recorded + re-gradable. |
| C1 | HIGH | Re-grade paths dropped the run-level `artifact_missing` marker (`details=score.details`). | `_regrade_row` carries `artifact_missing` forward + adds `regrade_of`. |
| C2 | MED | Duplicate task ids silently overwrote each other in the grader/prepared maps → every shadowed cell mis-graded. | `_resolve_tasks` raises `ValueError` on duplicate ids. |
| P3 | MED | `_capture_output` could read a committed file (restored by `reset_clean`) as the model's artifact → quality-0 scored high. | Only files with `mtime >= run start` qualify; reads guarded (`OSError/UnicodeError` → missing). |
| P4 | MED | `load_rows` silently dropped **any** unparseable line → a corrupt middle row silently re-runs (re-pays) a cell. | Tolerate only a truncated **final** line; raise `ValueError` on mid-file corruption. |
| P5 | MED | An all-`grader_error` sweep (e.g. missing validator) reported `ran=total, failed=0` — looked perfect, graded nothing. | `SweepSummary` gains `graded_ok/unparseable/grader_error`; CLI prints them + a warning. |
| P6 | MED | Re-grade rows duplicated the original run's `cost_usd` → naive Phase-4 cost sums over-count. | Re-grade rows zero `cost_usd`/`latency_s` (the re-grade incurred no model cost). |
| P7 | LOW | `_global_layer_digest` `read_bytes()` could raise on an unreadable file, aborting the sweep at startup (violates the provenance "never raise" contract). | Wrapped in `try/except OSError`. |
| P8 | LOW | Hard-cap detection required two English substrings (brittle). | Relaxed to the `"usage limit"` account-cap phrase; `max_retries` still bounds a missed detection. |

## Cross-validated as correct (both reviewers, not re-litigated)
- `_grade`/`grade_run` short-circuit every non-`ok` run to `grader_error` (infra ≠ quality).
- Halt-on-hard-limit leaves the ledger resumable (the halt cell appends no row; `done`/`ok_rows` updated only after a successful append).
- `append_row` flush-per-line + deterministic `expand_grid` order = genuine crash-safe resume.
- `reset_clean` `.git`-is-file guardrail; bad-variant catch is appropriately narrow.
- `mcp_servers` colon-name parse (`partition(": ")`); ledger JSON-string round-trip.

Result: 152 tests (+7 regression), coverage 95% (orchestrate 94%, graders ≥96%);
ruff + black + mypy --strict clean.
