# 390 — Sealed acceptance: declaring "done" upfront, where it earns its keep, and where it is ceremony

> **Operator goal (2026-06-21).** Expand the definition-of-done concepts and
> tooling: make it easy for an agent to *declare* what "done" means **upfront**,
> authored in a **separate context** (so the worker cannot co-design the
> success bar with the work and game it — the anti-reward-hacking move), then let
> the worker run, then let the DOS gates run, then have an **additional
> independent gate** look at the result. The operator also said, plainly: *there
> are a lot of issues with that concept — think about how to progress it and
> where it is reasonable or applicable.* This note takes that seriously. It is
> mostly a **subtraction**: it finds the one thing the idea genuinely adds, names
> the much larger set of places it is ceremony or actively harmful, and ships a
> minimal increment that captures the win without the harm.

> **The one-line finding.** *Sealing the done-criteria upfront buys **integrity,
> not grounding.*** The seal fixes **who** wrote the criteria and **when** — but
> nothing about **whether** they measure the right thing or **cover** the real
> job. The mechanism that actually catches a faked "done" — a non-forgeable
> witness reading back an effect the worker did not author, plus an external
> account of the job's true size — already ships in DOS
> ([`effect_witness`](../src/dos/effect_witness.py),
> [`scope_source`](../src/dos/scope_source.py)) and works **identically whether or
> not the criteria were sealed.** So the seal is not the load-bearing part. It
> adds exactly **one** non-redundant property — *auditable separation of duties*:
> git ancestry proves the criteria predate the work and were authored apart from
> it — and that property earns its keep in a **narrow** set of places, chiefly the
> reward label and the unattended goal-fleet worker whose effect has no free
> read-back.

---

## 1. The concept, stated fairly

The proposal is a four-beat pipeline:

1. **Declare upfront, in a separate context.** Before the worker starts, a
   *different* agent (or a human, or an external ticket) writes the
   definition-of-done as checkable criteria, in a context that never sees the
   worker's implementation choices.
2. **Seal it.** Commit the criteria so they cannot be edited after the work has
   seen them — a tamper-evident record that the bar predates the work.
3. **The worker runs**, ideally blind to (or at least unable to rewrite) the
   sealed criteria.
4. **Gate against the seal.** The DOS gates run; then an additional, independent
   gate (in the operator's framing, the live **fleet** consumer repo) re-reads the
   sealed criteria — which it did not author — and the ground-truth effects —
   which the worker did not author — and rules.

The analogies are good ones: scientific **preregistration** (declare the
hypothesis and the analysis before seeing the data, so you cannot p-hack),
**test-first / TDD**, **separation of duties** in audit, a **held-out** eval set,
and a cryptographic **commit-reveal**. They all share one move: *fix the standard
before the thing being judged can influence it.*

DOS already has the **backward** half of "done" — every existing rung asks "what
did you declare, and did you finish it?" The genuinely missing piece this concept
points at is a **forward, sealed, separate-author** declaration. That gap is real
(a code search finds no upfront-sealed-criteria primitive today). The question is
how much of the gap is worth filling, and where.

## 2. Why most of the concept is ceremony — the integrity-vs-grounding split

Six independent critiques, each from a different angle, converge on the same
fault line. It is worth stating once, sharply, because it determines the entire
design.

> **Integrity** = *who authored the proxy, and when* — the seal delivers this.
> **Grounding** = *whether the verdict bottoms out in evidence the judged agent
> could not author* — the seal delivers **none** of this.

The two are orthogonal, and only grounding stops a faked "done." Walk the
critiques:

- **Goodhart / proxy.** A sealed criterion is still a *proxy*. Sealing fixes the
  checklist; it does not make the checklist measure the thing you care about. A
  sealed-but-wrong criterion is a wrong criterion — now *frozen and harder to
  revise*. DOS already concedes this in code: `dos verify` answers **extent**,
  not **quality**; [`completion`](../src/dos/completion.py) is explicit that
  `COMPLETE` means *"every declared unit is verifiably closed,"* **not** *"the
  work is good"* (docs/117 §8). Sealing a quality bar does not upgrade it to a
  truth.

- **Under-declaration (the incentive).** The failure that actually bites is
  docs/117 **Gap B**: *"declare three steps, ship three, earn `COMPLETE` while the
  real job needed eight."* The only check that catches it is an **external account
  of true scope** — the [`ScopeSource`](../src/dos/scope_source.py) rung — and that
  check runs the **same** whether the three steps were sealed or not. The seal
  freezes *when* the criteria were written; it does nothing about *whether they
  cover the job.* So on the one failure that matters most, the seal is *ceremony
  bolted onto the side of the mechanism that does the work.*

- **Discovery.** Real engineering learns what "done" means *during* the work.
  Sealing freezes the **declared extent** — the very denominator of
  `completion.classify` — at the moment of **maximum ignorance**. Worse, the
  existing refuse-more-only machinery has no honest path to *grow* the extent
  mid-run: `scope_source.honest_under_floor` is one-directional
  (`COMPLETE → UNDERDECLARED` only), so an agent that *correctly* discovers the
  real job is steps 4′–6′ has no seal-compatible move. Its good discovery is
  laundered as a false `UNDERDECLARED`, or driven into permanent
  `THRASHING` — **punishing correct work as if it were gaming.**

- **Who watches the watcher.** The separate criteria-author and the additional
  result-gate are *also agents* — usually the **same model family**, with
  **correlated blind spots.** An independent author drawn from the same
  distribution writes a proxy that is independently wrong *in the same direction*,
  and the seal locks the shared error in. Separation of *context* is not
  separation of *distribution.* Without a held-out label authored by something
  that is **not** an agent, the independence is assumed, not earned.

- **Checkability.** The criteria that *can* be machine-sealed and auto-checked are
  exactly the **shallow** ones (a file exists, a test passes, a string is
  present). The deep parts of "done" (is the design good, did it solve the real
  problem) have no cheap deterministic oracle. Sealing the shallow bar upfront
  makes the shallow bar **authoritative** — the worst direction.

- **Cost.** Every existing DOS rung is a *zero-LLM-cost fold* over evidence that
  exists regardless of whether a gate runs (`scope_source` default is `[]`,
  `honest_under_floor(())` is honest, `witness_effect` only *joins* facts already
  in hand). This concept **mints two guaranteed inference contexts** — a separate
  criteria-author and a final adjudicator — *unconditionally, upfront*, to defend a
  perimeter `dos verify` often settles for free. On a low-stakes one-file fix that
  triples the agent count against a failure the deterministic floor already
  catches. Net-negative.

The honest summary: **the seal is integrity (provenance + timing), never
grounding.** Any design that sells it as grounding — that lets a *sealed-but-passed*
criterion **grant** "done" — re-imports the exact disease the kernel exists to
refuse, now wearing a commit hash.

## 3. Where it genuinely earns its keep

Subtract everything in §2 and a small, real residue remains. The seal pays for
itself precisely where its one property — *auditable separation of
duties* — defends a perimeter the free rungs leave open. Three places:

1. **The RL reward label.** [`reward.py`](../src/dos/reward.py) mints the
   non-distillable training label, and it is the **highest-stakes faked-done**: a
   poisoned positive propagates into the next model. If the worker co-designs the
   success bar it is then graded on, the label is poisoned *at the source.*
   Sealing the criteria in a separate context and committing them **before** the
   rollout (git ancestry as the non-forgeable timestamp — not secrecy) is exactly
   the commit-reveal that stops the worker reverse-fitting the bar. Crucially the
   seal stays **strictly refuse-more**: it can only **withhold** a positive label,
   never mint a `CONFIRMED` — so a wrong-but-passed proxy still cannot manufacture
   reward.

2. **An unattended goal-fleet worker whose effect has no free read-back.** When a
   [`dos-goal-fleet`](../src/dos/skills/dos-goal-fleet/SKILL.md) worker's one goal
   is a **high-blast-radius** effect that `effect_witness` would otherwise return
   `UNWITNESSED` for — there is no git phase, no OS exit code, no third-party
   read-back the free floor can reach — the upfront sealed **effect-claims** give
   the Stop gate something non-forgeable-in-*timing* to refuse against. The seal
   earns its keep exactly on the undefended slice; it only ever **refuses the
   Stop**, never grants it.

3. **A backlog issue / merge-gate with a genuinely-external, human-authored
   done-condition.** This is the cleanest case, and it needs **no separate
   criteria-author agent at all**: a GitHub issue's acceptance criteria, a
   plan-registry phase list, or a PR's changed-files are *already* a sealed spec
   whose **byte-author is not an agent.** Route the worker's declared extent
   through a `ScopeSource` that cross-checks it against that external account
   (docs/117 Gap B), AND-ed under the floor, fail-to-strict. The seal rides along
   only as tamper-evidence; the **grounding comes from the external byte-author.**

The unifying condition, stated as a break-even: the machinery is worth it only
when **P(fake) × cost-of-a-fake > the extra agent cost**, *measured on the residual
the free rungs miss.* That product is positive in exactly the three cases above
(undefended perimeter, irreversible/outward-amplifying effect, or a
training-label poison) and negative almost everywhere else.

## 4. The no-go rules — the guardrails the design must encode

These are not stylistic preferences; each one is the negation of a failure in §2,
and each is mechanically enforceable.

1. **The seal must never GRANT done.** A sealed criterion may only flip
   `COMPLETE → UNDERDECLARED` / `INDETERMINATE` (the `scope_source` refuse-more
   position). The positive done-bit must still bottom out in a non-forgeable
   anchor — git ancestry via the oracle, an OS exit code, a clock, or a
   third-party `effect_witness` read-back. A sealed-but-passed proxy must never
   manufacture a `CONFIRMED`.
2. **A sealed QUALITY rubric must never be a deterministic gate.** Quality has no
   gold state (`effect_witness` refuses global correctness; docs/117 §8). A frozen
   quality predicate is a forgeable judge prompt with a commit hash; it may ride
   only the advisory [`dos.judges`](../src/dos/judges.py) seam (AGREE / DISAGREE /
   **fail-to-ABSTAIN**). In an unattended loop, a sealed quality gate that grants
   "done" **inverts authority** and suppresses the human review docs/117 reserves —
   forbidden.
3. **The seal must never be sold as grounding.** It is integrity only. The verdict
   stays typed as *extent-against-a-frozen-proxy*, never *quality.* A passed sealed
   gate surfaces as *"the frozen proxy was satisfied / no checkable objection"* —
   inheriting `completion`'s *"not good, just closed"* stance verbatim — never as
   *"the work is correct."*
4. **Never freeze a DISCOVERED extent.** Only the a-priori-knowable
   **acceptance-evidence shape** (which non-forgeable witness must read back) may be
   sealed; the implementation extent must allow **monotone, append-only,
   independently-witnessed refinement that only ever tightens the bar.** A
   `THRASHING` residual must route to a *witnessed re-author* of the criteria, not
   burn the loop's cap. Punishing discovered-and-added work as gaming is the bug,
   not the feature.
5. **Never make an upfront declaration a precondition-to-START.** Requiring "declare
   your done-criteria to begin" manufactures the under-declaration incentive
   (it pressures the declarer toward the *minimal passable* spec) that docs/117
   Gap B otherwise neutralizes. The **external scope account**, not the seal, gates
   `COMPLETE`. The seal is an *affordance*, never the thing that grants belief. This
   also preserves the **`verify`-needs-no-plan** invariant: the floor must answer on
   a plain repo with no contract.
6. **Never assume a same-family sealer is independent.** Until proxy-target
   divergence is *measured* by a held-out label the agents did not author (the
   docs/117 Phase-5 false-`COMPLETE`-rate instrument, re-aimed at
   "sealed-gate-passed-but-work-bad rate"), the sealed criteria are
   `AGENT_AUTHORED` **forgeable-floor** evidence — a `CONFIRMED` still needs a
   non-forgeable second byte-author via the join.
7. **New decision-bearing logic is born in Go at the driver seam** (docs/385). The
   kernel keeps only the conjunctive refuse-more floor and names no host; the proxy
   *content* (the criteria, the effect-claims, the result-gate) lives in a
   `dos.scope_sources` / `dos.judges` driver. Adding this as new Python *core*
   logic is forbidden — the Python here is confined to the genuine OS seam (git/fs
   reads) inside a driver, which the `ScopeSource` contract already permits.
8. **Sufficiency is MANDATORY, not an optional add-on** (the stress-test's sharpest
   finding). A worker can author a sealed contract of only-trivially-true grounded
   clauses, ship exactly those, and fold to `MET` — every clause genuinely satisfied,
   the contract a lie by omission. The seal proves the clauses *predate* the work; it
   says nothing about whether they *cover* the job. So every contract MUST carry a
   coverage clause routed through a `dos.scope_sources` rung whose external account's
   **byte-author is provably not either agent** (a human-committed plan-registry phase
   list, a third-party tracker's acceptance field fetched by read-back, a CI-enforced
   required-checks manifest) — AND-ed under `honest_under_floor`, fail-to-strict, so a
   contract whose clause-set under-covers that account folds to `UNDERDECLARED`. An
   agent-drafted issue's acceptance criteria do **not** qualify — that just relocates
   the collusion one level up. Absent a non-agent coverage account, the verdict is
   "*covered only to the extent declared*," never "done."
9. **`contract-MET` may only GATE a reward, never BE the label** (the incentive
   side-door). The kernel path is safe (a contract can only withhold), but if a
   downstream consumer — `reward.py`, the `improve` loop, the "fak" judge's surfaced
   signal — reads `MET` as a *positive* signal, the worker is pushed back toward the
   minimal passable sealed spec. `contract-MET` may only *withhold* a positive label
   that a non-forgeable witness already minted; it must never itself be that label.
   Pin it with a soundness test: a *thinned* contract (fewer / looser clauses) cannot
   raise any reward or `improve` metric.

## 5. The DOS-shaped design that survives — the "sealed acceptance" manifest

The design is therefore **not a new trust primitive.** It is a thin, sealed
**front door** onto the machinery DOS already ships, plus the one new check the
seal makes possible. Concretely: a **sealed acceptance manifest** — a small typed
artifact, committed before the work, listing checkable **effect-claims**, each
**tagged with the rung that will adjudicate it.**

```
# acceptance.toml  (committed before the worker starts; the commit IS the seal)
[[claim]]
  id      = "auth-endpoint-live"
  rung    = "oracle"            # git ancestry / a shipped (plan,phase) — non-forgeable
  effect  = "phase:docs/82 liveness"
[[claim]]
  id      = "row-written"
  rung    = "witness"           # effect_witness read-back — non-forgeable IFF the witness is
  effect  = "db:users/where=email"
[[claim]]
  id      = "reads-cleanly"
  rung    = "judge"             # dos.judges — ADVISORY ONLY, fail-to-abstain, never grants done
  effect  = "design: no module imports a host"
```

The manifest above is the *authoring surface*; the kernel type behind it is a
small, typed value with a pure fold — and that fold is the **only** genuinely-new
decision-bearing logic in the whole design:

```
DoneContract { contract_blob: str   # the git blob sha — content-addressed, so the id IS the seal
               run_id: str
               clauses: tuple[Clause, ...] }
Clause       { key: str             # opaque effect identity (the effect_witness.key bridge)
               rung: Rung           # ORACLE | JUDGE | HUMAN — a closed enum
               narrated: str        # the human-readable criterion — carried for the surface,
                                     # NEVER parsed for truth
               subject: str         # witness/probe correlation handle
               oracle_ref: (plan, phase) | None }   # only on ORACLE clauses
classify(contract, per_clause_adjudications, completion_floor) -> ContractVerdict
ContractVerdict { state: MET | UNMET | INDETERMINATE ; unmet_clauses ; reason }
```

A clause is a typed **request routed to the existing adjudicator its rung names** —
never a new oracle. And only **one** rung contributes a hard pass-or-withhold,
because only one is grounded: **ORACLE** (a `(plan,phase)` `dos verify` answers from
ancestry, or an `EffectClaim` whose read-back reaches a non-forgeable
`OS_RECORDED`/`THIRD_PARTY` rung in `effect_witness`). **JUDGE** clauses ride
`dos.judges` (advisory, can `DISAGREE`-to-withhold, never `AGREE`-into-done);
**HUMAN** clauses are surfaced to `dos decisions`, never auto-resolved. With no
contract supplied, the fold is byte-identical to today's completion floor.

Each piece maps to an existing rung; nothing new is invented for the *grounding*:

- **The seal = git ancestry.** No new crypto, no new trust root. The manifest's
  declaring commit is checked to be an **ancestor of the worker's HEAD**
  (`dos.vcs.is_ancestor`), and the verdict reads the **committed blob**
  (`dos.vcs.read_blob`), not the working-tree copy — so a worker that edits
  `acceptance.toml` mid-run cannot have the edit counted. This reuses the exact
  substrate `dos verify` already trusts: oracle reads commit *order*, which is the
  one part of the seal that is a real anchor. "Separate context, authored before"
  becomes a **git-checkable fact**, not a promise.

- **The post-gate folds refuse-more-only.** Each clause is adjudicated by its
  tagged rung; the contract verdict is the **AND** over clauses, exactly
  `scope_source.honest_under_floor`. A clause can only **withhold** "done." Belief
  that a clause is *met* comes only from a **non-forgeable, non-agent-authored**
  witness (`effect_witness.believe_under_floor`): a clause "checked" by the
  worker's own narration (the `AGENT_AUTHORED` forgeable rung) is recorded but
  **structurally cannot flip the verdict** — the worst a lying same-surface witness
  does is be ignored.

- **The two orthogonal non-forgeabilities, both required.** *Spec-integrity* (the
  git-ancestry seal: the bar was fixed before the work and apart from it) and
  *evidence-grounding* (the witness rung: the verdict bottoms out in bytes the
  worker did not author). The concept only works with **both.** The seal without
  grounding is §2's ceremony; grounding without the seal is what DOS already does —
  good, but it leaves the reward-label / co-design hole the seal closes.

- **The additional independent gate is a driver, not a kernel edit.** The
  "additional gate that looks at it" — the operator's live **fleet** consumer
  repo — wires in as a [`dos.judges`](../src/dos/judges.py) adjudicator: advisory,
  **fail-to-ABSTAIN**, resolved **by name**, structurally able only to refuse-more.
  The kernel ships the *socket*; the fleet repo provides the *live adjudicator.*
  The kernel still names no host — the same one-way arrow as every other driver
  (docs/98: the orchestrator is a driver). If "fak"/fleet is a specific external
  adjudicator, it plugs here without a single kernel line naming it.

- **Refinement without re-opening the hole — and its honest limit.** The manifest is
  **append-only** and refinements may only **tighten** (add a clause, raise a witness
  rung — never delete a clause or downgrade a rung). A tightening is itself a new
  sealed commit, so the audit trail shows every bar the work ever had to clear. This
  answers the *add-only* half of the discovery objection: correct mid-run discovery
  *adds* witnessed clauses without re-opening the co-design hole, and a refinement
  whose monotonicity cannot be **mechanically** decided must be **rejected**
  (fail-to-reject — an `ABSTAIN`-ing refinement judge can never loosen). But the
  stress-test found the *replace* half is genuinely hard and must not be papered over:
  *"step 4 was the wrong design; steps 4′–6′ are the right job"* is a clause that needs
  **dropping**, which tighten-only forbids — so a now-misfit sealed clause becomes a
  permanent false `UNDERDECLARED`. Supersession can only stay refuse-more-safe if the
  replacement carries a non-forgeable witness for the *new* effect **and** the dropped
  clause is marked `SUPERSEDED` by the **independent** (non-worker) author with its own
  sealed ancestry. Where that is unavailable, the honest limit is: sealed acceptance
  handles *under-declaration* discovery (add-only), **not** *wrong-design* discovery
  (replace) — the latter falls back to re-authoring the whole manifest in a fresh run.
  That bounds the seal's value to the three narrow earns-its-keep cases of §3; it is
  **not** a general iterative-work gate.

## 6. How to benchmark it — the falsifiable instrument

A design this hedged is worthless unless its central claim is *measured.* Two
numbers, both extensions of the docs/117 Phase-5 pattern:

1. **The confusion grid (does the gate lie?).** Over a labelled corpus of runs
   (truly-done / under-declared / faked), report the **false-DONE rate** (a
   sealed-acceptance verdict said done when the work was not) and the
   **false-UNMET rate** (it withheld done from genuinely-finished work — the
   discovery-punishment failure of §2). The refuse-more floor predicts false-DONE
   ≈ 0 by construction; the instrument *proves* it and surfaces the false-UNMET
   cost the seal introduces.

2. **The gaming-resistance ablation (does the seal matter?).** Run the *same*
   workers twice: once **blind** to the criteria, once **shown** them before
   working. If the seal does what it claims, the *shown* arm's false-DONE rate
   rises (workers reverse-fit the bar) and the *blind+sealed* arm holds. This is
   the one experiment that distinguishes "the seal earns its keep" from "the seal
   is ceremony" — and it is the headline result the whole note turns on. Pair it
   with the **proxy-target divergence** label of no-go rule 6 (an
   author-independent "sealed-gate-passed-but-work-bad rate") so the
   correlated-blind-spot failure is visible, not assumed away.

## 7. Phasing — and the minimal spike landing in this pass

- **Phase 0 — this note.** The integrity-vs-grounding finding (§2), the
  applicability map (§3), and the no-go rules (§4) are the load-bearing record,
  independent of build order.
- **Phase 1 — the coverage/sufficiency half (SHIPPED this pass).** A
  `dos.scope_sources` **driver**, `SealedAcceptanceScope`
  ([`src/dos/drivers/sealed_acceptance.py`](../src/dos/drivers/sealed_acceptance.py)),
  that: (a) reads a committed acceptance manifest, (b) verifies the seal by reading
  the manifest as the **committed blob at the run's start commit**
  (`dos.vcs.read_blob(state.start_sha, path)` — present-before-the-work, immune to
  working-tree tampering), (c) cross-checks the run's declared extent against the
  manifest's sealed required ids, and (d) returns a `ScopeVerdict` that can **only
  withhold** completion (broken / unanchored / malformed seal, or an uncovered sealed
  id → `extent_honest=False` → `UNDERDECLARED`). Tests
  ([`tests/test_sealed_acceptance.py`](../tests/test_sealed_acceptance.py), 22) pin
  the load-bearing properties through the **real** `completion.classify`: *the seal
  catches a post-hoc / late-added manifest; the verdict reads the committed blob, not
  a tampered working tree; a sealed-but-passed proxy never grants `COMPLETE`.* This is
  deliberately the **sufficiency** answer to the design's first open question (a
  worker can author only-trivially-true clauses) — it is the external-scope **coverage**
  check (docs/117 Gap B) made tamper-evident, and it stays a plain Python driver
  precisely because it adds the new *check* (the seal) while riding the **existing**
  refuse-more fold (`honest_under_floor` / `run_scope` fail-to-strict) — no new kernel
  *decision* logic, so no-go rule 7 does not bind it yet.
- **Phase 2 — the done-contract typed fold, BORN IN GO.** The `DoneContract` /
  `Clause` / `Rung` / `ContractVerdict` value type + the pure
  `classify(contract, per-clause-adjudications, completion_floor) -> ContractVerdict`
  of §5. *This* is the genuinely-new decision-bearing logic, so per the strongly-typed
  mandate (docs/385, no-go rule 7) it is **born in Go**, mirrored at the Python parity
  seam for the byte-green soak (docs/124) — not ported later. Pure, refuse-more-only,
  byte-identical to the completion floor when no contract is supplied; replay-tested on
  frozen fixtures, with the soundness pin that a lying clause cannot manufacture `MET`.
- **Phase 3 — the per-rung bindings (drivers).** `ORACLE` clauses bound to
  `oracle.is_shipped` / `effect_witness` (`contract_oracle`); `JUDGE` clauses through
  `dos.judges` (fail-to-ABSTAIN); `HUMAN` clauses surfaced to `dos decisions`. The
  post-gate wires into `dos hook stop` beside the existing `claim_extract` check; the
  fleet repo's "fak"-style adjudicator registers as a `dos.judges` driver **by name**.
  A `dos contract` CLI verb (`admit` / `show` / `verify`) + the opt-in
  `CONTRACT_ABSENT` pre-gate refusal for high-stakes lanes only (never general).
- **Phase 4 — the benchmark (§6)** as a `benchmark/` consumer: the confusion grid +
  false-DONE / false-UNMET rates + the blind-vs-sighted gaming ablation.

## 8. What an adversarial stress-test left standing — and what it didn't

This design was red-teamed by an independent panel: six skeptics each tried to
break it from a different angle (Goodhart, checkability, discovery, collusion,
cost, incentive), then re-checked whether the design *resolves* their objection or
*papers over* it. The result is worth recording verbatim, because it is the honest
boundary of the whole idea.

**What is fully resolved.** Every objection's **fatal** form is closed, and closed
*structurally* — verified against the shipped code, not asserted. A sealed clause
**cannot manufacture a positive done-bit**: the fold rides `honest_under_floor`
(`scope_source.py` — a verdict can only flip `True→False`), `effect_witness`
caps any agent-authored read-back at `UNWITNESSED` (`believe_under_floor`), and
`run_judge` converts any judge to `ABSTAIN`, never `AGREE`. The checkability
objection — *"sealing re-labels the shallow done as authoritative and suppresses
human review"* — is rated **resolved**: the verdict stays typed as
extent-against-a-frozen-proxy, quality is confined to the advisory rung, and
`HUMAN` clauses can never be auto-resolved.

**What survives as honest residue** (each rated *partial*, each now a no-go rule or
an open question rather than a hidden assumption):

1. **Sufficiency is the rung that actually bites — and it is borrowed, not
   intrinsic.** The contract grounds whether each *authored* clause is satisfied,
   never whether the clause *set* covers the job. The fix is real but external: a
   non-agent-authored coverage account, AND-ed under the floor (now **no-go rule
   8**). The shipped Phase-1 spike *is* that coverage rung — which is precisely why
   the seal remains orthogonal to the mechanism that bites, exactly as the critique
   charged. The design accepts this rather than refuting it.
2. **Same-family correlation is *measured*, not *defeated*.** For any clause that
   does not reduce to an ORACLE-rung witness, a sealer and worker from the same model
   family share blind spots the blind-vs-sighted ablation cannot see. The only cure
   is a held-out, non-agent label (no-go rule 6); the benchmark should add a
   **same-family-vs-cross-family arm** that either justifies or *retires* the
   independence claim — keeping only the integrity (timing) claim, which is the part
   git ancestry actually anchors.
3. **Discovery is handled for *add*, not *replace*** (§5's refinement limit). The
   THRASHING→re-author routing is also intent, not wiring: `completion.convergence`
   emits `THRASHING` but nothing consumes it; making spec-revision a first-class
   `dos decisions` event is unbuilt.

**One accuracy correction the panel caught:** the seal's ancestry check is a small
**new `dos.vcs` read** (`read_blob` at the start commit / an is-ancestor predicate),
**not** a reuse of `oracle.is_shipped` (which reads ship-stamp grammar, a different
thing). The shipped spike is already correct on this — it calls `vcs.read_blob`
directly; the prose elsewhere should not imply the oracle answers it.

The net: the seal is a sound *integrity* gate that can only ever refuse-more, and
its grounding is borrowed wholesale from the existing witness/scope rungs. That is
the honest claim — and the reason this note is mostly subtraction.

## 9. Non-goals

- **No semantic-correctness claim.** Like `completion`, a passed sealed-acceptance
  verdict means *"every required, sealed effect is verifiably present against
  ground truth"* — **not** *"the work is good."* Quality stays the JUDGE/HUMAN rung.
- **The kernel invents no scope and names no host.** Where there is no external
  account of the job's true extent, the floor answers from the declared steps alone
  and *says so* (`verify`-needs-no-plan). The seal is opt-in evidence, never a
  kernel guess, never a precondition.
- **The kernel does not re-run the work.** The verdict is advisory (docs/99); the
  act of stopping / re-dispatching is the loop's.

## 10. Provenance

This note re-aims shipped machinery and refuses to add a new trust root. The
extent rung, the refuse-more conjunction, and the fail-to-strict boundary are
[`scope_source`](../src/dos/scope_source.py) (docs/117 Phase 4); the
non-forgeable-witness fold is [`effect_witness`](../src/dos/effect_witness.py) /
[`reward`](../src/dos/reward.py); the advisory adjudicator seam is
[`judges`](../src/dos/judges.py) (docs/86); the git-ancestry anchor is
[`oracle`](../src/dos/oracle.py) read through [`vcs`](../src/dos/vcs.py)
(docs/379); the "declared extent is a self-report" insight is docs/117 Gap B
pointed at the *front* of the run. The new surface is small on purpose: a sealed
manifest, a seal check that is just git ancestry, and the honest discipline that
the seal may only ever **refuse** — never grant — a "done" the world has not yet
witnessed.
