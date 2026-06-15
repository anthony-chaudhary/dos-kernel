# Playbook 10 — two independent hosts, one gate, one WAL (P-SPOKEN)

> **Goal:** prove the collision-refusal is **universal, not per-host** — two
> *different* agent hosts coordinate one concurrent run through DOS's verbs, and
> the second host's colliding write is refused **by the same gate reading the same
> lease WAL**, with **no host-specific collision check re-implemented**. This is
> the existence proof behind [docs/342](../../docs/342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md)'s
> **P-SPOKEN** property and [docs/340 §3.1](../../docs/340_what-dos-means-the-winning-move-when-narration-dies.md)'s
> winning move: *own the verbs so hosts share one check instead of each
> re-deriving its own.*

This is the cross-host sibling of the single-repo lane demos
([playbook 02](02_polyglot-web-service.md), [05](05_infra-monorepo.md)). Those
show one fleet, one host. This one shows the property that makes DOS a *substrate*
and not just a checker: **a host it has never seen routes its pre-effect write
through the same gate and gets the same refusal — because the gate, the WAL, and
the refusal vocabulary are the shared standard, not anything either host owns.**

---

## What "two hosts, one gate, one WAL" means

| Piece | What it is | Who owns it |
|---|---|---|
| **Host A** | a Claude Code session — wired by `dos init --hooks claude-code` | the host |
| **Host B** | a **different** host (Cursor — `dos init --hooks cursor`) | the host |
| **The WAL** | one append-only lease journal under `.dos/` in the shared repo | DOS (neutral) |
| **The gate** | `dos apply` → `apply_gate.decide` (declared-tree escape + the `ratio_max=0` sibling-collision floor) | DOS (neutral) |
| **The refusal** | the closed-vocabulary `SCOPE_ESCAPE` token | DOS (`dos.toml [reasons]`) |

The load-bearing fact: **host B re-implements no overlap logic.** Both hosts run
the same `dos` binary; B's pre-effect check is the same `dos apply` call A would
make. The collision is adjudicated from A's lease *in the one WAL both hosts
share* — exactly the way every app over TCP shares one sequencing check instead of
each re-deriving its own ([docs/335](../../docs/335_tcp-for-agents-validating-the-reliability-analogy.md)).

> **Why the gate runs by exit code here.** DOS binds the gate three ways — a
> git pre-commit hook (`dos apply --staged`), a host PreToolUse binding, and the
> bare exit-code tier ([cookbook-exit-code-tier](cookbook-exit-code-tier.md)).
> This walkthrough reads `dos apply`'s **exit code** directly (0 = ALLOW, 1 =
> REFUSE — the verdict *is* the exit code), which is the host-agnostic tier: it
> is byte-identical to what a pre-commit hook or a PreToolUse binding runs, but
> it reproduces the same on every OS without depending on one host's hook-exec
> semantics. The point being proved is *one gate over one WAL*, and the exit code
> is that gate's verdict regardless of which host invokes it.

---

## The versioned verb contract this exercises

Every verb below is a **Stable** CLI surface under
[docs/STABILITY.md](../../docs/STABILITY.md) ("verb names, documented flags,
documented exit codes, and the top-level keys of every documented `--json`
output"), governed by the `dos-kernel` distribution version (currently the
`0.x` line). The closed `SCOPE_ESCAPE` reason is a member of the Stable refusal
vocabulary. So this is a *versioned* contract two hosts can both depend on, not
an ad-hoc check:

| Verb / token | The shared meaning both hosts rely on | Exit / shape |
|---|---|---|
| `dos lease-lane acquire --lane L --owner O` | book lane `L`'s region in the shared WAL | `0`=acquired |
| `dos lease-lane live` | read the one WAL both hosts share (`--json`) | the held-lease list |
| `dos apply --lane L --file P` | the BINDING pre-effect gate: may this write land? | `0`=ALLOW `1`=REFUSE |
| `--json` `{allowed, reason_class, refused_files}` | the machine-readable verdict | additive keys |
| `SCOPE_ESCAPE` | the typed refusal a refused apply carries | `dos.toml [reasons]` |

---

## Run it (one command, ~10 seconds)

The whole proof is a single self-contained script —
[`two_hosts_one_gate.sh`](two_hosts_one_gate.sh). It needs only `dos`
(`pip install dos-kernel`) and `git`, and it builds a throwaway repo so it leaves
nothing behind:

```bash
sh examples/playbooks/two_hosts_one_gate.sh
```

It sets up one repo with two disjoint lanes (`src/**`, `ui/**`), has **host A**
take lane `src` in the WAL, then has **host B** attempt two colliding writes and
one clean one — asserting the gate's verdict at each step. It exits non-zero if
any witness fails, so a green run *is* the proof.

---

## What it does, step by step (verbatim output)

### 0 + 1 — one shared repo + WAL; host A books lane `src`

```bash
# host A — DISPATCH_HOST_ID stamps A's identity onto its WAL lease
DISPATCH_HOST_ID=host-A-claude-code \
  dos --workspace . lease-lane acquire --lane src --kind cluster --owner host-A-claude-code
dos --workspace . lease-lane live --pretty
```
```json
[
  {
    "acquired_at": "2026-06-14T23:04:20Z",
    "holder": "host-A-claude-code",
    "host_id": "host-A-claude-code",
    "lane": "src",
    "lane_kind": "cluster",
    "tree": ["src/**"]
  }
]
```

The WAL now records A's region (`src/**`). This file is the shared ground: host B
reads the *same* WAL on every `dos apply`.

### 2a — host B (holds lane `ui`) writes into `src/` → escapes its lane

```bash
DISPATCH_HOST_ID=host-B-cursor \
  dos --workspace . apply --lane ui --file src/auth/login.py --json ; echo "exit: $?"
```
```json
{"allowed": false,
 "reason": "write REFUSED (WRONG_TARGET) — stamped lane ui but NONE of the 1 touched file(s) fall in its tree — footprint: src/auth/login.py",
 "reason_class": "SCOPE_ESCAPE",
 "refused_files": ["src/auth/login.py"],
 "self_lane": "ui"}
```
```text
exit: 1
```

### 2b — host B claims lane `src` (A's lane) and writes `src/` → **WAL collision**

This is the canonical cross-host case: B's write *is* in-scope for the lane it
named, but A holds an overlapping live lease in the WAL, so the gate's
sibling-collision floor refuses it. **B ran no collision check** — `dos apply`
read A's lease from the shared WAL:

```bash
DISPATCH_HOST_ID=host-B-cursor \
  dos --workspace . apply --lane src --file src/auth/login.py --json ; echo "exit: $?"
```
```json
{"allowed": false,
 "reason": "write REFUSED — footprint collides with a live lease's region (src/auth/login.py); a sibling holds an overlapping write lock",
 "reason_class": "SCOPE_ESCAPE",
 "refused_files": ["src/auth/login.py"],
 "scope": {"allowed": true, "verdict": "IN_SCOPE"},
 "self_lane": "src"}
```
```text
exit: 1
```

Note `scope.allowed: true` (B's write is inside lane `src`'s tree) yet the
top-level verdict is `false`: the refusal came from the **WAL collision floor**,
reading A's live lease — not from B's own scope.

### 3 — the filesystem witness

```text
=== 3. filesystem witness — A's region is UNCHANGED on disk after B's refusal ===
UNCHANGED — the refusal held; no colliding byte landed
```

The witness is the file on disk, not the script's say-so: a refused write touches
nothing.

### 4 — the control: host B's in-lane, non-colliding write **passes**

```bash
DISPATCH_HOST_ID=host-B-cursor \
  dos --workspace . apply --lane ui --file ui/page.tsx --json ; echo "exit: $?"
```
```json
{"allowed": true,
 "reason": "write ALLOWED — all 1 touched file(s) fall inside lane ui's tree",
 "reason_class": "", "refused_files": []}
```
```text
exit: 0
```

The gate refuses the *collision*, not every write — the same binary, the same
WAL, a clean verdict.

---

## What this proves — and what it does not

**Proven (the existence proof):** two independent hosts (`host-A-claude-code`,
`host-B-cursor`), **one** refusal mechanism (`dos apply` over `apply_gate.decide`),
**one** WAL. Host B carries no overlap logic of its own; the gate read host A's
lease from the shared WAL and refused B's colliding write with a typed
`SCOPE_ESCAPE`, the file unchanged on disk — while letting B's in-lane write
through. The verbs, the WAL format, and the refusal token are the shared standard
both hosts speak.

**Not claimed (the honest limit).** P-SPOKEN is a **limit / adoption** property
(docs/342 §1, docs/340 §5), not a "the field has standardized on DOS" claim. This
is the *mechanism* existence proof — that a second, independent host coordinates
through the same gate without re-deriving the check. Whether the ecosystem
*adopts* the shared verbs is a standardization fight DOS has not yet won; the bet
is on the derivative (docs/340 §5). What is shown here is exactly and only: **two
hosts, one gate, one WAL, no re-implemented collision check.**

This is also a **conformance** property, not a correctness one: the gate refuses
a write that *escapes its lane or collides with a live lease*; it never claims the
write is *good* (that is the test suite's job — docs/340 §5,
[docs/183](../../docs/183_how-much-does-this-lean-on-git.md)).

---

## See also

- [`cookbook-exit-code-tier.md`](cookbook-exit-code-tier.md) — the host-agnostic
  integration tier this walkthrough rides (any runner reads `dos apply`'s exit
  code; no hook adapter, no MCP client).
- [`cookbook-cursor.md`](cookbook-cursor.md) — wiring host B (Cursor) for real
  with `dos init --hooks cursor`.
- [`02_polyglot-web-service.md`](02_polyglot-web-service.md) /
  [`05_infra-monorepo.md`](05_infra-monorepo.md) — the single-host lane demos this
  generalizes across hosts.
- [docs/126](../../docs/126_the-mediated-write-and-the-apply-gate-pep.md) — the
  apply-gate PEP (`dos apply`, `SCOPE_ESCAPE`) this proof exercises.
- [docs/340 §3.1](../../docs/340_what-dos-means-the-winning-move-when-narration-dies.md) /
  [docs/342](../../docs/342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md)
  — the strategy (own the shared effect-language) and the P-SPOKEN property this
  is the witness for.
```
