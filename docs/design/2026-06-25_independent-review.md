# Independent review of Phase 0/1 + roadmap (2026-06-25)

Two independent voices (Codex `default`, Gemini 3.1 Pro High) reviewed `runner.py`,
`worktree.py`, `cli/main.py`, `CLAUDE.md`, `pyproject.toml`, `README.md`, and the plan.
Reconciled below; Claude verified each claim against the code before acting.

## Confirmed → act on (convergent unless noted)

| # | Finding | Severity | Phase |
|---|---------|----------|-------|
| 1 | **Cell-state leakage**: persistent worktree reused across cells; agentic T2 writes into it → cell N+1 inherits cell N's mutations. Sequential carryover (Q3 only fixed concurrent contention). | CRITICAL | **1.5** |
| 2 | **Runner envelope incomplete**: drops `stderr`/`returncode`/`argv`; `except TimeoutExpired:` has no `as e` (loses transcript); only `TimeoutExpired` caught → missing `claude`/bad cwd crashes the sweep instead of `infra_error`; `json.loads` unguarded against stdout preamble. | HIGH | **1.5** |
| 3 | **Worktree reuse under-validated**: `path.exists()` trusts half-created / wrong-repo dirs; needs `rev-parse --verify <ref>^{commit}`, health check, lock. | MED-HIGH | **1.5** |
| 4 | **Decouple run from grade** (Gemini's strongest idea): a fixed grader can't re-score old rows under a resumable ledger. Runner → raw `RunRecord` (transcript+output+cost+latency+status); grading = separate cheap pass over stored transcripts, keyed by `grader_version`. Idempotency key = (task,model,effort,variant,epoch,**grader_version**). | HIGH | **2/3** |
| 5 | **Statistical precision**: bootstrap CI over the ~60 examples *within* a cell; epochs = separate run-variance axis. 3 epochs ≠ "real". Label v1 exploratory. | MED | **4** |
| 6 | **Provenance**: stamp `claude --version`, global `~/.claude` state/hash, MCP list per ledger row (the unversioned global layer). | MED | **3** |
| 7 | **Back-off placement**: runner only classifies; the orchestrator halts on hard usage-limit (until reset date) and retries transient 429 with back-off. Reconcile the "runner backs off" wording. | MED | **3** |
| 8 | **`--estimate` projects tokens + turns** (true rate-limit proxy), not just USD. | MED | **5** |
| 9 | **Cell contract** integration test: two repeated cells prove clean isolation + full envelope capture + status + transcript + resumable identity. | adopt | **1.5** |

## Refuted / caveat

- **"Packaging broken" (Codex)** — FALSE POSITIVE from the review sandbox flattening staged files; real layout is `src/claude_ablation_lab/` and `ablation version` → `0.1.0`. No action.
- **Training-data memorization (both)** — shuffled-label gate catches *grader* leakage, not model memorization of the public T1 set. For this harness's purpose (relative deltas across model×effort×config) memorization largely cancels; it only taxes *absolute* T1 numbers. Document, don't block.

## Decision

Insert **Phase 1.5 "harden the run cell"** before Phase 2 (findings 1,2,3,9 — the convergent "#1 before Phase 2"). Adopt **run/grade decoupling** (4) as an architecture change threading through Phases 2–3. Fold 5–8 into their phases. This is the refined roadmap (see plan + CLAUDE.md build phases).
