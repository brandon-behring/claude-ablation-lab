# PR #4 adversarial review — codex + gemini (2026-06-26)

External multi-model review of the phases 3–5 branch (`adversarial_review.py 4`:
codex gpt-5.5@xhigh + Gemini 3.1 Pro). **Caveat:** a big-diff guard omitted 19
files (incl. `analyze.py`, `ledger.py`, `cli/main.py`), so the voices reasoned
mostly from `orchestrate.py` — two findings were mis-attributed but pointed at
real issues. Each was tool-grounded against the code before folding.

| # | Voice | Conf | Finding | Resolution |
|---|-------|------|---------|------------|
| 1 | codex | 95 | Re-grade rows zeroed `cost_usd`/`latency_s` (Phase-3 P6), but `report` dedupes to the latest grade per `run_id` → a re-graded paid run reads as **free/instant**. | **Reverted P6.** Re-grade rows now preserve the original run's cost/latency (report already dedupes by `run_id`, so the zeroing was both needless and harmful). |
| 2 | codex / gemini | 78 / 100 | A run that succeeded but whose **grade** failed (`grader_error`) was treated as "done" → skipped on resume, dropped from analysis → permanently invisible; gemini's "infinite Claude re-call" was wrong (it skipped) but the area is real. | **Skip now requires `grade_status != "grader_error"`.** A grader_error falls through to Path 2 and re-grades the stored output for **free** (no Claude); `ok`/`unparseable` stay settled. Same fix in `regrade_ledger`. |
| 3 | codex | 87 | `_capture_output` mixed the exact artifact path with all same-basename matches and picked by mtime → a newer nested file could beat the requested path. | Prefer the **exact** `cwd/artifact` when fresh+readable; only then fall back to a nested match. |
| 4 | gemini | 95 | The shared `neutral_dir` was never cleared between `none`-variant cells → cross-cell leakage (the worktree path gets `reset_clean`; the neutral path got nothing). | `_resolve_cwd` now **wipes the neutral dir before each `none` cell** (matching worktree isolation). |
| 5 | gemini | 95 | `_modified_since` compared a float `time.time()` baseline to a (possibly floored) `st_mtime` → on coarse-resolution filesystems a fresh artifact could read as stale → false-missing 0.0 scores. | Baseline is now a **sentinel file's mtime** (`_sentinel_mtime`) — two same-filesystem mtimes, robust to coarse resolution. |
| 6 | codex | 92 | `regrade_ledger` `total = len(ok_rows)` counted the whole ledger; with `--task` it mismatched the work done. | `total` now counts **only rows for tasks in the supplied suite**. |
| 7 | codex | 96 | Unescaped `\|…\|` pipes in the Phase-4 audit table broke the markdown render. | Escaped. |
| 8 | codex | 70 | Rate-limit retries reuse the same `cwd` without a reset; a partial artifact from a throttled attempt could be captured if a retry succeeds without rewriting. | **Documented** as a v1 limitation in `run_with_backoff` (narrow: agentic + throttle-mid-write + retry-without-rewrite; a normal retry overwrites). |

Net: 6 code fixes + 1 doc fix + 1 documented limitation. 173 tests (+4 regression);
coverage 92%; ruff + black + mypy --strict clean. The cost-attribution (1) and the
grader-error-visibility (2) fixes are the substantive ones — both are the harness's
own honesty contract (a paid run must never look free; a failure must never become
invisible).
