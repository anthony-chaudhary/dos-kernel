# 344 — The dark fleet: coordination when no one is reading

> **The question behind the question.** [`340`](340_what-dos-means-the-winning-move-when-narration-dies.md)
> proved a *convergence law*: as agent output moves from prose a human reads to
> tool calls a machine reads, every trust surface except the un-authored effect
> either goes blind or collapses into a self-report. That note named the limit.
> This note names the *system that lives at the limit* — the **dark fleet** — and
> answers the operator's three-part ask against it: expand the concept, audit what
> the 2026 field actually has, and find where DOS gives **clear, falsifiable lift**.

This is a vision note, sibling to [`333`](333_verification-as-steering-and-the-verification-first-harness.md),
[`335`](335_tcp-for-agents-validating-the-reliability-analogy.md),
[`336`](336_the-prose-to-tool-call-shift-and-the-substrate.md), and
[`340`](340_what-dos-means-the-winning-move-when-narration-dies.md). It ships no
mechanism and carries no litmus; it is a thesis about a regime, an audit of the
field in that regime, and a lift map — each claim checked against the kernel that
already exists and against primary 2026 sources (§3 lists them; the two papers
that independently arrive at DOS's own thesis were fetched and confirmed, not
recalled).

The thesis in one line: **a dark fleet is a fleet whose work is legible only to
machines — many agents, high throughput, acting on shared state, with no human
reading the transcripts in real time. It is not a hypothetical; it is the
operating point the prose-to-tool-call trend is driving every serious agent
system toward, and it is the regime in which the open-loop failure modes stop
being recoverable. The 2026 field has built isolation, a reliability-decay
science, and end-of-run test gates for it — but the *continuous, neutral,
pre-effect, effect-grounded* trust-and-concurrency layer a dark fleet actually
needs is an open hole the literature is only now naming. That hole is the DOS
shape, exactly.**

---

## 1. What a dark fleet is — the concept, made precise

The term names a regime, not a product. A fleet is **dark** when *no observer with
authority to act on the fleet's behalf can read what the fleet is doing in time to
intervene.* Three conditions, each independently sufficient to darken a fleet, and
in the trend's end state all three hold at once:

1. **Narration-dark** — the agents emit tool calls and almost no human-legible
   prose ([`336 §1`](336_the-prose-to-tool-call-shift-and-the-substrate.md)). There
   is no transcript to skim because there is no transcript.
2. **Throughput-dark** — even where prose exists, the fleet generates faster than
   any human can read. A reviewer who *could* follow one agent cannot follow forty.
   The transcript exists and is unread, which is operationally identical to its not
   existing.
3. **Concurrency-dark** — the agents act on *shared* state at the same time, so the
   meaningful unit is not "what did agent *i* say" but "what is the *interleaving*
   of N agents' effects," and no agent narrates the interleaving because no agent
   can see it.

"Dark" is deliberate: it is the same word an air-traffic controller uses for an
aircraft that has dropped off the transponder. The plane is still flying, still on
a trajectory, still able to collide — you have simply lost the channel that let you
*know* before the fact. A dark fleet is still shipping commits, still mutating the
tree, still able to bank a confident lie or silently overwrite a sibling — you have
lost the narration channel that, in the prose-heavy era, let a human catch it by
reading.

**The dark fleet is the terminal state of [`340`](340_what-dos-means-the-winning-move-when-narration-dies.md)'s
convergence.** 340 proved that trust converges onto the un-authored effect because
the substitutes go blind. The dark fleet is the system *operating at that limit*:
a population of agents for which the effect-channel is not merely the *best* trust
surface but the *only remaining* one. Everything 340 said about a single verdict,
this note says about a running population.

Two clarifications that keep the concept honest:

- **Dark is a spectrum, not a switch.** Today's fleets are *dimming*, not dark —
  prose is thinning, humans still skim short transcripts, a fleet of two is not yet
  throughput-dark. The concept's *urgency* scales with the trend's derivative
  ([`340 §5`](340_what-dos-means-the-winning-move-when-narration-dies.md)), and the
  derivative could flatten (regulation forcing narration, interpretability-driven
  models that narrate by design). The dark fleet is the direction, stated cleanly so
  the lift map can be built against the operating point we are heading for, not the
  one we have.
- **Dark ≠ adversarial.** A dark fleet is not a swarm of attackers; it is a swarm of
  *cooperative, well-intentioned, confidently-wrong* agents. The danger is not
  malice — it is the [`158`](158_recall-expansion-silent-and-frontier-failures.md) failure mode
  (fail while asserting success) multiplied by fanout and compounded across horizon,
  with the human catch-channel removed. This matters because most of the field's
  coordination work (§3) assumes cooperative agents and *therefore* assumes honest
  self-reports — the exact assumption darkness invalidates.

---

## 2. What goes dark — every observer, named

340 §2 walked the *trust surfaces*. The dark fleet sharpens it to *observers* — the
parties who, in a supervised fleet, would catch a problem, and what each one reads
once the fleet darkens. This is the audit of who you lose.

- **The human reviewer** reads **nothing in time.** Not because the work is hidden
  but because the arrival rate exceeds the read rate. This is not a metaphor: it is
  Kingman's M/M/1 wait `ρ/(1−ρ)·(1/μ)` blowing up super-linearly as the "done"
  arrival rate ρ → 1 ([`170 §1b`](170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md),
  the Faros productivity paradox — high-AI teams merged +98% more PRs while review
  time rose +91%). The faster the fleet, the darker it is to the human, *by
  construction*.
- **The LLM-judge / supervisor agent** reads **a self-report.** It summarizes the
  acting model's call stream — output optimized by the same training that produced
  the action ([`336 §5`](336_the-prose-to-tool-call-shift-and-the-substrate.md)). The
  2026 evaluation literature makes this damning, not merely suspect: self-preference
  bias is *stronger in more capable models* (arXiv 2410.21819, 2604.22891), position
  bias flips rankings by reordering (2406.07791), and win-rate metrics provably
  cannot separate "narcissism" from quality. A judge reading the fleet's narration is
  a self-report wearing a second model's voice.
- **The acting agent itself** reads **its own trace, and cannot correct it.** *The
  Self-Correction Illusion* (arXiv 2606.05976) is the sharpest 2026 result here: with
  the erroneous claim held byte-identical, relabeling it from the agent's own
  "thought" to an *external* source lifted correction rates **23–93 points** across
  13 model-domain cells. An agent grading its own work is structurally the weakest
  observer in the system — and a dark fleet, by removing every external reader, leaves
  exactly that weakest observer in charge. This is [`136 §2`](136_the-closed-loop-as-the-organizing-principle.md)
  ("a loop whose sensor is the plant is open with extra steps") as a published
  empirical fact.
- **The observability stack** reads **what the agent emitted.** LangSmith, AgentOps,
  Arize Phoenix, and the OpenTelemetry GenAI semantic conventions standardize and
  record prompts, tool calls, outputs, reasoning spans — all *authored by the agent's
  own execution*. By construction the span content is the agent's voice, more
  faithfully captured. **Richer tracing of a self-report is not honesty; it scales the
  narration, not the truth.** A dark fleet with perfect OTel coverage is still dark —
  you have a high-resolution recording of forty agents each confidently saying "done."

Walk the list and the conclusion is forced: **every observer a supervised fleet
relies on either goes blind (the human) or collapses onto reading the agent's own
output (the judge, the agent, the tracer).** The single exception is the one that
reads something *no agent authored* — git ancestry, the exit code, the lease
journal, the DB row. In a dark fleet that is not one trust surface among several. It
is the **only** one with a pulse.

---

## 3. SOTA audit — what 2026 has for the dark fleet, and the hole

The audit was run as a three-way fan-out over the 2026 literature and shipping
systems (agent-to-agent protocols; multi-agent SWE + long-horizon reliability; trust
/ governance / verifiability), each slice tasked to separate **REAL** (shipped /
standardized) from **PROPOSED** (paper / draft) from **VAPOR** (branding past
mechanism), and to answer one question per item: *does it ground trust in an
un-authored effect, or does it assume an honest self-report?* The findings converge
hard.

### 3.1 — What the field has REAL

- **Isolation is the de-facto primitive.** Git worktree / sandbox-per-agent is now
  standard (Claude Code `--worktree` and subagent worktree isolation, VS Code,
  JetBrains 2026.1, Container-Use playbooks). **CAID** (arXiv 2603.21489, *Effective
  Strategies for Asynchronous Software Engineering Agents*) is the strongest published
  result: **+26.7% PaperBench / +14.3% Commit0** via centralized async delegation +
  worktree isolation, consolidated by **executable test-based verification** — a
  genuine un-authored gate, not an LLM-judge. This is the closest published cousin to
  DOS's lane + `verify` shape, from an independent lab.
- **A reliability-decay science exists.** *Beyond pass@1* (arXiv 2603.29231, 10
  models, 23,392 episodes) shows capability and reliability *diverge as duration
  grows*, worst in software engineering (GDS 0.90 → 0.44). METR's time-horizon work
  shows the published 50%-reliability horizon doubling every ~4–7 months — and that
  the **80–99% horizon is dramatically shorter**, i.e. a long-running unattended fleet
  is operating well past its high-reliability horizon. The science says, precisely:
  *a dark fleet maximizes the one variable (uninterrupted duration × many actors) that
  destroys reliability.*
- **Identity and transport are mature and standardized.** **A2A** (Linux Foundation,
  v1.0, Signed Agent Cards), **MCP** (tool transport + OAuth-style authz), **ACP**
  (IBM/BeeAI messaging), **AGNTCY** (Cisco/LangChain discovery + identity + telemetry),
  and the identity stack (**DIDs**, **AP2** mandates, **ERC-8004** registries) make
  *who is talking* and *was it authorized* non-repudiable.
- **Tamper-evident provenance is real** — Sigstore/Rekor (RFC-6962 transparency log)
  certifies *artifacts and signing events* un-forgeably.
- **Regulation manufactures demand for a neutral verdict.** EU AI Act Art. 12
  mandates automatic event logging for high-risk systems (Annex III obligations land
  Aug 2, 2026), and draws a provider-vs-deployer line that wants oversight the vendor
  did not author.

### 3.2 — The hole, named three ways

Across every slice, the same gap: **the field has identity + transport + isolation +
a decay science + end-of-run test gates, and lacks a continuous, neutral, pre-effect,
effect-grounded trust-and-concurrency layer.** Three sharp edges of that one hole:

1. **The protocols verify *who* and *whether-authorized*, never *what-landed*.** A2A's
   Signed Agent Cards prove a card's origin; the receiving agent still learns task
   `status` from the executing agent's **own self-report**. AP2 mandates prove the
   user said "buy," not that the right thing was bought. ERC-8004's reputation is
   aggregated feedback — social, gameable — and its only true effect rung (TEE/ZK
   attestation) is narrow and largely future. **No mainstream protocol adjudicates a
   claimed effect against un-authored ground truth.** (A2A/MCP/ACP repos + specs;
   ERC-8004 EIP.)

2. **Concurrency on shared state is paper-only or deferred to merge.** No shipped
   standard serializes who-may-write-which-files *before* the edit. Worktree isolation
   — the industry answer — by its own docs *moves collisions to merge time*:
   file-level clashes become git conflicts and **semantic** collisions (two agents
   satisfying overlapping intent inconsistently) survive a clean merge. **CodeCRDT**
   (arXiv 2510.18893) reports 100% CRDT *convergence* yet **5–10% semantic conflict
   rates convergence cannot catch**, asymmetric outcomes (+21% best case, −39% worst),
   and a practical 3–4-agent ceiling — *convergence is not correctness*. Real
   admission control (leases / locks for distrusting agents) exists only in research
   (STORM, TraceFix, HearthNet — paper-stage).

3. **Observability traces the self-report; "evaluation" is the judge reading the
   narration.** §2 already named this; the audit confirms it is the *whole* shipped
   answer. The OTel GenAI conventions standardize, by construction, what the
   instrumentation around the agent emits. The evaluation layer is overwhelmingly
   LLM-as-judge, whose error rate exceeds 50% on complex tasks. There is no
   widely-adopted layer that checks each claimed effect against an un-authored source
   *continuously, across a shared mutating state*.

### 3.3 — The convergence: the literature is now *naming* DOS's thesis

The strongest signal is not the gap but that independent 2026 work is arriving at
DOS's exact design, from the outside, and stopping one rung short:

- **"From Logic Monopoly to Social Contract: Separation of Power…"** (arXiv 2603.25100,
  Anbang Ruan — *fetched and confirmed*) names the pathology in DOS's own terms: an
  agent that "plan[s], execute[s], and evaluate[s] its own actions" is a **logic
  monopoly**, and the fix is to split **Adjudication** off as a separate branch. That
  is [`333 §5`](333_verification-as-steering-and-the-verification-first-harness.md)'s
  co-resident-self-grading limit and DOS's "the referee cannot report to a contestant"
  inversion, published. But the mechanism is a 143-page pre-peer-review paper anchored
  to a proprietary blockchain framework — the *shape*, not a neutral effect oracle.
- **"Right to History: A Sovereignty Kernel for Verifiable AI Agent Execution"**
  (arXiv 2602.20214, Jing Zhang — *fetched and confirmed*) builds an RFC-6962
  append-only ledger making agent execution history un-forgeable and third-party
  auditable, *independent of the agent's claims*. That is the lease-WAL discipline
  generalized. But it proves *what an agent did*, not *whether what it did was correct
  or actually shipped the claimed phase* — tamper-evidence, not adjudication.

Both arrive at "separate the judge from the actor; ground the record in something the
agent didn't author" — and both stop before the thing DOS ships: a **domain-free,
neutral oracle that adjudicates whether the claimed effect actually landed**, from git
ancestry and exit codes, and **refuses on a closed vocabulary**. The 2026 literature
is converging on the problem statement precisely because it is unsolved. *That* is the
opening 340 §3 calls the substrate-of-record position.

---

## 4. Where DOS provides clear lift — the falsifiable map

The discipline ([`170`](170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md),
[`102 §5`](102_when-to-trust-an-agent.md)): a lift claim is worthless without the
regime it lives on and the falsifier that kills it. Each lift below names the dark-fleet
condition (§1) it answers, the SOTA hole (§3) it fills, the **real kernel surface** that
delivers it, and the honest boundary. None of these is a per-model capability boost —
DOS's detect/intervene value is regime-bound to weak models and ~0 on the frontier by
construction ([`170 §1a`](170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md)).
The dark-fleet lift is a **fleet/horizon/coordination** property, orthogonal to per-model
strength and *amplified* by frontier throughput.

### Lift 1 — The only observer with a pulse (verify / commit-audit)

**Answers:** narration-dark + throughput-dark. **Fills:** §3.2(3) — observability
traces self-report.
When every reader in §2 has gone blind or self-referential, `dos verify` (did
`(plan, phase)` ship? — git ancestry + stamp grammar, never the `Status:` line) and
`dos commit-audit` (does the commit's *claim* match its own *diff*?) read the one
channel no agent authored. This is not a better judge; it is a *categorically
different observer* — the only one a dark fleet has not blinded.
- **Boundary:** the effect-channel answers *did the kind of thing claimed happen*, never
  *is the work correct* — a green suite on wrong tests is still a forgeable rung
  ([`138`](138_what-is-truth-the-throughline.md), [`340 §5`](340_what-dos-means-the-winning-move-when-narration-dies.md)).
  DOS is the arbiter of *whether the claimed effect occurred*, a smaller and more
  honest thing than correctness.
- **Falsifier:** at N=1 with a human watching every step, the human is not blind and the
  lift is ~0. The value turns on *darkness*, not on the kernel being clever.

### Lift 2 — Admission control *before* the write, not at merge (arbitrate / the lease WAL / the pre-effect gate)

**Answers:** concurrency-dark. **Fills:** §3.2(2) — no shipped pre-write serialization;
isolation defers collisions to merge.
This is the lift the SOTA gap is widest on. The whole industry answer — worktree
isolation — *defers* the collision to merge time and lets semantic collisions through a
clean merge (CodeCRDT's 5–10%). DOS `arbitrate` adjudicates file-tree overlap from the
leases *actually held* and refuses the colliding acquire **before** either agent writes
([`89`](89_the-lane-is-a-region-lock.md), the region lock). The in-flight pre-effect gate
([`340 §3.3`](340_what-dos-means-the-winning-move-when-narration-dies.md), the docs/126
binding turnstile) turns this from a witness into a *controller* — refuse the escaping or
colliding write before it touches the tree. A dark fleet *cannot self-coordinate by
reading each other's chat* (there is no chat); it can only coordinate by reading each
other's **adjudicated leases** — which is exactly what the WAL is for.
- **Boundary:** the arbiter is sound on file-tree disjointness; it does not catch a
  *semantic* collision two disjoint trees can still create, and declared-future scope is
  agent-authored and forgeable (the docs/117 scope-source hole) — so forward-looking
  contention warnings reduce *latency*, they are not a BLOCK guarantee. The unforgeable
  floor stays the acquire-time arbiter over real leases.
- **Falsifier:** `generate_disjoint()` — when footprints are truly disjoint the arbiter
  refuses nothing and the lift is exactly 0. The value is monotone in *write contention*,
  which is monotone in fanout; it is 0 at fleet=1 by equation
  ([`170 §4 F8`](170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md)).

### Lift 3 — Neutral ground for mutually-distrusting parties (the domain-free kernel)

**Answers:** the dark fleet as a *multi-party* system (cross-vendor, operator-vs-supplier).
**Fills:** §3.2(1) + §3.3 — the protocols verify identity not effect; the convergence
papers stop short of a neutral oracle.
Every SOTA trust layer in §3 is either owned by a party to the work (a vendor grading its
own agents' output — a self-report wearing a dashboard) or assumes cooperative agents. A
dark fleet spanning two suppliers, or an operator who does not trust its vendor's
dashboard, has *no one whose verdict both sides can accept* — except a verdict standing on
an effect *neither side authored*. DOS's domain-freedom (the [`CLAUDE.md`](../CLAUDE.md)
litmus: kernel names no host, no vendor, no judge) is, in the dark-fleet regime, not
hygiene — it is the entire value. The convergence papers (§3.3) confirm the field wants
this and that a *neutral* version is unbuilt; the regulation (EU AI Act, §3.1) confirms the
demand is becoming mandatory.
- **Boundary:** a vendor consortium could bless its own effect-trust API and win on
  distribution despite the co-resident-self-grading flaw
  ([`340 §5`](340_what-dos-means-the-winning-move-when-narration-dies.md)) — neutrality is
  a bet that buyers *value* it enough to route around the incumbent, not a guarantee.
- **Falsifier:** if a single vendor owns the whole fleet end-to-end and the operator
  trusts that vendor's verdict, the neutrality premium is 0. The lift turns on *more than
  one distrusting party*, which the cross-vendor / regulated regimes supply and a captive
  single-vendor stack does not.

### Lift 4 — Refuse in a shared, verifiable vocabulary (refuse / the closed reason set)

**Answers:** the dark fleet as a *legibility* problem between machines. **Fills:** §3.2(1)
— protocols carry messages, not typed, verifiable refusals.
When a human stops reading, "no" has to mean the same thing to the next machine as it did
to the last. A free-text refusal is prose — exactly the channel that died. DOS's closed
refusal vocabulary ([`docs/HACKING.md`](HACKING.md)'s reason registry; `dos refuse` /
`check_reason`) makes a refusal *emittable, verifiable, and refusable* — a typed value a
sibling agent can route on without re-reading a paragraph. This is the [`340 §3.1`](340_what-dos-means-the-winning-move-when-narration-dies.md)
"own the verbs" move applied to the *negative* path: the dark fleet needs a shared language
for *declining*, and the field's protocols (§3.1) standardize the happy-path message, not
the typed refusal.
- **Boundary:** the vocabulary is only as good as its adoption — a refusal reason is a
  network effect ([`340 §4`](340_what-dos-means-the-winning-move-when-narration-dies.md)),
  worthless until a second fleet speaks it. This is a standardization bet, not a mechanism
  that pays off at N=1.
- **Falsifier:** an UNCLASSIFIED reason (one outside the closed set) is the prose-drift the
  vocabulary exists to kill — if most real refusals fall outside the set, the vocabulary is
  too small to be load-bearing and the lift is illusory.

### The lift, in one line

**On a dark fleet, DOS is the only observer with a pulse (Lift 1), the only coordinator
that acts before the collision instead of witnessing it after (Lift 2), the only verdict
two distrusting parties can both point at (Lift 3), and the only shared language for "no"
(Lift 4) — each answering a specific darkness condition, each filling a named SOTA hole,
each with a falsifier that sets it to 0 in the light.**

---

## 5. The honest limits — what this note is not

- **The dark fleet is a direction, not today's deployment.** §1 said it: fleets are
  dimming, not dark. The lift map is built against the operating point the trend points
  at; its urgency is the trend's derivative, and the derivative could flatten. This note
  describes where the value *goes*, not a payoff already banked.
- **The strongest fleet-coordination evidence is still SIMULATED.** DOS's own velocity
  result (FleetHorizon, [`170 §1d`](170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md))
  hardcodes a lie rate; the one real arc measured a short-horizon *net loss*. The dark-fleet
  lift is proven as a *mechanism* and as a *direction*, not yet as a live frontier dollar
  figure. The cheapest-first instruments (docs/170 §4 Tier 3) to close that gap are
  unchanged by this note.
- **DOS does not make the dark fleet correct — it makes it *reviewable and
  non-colliding*.** Every lift in §4 carries the same ceiling: the effect-channel grades
  *whether the claimed thing happened*, never *whether it was the right thing*. A dark fleet
  on DOS still needs a test suite for correctness; DOS removes the *trust and concurrency*
  failure modes, not the *capability* ones.
- **Being the substrate of record is a standardization fight, not a guarantee.** Lifts 3
  and 4 are ecosystem positions ([`340 §3`](340_what-dos-means-the-winning-move-when-narration-dies.md)),
  contingent on adoption and on shipping the pre-effect gate (Lift 2's controller half).
  A witness-only DOS is a court, not a coordinator — a weaker position than the one this
  note maps.
- **A neutral substrate everyone routes through is itself a single point of trust.** The
  dark fleet's safety resting on DOS raises the bar on DOS's *own* integrity
  ([`329`](329_witness-tamper-floor-the-keep-gate-cannot-see-a-harness-edit-plan.md)'s
  witness-tamper floor, [`334`](334_purged-memory-and-instruction-file-self-edits.md)'s
  self-edit guards) to a level a mere advisory verifier never had to meet. The part that
  doesn't believe the agents had better be the part nobody can quietly edit.

Keep these. Without them the note claims the dark fleet is solved. What it actually claims
is narrower and defensible: **the trend is driving fleets toward a regime where the
un-authored effect is the only trust surface left, the 2026 field has built everything for
that regime except the neutral effect-grounded trust-and-concurrency layer, and that
missing layer is the DOS shape — clear lift on four specific darkness conditions, each
falsifiable, each → 0 in the light, contingent on shipping the pre-effect gate and winning
a standardization fight not yet won.**

---

## 6. The synthesis (one paragraph)

A dark fleet is many agents working shared state at a throughput and a prose-density at
which no human reads the transcripts in time — the terminal state of
[`340`](340_what-dos-means-the-winning-move-when-narration-dies.md)'s convergence law, a
regime the prose-to-tool-call trend is driving every serious agent system toward. Walk its
observers and each one a supervised fleet relied on either goes blind (the human, drowned by
the Kingman arrival rate) or collapses onto reading the agent's own output (the LLM-judge
laundering a self-report, the agent that *The Self-Correction Illusion* proves cannot
correct its own trace, the OTel stack that records the narration in higher resolution), so
the only observer left with a pulse is the one reading an effect no agent authored. The 2026
field, audited, has built the dark fleet's *isolation* (worktrees, but they defer collisions
to merge), its *decay science* (reliability falls fastest in coding over duration), its
*identity and transport* (A2A, MCP, ERC-8004 — who and whether-authorized, never
what-landed), and its *end-of-run test gates* (CAID's genuine un-authored gate, the closest
cousin) — and the literature is now *naming* DOS's own thesis (Separation of Power's logic
monopoly, Right to History's agent-independent ledger) while stopping one rung short of a
neutral oracle that adjudicates whether the claimed effect *landed*. That one missing rung is
where DOS provides clear lift: the only observer with a pulse (`verify`/`commit-audit`), the
only coordinator that refuses the colliding write *before* it lands (`arbitrate` + the
pre-effect gate, against the merge-deferred isolation the field settled for), the only
verdict two distrusting parties or a regulator can point at (the domain-free, vendor-neutral
kernel), and the only shared language for "no" (the closed refusal vocabulary) — each tied to
a specific darkness condition, each filling a named hole the field left open, each honest
about the falsifier that sets it to zero in the light. Contingent and not yet won: but the
fleet is going dark on the very channel DOS was built to read.

---

## 7. See also

- [`340_what-dos-means-the-winning-move-when-narration-dies.md`](340_what-dos-means-the-winning-move-when-narration-dies.md)
  — the convergence law this note operates at the limit of; its §3 substrate-of-record move
  is this note's §4 lift map made concrete.
- [`336_the-prose-to-tool-call-shift-and-the-substrate.md`](336_the-prose-to-tool-call-shift-and-the-substrate.md)
  — the prose-to-tool-call trend that darkens the fleet (§1's narration-dark condition).
- [`335_tcp-for-agents-validating-the-reliability-analogy.md`](335_tcp-for-agents-validating-the-reliability-analogy.md)
  / [`342`](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md) — the substrate /
  protocol framing and the equal-caliber goal the pre-effect gate (Lift 2) must clear.
- [`333_verification-as-steering-and-the-verification-first-harness.md`](333_verification-as-steering-and-the-verification-first-harness.md)
  — the foundation-vs-bolt-on fork and the co-resident-self-grading limit that §3.3's
  "logic monopoly" paper independently reaches.
- [`170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md`](170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md)
  — the lift discipline §4 follows (regime-bound, falsifiable, → 0 at N=1) and the
  fleet/horizon/throughput axis the dark fleet maximizes.
- [`138_what-is-truth-the-throughline.md`](138_what-is-truth-the-throughline.md) — the
  byte-author invariant that makes the effect-channel the only un-blinded observer (§2) and
  bounds what the substrate can arbitrate (§5).
- [`136_the-closed-loop-as-the-organizing-principle.md`](136_the-closed-loop-as-the-organizing-principle.md)
  — "a loop whose sensor is the plant is open with extra steps," which §2's self-correction
  finding publishes as an empirical fact.
- **External (2026), fetched/confirmed where load-bearing:** Separation of Power
  (arXiv 2603.25100), Right to History (arXiv 2602.20214), The Self-Correction Illusion
  (arXiv 2606.05976), CAID (arXiv 2603.21489), CodeCRDT (arXiv 2510.18893), Beyond pass@1
  (arXiv 2603.29231); A2A / MCP / ERC-8004 specs; EU AI Act Art. 12.
