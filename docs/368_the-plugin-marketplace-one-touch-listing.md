# docs/368 — The plugin marketplace: one touch from an agent to a listed plugin

> **Status:** design note. No code yet — this is the shape, the trust model,
> and the three audiences (public, internal-team, vendor). Mechanism lands as
> a separate `scripts/` + a thin `dos market` verb later; nothing here edits a
> kernel leaf.

## The one-line thesis

DOS is *already* a marketplace substrate. We don't need to invent a plugin
mechanism — we shipped one: **eleven typed entry-point groups** (`dos.drivers`,
`dos.judges`, `dos.predicates`, `dos.exporters`, `dos.notifiers`,
`dos.evidence_sources`, `dos.hook_dialects`, `dos.stop_policies`,
`dos.memory_stores`, `dos.vcs`, `dos.mcp_tools`, …), each with an
**unshadowable built-in floor** and a **stated safety invariant**
(`src/dos/plugins.py`). A plugin is just a pip-installable package that
registers one occupant under one of those groups. `dos plugins` already lists
what's installed; `dos plugin new <seam>` already scaffolds a correct stub.

So the marketplace is not new mechanism. It is **an index + a trust layer + a
one-touch install** over a registration surface that already exists. The whole
design question is: *what does "one touch from an agent to a listed plugin"
mean, and what makes our index trustworthy when every other plugin registry
trusts the author's own description?*

## The crux: a marketplace where the kernel doesn't believe the listing

Every plugin registry in existence — npm, PyPI, the VS Code marketplace, the
MCP registries — indexes **what the author says their package does**. The
description, the tags, the "verified" badge: all self-reported. This is the
exact failure class DOS exists to refuse. A package that says
`dos.judges: acme-judge` and is secretly a `dos.predicates` occupant that
force-admits every lease is a *self-narrating worker* — and the kernel is the
part that doesn't believe self-narrating workers.

The distinctive marketplace, the one only DOS can build, **verifies the listing
the way it verifies a claim**: against evidence the author didn't author.

- **The seam claim is checkable.** A listing says "I register under
  `dos.judges`." That's not a description — it's the package's own
  `entry-points` metadata, which `importlib.metadata` reads independently. The
  index records the *discovered* group, never the marketing copy. A package
  whose README says "judge" but whose entry point is `dos.predicates` is listed
  as a predicate, with the mismatch flagged.
- **The invariant claim is checkable.** Each seam has a stated safety invariant
  (`SeamDescriptor.invariant`). A judge "FAILS TO ABSTAIN, never auto-AGREE." A
  predicate is "CONJUNCTIVE-ONLY, can only REFUSE." We can run the occupant
  against a **conformance harness** — the same shape as the kernel's own seam
  tests — and record a *witnessed* conformance verdict, not a badge the author
  pinned on themselves. A judge that returns AGREE on an empty claim fails
  conformance and is listed as such.
- **The provenance claim is checkable.** "Built by 4,900 passing tests, 99.8%
  diff-witnessed commits" is exactly the scoreboard story we already tell about
  *this* repo (`docs/scoreboard/`). A listed plugin's repo gets the same
  treatment: `dos commit-audit` over its history, a test-count read, a
  truth-clean check. The listing carries a **DOS-verified provenance card**,
  not a star count.

That is the moat. Anyone can build a faster index. Only the trust substrate can
say *"this listing is what it claims, verified against bytes its author didn't
write."* The marketplace is the witness invariant pointed at supply chain.

## The three audiences

The same index serves three flows. They differ in **who lists** and **what
"verified" means**, not in mechanism.

### 1. Public — the open registry

The PyPI-shaped flow. Anyone publishes a `dos-*` package that registers a seam
occupant; the public index discovers it. The honest denominator here is *low
trust* — the listing tells you the discovered group, the conformance verdict,
and the provenance card, and it is explicit that **install ≠ endorsement**.
The value is *legibility*: an agent (or operator) can ask "what notifiers
exist?" and get a typed, conformance-tested answer instead of a search-engine
guess.

The one-touch listing path:

```
dos plugin new notifier --name acme    # scaffold (already shipped)
# ... fill in the stub, pip publish ...
dos market submit                      # registers the published package with the index
```

`dos market submit` reads the package's *own* entry-point metadata + runs the
conformance harness locally, then posts the **verified facts** (discovered
group, conformance result, provenance card) to the index — not a free-text
description. The author cannot list a lie because the index records what the
harness saw, not what the form said.

### 2. Internal teams — the private registry, the higher-trust default

This is the flow most teams will actually live in, and it's where DOS earns its
keep. A company runs many agent fleets; internal teams ship their own host
policy packs (`dos.drivers`), their own LLM judges, their own evidence sources
(the team's CI status, their own approval envelope). They do **not** want these
on public PyPI, and they want a *higher* trust bar than "anyone can publish."

A private index is the same index, pointed at a private package source (a
internal PyPI mirror, a git org, an artifact registry). The trust model
inverts: here **listing IS a gate**. A team's plugin is listed only when:

- the conformance harness passes (same as public), **and**
- its provenance card clears the org's policy (e.g. "diff-witnessed ≥ X%,
  truth-clean, the suite is green") — which is itself a DOS verdict, so the
  *marketplace admission decision is a kernel decision*, refused with a typed
  reason (`MARKET_CONFORMANCE_FAIL`, `PROVENANCE_BELOW_FLOOR`) from the closed
  vocabulary, not an approval-meeting.

The one-touch path for an internal author is the same `dos market submit`, but
the verdict it returns is binding: the plugin is discoverable to every fleet in
the org the moment it clears, and refused (with the typed gap) until it does.
This is the supply-chain analogue of `dos arbitrate`: it serializes *what gets
trusted into the fleet's import path*, the way arbitrate serializes *what edits
shared files*.

### 3. Vendors — the curated channel, dogfood as the credential

Runtime vendors (the hosts: Cursor, Codex, Gemini, Cowork, Hermes — all of whom
already have a `dos.hook_dialects` occupant in-tree) ship first-party plugins:
a dialect, an install spec, a host policy pack tuned to their runtime. The
credential that matters here is **dogfood**: "this vendor uses DOS to build the
plugin they're shipping you." The scoreboard already shows vendors dogfooding
their own tool; the curated channel makes that the *listing credential*. A
vendor plugin carries a provenance card that says "built under DOS, here's the
verdict stream" — the strongest possible trust signal, and one a vendor cannot
fake because the verdicts are git-witnessed.

## What "one touch from an agent" actually requires

The user's framing — "one touch from agent to get a person's plugin listed" —
is the design constraint. An agent in a session should be able to take a plugin
from *exists* to *listed* in a single verb, with the kernel doing the
verification inline. Concretely, `dos market submit` must, in one call:

1. **Discover** the package's entry-point group(s) — `importlib.metadata`, the
   same scan `dos plugins` does. (No author description trusted.)
2. **Conform** — run the per-seam conformance harness against each occupant.
   This is the new code: a `dos.market` conformance table keyed by seam,
   asserting the invariant (judge fails-to-abstain, predicate refuse-only,
   exporter fail-soft, …). Reuses the *shape* of `src/dos/plugins.py`'s
   descriptor table — the invariants are already written down there.
3. **Provenance** — `dos commit-audit` + test-count + truth-clean over the
   package's repo, producing the provenance card.
4. **Submit** — post the verified facts to the index (public or private,
   selected by config the same way every other seam selects its backend).
   Refuse with a typed reason if conformance or provenance fails.

The agent touches one verb. The kernel does the distrust. That's the product.

## The honest limits (state them first, the residual-review discipline)

- **Conformance ≠ correctness.** The harness proves the occupant honors the
  seam's *invariant* (a judge abstains rather than auto-agrees), not that its
  judgment is *good*. Same limit as `dos review`: shape is witnessable,
  correctness is not. The listing must say this.
- **Provenance ≠ safety.** A diff-witnessed, suite-green plugin can still do
  something you don't want — the card proves *how it was built*, not *what it
  does to your fleet*. The conjunctive-only / fail-soft / unshadowable-floor
  invariants are what actually bound a malicious occupant's blast radius, and
  those are kernel guarantees that hold *regardless of the listing*. The
  marketplace's safety story is "the seam invariants contain any occupant" —
  the index just makes the trustworthy ones findable.
- **The index is infrastructure, not a kernel leaf.** Per the layering, this is
  `scripts/` + a `dos market` helper verb + a driver-resolved index backend
  (public/private/curated = a `dos.market_backends` seam, naturally). Nothing
  here touches `src/dos/` adjudication. The conformance harness *uses* the
  kernel; it doesn't change it.

## Decisions (operator, 2026-06-16)

- **Backend-agnostic seam first.** Build no concrete hosted index up front.
  Ship the `dos.market_backends` driver seam + the conformance harness +
  `dos market submit` against a **file backend** (a JSONL index on disk).
  Public / private / vendor are then each just a registered backend resolved
  by name the same way every other seam selects its implementation — the most
  DOS-idiomatic shape, and it means the trust logic is written once and reused
  across all three audiences.
- **Prototype `dos market submit` end-to-end first.** Prove the one-touch flow
  against the file backend with the real discover + provenance steps and a
  starting conformance check, then deepen the per-seam conformance table. The
  point of the prototype is to make "one touch from an agent" concrete before
  investing in hosted infra.
- **Lives in `scripts/`, not a kernel leaf — and not a `cli.py` edit yet.** The
  prototype is `scripts/dos_market.py` (a tooling module that imports `dos`,
  never edited by the kernel) plus the `dos.market_backends` seam descriptor.
  The `dos market` CLI verb is a one-line follow-up in `src/dos/cli.py` once
  the override is armed and the tree is calm — deferred so the prototype never
  races the kernel's own running code (the SELF_MODIFY discipline).

## Why this is the right next bet

The extensibility seams were the precondition (shipped: `dos.drivers` +
`dos.mcp_tools` entry-point groups, `dos plugins`/`dos plugin new`). The
marketplace is what turns a *mechanism* into an *ecosystem*: the seams let a
third party extend DOS without a fork; the marketplace lets the fleet *find and
trust* those extensions. And it's the rare growth lever that is also a
distrust lever — every other registry indexes self-report; ours indexes
witnesses. The marketplace is the trust substrate applied to its own supply
chain.
