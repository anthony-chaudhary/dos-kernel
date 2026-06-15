# 343 — The exactly-once envelope and the re-drive contract (docs/342 M4 Phase A)

> **The claim, drawn tight.** DOS guarantees **exactly-once for git-resident
> effects only**. A non-git side effect — an external POST, a charged card, a sent
> email, a pushed artifact — is **outside the substrate's reliability envelope**;
> the host owns its idempotency. This note declares that boundary as a *fact*, not
> an unstated assumption, and ships the verdict that makes the boundary
> **checkable**: `resume.redrive_contract`. Drawing the line tight is itself the
> equal-caliber move — TCP's caliber includes *knowing exactly what it does and
> does not guarantee* ([`342 §4`](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md)).

Status: **Phase A shipped** (the honest floor). The envelope is declared in
`src/dos/resume.py` and proven by `tests/test_resume.py`; Phase B (an effect-ledger
with idempotency keys at the gate) remains the stretch target, unbuilt. This is the
M4 entry in the [`342`](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md)
milestone ladder — the one property §4 flagged as most likely to *break rather than
bend*, so the floor is shipped first and the floor is honest about its ceiling.

---

## 1. Why exactly-once is the hard property

TCP gets idempotent retransmission **for free**: it layers over a pure byte
stream, and re-sending segment 7 is harmless because a byte has no side effect —
the receiver dedupes by sequence number and the application sees each byte once
([`342 §4`](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md)).

DOS's "stream" is **agent effects**, and an effect can be a card charge. The
recovery path — `resume.resume_plan` — re-drives a *residual* (the unfinished tail
of a crashed or paused run). Re-driving a residual that already fired a non-git
effect before it committed is **at-least-once execution**, and DOS has **no Undo**:
[`114`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md) dropped
the ARIES third phase; the lineage is *forward recovery over an event-sourced
intent ledger*, not backward recovery. So DOS cannot promise exactly-once for an
effect it never witnessed.

[`342 §4`](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md) laid out
three options: **(1)** bound the envelope to git effects (the conservative,
shippable, honest floor); **(2)** an effect-ledger with idempotency keys at the
gate (the real fix); **(3)** compensators (judged over-heavy, skipped). This note
ships option 1 and names option 2 as the target.

## 2. The envelope, stated so it can be relied on

| | Inside the envelope | Outside the envelope |
|---|---|---|
| **Effect** | a git commit | an external POST / charge / email / push |
| **Why safe / unsafe** | git is content-addressed and atomic; re-applying committed work is a no-op or an identical commit | the effect already fired; re-driving fires it again |
| **Who owns exactly-once** | **DOS** (the kernel) | the **host** (an idempotency key on the POST, or accept at-least-once) |
| **The TCP parallel** | TCP guarantees the byte stream | the application owns end-to-end exactly-once above TCP (the end-to-end argument, [`335 §4`](335_tcp-for-agents-validating-the-reliability-analogy.md)) |

The burden moves to the host **exactly as it does above TCP**. Under this bound DOS
is already TCP-equivalent on its own layer — and the honesty of declaring the
boundary is the caliber, because a substrate that over-claims its envelope is
*lower* caliber than one that draws the line tighter.

## 3. The re-drive contract — the boundary made checkable

The safety property was already true in prose. `resume.py`'s docstring has long
said: *you resume from the last committed, verified SHA, never the last claimed
step — so re-execution re-does at most the uncommitted tail, which is idempotent by
construction.* Phase A turns that **docstring claim into a verdict**:
`redrive_contract(plan, state, ancestry) -> RedriveVerdict`.

The bound it asserts is the docstring's safety property restated as a test:

> **The re-drive starts from a COMMITTED anchor.** `resume_sha` is in ancestry (a
> git fossil) or empty (re-derive from HEAD) — *never* a non-ancestry self-reported
> SHA. Re-driving the residual **forward of a committed anchor** is git-idempotent,
> because git is content-addressed: re-applying work already in history yields the
> same tree, never a doubled durable git effect.

- **`GIT_IDEMPOTENT`** — the anchor is a committed fossil (or empty). Safe to
  re-drive; the envelope holds.
- **`ENVELOPE_BREACH`** — the anchor is a non-empty SHA *not* in ancestry: a
  re-drive "from" an uncommitted self-report. Refused — re-derive from HEAD instead.

**Why the anchor, not a per-step check.** A first design flagged any residual step
whose commit is in ancestry as a breach. That is wrong, and a test caught it: a
step verified but *downstream of a hole* (s2 committed, s1 not) is correctly put
back in the residual by `resume_plan` (the resume restarts from before the hole).
Re-driving s2 then re-applies a commit already in history — which is **git-
idempotent, not a breach**, because git dedups identical content. The real hazard
is re-driving from an anchor that is **not** a committed fossil (the
[`103`](103_memory-is-an-unverified-agent.md) disease: trusting a
self-reported SHA). So the contract guards the **anchor**, which is both the true
invariant and a property `resume_plan` already satisfies by construction.

**Distrust preserved.** The anchor's membership is tested against
`AncestryFacts.contains` — the boundary's re-read of git — *never* a stored
`STEP_VERIFIED` record. A plan that *names* a SHA the ledger "verified" but which
the boundary did not find in ancestry is a breach: the kernel does not believe the
agent's claim that its anchor was committed
([`107 §5`](107_resumable-work-and-the-intent-ledger.md)).

## 4. The proven bound, and what it does NOT claim

`tests/test_resume.py` proves both directions, on frozen fixtures (no live crashed
run needed):

- **The bound holds across the whole `resume_plan` output space** — a parametrized
  test runs every verdict shape (no prefix, a contiguous prefix, a hole, all
  verified) and asserts the emitted plan is always `GIT_IDEMPOTENT` and its
  residual is disjoint from its verified prefix. *The kernel never breaches its own
  envelope.*
- **The contract refuses a forged/hand-built breach** — a plan whose anchor is a
  non-ancestry self-reported SHA is `ENVELOPE_BREACH`, fail-closed. *The proof
  catches a malformed plan, it does not assume well-formedness.*
- **The honesty caveat is never dropped** — a `GIT_IDEMPOTENT` verdict carries
  `non_git_caveat = True` always. The verdict speaks *only* to the git effect; a
  step's non-git side effect is outside the envelope and the host's to dedup.

What Phase A does **not** claim, and must not be read to claim: that DOS makes a
double POST safe. It does not. The kernel does not witness the POST, so it cannot
and does not guarantee exactly-once for it. That is the whole point of declaring the
boundary — `effect ≠ correctness` still holds, and now `git-effect ≠ external-effect`
is a stated, tested line, not a hidden assumption.

## 5. Phase B — the target, not yet built

The real fix ([`342 §4`](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md)
option 2) is an **effect-ledger with idempotency keys** on the mediated-write spine
the M1 gate owns: record an idempotency key per external effect; a re-drive
presenting a key already in the ledger is **refused at the gate** — the effect is
not re-fired. This is the saga / event-sourcing direction
[`114`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md) pointed at,
buildable on the intent ledger (`src/dos/intent_ledger.py`). The seam stays a
**driver/host-policy** one for the external-effect adapter: the kernel owns the
key-dedup verdict, **not** the POST. Phase A is the floor that makes Phase B
optional rather than load-bearing — the envelope is honest with or without it.

---

## 6. See also

- [`342 §4`](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md) — the
  goal: P-EXACTLY-ONCE, the three options, and the commitment to option 1 as the
  shipped floor + option 2 as the target.
- [`114`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md) — the
  dropped-Undo / forward-recovery framing the envelope rests on (§"third ARIES phase").
- [`107`](107_resumable-work-and-the-intent-ledger.md) — the intent ledger + the
  resume verdict the contract sits on; §5 is the distrust rule the anchor check obeys.
- [`335 §4`](335_tcp-for-agents-validating-the-reliability-analogy.md) — the
  end-to-end argument: why the host owning idempotency above the substrate is
  exactly TCP's posture, not a DOS shortfall.
