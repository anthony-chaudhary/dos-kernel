# 296 — the operator-armed SELF_MODIFY override: a wired affordance for the HUMAN rung

> **Status:** 🚧 **P1–P2 SHIPPED, P3 OPEN** (2026-06-10) — closed by the
> oracle, not this sentence: `dos verify --workspace .
> docs/296_operator-armed-self-modify-override-plan "Phase 1"` / `"Phase 2"`.
> Phase 3 (Go hook parity + the live-binary swap) is operator-gated and open —
> until it lands, the LIVE plugin hook (the Go fast path) still denies inside
> an armed window; the Python hook path and any wiring that falls through to
> it honor the window now. One P2 line item deliberately rides the FIRST
> armed window instead of shipping here: the `dos man wedge SELF_MODIFY`
> TYPICAL-FIX sentence (it lives in the T1 reason registry, so its own edit
> is, fittingly, the seam's first customer). Commissioned 2026-06-10 by the
> operator, after a session in which the guard worked exactly as designed and
> the sanctioned escape hatch turned out not to exist as a *mechanism*, only
> as a *sentence*.
>
> **Design revision (2026-06-10, same day, before any code):** the first cut
> put the override evidence INTO the pure classify (`self_modify`, T1) — which
> recreated the very problem this plan retires: its own Phase 1 needed an
> out-of-band application ("the bootstrap irony"). The revision is the docs/293
> move, again: the override is not *verdict* evidence, it is **enforcement
> disposition**. The pure classify keeps saying SELF_MODIFY — that fact does
> not stop being true because the operator armed a window — and the HOOK layer
> (the PEP-side emitter, non-T1) consults the operator's armed window when
> rendering the verdict to the host: ADMIT-with-note inside the window, the
> byte-identical deny outside it. PDP decides; PEP disposes. Consequence: the
> whole Python implementation is non-T1, buildable by an ordinary supervised
> session with no bootstrap; the ONLY operator-gated step left is the live Go
> hook binary (Phase 3), because rebuilding the running adjudicator is
> self-modification in spirit regardless of which files the matcher lists.

## The gap this closes

The SELF_MODIFY guard refuses to let a live agent session rewrite the kernel
that is adjudicating it. That refusal is right, it held twice in the field on
2026-06-10 (issue #11), and nothing in this plan weakens it.

But the guard's own typed fix names a counterparty move that has no wired
mechanism. `dos man wedge SELF_MODIFY` says:

> TYPICAL FIX: Edit kernel runtime files OUTSIDE a live dispatch loop, or pass
> `--force` to override (the operator's explicit "I am deliberately editing the
> kernel between loop runs").

`--force` exists on `dos arbitrate` — but the PreToolUse hook ABI deliberately
gives the *agent* no force, and gives the *operator* nothing at all. So when
the human genuinely wants a supervised kernel edit, the protocol degenerates to
out-of-band tooling. The issue-#11 session is the type specimen:

1. agent Edit calls on `arbiter.py` → DENIED (correct);
2. operator explicitly authorizes the change, on the record;
3. the edit lands via a byte-exact Python script run from a temp dir — i.e.
   through the same arbitrary-exec hole the guard cannot close anyway;
4. `git add <path>` → DENIED by the path-MENTION matcher (issue #12), so
   staging routes through `--pathspec-from-file`;
5. nothing in the journal or the stats log records that an override happened.

Every step was sanctioned and visible in the conversation — and invisible to
DOS. The kernel's trust ladder is ORACLE → JUDGE → HUMAN; the HUMAN rung ruled,
and the kernel had no way to *hear* the ruling. Related field evidence: a
sibling loop burned 21 identical refused Writes on one T1 file because a deny
never escalates anywhere (issue #14).

## The design — an arm file the operator writes, the hook reads, the clock expires

One new piece of **declared, operator-authored, expiring data**, read at the
hook boundary; one new evidence field on the pure classify; zero behavior
change while unarmed.

### The arm file

```toml
# .dos/override/self-modify.toml — hand-written by the operator, never by an agent
until  = 2026-06-10T23:30:00Z          # hard deadline, UTC; REQUIRED
scope  = ["src/dos/arbiter.py"]        # optional; absent/empty = the whole T1 set
reason = "issue #11 — refuse-reason rename"   # REQUIRED; lands in the audit row
```

* **Arming is manual by construction.** There is deliberately NO `dos override
  arm` verb: any verb an agent's shell can call is an arming path an agent can
  take. The docs print the two lines the operator types. The arm path itself
  joins the hook's deny set (a cooperative agent's Write/Edit to
  `.dos/override/` is refused exactly like a T1 file).
* **Expiry is a clock, not a consume.** The hook is a PDP — a read-only
  adjudicator — so the one-shot semantics come from `until`, not from the hook
  deleting the file on use (a hook that mutates state on admit is a layering
  break). The clock is env-authored ground truth, the same witness the
  temporal-verdict family already trusts. Recommended window ≤ 30 minutes; the
  kernel never extends it.
* **Fail closed, every branch.** Missing file, unparseable TOML, missing
  `until`/`reason`, expired deadline, target outside `scope` → exactly today's
  deny, byte-identical. A malformed override can only fail to admit, never
  fail to deny.
* **Disarm is always safe, for anyone.** `dos override disarm` deletes the arm
  file; `dos override status` reports it. The asymmetry IS the security
  property: anyone may lower the drawbridge's gate; only the human raises it.

### The disposition and the audit trail

The pure classify is **not edited and not consulted differently**: a T1 hit
still produces the SELF_MODIFY refusal, because it still IS self-modification.
The change is one rung up, at the hook boundary (the PEP-side emitter): before
rendering that refusal to the host, the hook reads `OverrideFacts` (armed-until,
scope, reason — a frozen value from the arm file + `now`, the "I/O at the
boundary" rule) and, iff armed ∧ unexpired ∧ target-in-scope, emits **ALLOW
with `additionalContext`** ("operator override armed until T — admitting
supervised kernel edit: <reason>") instead of the deny. Never a silent pass:
the observation log row carries `override=until-T`, and `dos stats` folds an
"override-admitted" count, so every admitted call inside a window is on the
record next to the verdict it overrode. The path-mention matcher (issue #12)
honors the same disposition, which retires the `--pathspec-from-file`
workaround inside the window.

### The threat model, stated honestly

This guard was never proof against adversarial exec — an agent with a shell can
write any file through a subprocess, and the issue-#11 session demonstrated
exactly that (with permission). The guard's real job is stopping *heedless*
self-modification by cooperative agents, and that job is unchanged: unarmed
behavior is byte-identical, the arm path is deny-listed against cooperative
writes, and the new channel is time-boxed, scoped, reasoned, and audited. The
seam strictly improves the status quo by replacing an invisible out-of-band
script with a recorded protocol move. What the arm file is NOT: a forgeproof
witness in the docs/138 sense — its authorship is trust-on-convention, exactly
as strong as the machine's single trust domain allows, and the plan claims
nothing stronger.

## Phases

### Phase 1 — the boundary leaf + Python hook disposition

A new non-T1 leaf (`dos.override_facts`: tomllib read of the arm file +
`now` capture → frozen `OverrideFacts | None`, fail-closed on every branch:
missing, unparseable, missing `until`/`reason`, expired) and a PURE disposition
helper (`disposition(verdict, facts, target, now) -> deny | admit-with-note`),
wired into the Python `dos hook pretool` emission path; `.dos/override/` joins
the hook's own deny set; the `additionalContext` note + observation-log field.
Pinned by: armed/expired/out-of-scope/malformed × T1-hit/no-hit disposition
tests, plus end-to-end hook tests (armed file → ALLOW with note; agent Write
to the arm path → denied; expiry → deny restored byte-identical). No T1
module is touched; the pure classify is unchanged.

### Phase 2 — the operator surface: `dos override status|disarm`, doctor, stats, man page

The read/disarm verb (no arm verb — see design), a `doctor` row
(`self-modify override   disarmed` / `armed until T (reason)`), the `dos stats`
fold, and the `dos man wedge SELF_MODIFY` TYPICAL FIX text gains the third
path: "or arm the operator override (docs/296)".

### Phase 3 — Go hook parity + the live-binary swap (operator-gated)

`go/internal/hook/decide.go` learns the same arm-file read (same fail-closed
branches), pinned by the existing Go/Python parity corpus pattern. The plugin
hook command runs the Go binary first (`dos-hook pretool || python -m dos.cli
hook pretool`), so the seam goes LIVE only when the bundled binary is rebuilt
and swapped — and rebuilding the running adjudicator is the operator's act by
definition, whatever the matcher says. Until then the Python implementation is
the reference (and serves any wiring that falls through to the fallback arm).

## What this plan does NOT do

* No agent-side force, no arm verb, no env-var override — arming stays a human
  hand on a guarded file.
* No change to any verdict while unarmed: the entire diff is reachable only
  through a file that does not exist on an unarmed workspace.
* No claim of forgeproof-ness the single-machine trust domain cannot deliver.
