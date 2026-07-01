# demo-infra — the showcase A/B

The reproducible headline of claude-ablation-lab: a self-contained `infra_repo@ref` A/B that
shows the harness detecting a **skill's** effect, with no private infra.

## What it is

`setup.sh` builds a tiny local git repo with two refs:

| ref | contents |
|-----|----------|
| `without-skill` | a baseline project (a README, no `.claude/`) |
| `with-skill` | the same project **plus** `.claude/skills/project-reference.md` — the *Project Vega* reference |

Task **`t4_demo_infra`** asks Claude to extract three verbatim quotes from the Project Vega
reference. Under the `with-skill` worktree the reference is in context, so the quotes are exact
substrings and the shipped `anchor` grader scores ~1.0; under `without-skill` Claude has never
seen "Project Vega" and cannot quote it, so it scores ~0. The gap is large and **honest** — the
skill supplies genuinely-needed knowledge, not a rigged nudge.

## Run it

```bash
examples/demo-infra/setup.sh          # → ./.demo-infra  (refs: with-skill, without-skill)
ablation run tasks/ grids/showcase.yaml --task t4_demo_infra
ablation compare results/showcase.jsonl \
  --a .demo-infra@without-skill --b .demo-infra@with-skill      # is the difference real?
```
