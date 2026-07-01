#!/usr/bin/env bash
# Materialise the demo-infra A/B as a local git repo with the two refs the ablation
# showcase compares:
#   without-skill  — a baseline project (no skill)
#   with-skill     — adds .claude/skills/project-reference.md (the Project Vega reference)
#
# grids/showcase.yaml points task t4_demo_infra at <dest>@with-skill / @without-skill.
#
# Usage:  examples/demo-infra/setup.sh [dest]      (default: ./.demo-infra)
set -euo pipefail
DEST="${1:-$PWD/.demo-infra}"
HERE="$(cd "$(dirname "$0")" && pwd)"

rm -rf "$DEST"
git init -q -b without-skill "$DEST"
git -C "$DEST" config user.email demo@example.com
git -C "$DEST" config user.name demo

# without-skill: the baseline (a README, no .claude/)
cp "$HERE/content/README.md" "$DEST/README.md"
git -C "$DEST" add -A
git -C "$DEST" commit -q -m "demo-infra: baseline project (no skill)"

# with-skill: the same project plus the reference skill
git -C "$DEST" checkout -q -b with-skill
mkdir -p "$DEST/.claude/skills"
cp "$HERE/content/project-reference.md" "$DEST/.claude/skills/project-reference.md"
git -C "$DEST" add -A
git -C "$DEST" commit -q -m "demo-infra: add the project-reference skill"

git -C "$DEST" checkout -q without-skill
echo "demo-infra ready at: $DEST"
echo "refs: with-skill, without-skill"
