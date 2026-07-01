#!/usr/bin/env bash
# Materialise the demo-infra A/B as a local git repo with the two refs the ablation
# showcase compares:
#   without-skill  — a baseline project (no skill)
#   with-skill     — adds .claude/skills/project-reference/SKILL.md (the Project Vega reference)
#
# NOTE: Claude Code loads project skills only from `.claude/skills/<name>/SKILL.md`
# (a directory) — a flat `.claude/skills/<name>.md` does NOT load. Verified live.
#
# grids/showcase.yaml points task t4_demo_infra at <dest>@with-skill / @without-skill.
#
# Usage:  examples/demo-infra/setup.sh [dest]      (default: ./.demo-infra)
set -euo pipefail
DEST="${1:-$PWD/.demo-infra}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MARKER=".demo-infra-generated"

# Refuse dangerous / unexpected destinations before rm -rf.
case "$DEST" in
  "" | "/" | "$HOME" | "$HOME/" | "." | "./") echo "refusing to use DEST='$DEST'" >&2; exit 1 ;;
esac
if [ -e "$DEST" ] && [ ! -f "$DEST/$MARKER" ]; then
  echo "DEST='$DEST' exists and is not a generated demo-infra (no $MARKER) — refusing to clobber; pass a fresh path." >&2
  exit 1
fi

rm -rf "$DEST"
git init -q "$DEST"                          # `checkout -b` below is compatible with git < 2.28
git -C "$DEST" config user.email demo@example.com
git -C "$DEST" config user.name demo
git -C "$DEST" config commit.gpgsign false   # don't inherit a user's global commit signing
git -C "$DEST" checkout -q -b without-skill

# without-skill: the baseline (a README + our marker; no .claude/)
cp "$HERE/content/README.md" "$DEST/README.md"
touch "$DEST/$MARKER"
git -C "$DEST" add -A
git -C "$DEST" commit -q -m "demo-infra: baseline project (no skill)"

# with-skill: the same project plus the reference skill (directory + SKILL.md format)
git -C "$DEST" checkout -q -b with-skill
mkdir -p "$DEST/.claude/skills/project-reference"
cp "$HERE/content/project-reference.md" "$DEST/.claude/skills/project-reference/SKILL.md"
git -C "$DEST" add -A
git -C "$DEST" commit -q -m "demo-infra: add the project-reference skill"

git -C "$DEST" checkout -q without-skill
echo "demo-infra ready at: $DEST"
echo "refs: with-skill, without-skill"
