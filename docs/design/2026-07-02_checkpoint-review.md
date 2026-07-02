# Checkpoint adversarial review — pre-sweep go/no-go (2026-07-02)

**Subject:** the decision "the 2-cell pilot justifies spending the 54-cell public showcase sweep,"
plus everything frozen by that spend (grid, task prompts, grader, verdict semantics, pre-registered
policies). Post-run buildables (sanitizer, README, figures) were explicitly out of scope — the
Phase-C PR review covers those with artifacts in hand.

**Protocol:** three independent voices, blind to each other, each briefed to *refute* the go:
a fresh-context Claude agent with repo access, plus codex (gpt-5.5) and Gemini 3.1 Pro via
`ask_model.py` with staged artifacts (pilot ledger + transcripts + outputs, grid, tasks, analyze/
anchor/orchestrate/runner/cli sources, METHODOLOGY, setup.sh, and the checkpoint report verbatim).
Findings pooled, then tool-grounded against the live repo, session transcripts, and CLI before
accept/refute. Voice outputs: `.consult/` (lever) + the agent transcript; grounding evidence below.

**Tally: 3/3 voices returned GO BLOCKED.** After grounding: no voice found a defect in the
*measurement machinery* (one voice explicitly filed counter-evidence that it is sound); every
confirmed blocker is in the *experiment's framing, pre-registration, or environmental hygiene* —
all fixable at zero quota cost. Verdict: **go, conditional on the fix package below.**

## Confirmed findings (deduped across voices; grounded)

| # | Sev | Finding | Grounding | Resolution |
|---|-----|---------|-----------|------------|
| 1 | BLOCKER | Checkpoint report claimed the skill was invoked "by relevance alone"; the T4 prompt explicitly names and orders consultation of the skill (3/3 voices) | `tasks/t4_demo_infra.yaml:26` | Reword everywhere: the demo measures **prompt-directed skill consultation** — same prompt both arms, only the infra differs. Never claim autonomous discovery. |
| 2 | BLOCKER | The pre-registered "amend to explicit invocation and publish the re-run as headline" is (a) a rescue-headline selection loophole (2 voices) and (b) *incoherent* — the prompt is already explicit; the stronger slash-invoke form breaks the control arm (probe: bare `/cmd` → "Unknown command" → unparseable) (1 voice) | claims doc; 2026-07-01 infra-loading probe | **Replace with first-run primacy:** the original-spec run is always the published primary outcome. If not 6/6 nonzero pairs → publish `real=no` honestly + characterize which configs failed (from session transcripts). Any re-run is a clearly-labeled follow-up in an appendix, written to a **fresh ledger file**. |
| 3 | BLOCKER | Fixture README (present in BOTH refs, readable from every cell cwd) explains the entire A/B design to the subject model — arm identity, expected outcome, the other ref's skill path | `git -C .demo-infra show without-skill:README.md` (verified verbatim) | Neutralize `examples/demo-infra/content/README.md` to a bland project README; regenerate refs (new infra shas). It cannot inflate the control arm (no gold text in it) but invites demand-characteristics behavior and embarrassing transcripts. |
| 4 | BLOCKER | Control-arm validity rests on default-deny luck: cells load the user's MCP servers (pilot ledger: `github`, `playwright`) and the gold text is public on GitHub (`tasks/t4_demo_infra.yaml` on main) — a high-effort control cell has a *path* to fetch its own gold. Worktrees also sit under the harness repo root, so the harness `CLAUDE.md` (which names the fixture and the A/B) is an ancestor-memory candidate for every cell | pilot ledger `mcp_servers`; `worktree.py:33`; `CLAUDE.md:64-66`; `claude --help` confirms the guard flags exist | Hermeticity by construction: runner argv += `--strict-mcp-config` + `--disallowedTools WebSearch WebFetch` (Skill/Read stay available — the treatment arm needs them); expose `--worktree-base` on the run CLI and run the showcase with a base **outside the repo**. |
| 5 | MAJOR | "Pre-registered" policies existed only in a plan file at checkpoint time, not in the repo; publishing that framing without a pre-sweep commit would be post-hoc | METHODOLOGY.md @ a9a94e9 (grep: no retry/amendment text) | Commit the full pre-registration to METHODOLOGY **before** any sweep cell: resume-pass policy (≤2), in-cell rate-limit backoff (separate mechanism, `orchestrate.py:167-192`), completeness gate (headline requires 3/3 epochs ok × 6 configs × both arms), first-run primacy, fresh-ledger rule. |
| 6 | MAJOR | Aggregation has no completeness gate: `compare` averages whatever ok rows exist, so arm-correlated infra failures would bias the headline silently | `analyze.py:257,320` | Covered by the pre-registered completeness gate (#5); checked on the ledger before the verdict is quoted. |
| 7 | MAJOR | `_LATEST_OK` partitions by `run_id` only — regrade-shadowing works and resume is safe (failed rows are filtered by grade status), but a same-ledger re-run of amended cells would silently mix specs in averages | `analyze.py:89-90`; `orchestrate.py:474` (settled-row skip) | Fresh-ledger-per-run rule (#2/#5). Spec-aware partitioning noted as backlog hardening. |
| 8 | MAJOR | The sidecar transcript (argv + final stdout) cannot prove the Skill invocation; `num_turns=3` is consistent with a plain `Read` of the skill file | `runner.py:291-302` | For the pilot, proof exists at the session level: the session JSONL contains the literal `Skill {"skill": "project-reference"}` tool call (excerpt below). Post-run, harvest the same evidence for all with-skill cells; runner `stream-json` capture = backlog, not a pre-sweep change. |
| 9 | MAJOR | No opus cell has ever executed in this harness (smoke = haiku/sonnet only; the v1 sweep never ran), yet 18/54 cells and the priciest configs ride on extrapolation; t3 has never been graded live under anchor **v2** (smoke was v1) | `results/smoke.jsonl`; `experiments/log.txt` | Extended pilot before the sweep (~5 cells): re-pilot the t4 haiku/low pair post-fixes + a t4 **opus/high** pair + 1 graded t3 cell. |
| 10 | MINOR | Checkpoint report wording errors: ledger field is `run_status` (report table showed a null `status` column); default anchor is whitespace-normalized, not "char-exact" (that's `anchor_strict`); the runner does not itself strip session-nesting env vars (the invocation wrapper does) | `pilot.jsonl` keys; `anchor.py` docstring; `runner.py:38` | Corrected here; README/METHODOLOGY already state the anchor distinction correctly. |
| 11 | MINOR | Cost projection understated: extrapolating the haiku calibration cell ignores sonnet/opus pricing and high-effort inflation — realistic ≈ $4–8 quota-equivalent, 30–60+ min, not $1.20–1.60 | `estimate` docstring's own 2–5× warning | Expectation reset at the gate; `estimate`'s floor framing already discloses this. |

## Refuted findings (with grounding)

- **"Transcript cannot prove skill invocation" as a blocker** — true of the *sidecar*; refuted as
  unprovable: the with-skill session JSONL shows the exact `Skill {"skill": "project-reference"}`
  tool_use followed by "Launching skill: project-reference". Mechanism is proven for the pilot.
- **"Retry policy violates code behavior — orchestrate only retries rate_limited"** — misread:
  the pre-registered policy governs *resume passes* (re-invoking `ablation run` on the same ledger;
  settled rows skip at `orchestrate.py:474`, failed rows re-run), not in-cell retries. In-cell
  rate-limit backoff is a separate, complementary mechanism. Both now spelled out in METHODOLOGY.
- **"setup.sh not reproducible — content/ files missing"** — staging artifact of the review itself
  (`examples/demo-infra/content/` was not staged to the external voices); both files exist and
  setup ran clean.
- **"Expand statistical headroom by treating epochs as pairs"** — pseudoreplication; epochs are
  repeated measures of a config, not independent pairs. The pairing unit stays (model, effort).
- **"Sanitizer doesn't exist"** — sequencing, not a defect: it is Phase C step 9, built after the
  run, reviewed in the PR.
- **t3 risk overstatement (self-correction by a voice):** t3 graded live 4/4 at 1.0 (haiku/sonnet ×
  low/high, 2026-06-26 smoke, grader v1) and its stored outputs would pass the v2 floors; the
  extended pilot's single t3 cell closes the v2-live gap cheaply.

## Mechanism evidence (session transcript excerpt, with-skill pilot cell)

```
TOOL_USE: Skill | input: {"skill": "project-reference"}
  RESULT: Launching skill: project-reference
```
(session f20041c4…, worktree `.demo-infra@bab3a09`; the without-skill cell made zero tool calls and
returned `{"claims":[]}` with "The 'project-reference' skill is not listed in my available skills.")

## Outcome

Review recommendation: **go, conditional on the fix package** (wording, first-run-primacy
pre-registration committed pre-sweep, fixture neutralization, hermeticity flags + out-of-repo
worktree base, extended 5-cell pilot). User ratification of the package and the extended-pilot
spend happens at the sweep gate; the 54-cell sweep remains a separate explicit go after the
extended pilot passes.
