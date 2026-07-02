# Phase 6 — deferred items (decision record)

*2026-07-01. Phase D shipped plotting (D1), the anchor-strict grader (D2), and the public
demo-infra A/B (D6). Three Phase-6 items were considered and deliberately **not** built; this
records why, so each omission reads as a decision, not an oversight.*

## D3 — ApiRunner (deferred)

An `anthropic`-SDK runner conforming to the `Runner` protocol is straightforward — effort maps
1:1 to the API's `output_config.effort`, and the errors map cleanly onto the
`ok|rate_limited|infra_error|timeout|parse_fail` taxonomy. It is **not built** because it is
structurally infra-blind: it cannot exercise the `variant = infra_repo@ref` axis — the entire
reason this harness exists — and cannot run agentic tasks (T2) at all. Its only honest use is an
infra-stripped base-model baseline on T1/T3, at the cost of a real-\$ vs subscription cost split
(a new `runner_kind` ledger field + an `analyze` grouping change) and an adapter pinned to a
moving API. The `api` optional-dependency in `pyproject.toml` remains the declared seam; build
only if a concrete base-model-baseline need appears.

## D4 — probability-elicitation AUROC (deferred)

The T1 classification grader reports hard-label AUROC (== balanced accuracy), which already
answers the config-delta question. True ranking AUROC needs a per-example probability, which
changes the T1 JSON schema → changes `spec_sha` → forces a **full paid T1 re-sweep** (not a free
regrade), to measure notoriously-miscalibrated verbalized confidences. Build only for a dedicated
calibration study (Brier / ECE), and then as a *parallel* task (`t1-clf-prob-v1`), never a
replacement.

## D5 — "book spinoff" (parked)

Authoring, not code. The harness's methodology (the leakage gate, infra-sensitive variants,
run/grade decoupling, cheapest-per-successful-outcome) and the Phase-C figures could become
chapter material, but that is out of scope for this repository. Needs scoping — a chapter draft?
a reproducible appendix? just the figures? — before any work.

## D6 — hermeticity follow-ups (from the PR #11 review round, 2026-07-02)

**All four items below shipped in the `chore/d6-hardening` PR (2026-07-02)**, driven by the
"clear the runway for T2" goal — items 1 and 3 were foregrounded as the actual T2 unblockers
(task-scoped tools + mechanism evidence); 2 and 4 shipped minimal-but-done. **A standing 3-voice
adversarial review of that PR (codex + gemini + a blind Claude subagent) then found one severe
regression and several real correctness/precision gaps**, folded into the same PR before merge —
noted inline below and detailed in `docs/design/2026-07-02_d6-review.md`.

- **Task-scoped tool policy — DONE.** `Task.tools` (YAML `tools: [...]`) → the validator
  preparer computes `Prepared.disallowed_tools` = the hermetic catalog minus what the task
  declares → threaded through `Runner.run`/`_argv` as a per-call override, mirroring the
  existing `permission_mode` pattern exactly. `t2_research_plan.yaml` now declares
  `tools: [Read, Write, Bash]`, verified against the skill's own `allowed-tools` frontmatter
  (not guessed — see `docs/design/2026-07-02_t2-runway.md` §4). **Review round additions:**
  `load_task` now validates `tools:` — rejects a bare YAML scalar (`tools: Bash` parses as the
  *string* `"Bash"`, and iterating a string yields characters, not a list — a real, reproduced
  failure mode) and any name not in `KNOWN_BUILTIN_TOOLS`, loud at load time instead of a silent
  no-relaxation (codex + the blind voice, independently). `spec_sha` now includes a task's
  declared `tools` — unlike `permission_mode` (execution friction only), a tool-policy change
  changes what the cell can even do, so reusing a pre-change ledger row would silently compare
  results measured under different tool boundaries (codex, confidence 92–93, cross-confirmed
  across two independent review passes).
- **Deny-list catalog check — DONE, via live verification, not a pre-sweep probe.** The CLI
  has no tool-enumeration flag (confirmed: `--help` documents only name-accepting flags). Built
  instead from a real `-p` call passing ~40 candidate names to `--disallowedTools`: an unknown
  name gets a `"matches no known tool"` stderr warning but the CLI still proceeds (exit 0) — so
  over-including a name is free, and the probe cleanly separates real tool names from
  typos/renames. Result: `KNOWN_BUILTIN_TOOLS` (47 names in `runner.py`, exact breakdown: 42
  live-probed-as-recognized + `DesignSync` from a live `system/init` stream-json event + 2
  docs-only (`Agent`, `AskUserQuestion`) + `StructuredOutput` (see below) + `Skill`).
  `HERMETIC_DISALLOWED_TOOLS` is *derived* from it (structural, not hand-duplicated) minus the
  two always-allowed tools, + a CI subset test + a `CATALOG_VERIFIED_CLAUDE_VERSION` runtime
  gate (`cli/main.py run` **and** `estimate` — review finding: `estimate` originally built its
  own runner without the gate — hard-stop on version drift unless `--allow-unverified-tools`).
  **This same probe caught a live bug**: `"SlashCommand"`, previously in
  `HERMETIC_DISALLOWED_TOOLS`, gets the identical `"matches no known tool"` warning as a
  deliberately-fake control name. **Review-round correction to this finding's own evidence**: that
  warning alone is *not* proof of "zero protection" — a targeted follow-up probe found
  `"StructuredOutput"` gets the *same* warning yet still functionally blocks the model's
  structured-output tool call when denied (`--json-schema`, T1's mechanism, is implemented as a
  synthetic `StructuredOutput` tool call — confirmed live). The real evidence for SlashCommand
  being dead weight is the published showcase's 54-session harvest: all 18 with-skill cells
  invoked `Skill` successfully *while `"SlashCommand"` sat in this exact deny list* — so whatever
  it does or doesn't match, it demonstrably never blocked the one thing it might have been
  guarding. `StructuredOutput` itself is now excluded from `HERMETIC_DISALLOWED_TOOLS` (the same
  treatment as `Skill` — a response-shaping mechanism the harness itself requests, not an
  escape-surface tool) — this was a **live regression**: the pre-review-fix version of this PR
  would have silently broken every T1 cell the moment `capture_mechanism`'s new default
  (`stream-json`) combined with T1's `json_schema`. See D6.1 for the residual gap SlashCommand's
  removal exposes, and D6.2/D6.3 for two related follow-ups this round surfaced.
- **Mechanism capture without session files — DONE.** `ClaudeCodeRunner(capture_mechanism=True)`
  switches to `--output-format stream-json --verbose`; `parse_stream_json` collects ordered
  `tool_use` block names into `RunResult.tools_used` → `LedgerRow.tool_calls` (a
  `Counter`-derived name→count summary, JSON-string-encoded like `subscores`/`details`). Verified
  against a real captured probe (`tests/fixtures/claude_stream_json_tool_use.txt`; trimmed of a
  leaking `system/init` preamble the parser doesn't read anyway), not hand-authored. **Review
  round fix**: both fields now default to `None` ("not measured"), not `()`/`{}` — the original
  shared default made "we didn't look" indistinguishable from "we looked and saw nothing" for
  any pre-D6 ledger row or any `capture_mechanism=False` run (codex, confidence 95; exactly the
  overclaim shape PR #11's own review round already burned this project on once).
- **Sanitizer keep-list inversion — DONE.** `showcase.py`'s `STRIP_FIELDS` (deny-list) is
  retired; `KEEP_FIELDS` (allow-list, = the previously-separate `_PUBLISHED_KEYS` test pin +
  `tool_calls`) is now the sanitizer's only source of truth. A future ledger field is excluded
  by default, not published-unless-remembered. `_scan`'s path-fragment/oversized-string check
  stays as defense-in-depth on top.

### D6.1 — slash-command injection: a residual gap the SlashCommand fix exposed

Removing the dead `"SlashCommand"` deny-list entry (above) doesn't add protection back — it
removes a **false claim** of it. `--help` confirms `--disable-slash-commands` — the only flag
governing that surface — also disables **all Skills** (`--bare`'s own description: "Skills still
resolve via /skill-name" even with everything else stripped; a live `system/init` event lists
`research-plan` in both `skills` and `slash_commands`, confirming they share one resolution path
in this CLI version). Using it in `HERMETIC_DISALLOWED_TOOLS`/argv would silently zero out the
with-skill treatment arm — the exact failure mode the 2026-07-02 checkpoint review was built to
catch — so it was deliberately not adopted. **There is currently no way to block user-level
`~/.claude/commands` injection into a cell without also disabling `Skill`.**

**Correction (review round):** this was originally written as "narrow in practice today (no
task prompt or Skill output contains injectable command text)" — that's wrong for the one task
that actually matters. T2's own prompt *is* `/research-plan time-series anomaly detection...` —
exactly slash-command-shaped. If a user-level `~/.claude/commands/research-plan.md` ever exists
on the machine running a T2 cell, whether it would shadow the project skill of the same name is
**unverified** — this repo doesn't currently know the precedence rule for that name collision.
T2 doesn't run today regardless (blocked on the flat-skill conversion, `docs/design/2026-07-02_t2-runway.md`
§2), so this isn't a live exposure yet, but it must be resolved — checked, or a scrubbed
`~/.claude/commands`/`HOME` redirect adopted — **before**, not incidentally alongside, any real T2
run. Needs either a future CLI flag that separates the two mechanisms, or a different mitigation
entirely (unexplored).

### D6.2 — a second, unreconciled tool-enumeration source

A live `--output-format stream-json` session's `type: "system", subtype: "init"` event carries
a `tools` array — a second, independent live enumeration, separate from the
`--disallowedTools` name-validity probe D6 actually shipped on. It's not the primary catalog
source because it disagrees with the probe on membership (e.g. it omits `Grep`/`Glob`, both
confirmed real by the probe) and that discrepancy isn't understood — possibly it reflects
only tools "wired up" for the specific session's config rather than the full static registry.
Worth reconciling later: it could replace the version-pin gate with a true live per-sweep
enumeration (closer to the original D6 ask, "enumerates the installed CLI's tool set"), but
would need `run_sweep`/`estimate_sweep` to surface a cell's parsed init-catalog back to the CLI,
and the Grep/Glob discrepancy needs an explanation first.

### D6.3 — a native allow-list flag, not evaluated (blind-review suggestion, 2026-07-02)

**Not the same flag PR #11's review already checked and refuted.** That review's "replace the
deny-list with an allow-list flag" finding was about `--allowedTools` — confirmed there to be an
*auto-approve* list (skips the permission prompt for named tools; doesn't restrict what's
available) — and correctly refuted on those grounds. `--help` documents a second, separate flag
neither review pass had inventoried until now: `--tools <tools...>`: *"Specify the list of
available tools from the built-in set. Use "" to disable all tools... or specify tool names."*
This one genuinely restricts availability, not just approval. Not used anywhere in this repo
(`grep -rn -- "--tools" .` returns nothing outside this note). `--tools Skill` (or
`--tools Skill,Read,Write,Bash` for T2) would satisfy the harness's own "deny by default"
principle *by construction* — a future CLI version adding a tool would be excluded automatically,
eliminating the entire problem class `KNOWN_BUILTIN_TOOLS` + `CATALOG_VERIFIED_CLAUDE_VERSION`
exist to catch (though a *renamed* always-needed tool, e.g. `Skill` itself, would silently break
the allow-list rather than silently widen a deny-list — a different, not obviously smaller,
failure mode). Not a bug in what shipped — the deny-list, as built and now hardened, is correct
— just a simpler alternative worth a spike rather than a risky swap this late in an already-large,
already-tested PR. If built: needs its own probe-based verification pass (does `--tools` combine
with `--disallowedTools`? does it need the same `StructuredOutput`/`Skill` always-allowed
handling? are the semantics of `--tools ""` — "disable all" — distinct from an empty
`--disallowedTools`?) before it could replace, not just supplement, the current mechanism.
