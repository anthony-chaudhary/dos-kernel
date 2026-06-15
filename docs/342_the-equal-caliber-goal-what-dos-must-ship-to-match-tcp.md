# 342 — The equal-caliber goal: what DOS must ship to match TCP

> **The goal, in one sentence.** Make DOS an *equal-caliber substrate* to TCP/IP:
> a layer that does not merely *report* whether an unreliable actor's effect is
> trustable, but **guarantees that the only effects which become part of the
> shared record are trustable ones** — the agent-layer analogue of "the
> application never reads a corrupt packet." This note turns that ambition into a
> concrete, falsifiable target: it lists the exact properties TCP has that DOS
> still lacks, the milestone that closes each one (most already planned), and the
> acceptance test that decides — from a witness, not a claim — whether the
> milestone earned the property.

This is the **goal/roadmap** companion to [`335`](335_tcp-for-agents-validating-the-reliability-analogy.md),
which *diagnosed* the analogy: DOS matches TCP in problem-shape (relocate the
guarantee off the unreliable component onto an un-authored check of the end state)
but falls short on the verb — TCP *drops the corrupt packet at a mandatory gate*;
DOS *observes the corrupt effect after it landed*. 335 named the gap as "ahead of
the mechanism by one enforcement point." This note is what closing that gap looks
like as a program of work, and it is deliberately written *after* the first
enforcement point already shipped ([`126`](126_the-mediated-write-and-the-apply-gate-pep.md)
Phase 1, `dos apply`) — so it is a goal grounded in a started build, not a wish.

It carries no litmus and ships no mechanism of its own; it is the target the
mechanism docs ([`126`](126_the-mediated-write-and-the-apply-gate-pep.md),
[`85`](85_extending-the-verifiable-surface.md)) and the positioning doc
([`340`](340_what-dos-means-the-winning-move-when-narration-dies.md)) are aimed at.

---

## 1. What "equal caliber" means — the bar, stated so it can be failed

"Equal caliber to TCP" is not "as famous as TCP" or "as widely used." It is a
*technical* bar with five named properties, because TCP's reliability is exactly
five mechanisms (335 §1). DOS reaches TCP's caliber when, and only when, it has an
honest counterpart to **all five**, each verified by a test a self-report cannot
pass. The five, and the one-line statement of the bar for each:

| # | TCP property | The equal-caliber bar for DOS |
|---|---|---|
| **P-GATE** | A corrupt packet is **dropped at a mandatory gate** on the only path to delivery | An untrustable effect is **refused before it enters the shared record**, at a gate the actor **cannot decline to traverse** |
| **P-FENCE** | Sequence numbers make a stale/duplicate segment **rejected, not delivered twice** | A stale or superseded actor's write is **rejected at the gate**, not silently applied over a re-granted region |
| **P-CHECK** | The checksum is over **the bytes that matter**, binary and mandatory | The binding completion rung is over an **un-authored artifact** (diff footprint / OS exit), not a forgeable label the actor controls |
| **P-EXACTLY-ONCE** | Retransmission is **idempotent at the receiver** — re-sending segment 7 never acts twice | Re-driving a residual **never double-executes a non-idempotent effect** (no double POST / double charge) |
| **P-SPOKEN** | The guarantee is **universal because the protocol is spoken** — every stack runs the same TCP | The effect-language is **shared** — hosts coordinate through DOS's verbs, not each through its own re-derived check |

The bar is deliberately conjunctive. A substrate that nails four and fails one is
not "80% TCP-caliber"; it is a substrate with a hole, and on a long horizon the
hole is where the corruption rides through (335 §5's *pⁿ* regime). The goal is all
five, and the honest status — set in §2 — is that DOS has **one shipped, two
planned, one partial, and one strategic**, which is why this is a goal and not a
report.

---

## 2. Current standing against the bar — where each property actually is today

No narration: each row is graded by what has *shipped*, with the witness named.

- **P-GATE — STARTED, not yet mandatory.** [`126`](126_the-mediated-write-and-the-apply-gate-pep.md)
  **Phase 1 shipped** `dos apply`: a binding diff turnstile that refuses a staged
  write escaping the run's lane or colliding with a sibling's live lease, *before*
  the commit lands (exit 1). This is a real PEP — the detect→prevent flip 335
  called the missing enforcement point. **But it is opt-in and CLI/pre-commit-stage
  today** (`dos apply --staged`); the actor running raw `git commit` without the
  hook still bypasses it. TCP's gate is *on the only path*; DOS's gate is on *a path
  the host installs*. The milestone that closes the gap is **126 Phase 1.5** — wire
  `apply_gate.decide` into the `pretool_sensor` PreToolUse surface that *physically*
  blocks (`permissionDecision: deny`), generalizing the already-binding SELF_MODIFY
  block from T1-files to any-lease-tree. *Status: keystone shipped; mandatoriness
  pending P1.5 (blocked on the docs/143 softening trap, named in 126 P1.5).*

- **P-FENCE — PLANNED.** [`126`](126_the-mediated-write-and-the-apply-gate-pep.md)
  **Phase 2**: a generation/epoch number on each lease grant, checked at the
  mediated write, so a stale-paused holder that wakes after a `SCAVENGE` cannot
  write over the re-granted region (the [`114 §A2`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md)
  hazard, Kleppmann's fencing-token result). This is where `liveness` first
  *binds* rather than advises. *Status: designed, unbuilt.*

- **P-CHECK — PARTIAL, hardenable rung-by-rung.** The evidence ladder
  ([`138`](138_what-is-truth-the-throughline.md)) already has checksum-grade rungs
  (`grep-artifact` file-path, `registry`, `OS_RECORDED`), but the *default* binding
  rung in many flows is still the forgeable `grep-subject` (an `--allow-empty`
  right-SHA commit clears it — 335 §3.2). TCP has no rung an attacker clears by
  writing a plausible label. The milestone is two-fold: make the **artifact/exec
  rung the binding default** where the host can supply it ([`126`](126_the-mediated-write-and-the-apply-gate-pep.md)
  **Phase 3**, `dos`-launched exec → `OS_RECORDED`), and continue
  [`85`](85_extending-the-verifiable-surface.md)'s rung-by-rung hardening. *Status:
  the strong rungs exist; making the strong rung the* binding *one is the work.*

- **P-EXACTLY-ONCE — UNSOLVED, and the deepest.** DOS's "retransmit the residual"
  is safe only because a git commit is idempotent; the moment a step has a non-git
  side effect (external POST, charged card, sent email) before it commits,
  re-driving is *at-least-once execution* and DOS has no compensator
  ([`114 §"third ARIES phase"`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md):
  Undo was dropped). TCP dodged this entirely by layering over a pure byte stream.
  This is the one property with **no current plan** — see §4, because it may be the
  property that decides how far the analogy can honestly go. *Status: open; not yet
  a phase.*

- **P-SPOKEN — STRATEGIC, partly in motion.** [`340 §3.1`](340_what-dos-means-the-winning-move-when-narration-dies.md):
  TCP won by being *spoken*, not clever — a standard every stack runs. DOS's
  equal-caliber move is to own the effect-language (the `dos` verbs / MCP surface)
  so hosts coordinate through one shared check rather than each re-deriving its own.
  Multi-host wiring (the `dos init --hooks` matrix, the MCP server, the plugin) is
  the seed; a published, versioned verb contract is the target. *Status: the
  surface ships; the* standard *— a stable, cross-host effect-vocabulary others
  adopt — is the open positioning bet.*

The synthesis of §2: **the gate exists but isn't mandatory; the fence and the
binding-exec rung are planned; exactly-once is unsolved; the standard is a bet.**
That is the whole goal in five clauses.

---

## 3. The milestone ladder — the order the properties must land, and why

The properties are not independent, and the order is forced by which one unblocks
the next. This is the engineering spine of the goal.

**Milestone 1 — make the gate mandatory (P-GATE complete).** Wire 126 P1.5: the
`apply_gate` verdict onto the PreToolUse block. *Why first:* every other property
is worthless without a gate the actor cannot skip. A fence (P-FENCE) checked only
by an opt-in CLI is a lock on a door with no wall around it. Mandatoriness is the
foundation the rest stands on — it is the single change that converts DOS from
"checker the host may consult" to "gate the write must pass," which is the entire
distance between 335's "false verb" and "true verb."
> **Acceptance test (witness, not claim):** an agent process that does *not*
> cooperate — issues a raw write to a file outside its lease via the host's normal
> tool path — is *blocked at the tool call*, with a typed `SCOPE_ESCAPE` refusal,
> and the file is unchanged on disk afterward. The witness is the **filesystem
> state + the host's deny record**, not the agent's report. This is the
> production-vs-acceptance test of 335 §5 run against a *non-cooperating* consumer:
> equal-caliber means the non-cooperating consumer's acceptance rate of bad effects
> is **still driven to zero** — exactly the test TCP passes and DOS today fails.

**Milestone 2 — fence the re-granted region (P-FENCE).** 126 Phase 2 generation
numbers, checked *at the now-mandatory gate from M1*. *Why second:* the fence is
only meaningful at a mandatory write path (M1 builds that path); before M1 there is
nowhere to check the generation that the actor can't route around.
> **Acceptance test:** scripted interleaving — holder A is scavenged, region
> re-granted to B, A wakes and attempts a write through the gate. A's write is
> refused on a superseded generation; B's passes. The witness is the **WAL
> generation record + the gate's refusal**, reproducible in a test.

**Milestone 3 — make the binding rung un-authored (P-CHECK).** 126 Phase 3 +
85's hardening: the gate's *completion* decision binds on `OS_RECORDED` / artifact,
not `grep-subject`, wherever the host supplies an execution command. *Why third:*
once the gate is mandatory and fenced, the remaining forgery is at the *check
itself* — an actor that clears a weak rung at a strong gate is the residual hole.
> **Acceptance test:** an `--allow-empty` right-SHA commit that clears
> `grep-subject` is graded `NOT_SHIPPED` (or refused at the gate) when the host has
> declared an execution rung, because the OS exit / artifact footprint disagrees.
> The witness is the **OS exit code the kernel captured**, which the agent did not
> author.

**Milestone 4 — the exactly-once question (P-EXACTLY-ONCE).** See §4 — this is
research-shaped, not a wired phase, and it is sequenced last because the first
three are achievable with known mechanism and this one may not be fully closable.

**Milestone 5 — the spoken standard (P-SPOKEN).** Runs in *parallel* to 1–4, not
after: a versioned, cross-host verb contract that other agent runtimes adopt
([`340 §3.1`](340_what-dos-means-the-winning-move-when-narration-dies.md), the
re-derivation-tax argument). *Why parallel:* standardization is a multi-year
adoption curve that does not wait on the gate's internals, and the gate's value
compounds with every host that speaks the verbs.
> **Acceptance test:** a second, independent agent host (not the reference one)
> coordinates a real concurrent run through DOS's verbs and is refused a colliding
> write *by the same gate*, with no host-specific check re-implemented. The witness
> is **two different hosts, one refusal mechanism**.

---

## 4. The hard property — exactly-once, and the honest ceiling

P-EXACTLY-ONCE deserves its own section because it is where the analogy is most
likely to *break rather than bend*, and a goal that hid that would be the
propaganda 340 §5 warns against.

TCP achieves idempotent retransmission for free: it layers over a **pure byte
stream**, and re-sending bytes is harmless because bytes have no side effect — the
receiver dedupes by sequence number and the application sees each byte once. DOS's
"stream" is **agent effects**, and an effect can be a card charge. Re-driving a
residual that already fired a non-idempotent external effect is at-least-once
execution, and DOS has no Undo (114). Three honest options, in increasing
strength:

1. **Stay in the idempotent regime (the conservative bound).** Declare that DOS's
   exactly-once guarantee holds *for git-resident effects only*, and that non-git
   side effects are **outside the substrate's reliability envelope** — the host
   must make them idempotent (an idempotency key on the POST) or accept
   at-least-once. This is *honest and shippable today* and is probably the right
   first answer: TCP, too, only guarantees the byte stream — the application owns
   end-to-end exactly-once on top (the end-to-end argument again, 335 §4). **Under
   this bound DOS is already TCP-equivalent on its own layer**, and the burden moves
   to the host exactly as it does above TCP.
2. **An effect-ledger with idempotency keys at the gate (the real fix).** The
   mediated write (M1's gate) records an idempotency key per external effect; a
   re-drive that presents a key already in the ledger is refused at the gate
   (the effect is not re-fired). This is the saga/event-sourcing direction 114
   pointed at (forward recovery over the intent ledger) and is *buildable* on the
   spine the gate already owns. It is the genuine P-EXACTLY-ONCE milestone.
3. **Compensators (the full ARIES Undo, almost certainly not worth it).** A
   per-effect compensating action. 114 already judged this over-heavy for the
   domain; named here only for completeness.

**The goal commits to option 1 as the shipped floor and option 2 as the target**,
and states plainly that option-1-honesty (declaring the envelope) is *itself* an
equal-caliber move — because TCP's caliber includes **knowing exactly what it does
and does not guarantee**. A substrate that over-claims its envelope is lower
caliber than one that draws the line precisely, even if the line is drawn tighter.

---

## 5. The definition of done — when DOS may claim the verb

The goal is complete — DOS may honestly say *"makes unreliable actors produce
trustable effects"*, TCP's full verb — when all of the following are true, each
behind a witness:

1. **A non-cooperating actor cannot land an out-of-lane or colliding effect**
   (M1: gate mandatory). *Witness: filesystem state after a hostile write attempt.*
2. **A stale/superseded actor cannot write over a re-granted region** (M2: fence).
   *Witness: WAL generation + gate refusal in a scripted interleave.*
3. **The binding completion rung is un-authored wherever an exec command exists**
   (M3: `OS_RECORDED` binds). *Witness: OS exit code disagreeing with a forged
   stamp.*
4. **Re-drives never double-fire a non-idempotent effect, OR the envelope is
   declared and the host owns idempotency** (M4: option 2 shipped, or option 1
   documented as the bound). *Witness: an effect-ledger key refused on re-drive, or
   a written envelope boundary.*
5. **At least one independent host coordinates through the verbs and is refused by
   the same gate** (M5: spoken). *Witness: two hosts, one refusal mechanism.*

Until all five hold, the precise public claim stays the one 335 settled on:
**"TCP-for-agents for a consumer that gates."** The five milestones are exactly the
distance from that bounded claim to the unbounded one — and the running scoreboard
metric that tracks the distance is 335 §5's **acceptance-rate against a
non-cooperating consumer**: it starts near the raw production rate (today, gate
opt-in) and the goal is achieved when it reaches ~zero (gate mandatory, fenced,
un-authored-checked) — the exact signature TCP shows, measured the exact way.

---

## 6. The one-paragraph synthesis

DOS already makes TCP's *architectural* move — relocate the reliability guarantee
off the unreliable actor onto an un-authored check of the end state — and 335
proved it. What it lacks for *equal caliber* is the enforcement TCP's verb assumes,
and that lack decomposes into five named properties: a **mandatory** gate
(P-GATE — keystone shipped in 126 P1, made mandatory by P1.5), a **fence** against
stale writers (P-FENCE — 126 P2), a **binding un-authored check** rather than a
forgeable label (P-CHECK — 126 P3 + 85), **exactly-once** under re-drive
(P-EXACTLY-ONCE — honestly bounded to the git envelope today, an effect-ledger as
the target), and a **spoken standard** so the guarantee is universal rather than
per-host (P-SPOKEN — 340 §3.1). The goal is all five, each closed by a witness a
self-report cannot pass, sequenced so mandatoriness comes first because nothing
else matters without it. The measure of progress is one number — the rate at which
a *non-cooperating* consumer accepts a bad effect — and DOS earns TCP's verb the
day that number reaches zero, which is the day "the application never reads a
corrupt packet" becomes "the record never holds an untrustable effect."

---

## 7. See also

- [`335_tcp-for-agents-validating-the-reliability-analogy.md`](335_tcp-for-agents-validating-the-reliability-analogy.md)
  — the diagnosis this goal answers: the five-mechanism decomposition, the
  detect-vs-enforce gap, and §5's production-vs-acceptance falsifiable test that is
  this note's progress metric.
- [`126_the-mediated-write-and-the-apply-gate-pep.md`](126_the-mediated-write-and-the-apply-gate-pep.md)
  — the mechanism plan: Phase 1 (`dos apply`, shipped) = P-GATE keystone, Phase 2 =
  P-FENCE, Phase 3 = P-CHECK's un-authored binding rung. The goal is this plan's
  *why* in TCP terms.
- [`340_what-dos-means-the-winning-move-when-narration-dies.md`](340_what-dos-means-the-winning-move-when-narration-dies.md)
  — P-SPOKEN: own the verbs so the field shares one effect-language (TCP won by
  being spoken); the substrate-of-record positioning the gate makes possible.
- [`114_prior-art-audit-where-the-branding-outruns-the-mechanism.md`](114_prior-art-audit-where-the-branding-outruns-the-mechanism.md)
  — the original PDP-with-no-PEP finding (P-GATE), the fencing-token hazard
  (P-FENCE / §A2), and the dropped-Undo / forward-recovery framing (P-EXACTLY-ONCE / §4).
- [`138_what-is-truth-the-throughline.md`](138_what-is-truth-the-throughline.md) /
  [`85_extending-the-verifiable-surface.md`](85_extending-the-verifiable-surface.md)
  — the evidence ladder and its rung-by-rung hardening: P-CHECK's path from a
  forgeable label to an un-authored artifact.
- [`333_verification-as-steering-and-the-verification-first-harness.md`](333_verification-as-steering-and-the-verification-first-harness.md)
  — why the gate must be foundation-built, not bolted on (the pre-effect admission
  is an architecture-time channel); the control-loop sibling of the TCP framing.
