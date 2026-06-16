# 364 — Default-on enforcement posture and the fail-safe contract

> **Status:** DECIDED — applies to `pretool_sensor.decide()` Rung A.5
> (apply-gate), the enforce handler seam (Rung B), and the fleet fail-safe
> contract. Implementation: remove `""` from `pretool_sensor._APPLY_GATE_OFF`.

## The wound, stated once

DOS shipped the enforcement mechanism disarmed. The apply-gate is off when
`DOS_APPLY_GATE` is not set. The built-in `enforce_handlers` is `observe`.
So a fresh install records every violation and blocks nothing. The honest
one-liner is "DOS catches the lie after it lands."

That was the right call when the gate was new (docs/126 Phase 1). The gate
is tested now. This note fixes the default and writes the contract for when it
goes wrong.

---

## 1. The default-on posture

Three rungs decide what the kernel does with a write. Each has its own
safe-failure direction. Two of the three already have a decided default.
This note decides the third.

### Rung A — structural admission (already default-on, no change)

A dispatch loop session that tries to write the kernel's own runtime files
gets a hard deny (`SELF_MODIFY`). An interactive operator session (no loop
env) gets a warning instead, per docs/355. This is unchanged.

### Rung A.5 — apply-gate: **BLOCK is the new default for loop sessions**

The apply-gate (`apply_gate.decide`) checks three things for a mutating write:

1. **Scope containment.** Did the write stay inside the held lane's declared
   tree? A write that escapes (SCOPE_CREEP, WRONG_TARGET) is refused.
2. **Sibling collision.** Does the write overlap another live lease's region?
   An overlapping write is refused.
3. **Stale fence.** Is the held generation superseded by a later grant
   (docs/342 M2, docs/114 §A2)? A stale write is refused.

**Implementation:** Remove `""` from `pretool_sensor._APPLY_GATE_OFF`. An
unset `DOS_APPLY_GATE` now means ON. An operator who needs to disable the
gate sets `DOS_APPLY_GATE=0`.

**What the new default blocks:**

- A loop session write that escapes the held lane's tree (`SCOPE_ESCAPE`).
- A loop session write that overlaps a sibling's live lease region
  (`SCOPE_ESCAPE`, sibling collision).
- A loop session write from a holder whose lease generation was superseded
  (`STALE_GENERATION`).

**What the new default does NOT block:**

- Read-only tool calls (no write footprint, zero collision risk).
- An un-leased interactive session (no self-lease resolves, gate is dormant).
- An interactive operator session (no loop env — these get a WARN per the
  existing operator-session softening, never a deny).
- Behavioral provenance violations (Rung B — see below).

The gate only fires when: (1) the call is mutating, (2) the tree is known
and non-empty, (3) a self-lease resolves. All three must be true. Anything
less and the gate is dormant — so a workspace with no active leases, or a
read, or a path the sensor cannot parse, passes unchanged.

### Rung B — behavioral provenance: **OBSERVE (default-on, no change)**

The enforcement handler seam (`enforce.run_handler`) fails to OBSERVE. That
is the correct safe direction for actuation: a handler bug must never become
a spurious block. The built-in `ObserveHandler` proposes OBSERVE on
everything — the call fires, the verdict is recorded. Actuating BLOCK/DEFER
on provenance violations requires a ruling handler in a driver; the kernel
ships none. This is a bring-your-own-PEP surface, not a default-block.

---

## 2. The fail-safe contract

### Visibility — the operator sees the block

A denied call emits `permissionDecision: deny` in the CC `PreToolUse`
dialect. The reason string names:

- The exact refused files.
- The reason class (`SCOPE_ESCAPE`, `STALE_GENERATION`, `SELF_MODIFY`).
- The held lane.

Every non-passthrough outcome is written to the enforce journal. Run
`dos doctor --workspace .` to see the enforce summary and which calls
were blocked in the last cycle.

### Override paths

| Cause | Override |
|---|---|
| `SCOPE_ESCAPE` / sibling collision | `DOS_APPLY_GATE=0` (disables the gate) |
| `STALE_GENERATION` | Re-lease (`dos arbitrate`); the gate then resolves a fresh generation |
| `SELF_MODIFY` in a loop session | Arm the override window (`dos override arm`) |
| `SELF_MODIFY` in an interactive session | No override needed — already a WARN (docs/355) |
| Any, one call, at the CLI | `dos apply --force` (audited, recorded) |

The PreToolUse ABI gives the agent no `--force`. That is deliberate: the
agent cannot self-grant an override. The operator acts outside the agent's
turn — either by setting an env var or by arming a window.

### The breaker — stopping a fail-closed storm

If the gate misfires and starts blocking every write in a live fleet, the
operator has three cuts, in order of speed:

1. **Cut the gate.** Set `DOS_APPLY_GATE=0` in the fleet env. The env is
   read on every `PreToolUse` event; the next call passes. No restart.
2. **Trip the loop breaker.** Run `dos breaker --workspace . --lane <lane>`
   for the affected lane. This trips the circuit on the stuck loop without
   destroying the lease state; the operator can inspect state and re-enable.
3. **Inspect the decisions.** Run `dos decisions --workspace .` to see the
   blocked calls and find the misfiring decision (a bad tree glob, a phantom
   lease, a wrong lane match).

The fail-safe direction is `DOS_APPLY_GATE=0`. It is a one-env-var cut that
requires no code change, no restart, and no coordination with individual
loops. The breaker handles the case where the loop itself is stuck — a gate
that keeps blocking the same write, rather than a gate that suddenly blocks
everything. The two tools are complementary.

A fail-closed storm that the gate cannot cause on its own (the gate is
dormant on reads and un-leased sessions) means a phantom lease is live.
`dos doctor --workspace .` shows the live lease set; a lease whose pid is
dead is a phantom. `dos scavenge --workspace .` removes it and clears the
collision.

---

## 3. Migration: observe-only → BLOCK, without a flag-day

An existing deployment with `DOS_APPLY_GATE=0` (or the env unset, under the
old default) opts up in three steps. No flag-day is needed because the env is
read per-call, not per-session.

**Step 1 — Read the journal first.** Wire or keep the PreToolUse hook. Leave
`DOS_APPLY_GATE=0`. Run a few loop cycles. Read the enforce journal for
decisions with `rung: apply-gate`. These are the writes that would have been
blocked. Are they real escapes or false positives?

**Step 2 — Fix any taxonomy gaps.** A blocked write means one of two things:
(a) the agent wrote outside its lane — fix the agent's lease request or its
write target; or (b) the lane's declared tree glob in `dos.toml` is too
narrow — widen the glob. Either fix is cheap. Do it before flipping the
default.

**Step 3 — Enable the gate.** Remove `DOS_APPLY_GATE=0` (or leave it unset).
The gate becomes active on the next tool call. Sessions mid-flight when the
env changes finish their current call, then the next call sees the gate live.
No lease renewal needed; the gate reads the current live leases on each call.

The gate is dormant for un-leased sessions regardless. So a workspace where
agents have not yet leased a lane sees no change at all until they do.

---

## 4. Litmus test

This test pins the enforcement default. If it turns red, the default
silently regressed to observe-only.

```python
# tests/test_apply_gate_default.py  (or inside test_pretool_sensor.py)
import importlib


def test_apply_gate_on_when_env_unset(monkeypatch):
    """apply-gate is ACTIVE when DOS_APPLY_GATE is not set."""
    monkeypatch.delenv("DOS_APPLY_GATE", raising=False)
    # reload so the module-level read (if any) re-runs
    import dos.pretool_sensor as pts
    importlib.reload(pts)
    assert pts._apply_gate_enabled(), (
        "apply-gate must default ON (opt-out, not opt-in); "
        "set DOS_APPLY_GATE=0 to disable."
    )


def test_apply_gate_off_when_env_zero(monkeypatch):
    """Operator can disable the gate with DOS_APPLY_GATE=0."""
    monkeypatch.setenv("DOS_APPLY_GATE", "0")
    import dos.pretool_sensor as pts
    importlib.reload(pts)
    assert not pts._apply_gate_enabled()
```

The implementation change that makes `test_apply_gate_on_when_env_unset`
green: remove `""` from `pretool_sensor._APPLY_GATE_OFF`.

---

## Why this does not violate the fail-to-observe discipline

The fail-to-observe rule lives at Rung B (the enforce handler seam). A
handler bug must not become a spurious block — the docs/143 −9 pp lesson.
That rule is untouched.

Rung A.5 (the apply-gate) is structural admission, not a handler. The
safe-failure direction for admission is already fail-CLOSED (docs/191 §3):
a predicate that cannot answer REFUSES. The apply-gate is a pure verdict
(`apply_gate.decide`) with no I/O; it cannot bug out the way a handler can.
The two fail-safe directions — fail-CLOSED for admission, fail-to-OBSERVE
for actuation — coexist without contradiction, as docs/191 §5 establishes.

---

## Fixes #195
