# Infra-loading probe — the harness's core mechanism, verified

*2026-07-01. Prompted by PR #8's adversarial review, which flagged that flat
`.claude/skills/<name>.md` skills do not load. A live `claude -p` probe settled the core
assumption the whole harness rests on — one that had never actually been tested (the "verified
live" smoke was T3 under variant `none`, which exercises no infra loading).*

## What was probed

Three throwaway dirs, run with `claude -p` and `cwd` set there (no `--bare`):
- a dir whose `CLAUDE.md` embeds a secret token;
- a dir with `.claude/skills/greet/SKILL.md` (directory form), invoked via `/greet`;
- the same dir with a flat `.claude/skills/greetflat.md` (control), invoked via `/greetflat`.

## Verdict

| Mechanism | Result |
|---|---|
| worktree `CLAUDE.md` auto-loads | ✅ token echoed; a bare-dir control did not |
| `.claude/skills/<name>/SKILL.md` skill loads + invokes via `/name` | ✅ |
| flat `.claude/skills/<name>.md` | ❌ `"Unknown command"` — does **not** load |

**The harness's infra thesis is sound**: `claude -p` genuinely loads a worktree's `CLAUDE.md`
and its `SKILL.md` skills, so `variant = infra_repo@ref` does change what the model sees. The
only broken layer is **flat skills**.

## Consequence for T2 (research_toolkit) — ACTION for the private path

`~/Claude/research_toolkit/.claude/skills/*.md` are all **flat**, and there is no
`.claude/commands/` — so `/research-plan` almost certainly does not resolve. T2 has never been
run (no ledger on disk), so this was never caught: T2 would score 0 for *infrastructure* reasons
(no `research_plan.md` artifact), not model quality. **Before running T2, convert
research_toolkit's skills to the `<name>/SKILL.md` directory form** (or add
`.claude/commands/research-plan.md`). research_toolkit is the author's separate (public)
repo, so the fix lives there — but it is load-bearing for any real T2 verdict.

## Consequence for the demo (this repo)

Fixed in PR #8: `examples/demo-infra` ships `.claude/skills/project-reference/SKILL.md`
(directory), and the T4 prompt lets the skill auto-invoke by relevance while returning an empty
claim list when no reference is available — so the `without-skill` baseline grades a clean
parseable `0` (not an excluded `unparseable` "Unknown command").
