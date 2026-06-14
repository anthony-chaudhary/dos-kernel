#!/bin/sh
# two_hosts_one_gate.sh — the docs/342 M5 (P-SPOKEN) existence proof, runnable.
#
# Stand up TWO independent agent hosts against ONE shared repo + ONE lease WAL,
# drive a real concurrent run where they race on an overlapping region, and show
# host-B's colliding write REFUSED by the SAME gate (`dos apply`) reading the SAME
# WAL — with NO host-specific collision check re-implemented on the B side. Both
# hosts route their pre-effect write check through the one `dos` binary; neither
# carries any overlap logic of its own.
#
# The versioned verb contract this exercises (all three are public `dos` CLI
# surfaces, the shared effect-language docs/340 §3.1 calls the winning move):
#   1. `dos lease-lane acquire`  — host-A books its region in the shared WAL
#   2. `dos lease-lane live`     — read the one WAL both hosts share
#   3. `dos apply`               — the BINDING pre-effect gate (docs/126 P1);
#                                  exit 1 = REFUSE, exit 0 = ALLOW (verdict IS
#                                  the exit code)
#   4. `SCOPE_ESCAPE`            — the closed-vocabulary refusal token a refused
#                                  apply carries (dos.toml [reasons] data)
#
# The witness is NOT this script's narration. It is: (a) the WAL record of A's
# lease, (b) the gate's exit code + typed SCOPE_ESCAPE on B's colliding write,
# (c) the file UNCHANGED on disk after the refusal, (d) an in-lane write by B
# that PASSES. A self-report cannot produce (a)–(d).
#
# Reproducible end-to-end: needs only `dos` (pip install dos-kernel) and git.
# Usage:   sh two_hosts_one_gate.sh            # uses a throwaway tempdir
#          DEMO_DIR=/path/to/empty/dir sh two_hosts_one_gate.sh
#
# Portable POSIX sh; runs the gate by EXIT CODE (the host-agnostic integration
# tier — examples/playbooks/cookbook-exit-code-tier.md), so it does not depend on
# any one host's hook-exec semantics. The same `dos apply` call is what a
# pre-commit hook or a PreToolUse binding would run; here we read its exit code
# directly so the proof is identical on every OS.
set -eu

DEMO_DIR="${DEMO_DIR:-$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/m5_two_hosts.$$")}"
mkdir -p "$DEMO_DIR"
cd "$DEMO_DIR"

say() { printf '\n=== %s ===\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. One shared repo, two disjoint lanes (src, ui), the SCOPE_ESCAPE reason.
#    This single repo + its single WAL is the shared ground both hosts point at.
# ---------------------------------------------------------------------------
say "0. one shared repo + WAL; two disjoint lanes: src/** and ui/**"
git init -q .
git config user.email demo@example.com
git config user.name  demo
mkdir -p src/auth ui
printf 'def login(): ...\n' > src/auth/login.py
printf 'export const Page = () => null\n' > ui/page.tsx

cat > dos.toml <<'TOML'
workspace = "."

# Two concurrent lanes whose trees are DISJOINT — a fleet may edit src and ui
# in parallel iff each stays in its own tree (the region lock, docs/89).
[lanes]
concurrent = ["src", "ui"]
exclusive  = ["global"]
autopick   = ["src", "ui"]

[lanes.trees]
src    = ["src/**"]
ui     = ["ui/**"]
global = ["**/*"]

# The closed-vocabulary refusal token the apply-gate carries (docs/126):
# host policy, declared as data — never a reasons.py edit.
[reasons.SCOPE_ESCAPE]
category = "MISROUTE"
summary  = "a staged write escapes the lane the run holds, or collides with a sibling's live lease"
fix      = "narrow the write to the held lane's tree, or take the lane the write targets"
TOML

git add dos.toml src ui
git commit -qm "src/SETUP: seed src + ui lanes and the SCOPE_ESCAPE reason"
dos --workspace . doctor 2>/dev/null | grep -i 'concurrent lanes' || true

# ---------------------------------------------------------------------------
# 1. HOST A (e.g. a Claude Code session) books lane `src` in the shared WAL.
#    `dos init --hooks claude-code` would wire A's PreToolUse/commit gate; here
#    A simply takes its lease through the public verb. The host_id stamped is A's.
# ---------------------------------------------------------------------------
say "1. HOST A acquires lane src — writes the shared WAL"
DISPATCH_HOST_ID=host-A-claude-code \
  dos --workspace . lease-lane acquire \
      --lane src --kind cluster --owner host-A-claude-code >/dev/null
dos --workspace . lease-lane live --pretty

# ---------------------------------------------------------------------------
# 2. HOST B (a DIFFERENT host — e.g. Cursor, wired by `dos init --hooks cursor`)
#    races on the SAME region. B re-implements NO collision check: it runs the
#    same `dos apply` over the same WAL. Two ways B can collide, both refused by
#    the one gate:
#       (a) B writes src/** while holding lane ui  → escapes B's own lane
#       (b) B writes src/** claiming lane src       → collides with A's live lease
# ---------------------------------------------------------------------------
say "2a. HOST B (holds lane ui) tries to write src/auth/login.py — escapes its lane"
BEFORE="$(cat src/auth/login.py)"
set +e
OUT_A="$(DISPATCH_HOST_ID=host-B-cursor \
  dos --workspace . apply --lane ui --file src/auth/login.py --json)"
RC_A=$?
set -e
printf '%s\n' "$OUT_A"
printf 'gate exit: %s\n' "$RC_A"
[ "$RC_A" -eq 1 ] || fail "expected REFUSE (exit 1) for B's out-of-lane write, got $RC_A"
printf '%s' "$OUT_A" | grep -q '"reason_class": "SCOPE_ESCAPE"' \
  || fail "expected a typed SCOPE_ESCAPE refusal"

say "2b. HOST B claims lane src (A's lane) and writes src/auth/login.py — WAL collision"
set +e
OUT_B="$(DISPATCH_HOST_ID=host-B-cursor \
  dos --workspace . apply --lane src --file src/auth/login.py --json)"
RC_B=$?
set -e
printf '%s\n' "$OUT_B"
printf 'gate exit: %s\n' "$RC_B"
[ "$RC_B" -eq 1 ] || fail "expected REFUSE (exit 1) for B's colliding write, got $RC_B"
printf '%s' "$OUT_B" | grep -q '"reason_class": "SCOPE_ESCAPE"' \
  || fail "expected a typed SCOPE_ESCAPE refusal on the WAL collision"
printf '%s' "$OUT_B" | grep -q 'collides with a live lease' \
  || fail "expected the collision reason to name A's live lease"

# ---------------------------------------------------------------------------
# 3. The filesystem witness: B's refused write never touched the tree.
# ---------------------------------------------------------------------------
say "3. filesystem witness — A's region is UNCHANGED on disk after B's refusal"
AFTER="$(cat src/auth/login.py)"
[ "$BEFORE" = "$AFTER" ] \
  && echo "UNCHANGED — the refusal held; no colliding byte landed" \
  || fail "src/auth/login.py CHANGED — the gate did not bind"

# ---------------------------------------------------------------------------
# 4. The control: B's IN-LANE, non-colliding write PASSES the same gate.
#    Proves the gate refuses the collision, not every write — same binary, same WAL.
# ---------------------------------------------------------------------------
say "4. control — HOST B's in-lane write (lane ui, file ui/page.tsx) PASSES"
set +e
OUT_OK="$(DISPATCH_HOST_ID=host-B-cursor \
  dos --workspace . apply --lane ui --file ui/page.tsx --json)"
RC_OK=$?
set -e
printf '%s\n' "$OUT_OK"
printf 'gate exit: %s\n' "$RC_OK"
[ "$RC_OK" -eq 0 ] || fail "expected ALLOW (exit 0) for B's in-lane write, got $RC_OK"

# ---------------------------------------------------------------------------
# Result: two independent hosts (A=host-A-claude-code, B=host-B-cursor), ONE
# refusal mechanism (`dos apply` over `apply_gate.decide`), ONE WAL. B carries no
# collision logic; the gate read A's lease from the shared WAL and refused.
# ---------------------------------------------------------------------------
say "PROVEN — two hosts, one gate, one WAL"
echo "host A: host-A-claude-code (held lane src, recorded in the WAL)"
echo "host B: host-B-cursor (refused by the SAME dos apply gate reading that WAL)"
echo "verb contract: dos lease-lane acquire + dos lease-lane live + dos apply + SCOPE_ESCAPE"
echo "demo dir: $DEMO_DIR"
