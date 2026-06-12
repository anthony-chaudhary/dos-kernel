#!/usr/bin/env bash
# Two agents, one file — the lost update, and the referee that refuses it.
#
# Two concurrent loops both "improve" the same file. Without a referee the
# second save silently destroys the first (the classic lost update) — and both
# agents report success. With the arbiter, the second loop is REFUSED before it
# starts, with a typed reason it can act on, and admitted the moment the lane
# frees. The contrast — refused-with-a-reason vs. silently-lost-work — is the
# whole demo.
#
#   bash examples/demo/clobber_demo.sh
#
# Requires: `dos` on PATH (pip install dos-kernel) and git.
set -euo pipefail

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

echo "\$ dos init . && git init -q"
dos init . | sed 's/^/  /'
git init -q
git config user.email demo@example.com
git config user.name  "Demo"
git config commit.gpgsign false
printf 'retry budget: 3\n' > service.md
git add -A
git commit -q -m "seed: the shared service notes"
echo

echo "# ROUND 1 — no referee. Agents A and B both read service.md, work, then save."
base="$(cat service.md)"
printf '%s\nA: raised the rate limit\n' "$base" > service.md
echo "  agent A saved its line — and reports success."
printf '%s\nB: added the retry loop\n' "$base" > service.md
echo "  agent B saved its line — and reports success."
echo
echo "# Both narrated success. What does the file actually hold?"
echo "\$ cat service.md"
sed 's/^/  /' service.md
if ! grep -q '^A:' service.md; then
  echo "  -> agent A's work is GONE (the lost update) — and no narration said so."
fi
echo

echo "# ROUND 2 — the same two agents, but each asks the arbiter before starting."
echo "\$ dos lease-lane acquire --lane main --owner agent-a"
dos lease-lane acquire --lane main --owner agent-a
echo
echo "# Agent B asks at the same moment it clobbered in round 1:"
echo "\$ dos arbitrate --lane main --explain"
set +e
dos arbitrate --lane main --explain
code=$?
set -e
echo "  exit=$code  (1 = REFUSE — typed, checkable, BEFORE any work was lost)"
echo
echo "# Agent A finishes and releases; B is admitted the moment the lane frees:"
echo "\$ dos lease-lane release --lane main --owner agent-a"
dos lease-lane release --lane main --owner agent-a
echo "\$ dos arbitrate --lane main --explain"
dos arbitrate --lane main --explain
echo
echo "# Boundary: the arbiter is ADVISORY — it returns the verdict; the loop's"
echo "# compliance (B waiting) is what prevents the write. Wire the call into"
echo "# your loop's admission step, exactly as round 2 did."
