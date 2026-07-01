# demo-infra

A tiny synthetic project used by claude-ablation-lab's public showcase to demonstrate a
`variant = infra_repo@ref` A/B. The `with-skill` ref ships a
`.claude/skills/project-reference/SKILL.md` that the `without-skill` ref lacks; task
**t4_demo_infra** asks Claude to quote the Project Vega reference, so only the `with-skill`
working directory can — a large, honest delta the `anchor` grader measures.
