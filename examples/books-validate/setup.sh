#!/usr/bin/env bash
# Materialise the books-validate fixture as a local git repo for the AGENTIC task (t6).
# The agent works in a worktree of this repo, edits chapter.mdx in place, and can run
# validate_fixture.py itself. The grader scores the edited chapter with check.py + expected.json
# read from the CANONICAL examples/books-validate/ dir in the harness repo — NEVER from here — so
# the answer key is never in reach of the agent.
#
# CRITICAL: this repo ships ONLY the agent-visible whitelist. expected.json and check.py (the
# answer key) are deliberately excluded — a copy of check.py's per-item output would reveal the
# required additions and tripwires. A test asserts they are absent.
#
# grids/books-pilot.yaml points task t6_books_validate_agent at <dest>@v1.
#
# Usage:  examples/books-validate/setup.sh [dest]     (default: ./.books-validate)
# Run from the REPO ROOT: the grid references the repo by the literal string `.books-validate`.
set -euo pipefail
DEST="${1:-$PWD/.books-validate}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MARKER=".books-validate-generated"

# The agent-visible whitelist — expected.json / check.py / setup.sh / README.md are NOT here.
WHITELIST=(chapter.mdx labels.json references.json files.json CLAUDE.md validate_fixture.py)

case "$DEST" in
  "" | "/" | "$HOME" | "$HOME/" | "." | "./") echo "refusing to use DEST='$DEST'" >&2; exit 1 ;;
esac
if [ -e "$DEST" ] && [ ! -f "$DEST/$MARKER" ]; then
  echo "DEST='$DEST' exists and is not a generated books-validate (no $MARKER) — refusing to clobber; pass a fresh path." >&2
  exit 1
fi

rm -rf "$DEST"
git init -q "$DEST"
touch "$DEST/$MARKER"                         # marker first: a crash below must not leave an
                                              # unmarked dir the clobber-guard then refuses forever
git -C "$DEST" config user.email demo@example.com
git -C "$DEST" config user.name demo
git -C "$DEST" config commit.gpgsign false
git -C "$DEST" checkout -q -b main

for f in "${WHITELIST[@]}"; do
  cp "$HERE/$f" "$DEST/$f"
done
git -C "$DEST" add -A
git -C "$DEST" commit -q -m "books-validate: seeded chapter + registries + fidelity validator"
git -C "$DEST" tag v1

echo "books-validate ready at: $DEST (ref: v1)"
echo "shipped: ${WHITELIST[*]}"
echo "withheld (answer key): expected.json check.py"
