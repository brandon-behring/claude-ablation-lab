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
(task-scoped tools + mechanism evidence); 2 and 4 shipped minimal-but-done.

- **Task-scoped tool policy — DONE.** `Task.tools` (YAML `tools: [...]`) → the validator
  preparer computes `Prepared.disallowed_tools` = the hermetic catalog minus what the task
  declares → threaded through `Runner.run`/`_argv` as a per-call override, mirroring the
  existing `permission_mode` pattern exactly. `t2_research_plan.yaml` now declares
  `tools: [Read, Write, Bash]`, verified against the skill's own `allowed-tools` frontmatter
  (not guessed — see `docs/design/2026-07-02_t2-runway.md` §4).
- **Deny-list catalog check — DONE, via live verification, not a pre-sweep probe.** The CLI
  has no tool-enumeration flag (confirmed: `--help` documents only name-accepting flags). Built
  instead from a real `-p` call passing ~40 candidate names to `--disallowedTools`: an unknown
  name gets a `"matches no known tool"` stderr warning but the CLI still proceeds (exit 0) — so
  over-including a name is free, and the probe cleanly separates real tool names from
  typos/renames. Result: `KNOWN_BUILTIN_TOOLS` (47 names, provenance-tagged: 42 live-probed +
  `DesignSync` from a live `system/init` stream-json event + 3 docs-only) in `runner.py`,
  `HERMETIC_DISALLOWED_TOOLS` now *derived* from it (structural, not hand-duplicated) + a CI
  subset test + a `CATALOG_VERIFIED_CLAUDE_VERSION` runtime gate (`cli/main.py run` hard-stops
  on version drift unless `--allow-unverified-tools`). **This same probe caught a live bug**:
  `"SlashCommand"`, previously in `HERMETIC_DISALLOWED_TOOLS`, is a confirmed-fake name — dead
  code providing zero protection. See D6.1 below for the residual gap that fix exposed.
- **Mechanism capture without session files — DONE.** `ClaudeCodeRunner(capture_mechanism=True)`
  switches to `--output-format stream-json --verbose`; `parse_stream_json` collects ordered
  `tool_use` block names into `RunResult.tools_used` → `LedgerRow.tool_calls` (a
  `Counter`-derived name→count summary, JSON-string-encoded like `subscores`/`details`). Verified
  against a real captured probe (`tests/fixtures/claude_stream_json_tool_use.txt`; trimmed of a
  leaking `system/init` preamble the parser doesn't read anyway), not hand-authored.
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
`~/.claude/commands` injection into a cell without also disabling `Skill`.** Narrow in practice
today (no task prompt or Skill output contains injectable command text), but real. Needs either
a future CLI flag that separates the two mechanisms, or a different mitigation entirely (e.g.
running cells with a scrubbed `~/.claude/commands` via `HOME` redirection — unexplored).

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
