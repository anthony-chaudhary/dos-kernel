# 347 — The predictive flip: from court to pricing oracle

> **The question behind the question.** The operator said: *DOS has so far aimed
> to conform the most to the current industry env — which is good — now imagine
> total freedom; what is the most valuable space to take, what assumption is worth
> flipping, prove it.* This note answers that ask. It is deliberately NOT another
> "where DOS already fits the trend" note ([`336`](336_the-prose-to-tool-call-shift-and-the-substrate.md),
> [`340`](340_what-dos-means-the-winning-move-when-narration-dies.md),
> [`344`](344_the-dark-fleet-coordination-when-no-one-is-reading.md) are those, and
> they are good). Those notes ask *given where the field is heading, where does the
> kernel already fit?* — a conformance frame. This note takes the freedom the
> operator offered and asks the opposite: **what does the entire corpus assume that
> is worth flipping, and is the flip cheap enough to be the higher-value move?**

This is a vision note with a runnable proof. Unlike its siblings it ships an
experiment — [`examples/plan_price/`](../examples/plan_price/) — that stands on the
shipped kernel and is pinned by tests. The thesis is falsifiable and the falsifier
runs green today.

The thesis in one line: **every DOS surface reads the past or present tense — `verify`
asks *did it ship*, `commit-audit` asks *did the diff match its claim*, `arbitrate`
refuses a colliding acquire *as* it arrives. The kernel is, by its own words, a court:
it rules after (or as) the effect is attempted. The flip is to make the kernel's most
valuable output a *price on a proposed plan, computed before any agent runs* — and the
proof that this is the higher-value move is that it costs ZERO new trust: the same
agent-blind geometry the arbiter already trusts, lifted one fan-out earlier, turns the
court into a pricing oracle.**

---

## 1. The assumption every note shares — named precisely

Read [`138`](138_what-is-truth-the-throughline.md), [`340`](340_what-dos-means-the-winning-move-when-narration-dies.md),
and [`344`](344_the-dark-fleet-coordination-when-no-one-is-reading.md) back to back and
one premise is never questioned, because it is the foundation all three stand on:

> **The kernel adjudicates effects that already landed. Its sensor reads the past
> tense.**

`verify` reads git ancestry — a commit that *exists*. `commit-audit` reads a diff that
*was written*. `liveness` counts commits that *happened*. Even `arbitrate`, the one
present-tense verb, refuses a colliding acquire only *as the Kth agent reaches the
turnstile* — it reads the leases already live and rules on the acquire in front of it.
And the "pre-effect gate" that [`340 §3.3`](340_what-dos-means-the-winning-move-when-narration-dies.md)
and [`344` Lift 2](344_the-dark-fleet-coordination-when-no-one-is-reading.md) call the
winning-move-not-yet-shipped is *still reactive*: it waits for a write to be attempted,
then refuses it. Every surface is a **witness or a turnstile**. 138 says it outright:
DOS is *"a PDP with no PEP… it decides a verdict and never enforces it."* A court.

The unexamined assumption underneath all of it: **truth is something you read after,
or as, the agent acts. The unit is the effect, and the effect is a thing that
occurred.**

This was the right assumption to build on. It is what makes the verdict un-forgeable
(138's one invariant: the byte-author of the evidence is not the judged agent). The
flip does not abandon it — it *extends the tense*.

---

## 2. The flip — price the plan, don't just judge the effect

> **What if the kernel's most valuable output is not a verdict on a past effect, but
> a price on a proposed plan — computed before any agent runs?**

Restated against the strategy notes: DOS today is **descriptive** (here is what the
bytes say happened) and, at the frontier of its roadmap, **reactive** (refuse this
write as it lands). The flip is to make it **predictive and prescriptive**: given a
proposed fan-out of N agents over a declared file-tree partition — the trees a planner
hands its workers *before launch* — compute the collision graph, the true collision-free
maximum concurrency, the expected rework, and the cheapest disjoint re-partition. And
make THAT the verb the field routes through.

Three reasons this is the higher-value space, each checked against the corpus:

**2.1 — A court is the wrong shape for a dark fleet, and 344 admits it.**
[`344 §5`](344_the-dark-fleet-coordination-when-no-one-is-reading.md) concedes the
ceiling in its own words: *"A witness-only DOS is a court, not a coordinator."* The whole
danger of a dark fleet ([`344 §1`](344_the-dark-fleet-coordination-when-no-one-is-reading.md))
is that, with no human reading, the time between a bad write and its discovery is
unbounded. A verdict delivered after the write has already raced is a post-mortem. The
valuable artifact in a dark fleet is the one delivered *before the fleet is even
launched*: a plan-level price the planner reads instead of a transcript no one reads.

**2.2 — The effect-channel's neutrality argument applies at plan time too — and no
note saw it.** [`340 §2`](340_what-dos-means-the-winning-move-when-narration-dies.md)'s
deepest claim is that the un-authored effect is the only thing two distrusting parties
can both point at, *because neither authored it*. Here is the move the corpus missed: **at
plan time the file-tree partition is also un-authored in the load-bearing sense.** A
declared scope's *geometry* — does `src/core/**` overlap `src/core/api/**`? — is a fact
about prefixes, decidable by the kernel's `_tree` algebra without believing any agent's
intent. The arbiter already proves this: it is pure, it computes disjointness from glob
geometry, it reads no self-report. We have been sitting on a predictive engine and only
ever firing it reactively, one lease at a time. The flip is not new physics; it is calling
the existing physics one fan-out earlier.

**2.3 — It makes DOS the thing you *route through*, which is the stated strategy.**
[`340 §3`](340_what-dos-means-the-winning-move-when-narration-dies.md) wants DOS to be
"the substrate of record every fleet routes ground truth through," and reaches for the TCP
analogy ([`335`](335_tcp-for-agents-validating-the-reliability-analogy.md)). But you do not
route *through* a court — a court is a place you appeal *to*, after the fact. TCP is not a
court that rules on dropped packets post-mortem; it is a controller that shapes the flow
*before the bytes leave the host*. The flip makes DOS TCP-shaped instead of court-shaped:
the planner asks "what does this fan-out cost?" the way an app asks the stack "is the
window open?" — and gets a number, before sending.

---

## 3. The proof — it runs, and the falsifier runs green

A vision claim is worthless without the experiment that could kill it
([`170`](170_frontier-lift-where-it-lives-and-the-features-that-grow-it.md),
[`102 §5`](102_when-to-trust-an-agent.md)). [`examples/plan_price/`](../examples/plan_price/)
is that experiment. It imports the **real** kernel predicate — `dos._tree.lane_trees_disjoint`,
the same geometry the shipped arbiter trusts (a test pins the identity, so the proof cannot
silently drift onto a private reimplementation) — and prices three proposed fan-outs *before*
any agent runs. The measured output:

| scenario | proposed | provable collisions | max safe concurrency | reactive court cost | predictive flip cost |
|---|---|---|---|---|---|
| `clean_fanout` | 4 | 0 | 4 | (4 launch, none collide) | 0 |
| `hidden_collision` | 4 | 1 (`feature_a`↔`refactor`) | 3 | **2 agents launch before refusal** | **0 agents launch** |
| `under_declared` | 3 | 2 (empty tree ↔ all) | 2 | 1 agent launches before refusal | 0 |

Read the three rows as the three things the flip has to prove:

- **`clean_fanout` is the falsifier, and it holds.** A truly disjoint partition prices to
  exactly 0 — no collisions, full concurrency, no re-partition. The flip adds **no friction
  in the light**, which is the discipline [`344 §4`](344_the-dark-fleet-coordination-when-no-one-is-reading.md)
  demands of every lift: → 0 when there is nothing to catch. If this row had cost anything,
  the flip would be overhead masquerading as insight. It costs nothing.
- **`hidden_collision` is the headline.** A plausible fan-out a planner would wave through
  hides one overlap: `refactor` (`src/core/**`) and `feature_a` (`src/core/api/**`) both
  reach into `src/core/`. The reactive court admits agents in order and only refuses when
  the colliding acquire arrives — by which point **2 agents have already launched and are
  mutating the tree**. The predictive price refuses the *whole plan with 0 agents launched*,
  names the exact colliding pair, and still hands back the maximal safe set (3 of 4) to run
  instead of nothing. Same un-forgeable geometry, one fan-out earlier.
- **`under_declared` shows the flip says something a post-hoc `verify` never can.** An
  agent ships an empty tree — an *unknown* blast radius. The kernel's existing conservatism
  (`lane_trees_disjoint` treats an empty tree as NOT disjoint, [`_tree.py:89-96`](../src/dos/_tree.py))
  prices it as colliding-with-everything, and the re-partition advice is "name your scope
  before you launch." There is no past-tense effect to read here — the agent hasn't run —
  so a court has *nothing to say*. The oracle has the most useful thing to say of all: fix
  the plan.

The max-concurrency column is itself a flip-only quantity: it is the maximum-independent-set
on the collision graph — "how many of these N can actually run at once" — which the reactive
arbiter *never computes* because it only ever sees one acquire against the leases already
live. The plan-level view is strictly more information than the sum of per-acquire views.

Run it:

```bash
python examples/plan_price/plan_price.py            # the rendered table above
python examples/plan_price/plan_price.py --json     # machine-readable prices
python -m pytest examples/plan_price/test_plan_price.py -q   # 6 falsifiers, green
```

---

## 4. Why the flip is *cheaper* than the conformance roadmap — the real argument

The strongest case for the flip is not that it is bolder. It is that it is **cheaper to
ship and pays off sooner** than the moves 340/344 hinge on.

The conformance strategy's two load-bearing moves — "own the verbs so the field shares one
effect-language" ([`340 §3.1`](340_what-dos-means-the-winning-move-when-narration-dies.md))
and "be the neutral substrate two suppliers route through" (§3.2) — are both **standardization
fights**. 340 §5 and 344 §5 are honest that these *"pay off at N≥2 fleets, not at N=1,"* are
*"contingent on adoption,"* and could be *"lost to a worse-but-bigger standard."* They are
multi-quarter, multi-party ecosystem bets whose value is zero until a second fleet speaks the
vocabulary.

The flip pays off at **N=1, today, inside a single fan-out**, because it is a reframing over
*pure functions that already ship*. A planner running one `/dos-goal-fleet` or one
`parallel()` over a proposed partition gets a price *this session* — no second party, no
adoption, no standard. The experiment is ~250 lines standing on `lane_trees_disjoint`. That
is the asymmetry: the conformance roadmap's value is real but deferred behind a standardization
war; the flip's value is real and immediate behind a CLI verb. **Under "total freedom," you take
the move that compounds from N=1, not the one that is zero until N=2.** And the two are not
rivals — the flip is the *thing worth standardizing*: a price is a far more natural shared verb
than a court ruling, because every planner wants the number and only an auditor wants the verdict.

---

## 5. The honest limits — what this note is NOT

A vision note with no failure conditions is propaganda (the bar [`102 §5`](102_when-to-trust-an-agent.md)
set and [`340 §5`](340_what-dos-means-the-winning-move-when-narration-dies.md)/[`344 §5`](344_the-dark-fleet-coordination-when-no-one-is-reading.md)
keep). Six things this note does not claim:

- **The price is geometric, not semantic — same ceiling the arbiter has.** It prices
  file-tree *overlap*, which over-approximates the real question (will these two agents'
  *intents* conflict?). Two provably-disjoint trees can still create a semantic collision
  (CodeCRDT's 5–10%, [`344 §3.2`](344_the-dark-fleet-coordination-when-no-one-is-reading.md));
  the predictive price inherits that boundary exactly — it is the arbiter's geometry one
  tense earlier, no wiser, just earlier. It moves *latency*, not the ceiling on what geometry
  can know.
- **The declared partition is agent-authored — the docs/117 scope-source hole, forward.**
  The trees a planner hands its workers are a *plan*, and a plan can under-declare (the
  `under_declared` scenario). The flip prices the declared geometry honestly (an empty tree
  → max risk), but it cannot verify the declaration matches what the agent will actually
  touch. That gap is the unforgeable floor's job *after* launch (the acquire-time arbiter over
  real leases stays the floor); the price is a forward *estimate* over a forgeable input, and
  is labeled as such. It reduces collisions; it is not a BLOCK guarantee.
- **The rework number is illustrative, not calibrated.** `_expected_rework` is one unit per
  colliding pair — transparent and monotone in contention, deliberately not a dollar model.
  The experiment proves the price is *computable from geometry before launch* and *monotone in
  contention*; calibrating it against real rework cost is unbuilt (issue below).
- **The max-independent-set core is brute-force.** Exact for the small N a real fan-out has
  (tens), but exponential past ~30 agents; a kernel verb needs a greedy/branch-and-bound bound.
  Named in the code and filed as an issue.
- **This is an experiment, not a shipped kernel verb.** It lives in `examples/`, imports the
  kernel, and is pinned by tests — it is a *proof of the flip*, not `dos price-plan`. Promoting
  it to a real picker-family verb (`pickable`/`enumerate` already live near this seam) is the
  follow-on, filed below. A vision note that claimed the verb shipped would be the exact
  self-report this kernel exists to refuse.
- **Predictive does not mean correct.** The price tells you a plan will *collide*, never that
  a non-colliding plan is *right*. Correctness is still the suite's job
  ([`138`](138_what-is-truth-the-throughline.md) "where truth is still forgeable"). The flip
  removes a *coordination* failure earlier; it removes no *capability* failure.

Keep these. Without them the note claims DOS can see the future. What it actually claims is
narrower and proven: **the kernel's own pure geometry, which it only ever fires reactively, can
answer a strictly forward plan-level question at zero new trust cost — turning the court into a
pricing oracle one fan-out before the court could rule, with a falsifier that runs green.**

---

## 6. The synthesis (one paragraph)

Every DOS surface reads the past or present tense — `verify` the shipped commit, `commit-audit`
the written diff, `arbitrate` the acquire in front of it — so the kernel is, in its own words, a
court: it rules after, or as, the effect is attempted, which is the right shape for an un-forgeable
verdict and the wrong shape for a dark fleet where the gap between a bad write and its discovery is
unbounded ([`344 §5`](344_the-dark-fleet-coordination-when-no-one-is-reading.md) concedes the
court-not-coordinator ceiling). The flip the operator's "total freedom" frame surfaces is to extend
the tense: make the kernel's most valuable output a *price on a proposed plan computed before any
agent runs* — the collision graph, the true collision-free maximum concurrency, the cheapest disjoint
re-partition — and the reason this is the higher-value move rather than a bolder one is that it costs
**zero new trust**, standing entirely on the same agent-blind prefix geometry (`lane_trees_disjoint`)
the reactive arbiter already trusts, fired one fan-out earlier. The proof runs: [`examples/plan_price/`](../examples/plan_price/)
prices three fan-outs off the real kernel predicate, the reactive court launches 2 agents before it
can refuse a hidden collision while the predictive price refuses the whole plan with 0 launched, and
the falsifier (`clean_fanout`) prices a truly disjoint partition to exactly 0 so the flip adds no
friction in the light. It is cheaper than the conformance roadmap's standardization fights because it
pays off at N=1 today behind a CLI verb instead of being zero until a second fleet adopts a shared
vocabulary — and it is the thing more worth standardizing, because every planner wants the number
where only an auditor wants the verdict. Honest and bounded: the price is geometric not semantic, over
an agent-declared (forgeable) partition, illustratively not calibratedly, and is an experiment not a
shipped verb — but the one claim it makes is proven, which is that DOS was always sitting on a
predictive oracle and only ever using it as a court.

---

## 7. See also

- [`340_what-dos-means-the-winning-move-when-narration-dies.md`](340_what-dos-means-the-winning-move-when-narration-dies.md)
  — the conformance strategy this note flips; its §3.3 reactive "pre-effect gate" is the move this
  note argues should be predictive instead.
- [`344_the-dark-fleet-coordination-when-no-one-is-reading.md`](344_the-dark-fleet-coordination-when-no-one-is-reading.md)
  — §5's "a witness-only DOS is a court, not a coordinator" is the admission this note builds on; its
  Lift 2 (arbitrate before the write) is the reactive half whose predictive completion this is.
- [`89_the-lane-is-a-region-lock.md`](89_the-lane-is-a-region-lock.md) — the region-lock geometry the
  price runs forward; [`90`](90_the-overlap-eval-and-the-cost-of-the-floor.md) — the overlap-eval whose
  false-admit measurement the calibrated price would extend.
- [`138_what-is-truth-the-throughline.md`](138_what-is-truth-the-throughline.md) — "a PDP with no PEP"
  is the court framing this note names; the byte-author invariant is why the plan's *geometry* (not its
  declared intent) is the un-forgeable part the price stands on.
- [`examples/plan_price/`](../examples/plan_price/) — the runnable proof: `plan_price.py` (the oracle over
  the real kernel predicate) and `test_plan_price.py` (the falsifiers, green).
