# 326 — DOS as the runtime "privilege-exercised" verification layer for agent IAM

> **Status:** design plan. Unbuilt. This doc is the position, the four adapter
> shapes, the trust boundary they share, and the phasing. It closes issue #142
> when its **P1** ships (`dos verify` on the phase). It is an *umbrella* over
> #119/#70/#52 — it specifies how those parts compose into one integration leg;
> it does not re-file them.
>
> **One line:** The 2026 agent-IAM standards own **issuance** — who an agent is,
> what token it carries, what scope it was *granted*. Every one of them names the
> same hole and leaves it unbuilt: the **"privilege *exercised*" audit leg —
> verified at runtime from evidence the agent did not author.** That leg is
> exactly DOS. This plan wires DOS *beneath* the issuance plane as that leg, all
> at the driver layer, additive and flagged, reusing verdicts that already ship.

---

## 0. The position (carry it intact)

> **DOS is the runtime "privilege-exercised" verification layer beneath the
> issuance plane. It is NOT an issuance stack.**

The standards converging in 2026 — CSA AIGF v1, the IETF AIMS/WIMSE/Agentic-JWT
drafts, OAuth on-behalf-of for agents, CoSAI, SPIFFE/SVID + SCIM-for-agents, the
EIC 2026 consensus — all build the **issuance** plane: identity binding, token
minting, scope *granting*, delegation parentage carried *in token claims*. Each
of them then names a second leg it does not build (or fills with anomaly-ML over
logs the agent influenced): **was the granted privilege actually *exercised*
within its bounds — judged from evidence the agent did not author?**

That second leg is the DOS thesis verbatim: *the kernel is the part that doesn't
believe the agents.* DOS already produces adjudicative — not statistical —
verdicts over byte-author-floor evidence (git ancestry, the transcript spawn
tree, the diff vs the lane). So the integration is not "make DOS an IAM stack."
It is: **let the issuance plane hand DOS its grant and its claimed delegation
chain, and let DOS hand back the one thing it uniquely produces — the
adjudicated *exercised* verdict the standards' own audit fields are waiting
for.**

The twist the standards under-weight, and the reason a conventional bolt-on
won't do: their deviation-detection anchors on the agent's **own declared
intent** (AIGF's "machine-readable intent declaration"). That re-imports the
trust the whole exercise removes — a jailbroken agent declares a benign intent
and deviates in its own favor. **DOS anchors on the human-authored grant
instead** (the #119 mandate), so the comparison is two-sided non-forgeable: the
human authored the grant, the agent authored the diff, *neither authored the
other.* The agent's declaration is carried as `narrated` context, never the
gate.

### The honest floor (state it; never sell past it)

- DOS verifies **exercise**; it does **not** issue identity. No token minting, no
  auth provider, no credential store enters `src/dos/`. "Agent IAM" here means a
  principal *derived from evidence* + grant *conformance* — never credentials
  stored.
- The **enforcement** leg (refuse-at-write on grant-exceeded) is the *unbuilt*
  apply-gate PEP ([docs/126](126_the-mediated-write-and-the-apply-gate-pep.md)).
  Today DOS ships **detection + attestation** of exercise; the PEP is the defined
  seam, not a present capability. This plan adds detection/attestation adapters
  only — it does not claim the PEP.
- The named verdicts the issue sketches (`WITHIN`/`EXCEEDED`/`EXPIRED_AT_EFFECT`,
  `DELEGATION_WITNESSED`) are the *target* vocabulary. What ships **today** is
  `scope.classify` → `IN_SCOPE`/`SCOPE_CREEP`/`WRONG_TARGET` and
  `commit_audit` → `OK`/`CLAIM_UNWITNESSED`/`ABSTAIN`. Each adapter below states
  which existing verdict it reuses and which named verdict is *new* (and where
  the new one is just a rename/projection vs. a real gap that #119 fills).

---

## 1. The DOS-shaped hole in each standard (the map)

Each row: the standard, the hole it names but does not fill, the **shipped** DOS
primitive that fills it, the concrete adapter (driver-layer; the vendor/standard
name lives there, never in the kernel).

| # | Standard | The hole it leaves | Shipped DOS primitive | Adapter |
|---|---|---|---|---|
| 1 | **CSA AIGF** — JIT access; "intent declared → privilege granted → privilege exercised" | The third audit leg is anchored on the agent's *own* machine-readable intent — forgeable. Deviation = exercised-vs-*declared*, gameable by lying in the declaration. | The lease + `scope.classify` (SCF) judged against the **human-authored grant**, not the declaration. | Ingest a JIT grant (scope/expiry/budget) into the #119 mandate; emit the conformance verdict back as a SCIM behavioral-attestation / deviation event. |
| 2 | **IETF Agentic-JWT / OAuth-OBO** (`actor_token`, `requested_actor`; AAuth grant) | Delegation parentage is carried *in the token claims* — asserted by the issuer/agent chain. Nothing checks the claimed chain against what the runtime actually spawned. | `model_health.build_agent_tree` reconstructs the operator→sub-agent tree from the transcript `parentUuid` links — **evidence-derived, not claimed**. | Parse the `actor_token` chain / SVID lineage into a delegation tree; reconcile against the evidence tree; emit `DELEGATION_WITNESSED` / `DELEGATION_UNWITNESSED`. |
| 3 | **CSA constrained delegation** — *no identity may delegate more privilege than it holds; scope attenuates each hop* | Stated as a principle the auth server enforces **at issuance**; nothing verifies the *exercised* effect of a sub-agent stayed inside the parent's attenuated scope. | The lease + `scope.classify` per hop: a diff that left its lane is `SCOPE_CREEP`/`WRONG_TARGET`. | Map each hop's attenuated scope to a lane/tree; run SCF on each hop's effects; surface any out-of-scope as a delegation-ceiling violation. |
| 4 | **CoSAI** — "agents need authority *grants* (what they can do), not identity passports" | Defines the grant; says nothing about **verifying the grant was honored** at runtime. | `verify` (did this phase ship? — git ancestry, never self-report) + `commit_audit` (subject vs its own diff). | None new — this is the **framing**: a DOS verdict is the runtime witness that a CoSAI authority-grant was exercised within bounds. |
| 5 | **EIC 2026 consensus** — "runtime authorization checking *every* action"; "traceable delegation through *signed receipts*" | The deployed reality is long-lived keys + audit trails that would not survive an incident. | The PRE-moment sensor + the apply-gate **seam** (per-action check, docs/126); `attest` (docs/246) is the signed receipt a non-participant verifies with the public key alone. | `attest` already emits the receipt; the adapter renders it in the audience's envelope (in-toto/SLSA — #70). |
| 6 | **SPIFFE/SVID + SCIM agent registry** | Identity + inventory are issuance; "behavioral attestation" is a *registry field expecting input* — the registry says it wants expected-vs-actual but does not itself produce the adjudicated *actual*. | DOS produces the adjudicated *actual*: conformance + commit-audit + delegation-witness verdicts ARE the behavioral-attestation payload. | A SCIM-emit adapter pushes the DOS verdict as the registry's behavioral-attestation / deviation record. (Driver-layer; the kernel never learns SCIM.) |

Rows 1–6 collapse to **four adapter shapes** (the done-condition's count):
delegation-chain ingest + reconcile (row 2), grant ingest into the mandate (rows
1, 3, 4 — same ingest, different read), conformance emit into in-toto + SCIM
(rows 5, 6), and the outward vocabulary that names the verdicts in each
audience's envelope. §2 specifies each.

---

## 2. The four adapter shapes (each driver-layer, flagged, additive, reuses a shipped verdict)

All four live under `src/dos/drivers/` (or a plugin). Each is **off by default**
behind an explicit flag, **adds** a surface without changing any kernel verdict,
and **reuses** an existing adjudication rather than minting a new one. The
standards' names (SPIFFE, OAuth, SCIM, in-toto, A2A) appear **only** inside these
modules — never in `src/dos/*.py` outside `drivers/`.

### Adapter A — delegation-chain ingest + evidence reconcile

- **Reuses:** `model_health.build_agent_tree` (the evidence spawn tree from
  `parentUuid`).
- **Shape.** `ingest_delegation_chain(actor_token_chain | svid_lineage) ->
  ClaimedDelegationTree`, then `reconcile(claimed_tree, evidence_tree) ->
  DelegationWitness`. The reconcile is a *claim-vs-evidence* check applied to the
  delegation chain itself: a claimed parent P with no matching spawn edge in the
  evidence tree is `DELEGATION_UNWITNESSED` (the docs/103 distrust discipline,
  aimed at the token's parentage claim).
- **New vocabulary:** `DELEGATION_WITNESSED` / `DELEGATION_UNWITNESSED` /
  `DELEGATION_ABSTAIN` (no evidence either way — fail-to-abstain, like
  `commit_audit`). This *is* a genuinely new verdict — it has no current
  CLI verb — but it is a thin classifier over an existing tree-diff, not a new
  evidence engine.
- **Trust boundary (§3):** which `actor_token` fields are issuer-authored vs
  agent-influenceable decides what the reconcile is allowed to trust as the
  *claimed* side.
- **Subsumes:** the delegation-chain half of the agent-IAM framing; pairs with
  #52's "which receipt fields are provider-authored" question, re-asked for
  delegation tokens.

### Adapter B — grant ingest into the #119 mandate

- **Reuses:** the lease + `scope.classify` (today: `IN_SCOPE`/`SCOPE_CREEP`/
  `WRONG_TARGET`). The *named* grant-conformance verdict
  (`WITHIN`/`EXCEEDED`/`EXPIRED_AT_EFFECT`) is **#119's deliverable** — this
  adapter is the *external ingest path* into that record, not the record itself.
- **Shape.** `ingest_grant(jit_grant | aauth_grant) -> MandateRecord` maps an
  external IAM grant's (scope → lane/tree, budget → work-account ceiling,
  expiry → effect-time bound) into the #119 mandate. Conformance is then the
  #119 verdict run over the diff/WAL vs the *journaled grant* — the
  human-authored side, never the agent's declaration. Until #119 lands, this
  adapter degrades honestly: it runs `scope.classify` per hop against the
  grant's scope→tree mapping and reports `SCOPE_CREEP`/`WRONG_TARGET` as a
  *scope-only* conformance proxy, explicitly flagged as not yet checking
  budget/expiry.
- **New vocabulary:** none of its own — it feeds #119's. The `EXPIRED_AT_EFFECT`
  leg (effect-time vs grant-expiry) is the one piece neither `scope` nor #119's
  current sketch covers; this adapter names it as the open dependency.
- **Subsumes:** #119 (the internal mandate) — this adapter is the bridge that
  lets an *external* IAM grant populate it.

### Adapter C — conformance emit into in-toto + SCIM

- **Reuses:** `attest` (docs/246 — the signed receipt; Phase 1 shipped) and the
  conformance/commit-audit/delegation verdicts from A and B.
- **Shape.** Two renderers, both pure projections of a verdict the kernel already
  produced:
  - `to_in_toto(verdict) -> Statement{Predicate}` — the #70 envelope; the DOS
    verdict becomes a SLSA-shaped predicate with per-field provenance.
  - `to_scim_attestation(verdict) -> SCIMEvent` — pushes the verdict as the SCIM
    agent registry's behavioral-attestation / deviation record.
- **New vocabulary:** none — these are output dialects, downstream of the
  verdict (the docs/CLAUDE.md "a dialect is output, never a kernel concern"
  litmus). The kernel never learns in-toto or SCIM; the renderer does.
- **Subsumes:** #70 (the in-toto/SLSA envelope) and the emit half of row 6.

### Adapter D — the outward vocabulary map

- **Reuses:** every verdict above.
- **Shape.** A single declarative table (driver-layer data, not code branching on
  vendor) mapping each DOS verdict to the term each audience expects: AIGF's
  "deviation event," SCIM's "behavioral attestation," CoSAI's "authority-grant
  honored," EIC's "signed receipt." This is the `dos.toml`-style closed-set
  discipline applied to the *output* names — so a new audience is a table row in
  a driver, never a kernel edit.
- **New vocabulary:** none of its own — it is the rename layer. It exists so the
  other three adapters speak DOS verdicts internally and the audience's dialect
  only at the boundary.

---

## 3. The trust boundary for delegation tokens (issuer-authored vs agent-influenceable)

The whole leg is sound only if the *claimed* side of each reconcile is held to
the right standard. A delegation token mixes fields of two provenances, and DOS
must treat them differently:

| Field class | Examples | Provenance | DOS treats it as |
|---|---|---|---|
| **Issuer-authored** | `iss`, signature, `exp`, the issuer's scope grant, an SVID's SPIFFE ID | Minted + signed by the authorization server | A trustable *grant* — the human/issuer side of the non-forgeability pair. Verified by signature, then journaled into the mandate (Adapter B). |
| **Agent-influenceable** | `requested_actor`, a self-asserted intent declaration, any claim the agent's request shaped | Set or shaped by the agent's own request | **Narrated, never the gate.** Carried for context; the reconcile (Adapter A) checks it *against* the evidence tree, it does not believe it. |

The load-bearing rule, stated so a reviewer can check it:

> **The reconcile trusts a delegation claim only to the extent it is
> issuer-authored AND signature-verified. The parentage *the agent's request
> could have shaped* is the claimed side checked against evidence, never the
> evidence itself.**

This is the same trust-boundary question #52 asks of sandbox execution receipts
(which receipt fields are provider-authored vs agent-influenceable), re-asked for
delegation tokens. Open sub-question carried to P1: for a chained `actor_token`,
is each hop's signature independently verifiable, or only the outermost? If only
the outermost, an intermediate hop's parentage is agent-influenceable and drops
to the narrated column — the reconcile must degrade to `DELEGATION_ABSTAIN` for
that hop rather than over-trust it.

---

## 4. Subsumption map (nothing duplicated)

| Adapter | Subsumes | Relationship |
|---|---|---|
| A — delegation ingest + reconcile | (the agent-IAM framing) + #52 | New thin reconcile over `build_agent_tree`; reuses #52's provider-authored-field question for token fields. |
| B — grant ingest into mandate | **#119** | The *external ingest path* into #119's mandate record; #119 owns the `WITHIN/EXCEEDED/EXPIRED_AT_EFFECT` verdict, this adapter populates its grant from an external IAM grant. |
| C — conformance emit | **#70** + row 6 | Renders the verdict into #70's in-toto/SLSA envelope and the SCIM registry field; no new verdict. |
| D — outward vocabulary | (all) | The rename layer so the three above speak DOS internally, dialect at the edge. |

This issue (#142) is the **umbrella**: it specifies the composition and the
shared trust boundary. It closes by landing this plan's P1 — not by re-filing
#119/#70/#52, which keep their own scopes.

---

## 5. Kernel-litmus compliance (the additive proof)

Every adapter is checked against the CLAUDE.md litmus before it is built:

- **No host/vendor/roster in `src/dos/`.** SPIFFE, OAuth, SCIM, in-toto, A2A,
  AIGF appear only under `drivers/` (or a plugin). The kernel verdicts
  (`scope.classify`, `build_agent_tree`, `commit_audit`, `attest`, `verify`)
  name none of them and gain no new import.
- **A driver is the only place policy lives.** Each adapter is a new `drivers/`
  module; no `config.py` edit, no kernel verdict changed.
- **A dialect is output.** Adapters C and D are renderers *downstream* of a
  verdict the kernel already produced — they cannot change a verdict, only name
  it for an audience.
- **Fail-to-abstain.** The new `DELEGATION_*` classifier abstains on absent
  evidence, exactly like `commit_audit` — it never fabricates a witness.
- **Additive + flagged.** Every adapter is off by default behind an explicit
  flag; with all flags off, the kernel is byte-identical to today.

---

## 6. Phasing

| Phase | What | Layer | Depends on | Status |
|---|---|---|---|---|
| **P1** | **Adapter A**: delegation-chain ingest + `reconcile` over `build_agent_tree`; the `DELEGATION_WITNESSED/UNWITNESSED/ABSTAIN` classifier; the §3 trust-boundary table encoded as the field-provenance map; tests. The smallest end-to-end exercised-side check that needs no other issue. | driver | `build_agent_tree` (shipped) | unbuilt — **closes #142** |
| **P2** | **Adapter B**: grant ingest into the mandate. Lands the scope-only conformance proxy now (per-hop `scope.classify` vs grant→tree); upgrades to the full `WITHIN/EXCEEDED/EXPIRED_AT_EFFECT` verdict when #119 ships. | driver | #119 (full verdict); `scope.classify` (proxy) | unbuilt |
| **P3** | **Adapter C**: `to_in_toto` + `to_scim_attestation` renderers over the P1/P2 verdicts. | driver | #70 (envelope shape); `attest` (shipped) | unbuilt |
| **P4** | **Adapter D**: the outward-vocabulary table + the `EXPIRED_AT_EFFECT` effect-time check (the one leg neither `scope` nor #119's sketch covers). | driver | P2 | unbuilt |

P1 is deliberately the closer: it is the one adapter whose every dependency
already ships (`build_agent_tree`), so #142's done-condition can be met without
waiting on #119/#70. P2–P4 layer on as their subsumed issues land.

---

## 7. What this is NOT

- **Not** an issuance stack. No token minting, no auth provider, no credential
  store in `src/dos/`. DOS verifies *exercise*; it never issues identity (§0
  floor).
- **Not** the apply-gate PEP. This plan adds *detection + attestation* adapters.
  Refuse-at-write enforcement is docs/126's unbuilt seam — do not sell it as
  present.
- **Not** a kernel edit. Every adapter is a `drivers/` module or plugin; the
  standards' names never enter the kernel; with flags off the package is
  byte-identical (§5).
- **Not** a re-file of #119/#70/#52. This is the umbrella that composes them; each
  keeps its own scope (§4).
