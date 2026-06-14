# 337 — WAL-integrity admission: the arbiter must see the torn ACQUIRE it folded away

> **Status:** 📐 **DESIGN — plan first, no code yet** (2026-06-14). Closes the
> design half of #78. Implementation ships under this plan's phases, each
> adjudicated by `dos verify --workspace .
> docs/337_wal-integrity-admission-plan "Phase N"` — never by a
> `> **Status:**` sentence. Until P1 lands the phases answer
> `NOT_SHIPPED via none` (the evidence horizon, not a lie).

## The one idea

The lane WAL keeps a `_CORRUPT` sentinel for a non-trailing torn line so an
audit still sees the breach ([[lane_journal.py]] `read_all`). But the fold that
the **admission** path runs throws the sentinel away: `live_leases` reads every
entry, then `replay` ignores `_CORRUPT` for state ([[lane_lease.py]] →
`read_all` → `replay`). So when the torn line *was* an ACQUIRE, the live-lease
set silently **shrinks**, and `arbitrate` admits a colliding tree against
evidence it could have read — the sentinel is sitting in the same `read_all`
result the fold came from.

The asymmetry is the whole hazard. A torn **RELEASE** errs safe: a lease
lingers, the arbiter refuses-more. A torn **ACQUIRE** errs dangerous: a held
region reads free, two loops collide on the same files — the exact
two-agents-overwrote-each-others-work failure the WAL exists to prevent.

> **This is `SELF_MODIFY` / docs/329 generalized to the *evidence channel*.**
> Those floors refuse a candidate that rewrites the *instrument* adjudicating
> it. This floor refuses an admission whose *evidence* is known-incomplete —
> the kernel must not grant a lane while holding a typed signal that the live
> set it is reasoning over lost a line. One env-authored bit, refuse-first,
> tighten-freely / loosen-signed, an operator escape between runs.

## 0. The position (carry it intact)

Four things this plan is NOT, so a later phase does not drift into them:

- **Not a fail-close on ANY sentinel.** A torn REFUSE / HALT / ENFORCE /
  ATTEMPT / SPAWN line never granted or removed a lease, so its loss cannot
  shrink the live set — folding it away is already correct. Tripping admission
  on those would wedge a fleet on a harmless torn line. The floor fires only on
  a sentinel that *could have been* a state-mutating op (§2).
- **Not self-healing by erasure.** `compact` deliberately preserves sentinels
  ([[lane_journal.py]] `compact`), so there is no path where the breach quietly
  ages out. The exit is an **operator verb**, on the record (§4) — never an
  in-loop force, never a silent drop.
- **Not a content judge.** The floor does not decide whether the torn line was
  benign or malicious, nor reconstruct what it said. It asks one fact the bytes
  already answer: *does the live-lease evidence carry a state-relevant integrity
  breach, yes or no?* The disposition of a yes is policy (§3); the bit is a
  fact.
- **Not a new kernel module, not an arbiter rewrite.** The bit is one field on
  the admission request and one branch in the conjunction, computed at the I/O
  boundary where `live_leases` already reads the journal. `arbiter.arbitrate`
  stays the pure `classify(evidence, policy)` it is — it reads a typed flag, it
  does not open the WAL.

## 1. The gap, precisely

Walk the load-bearing path ([[lane_lease.py]]):

```
live_leases(config):
    entries = lane_journal.read_all(path)   # <- sentinels ARE here
    folded  = lane_journal.replay(entries)  # <- sentinels dropped for state
    return folded                           # <- arbiter sees ONLY folded
```

`arbitrate` is then called with `live_leases=folded` ([[cli.py]]
`cmd_arbitrate`, the `lane_lease.live_leases(cfg)` read, and the in-process
`acquire` path). The sentinel never crosses the call boundary. Two facts make
this the dangerous direction, not a cosmetic one:

| torn op | fold effect on the live set | admission direction | safe? |
|---|---|---|---|
| RELEASE | a removed lease is **not** removed → set is **larger** | refuse-MORE | safe (a lingering lease) |
| ACQUIRE | a granted lease is **not** present → set is **smaller** | admit-MORE | **dangerous** (a held region reads free) |
| REFUSE / HALT / ENFORCE / ATTEMPT / SPAWN | never touched the lease fold | none | safe (loss is a no-op) |

The breach is auditable today (`dos journal tail` shows the sentinel, `compact`
preserves it) but **not load-bearing**: the one verdict the WAL exists to
ground never learns of it.

## 2. The env-authored fact

`read_all` already tags each sentinel with its raw text and line number
(`{"op": "_CORRUPT", "_raw": <str>, "_line": <int>}`). The integrity signal is
derived from the **same `read_all` result** `live_leases` already computes — no
second disk read, no clock:

```
# at the lane_lease I/O boundary, NOT in the pure arbiter
wal_integrity = wal_integrity_from(entries)   # entries = read_all(path)
```

Two design choices, both env-authored:

- **Only state-relevant sentinels count.** The floor must not fire on a torn
  REFUSE/HALT line. But a `_CORRUPT` sentinel is by definition *unparseable* —
  we cannot read its `op` to know it was an ACQUIRE. The honest, conservative
  reading: **any** state-relevant uncertainty is treated as possibly-an-ACQUIRE
  (the dangerous direction), because a torn line we cannot classify might have
  been the grant that the now-colliding request needs to see. This is the
  fail-closed reading of an unreadable line, consistent with `read_all`'s own
  "a half-written *trailing* line didn't happen" — except a *non-trailing* torn
  line sits between records that DID happen, so its loss can reorder/erase
  state. (P2 narrows this with per-payload checksums, §5; P1 is the safe
  superset.)
- **Severity is structural, not interpreted.** The bit is computed by counting
  `_CORRUPT` sentinels in `entries` whose position is non-trailing (every
  sentinel `read_all` emits already is — it breaks before appending a trailing
  torn line). Zero sentinels ⇒ clean ⇒ byte-identical to today. One or more ⇒
  the integrity flag is set, carrying the count and the line numbers for the
  operator.

The result is a small typed value, frozen at the boundary and handed to
`arbitrate` as one field on the admission request — the mechanism/policy split
the kernel already enforces (the driver/CLI reads the journal; the arbiter
reads a bool).

## 3. The disposition — typed refuse, NOT a bare WARN (a plan decision, decided)

#78 asks: *a typed refusal class (`WAL_INTEGRITY`) vs an advisory WARN on the
decision envelope?* **Decision: a typed REFUSE by default, with an operator
escape — not an advisory WARN, and not an unconditional hard-fail.**

Why refuse, not warn:

- **A WARN that admits is the bug, restated.** The hazard is a false ADMIT of a
  colliding tree. A warning that still returns `outcome=acquire` lets the
  collision happen and merely annotates it — the two loops still overwrite each
  other; the operator reads the warning after the damage. The whole point of
  the WAL is to *prevent* the collision, so the integrity breach must move the
  *outcome*, not just the prose.
- **The refuse is the closed-vocabulary kind, in-grain.** The arbiter already
  refuses with typed reasons that travel the `refuse(reason_class)` path
  (`SELF_MODIFY`, the exclusive-lane and same-lane refuses). A
  `WAL_INTEGRITY` reason is the right home: it is an **admission refuse**
  (category `MISROUTE` in [[reasons.py]]'s coarse classes — the request cannot
  be safely routed against unsound evidence), it is emittable by the arbiter,
  verifiable by the operator (`dos journal tail` shows the sentinel), and
  refusable by the loop (it routes to "surface to operator", not "replan").
  This is the closed-set discipline the kernel keeps for every refuse.

Why not an *unconditional* hard-fail:

- **`compact` preserves sentinels, so a naive hard-fail wedges forever.** Once
  a torn ACQUIRE is in the WAL, every future `arbitrate` would refuse until the
  operator hand-edits the log — a fleet frozen on one bad line, with no
  sanctioned exit. The refuse must be **exitable** by an operator act (§4).

The shape, in [[arbiter.py]] `arbitrate`, as an early floor (ahead of the
disjointness check — a colliding tree decided against unsound evidence is
exactly what we cannot trust):

```
# 0. WAL_INTEGRITY — the live-lease evidence carries a non-trailing _CORRUPT
#    sentinel that could have been an ACQUIRE (a shrunken live set, the
#    admit-more direction). Refuse BEFORE the disjointness check: a
#    "disjoint, admitted" verdict over a live set that LOST a lease is not a
#    safe verdict. Operator clears it with `dos journal quarantine` (§4).
if request.wal_integrity.breached and not request.wal_integrity.acked:
    return LaneDecision("refuse", reason=<WAL_INTEGRITY, naming the lines>,
                        free_clusters=...)
```

Properties this placement buys:

- **Ahead of disjointness, deliberately.** A request whose tree happens to miss
  every *surviving* lease would otherwise be admitted — precisely the false
  ADMIT. Refusing first means the integrity breach is the verdict whenever it
  applies, the more specific and more alarming fact.
- **Honors `--force` exactly like the other floors.** `--force` is the
  operator's "yes, I am deliberately proceeding" (the sole override the arbiter
  honors). A forced acquire over a known breach is the operator's call, on the
  record — same posture as forcing past `SELF_MODIFY`.
- **Default-dead without sentinels.** A clean WAL sets `breached=False`, the
  branch is byte-transparent, every existing arbitrate path and test is
  unchanged. The floor is armed only by the presence of a real sentinel — the
  config-as-data / dead-by-default idiom.

## 4. The remediation verb — `dos journal quarantine` (decided)

#78 asks for *the remediation verb so the fail-closed state is exitable*.
**Decision: a new operator verb `dos journal quarantine` that ACKs the named
sentinel lines, on the record, without erasing them.**

- **It does not delete the sentinel.** Erasing the breach is the one thing
  `compact` already refuses to do (an audit must always see it). `quarantine`
  appends a typed `WAL_QUARANTINE` record naming the acked `_line` numbers (and
  the operator + a reason), so the fold can read "these breaches are
  acknowledged" and `arbitrate`'s floor reads `acked=True` for them. The
  sentinel and its acknowledgement both survive — full audit trail.
- **It is an operator act, between runs.** Like the [[296]] override arm-file,
  the verb is the human's hand on the WAL, never the loop's. A loop that hits
  `WAL_INTEGRITY` surfaces to the operator (`dos decisions`); the operator
  inspects (`dos journal tail`), decides the torn line is understood (e.g. a
  known crash, the held lease already released elsewhere), and quarantines it.
  The admission floor then passes.
- **It is scoped to the named lines.** Quarantining line 42 does not blanket-ack
  a *future* torn line — a new `_CORRUPT` at line 91 re-trips the floor. The ack
  is per-breach, so the floor cannot be permanently disarmed by one quarantine.
  (The closure §6 of [[329]]: the guard cannot be turned off wholesale.)
- **Tighten-freely / loosen-signed.** Re-arming (the floor firing on a new
  sentinel) is automatic and free; clearing it (quarantine) is the signed,
  on-the-record operator act. The asymmetry every safety floor in the kernel
  obeys.

## 5. The scope boundary the plan takes a position on

#78 names per-payload checksums as the open scope question: *a
parseable-but-tampered line is invisible without them — the witness-tamper
family of #35.* **Position: P1 closes the torn-line (unparseable) breach; the
checksum/tamper case is P2, explicitly out of P1's scope and named so.**

- **P1's unit is the `_CORRUPT` sentinel** — a line `read_all` could not parse.
  That is the documented integrity unit of the #62 fault-injection fixtures,
  and it is exactly the breach that shrinks the fold. P1 makes that breach
  load-bearing on admission. This is the whole dangerous-direction fix for the
  failure #78 was found by.
- **A parseable-but-tampered line is a different threat** — the bytes parse to a
  valid-looking ACQUIRE/RELEASE that was altered. `read_all` emits no sentinel
  for it (it parsed), so P1's floor cannot see it. Catching it needs a
  per-record integrity tag (an HMAC/checksum over the payload, keyed outside the
  WAL) — the witness-tamper family (#35, [[329]]). P2 adds the per-payload
  checksum and folds a checksum-mismatch into the **same** `WAL_INTEGRITY`
  refuse + `quarantine` exit P1 builds, so the operator surface is one verb, not
  two. P2 is gated behind P1 and is not required to close #78's done-condition.

The honesty line: **P1 closes the breach #78 was found by (a torn ACQUIRE reads
a held region free); it does not claim to detect a forged-but-parseable line —
that is P2, named here so a later phase does not assume P1 covered it.**

## 6. Kernel-litmus compliance (the additive proof)

The plan must not violate a CLAUDE.md litmus. It does not:

- **Kernel imports no host.** The integrity bit is computed at the
  `lane_lease` / CLI I/O boundary (which already reads the journal); it is
  passed to `arbitrate` as a typed field. `arbiter.py` reads a bool + a small
  value object — no journal read, no host name, no I/O.
- **A driver is the only place policy lives.** Whether the floor refuses vs
  warns is **not** a per-host knob in P1 (refuse is the safe default and the
  decision of §3); if a host ever needs observe-only, it rides the existing
  `--warn-only`-style envelope, not a new `config.py` policy. The remediation
  verb is operator tooling (`scripts/` / CLI), not kernel.
- **`verify` needs no plan.** Untouched — this is the `arbitrate`/admission rung.
- **An overlap policy can only refuse-MORE.** The new floor only ADDS a refuse
  (a breached WAL refuses where today it would admit); it never turns a refuse
  into an admit. AND-ed under the prefix-disjointness floor, exactly the
  refuse-more-only rule.
- **Paths resolve via `SubstrateConfig.root`.** The journal path is already
  `_journal_path(config)`; no `__file__`.
- **No new SELF_MODIFY coupling.** `arbiter.py` does not import
  `self_modify.py`; the integrity floor is its own branch reading its own typed
  field. The two guards stay independent siblings (a test pins it).

## 7. Phasing

| Phase | What | Layer | Status |
|---|---|---|---|
| **P1** | The floor, end to end, **torn-line (`_CORRUPT`) only**: a typed `WalIntegrity` value computed from the `read_all` result at the `lane_lease` boundary; one field on the admission request; the §3 refuse-first `WAL_INTEGRITY` branch in `arbiter.arbitrate` (honoring `--force` and `acked`); the `WAL_INTEGRITY` token in [[reasons.py]] (category `MISROUTE`); `dos journal quarantine` appending a `WAL_QUARANTINE` ack record + the fold reading it; a CLI-boundary test where a journal with a corrupted formerly-ACQUIRE mid-file line makes `arbitrate` refuse `WAL_INTEGRITY`, and `quarantine` then clears it. **Closes #78's done-condition.** | kernel (arbiter branch) + lane_lease/CLI gather + reasons + journal verb | unbuilt |
| **P2** | The **per-payload checksum** (§5): an optional per-record integrity tag, a checksum-mismatch folded into the SAME `WAL_INTEGRITY` refuse + `quarantine` exit. The parseable-but-tampered line (#35 family). | journal + lane_lease | unbuilt |
| **P3** | **Operator ergonomics + the decisions surface**: `WAL_INTEGRITY` routed onto `dos decisions` with the derived unblock action ("inspect with `dos journal tail`, then `dos journal quarantine <lines>`"); the `dos-unstick`/recurring-wedge fold recognizes it; a doc recipe in the CI cookbook. | helpers + docs | unbuilt |

P1 is the closer for #78: it makes the existing-but-inert sentinel load-bearing
on the one verdict it threatens, and every dependency already ships (`read_all`
tags sentinels with `_line`; the arbiter's refuse path; the `reasons` registry;
the `dos journal` verb group). P2/P3 layer the forged-line case and the operator
surface on as needed — neither blocks the floor.

## 8. What this is NOT

- **Not** a claim the WAL is now tamper-proof. P1 closes the *unparseable*
  (torn) breach — the one that shrinks the fold. A forged-but-parseable record
  is invisible to P1 by construction; that is P2, named so no phase assumes it
  is covered.
- **Not** a self-healing erasure. The sentinel is never deleted; the exit is an
  on-the-record operator `quarantine`, the [[296]] between-runs posture.
- **Not** an arbiter that opens the WAL. The arbiter stays pure; the journal is
  read where it is already read, and the arbiter receives a typed bit.
- **Not** a fail-close on harmless sentinels. A torn REFUSE/HALT/ENFORCE line
  never touched the lease fold; P1's "any non-trailing `_CORRUPT` could have
  been an ACQUIRE" reading is the conservative superset, narrowed by P2's
  checksums where a host wants precision.

## 9. Lineage

- #78 — the gap this plan closes; the admit-more asymmetry of a torn ACQUIRE.
- #62 — the fault-injection sweep that pinned the `_CORRUPT` sentinel and made
  the asymmetry visible (corruption can only shrink the lease fold).
- #35 / [[329]] — the witness-tamper family: P1 closes the unparseable breach,
  P2's per-payload checksum is the parseable-but-tampered sibling.
- [[296]] — the operator-armed, between-runs override shape the `quarantine`
  verb reuses (the kernel says `WAL_INTEGRITY`; the operator's signed ack is the
  sanctioned exit).
- `dos.self_modify` ([[73]]) — the admission-side sibling: refuse a request
  whose *instrument* is compromised; this refuses a request whose *evidence* is
  known-incomplete.
- [[lane_journal.py]] / [[lane_lease.py]] — the WAL, `read_all`'s sentinel, the
  pure `replay` fold, and `live_leases` (the read this floor reads alongside).
