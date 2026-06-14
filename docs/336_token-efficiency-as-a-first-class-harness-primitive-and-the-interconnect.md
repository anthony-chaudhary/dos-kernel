# 336 — Token efficiency as a first-class harness primitive, and the interconnect angle

> **The claim.** *A coding harness that treats token spend as a billing detail
> is one architectural generation behind one that treats it as a verdict.* The
> first harness reads the meter at the end and forwards a number to the invoice.
> The second makes "is this run spending well?" a typed signal it can *steer on*,
> next to "is this run telling the truth?" — and routes the two through the same
> byte-clean discipline. This note argues that the second harness is the one the
> market is converging on, that DOS already ships the load-bearing half of it
> (`spend` + `efficiency`, docs/263/300), and that the part nobody has wired yet —
> **the hardware interconnect** — is exactly what turns "spend well" from an
> accountant's nicety into a physical constraint the loop must respect. It carries
> no litmus and ships no mechanism; it is a frame over leaves that already exist.

This is a theory note in the family of
[`333`](333_verification-as-steering-and-the-verification-first-harness.md)
(verification is the steering wheel — the control-loop reframe),
[`335`](335_tcp-for-agents-validating-the-reliability-analogy.md) (the networking
analogy and its honest limit), and
[`195`](195_dos-and-the-kv-cache-the-forward-direction.md) (the deepest the repo
has gone toward the hardware — *adjudicate the cache, never manage it*). Those
three settled *what the kernel verifies* (the effect), *why the verdict is
trustworthy* (byte-authorship), and *where the cache line is* (the PDP/PEP bright
line at KV granularity). This one answers the question the operator put on the
table: **why should token efficiency be a first-class citizen of the harness at
all — and what does the hardware interconnection have to do with it?**

The verdict in one line, so the rest can earn it: **token efficiency belongs at
the harness's center for the same reason verification does — it is a measurement
the agent cannot author, it scales with the horizon, and on the long horizon the
binding constraint is no longer the model's token *budget* but the machine's
token *bandwidth* — so the efficiency verdict and the interconnect are two ends
of one pipe, and DOS sits at exactly the seam where the spend becomes legible
without the kernel ever touching the silicon.**

---

## 1. Why efficiency is a verdict, not a meter reading — and why that makes it first-class

Start from the distinction the kernel already draws and that most harnesses blur.
A *meter* is a scalar you read off at the end and forward to the invoice: "this
run cost 1.4M tokens." A *verdict* is a typed judgment you can act on between
steps: "this run is **WASTEFUL** — it spent meaningful tokens and ~0 work
landed." The kernel ships the second
([`efficiency.py`](../src/dos/efficiency.py)): `classify(EfficiencyEvidence,
policy) → {EFFICIENT, COSTLY, WASTEFUL}`, the work/token *ratio*, sitting as the
lateral sibling of `liveness` (did state move at all?) and `productivity` (is the
work-per-step *rate* fading?). The family's own one-liner:

```
liveness.classify      -> did state move AT ALL?          (a lifetime count)
productivity.classify  -> is the work-per-step RATE fading?  (a trend over steps)
efficiency.classify    -> did the tokens buy WORK?         (a ratio: work / tokens)
```

Three properties make this *first-class* — a citizen of the loop, not a footnote
on the bill — and each one is the token-economics transcription of a property
[`333`](333_verification-as-steering-and-the-verification-first-harness.md) made
load-bearing for verification:

**1.1 It is byte-clean — the agent cannot author it.** This is the whole reason it
deserves to sit next to the truth verdict and not in a billing sidebar. Both
inputs to `efficiency` are counts the *environment* authors, never the judged
agent's narration: `tokens` is what the provider's usage record billed (the same
env-authored counter `liveness.tokens_spent_since` reads), and `work` is
ground-truth work the environment measured — commits landed, bytes diffed, tests
passed, the same unit `verify` confirms off git. So **WASTEFUL is "the
environment recorded N tokens spent and ~0 work landed," never "the agent says it
was inefficient."** And it is non-forgeable *in the direction that matters*: an
agent cannot move the verdict toward EFFICIENT by *narrating* productivity,
because the numerator is work the environment witnessed, not a claim the agent
emitted. This is the same invariant
[`138`](138_what-is-truth-the-throughline.md) draws for truth (byte-author ≠
judged party) and [`234`](234_the-non-distillable-reward-channel-lab-facing-proof.md)
draws for the reward label — re-aimed at *price*. The efficiency verdict is a
member of the trust substrate, not an adjacent accounting concern, *because it
obeys the substrate's one law.*

**1.2 It scales with the horizon — the same `pⁿ` regime verification lives in.**
On a single function call, efficiency is a curiosity: a one-shot answer either
costs little or it doesn't, and you see the bill in one glance. The horizon
changes the regime exactly as it does for correctness
([`333 §2`](333_verification-as-steering-and-the-verification-first-harness.md)).
A long-horizon agent does not waste tokens in a flat line; it wastes them
*compounding* — it re-reads the same files because it forgot it read them, it
re-derives a fact it already established, it marker-spins waiting on a barrier, it
loops on a thrash it cannot see because the corrupted state is upstream of its own
attention ([`333 §1`](333_verification-as-steering-and-the-verification-first-harness.md)'s
plant-reads-its-own-output drift). Each of those is a *token cost that grows with
the horizon*, and an end-of-run meter reading cannot separate "spent a lot because
the task was big" from "spent a lot because the agent was thrashing." Only a
ratio computed *between steps* — work landed per token spent, trended — can. The
value of the efficiency verdict scales with the horizon for the same reason
verification's does: **the failure it catches is the one that scales with the
horizon.** A harness that puts it at the end inherits the same defect docs/333
named for the truth check — too late, and read at peak temptation (the moment the
agent declares "done" is also the moment it has spent its budget and most wants
the spend to look justified).

**1.3 It is a steering vector, not a grade.** docs/333 §3's bar for a *steering*
signal — it must say which way to turn, distinguish converging from thrashing, and
grade its own confidence — the spend family already meets.
[`spend.py`](../src/dos/spend.py) does not return one scalar; it returns the
five-way split the field standardized in early 2026 (the OpenTelemetry GenAI usage
attributes): `input`, `output`, `cache_read`, `cache_creation`, `reasoning`. So a
WASTEFUL verdict can say *what kind* of spend bought nothing — and that "what
kind" is the heading correction:

- a high `reasoning_share` says **overthinking** — the run is deliberating, not
  acting; the correction is to cut the thinking budget or force a tool call.
- a low `cache_hit_ratio` says **cold-paying a prefix it should be reusing** —
  the correction is upstream, in how the loop reuses context (and §3 is where this
  stops being a software knob).
- a high `output_share` says the run is **generating, not reading** — the
  expensive, slow decode side dominating, the signature of a run writing prose
  instead of landing diffs.

A grade (`pass/fail` on the bill) throws all three away. A vector keeps them, and
**a harness that types its spend as a vector can steer on the spend the way it
steers on the truth** — which is the precise sense in which efficiency becomes a
first-class loop primitive rather than a number on the invoice. The leaf is
explicit that it reports *a price, never a quality*: like `productivity` says the
rate fell (never that the work was *wrong*), `efficiency` says the cost-per-unit
is high (never that the work was *bad*). A run can be perfectly correct and
WASTEFUL, or EFFICIENT and wrong. Quality is an advisory judge's call; the price
is the deterministic verb. Keeping that line is what lets efficiency live in the
trust substrate without pretending to be a correctness oracle.

---

## 2. The market already moved — efficiency went from proxy to verified outcome

This is not a speculative call about where harnesses *should* go; it is a reading
of where they *went*, the expensive way, in the first half of 2026. The whole
"token-efficiency turn" of 2026 is one move: **proxy → verified outcome**, the
same move DOS was built on.

The proxy was *token-maxxing* — the belief that tokens-burned is a productivity
KPI (Meta's "Claudeonomics" leaderboard crowning "Token Legends," staff running
agents on meaningless tasks to pad stats, an org burning its annual token budget
in four months). It died the same month the subscription crackdown landed,
because they were the *same event seen from two ends of the pipe*: token-maxxing
was the demand side of a token subsidy, the crackdown the supply side refusing to
keep paying for it. What replaced the proxy is telling: the metric the field
settled on is **loop count** (plan→edit→verify iterations to green) and **verified
completions** — *not* token rate. Smarter models win by needing ~60% *fewer*
loops, not by burning more tokens.

Read that against §1 and the convergence is exact. "Tokens burned → productive" is
a forgeable proxy — lossy at every hop, gameable by a run that spends without
landing work. "Loop count to a verified-green outcome" is `work / tokens` with the
numerator pinned to a witness — it is the `efficiency` verdict, arrived at
market-wide. The industry priced out token-maxxing for the same reason the kernel
refuses `grep-subject`-only `--allow-empty` stamps: **a crude forgeable proxy
standing in for the real thing is the category error**, whether the proxy is "has
`hermes.md` in git → uses Hermes" or "spent 1.4M tokens → did 1.4M tokens of
work." The market reaching "verified outcomes over token rate" is the market
reaching the part that doesn't believe the agent — applied to *spend* instead of
*completion*.

The consequence for a harness: token efficiency is no longer an optional cost
dashboard you bolt on for finance. It is the axis the field now *measures the
harness by*. A harness whose native verdict is "I think it's done" and whose
native cost story is "here's the bill" is behind on both axes at once — and they
are the same axis seen twice, because the cheap false "done" and the wasteful spin
are both the loop failing to read an un-authored signal between steps.

---

## 3. The interconnect angle — where the budget stops being the constraint

Everything above treats tokens as a *budget* — a quantity you are billed for and
want to spend well. That framing is correct and incomplete, and the incompleteness
is the operator's hardware question. On the long horizon at fleet scale, **the
binding constraint stops being the price of a token and becomes the bandwidth to
move the bytes a token's context rides on.** This is where "spend well" stops
being an accountant's preference and becomes physics.

### 3.1 The physical fact a harness can no longer abstract away

A token is not free to *carry*, independent of its price to *buy*. Every token in
a model's context occupies KV-cache — key/value tensors in High-Bandwidth Memory
(HBM) — and every decode step reads the *entire* KV cache for the attention
computation. Autoregressive decode is **memory-bandwidth-bound, not
compute-bound**: the GPU's arithmetic units sit idle waiting for the KV bytes to
arrive from HBM. So context length is not just a billing multiplier; it is a
*bandwidth tax paid per generated token*. A 200K-token context is not merely 200K
tokens of input cost — it is 200K tokens of KV that must be streamed out of HBM on
*every single decode step* of the response. The longer the agent's context, the
slower and more bandwidth-starved every subsequent token it generates.

Now scale to a fleet — the regime DOS exists for ([`195 §4`](195_dos-and-the-kv-cache-the-forward-direction.md),
the fanout regime). Run N agents on one repo and three interconnect facts bind:

- **Per-agent cold prefix.** Each agent that does not reuse a warm prefix
  *recomputes* it — a prefill pass over the shared context, paid in compute and
  HBM writes, per agent. At fanout this is the dominant cost, and it is exactly
  the cost a *cache-aware route* avoids (`195 §3`). The shared prefix is a
  bandwidth commons; an agent that cold-pays it is the token-economics analog of a
  worker that re-derives a fact the fleet already established.
- **KV transfer across the interconnect.** Disaggregated serving (prefill on one
  node, decode on another — Mooncake, Dynamo, LMCache) moves KV *between* GPUs
  over NVLink / InfiniBand. The KV cache for a long context is gigabytes; moving it
  is an interconnect event with real latency, and routing a request to the node
  whose cache is *already warm* is the difference between a transfer and a hit
  ([`195 §3`](195_dos-and-the-kv-cache-the-forward-direction.md)'s ~0.1× read vs
  ~12.5× miss asymmetry). The interconnect topology *is* the cost surface.
- **Memory pressure and eviction.** HBM is finite; a fleet's combined KV does not
  fit, so the engine evicts. Eviction is by LRU/TTL — *content-blind* — so an
  engine evicts a verified, reusable prefix exactly as eagerly as a poisoned,
  confidently-false one (vLLM #36311; [`195 §4.3`](195_dos-and-the-kv-cache-the-forward-direction.md)).
  Which prefix is *worth* keeping warm is a question the hardware cannot answer
  and the kernel can.

The synthesis of the three: **at fleet scale on a long horizon, token efficiency
is interconnect efficiency.** "Spend well" decomposes into "keep the right context
warm, on the right node, and don't recompute or re-transfer what's already
there" — and every one of those is a statement about HBM bandwidth and the GPU
fabric, not about the per-token price. The budget framing of §1 is the surface; the
bandwidth framing is the floor underneath it, and on the long horizon the floor is
what binds.

### 3.2 What this does to the harness's job description

If the binding constraint is bandwidth, the harness's efficiency job is no longer
"minimize the bill." It is **"minimize the bytes that have to move"** — which is a
context-and-routing discipline, and it has three parts that map cleanly onto the
spend vector of §1.3:

1. **Reuse the warm prefix instead of cold-paying it** — the `cache_hit_ratio`
   signal, now read as "am I paying the interconnect tax I could have avoided." A
   harness that fans out N children sharing a parent's context should route each
   child to the warm prefix, not let each recompute it. This is the single largest
   token-efficiency lever at fanout, and it is *entirely* an interconnect decision.
2. **Don't carry context the work doesn't need** — every token in the window is a
   per-decode-step bandwidth cost, so trimming a stale prefix is not just budget
   hygiene, it is *making every future token faster*. This is the forward sibling
   of [`178`](178_prefix-deletion-and-the-kv-cache.md)'s tail-truncation: the
   kernel's only sound cache move today is to cut the tail, and that cut *is* a
   bandwidth optimization, not only a budget one.
3. **Keep the *valuable* context warm under eviction pressure** — which prefix
   earns its HBM residency is a content-and-trust question (is this prefix
   verified-reusable, or a poisoned `NOT_SHIPPED` "done"?), and the engine, by
   construction, cannot see it.

A harness that treats token efficiency as first-class, *taken to the fleet-scale
long horizon*, is therefore forced into the interconnect. It cannot stay in the
billing layer, because the cost it is trying to minimize lives in HBM and on
NVLink. This is the real content of the operator's hardware question: **efficiency
as a first-class harness primitive is not complete until it reaches the
interconnect, because that is where the long-horizon fleet's token cost actually
is.**

---

## 4. Where DOS sits — adjudicate the spend, never manage the memory

Here the two halves of the note meet, and the bright line docs/195 drew is the
thing that keeps the synthesis honest. The temptation, having argued that token
efficiency *is* interconnect efficiency, is to conclude the harness should *manage*
the cache — pin prefixes, evict on content, schedule KV transfers. That is the
PEP, and for DOS it is forbidden for reasons that are structural and permanent
([`195 §1`](195_dos-and-the-kv-cache-the-forward-direction.md)): KV state lives in
the engine's address space, only the engine holds the attention graph that makes a
mutation sound, and reaching into block tables is a strict layer-*down* move, the
inverse of the kernel's defining arrow. **DOS must never manage the cache.**

What DOS *can* do is the thing this whole note has been circling: **make the spend
legible — adjudicate it — and let the engine act.** The bright line restated at the
token-economics layer:

> **DOS adjudicates token efficiency; DOS does not manage token memory.** The
> efficiency verdict, the spend breakdown, the "this prefix is verified-reusable /
> this one is poisoned" stamp — all are *verdicts the engine may honor* (PDP).
> Pinning, evicting, transferring, recomputing KV — all are the engine's
> (PEP). DOS reads the meter the provider authored and the work the environment
> witnessed, and emits a typed judgment; it never moves a byte of HBM.

This is the same PDP/PEP line as everywhere else in the kernel, and it resolves
the interconnect angle without violating the architecture. The harness's
*efficiency-first* job — §3.2's three levers — splits cleanly:

- The **measurement** half is DOS's, and byte-clean. Wiring the engine's
  `cache_read_input_tokens` into the spend ledger as a `THIRD_PARTY` witness
  (`195`'s direction #1, the one the ranking is "not close" on) replaces the
  `char//4` *assertion* with an *attested* cache cost. Until that lands, every
  efficiency claim the harness makes about cache reuse is `AGENT_AUTHORED` — "`git
  commit --allow-empty` in a different font." The interconnect angle makes this
  precondition urgent, not optional: you cannot steer on a bandwidth cost you only
  assert.
- The **trust-overlay** half is DOS's and *unowned by anyone else* — the
  intersection the engine is structurally blind to ([`195 §4.3`](195_dos-and-the-kv-cache-the-forward-direction.md)):
  *which* shared prefix is verified-reusable vs. poisoned, *which* requester may
  share a prefix (lineage), *which* anchor earns eviction priority because it ends
  at a non-forgeable verdict. The engine routes and evicts on the prefix *hash*; it
  caches a confident-but-false "done" prefix exactly as eagerly as a verified one.
  DOS knows `oracle.is_shipped == NOT_SHIPPED`. That is the residual — the
  token-efficiency decision that is *DOS-shaped and no one else's*.
- The **management** half — the actual byte movement across the interconnect — is
  the engine's, forever. DOS produces the verdict that *informs* the engine's pin /
  evict / route; it never performs it.

So the answer to "how does token efficiency become a first-class citizen of the
harness, including the hardware angle?" is not "the harness manages the cache." It
is: **the harness makes token efficiency a verdict it steers on — byte-clean, typed
as a vector, computed between steps — and at fleet scale that verdict reaches into
the interconnect as an *adjudication* of which bytes are worth moving and keeping
warm, handed to an engine that does the moving.** The kernel is the part that
prices the spend without believing the agent's account of it, and the part that
knows which warm prefix is trustworthy — and it stops, deliberately, exactly at the
silicon.

---

## 5. The honest limits

A note that only stated the thesis would be a self-report
([`102 §5`](102_when-to-trust-an-agent.md)). Four limits locate the claim.

- **The efficiency verdict is a price, never a quality (§1.3 restated as a
  boundary).** It can tell EFFICIENT-and-wrong from WASTEFUL-and-correct only by
  refusing to judge correctness at all. A harness that reads WASTEFUL as "bad work"
  has misread the verb. Efficiency steers the run *toward spending well*; whether
  the destination was worth reaching is a judge's or a human's call — the same
  conformance-not-correctness ceiling [`333 §5`](333_verification-as-steering-and-the-verification-first-harness.md)
  draws for the truth verdict.

- **The cache cost is asserted until the witness lands.** Everything in §3–§4
  about interconnect efficiency rests on a cost DOS today *computes as `char//4`*,
  not measures. The argument that "spend well = move fewer bytes" is sound in
  shape; the *numbers* are not byte-clean until `195`'s direction #1 ships an
  engine-authored `cache_read` witness. This note inherits that gap and does not
  paper over it: the interconnect angle is the *reason* to close it, not a claim
  that it is closed.

- **DOS adjudicates; the host enacts (the PDP/PEP floor, §4).** The efficiency
  verdict and the verified-prefix stamp are advisory. A harness that does not wire
  them to a real engine action — a cut, a route, a pin — gets the legibility and
  none of the bandwidth saving. "Token efficiency is first-class" is precise: DOS
  makes it *legible and steerable*; the saving is realized only when the host acts
  on the verdict, and that wiring is the host's responsibility and risk.

- **The interconnect value is a fleet-and-horizon value — it is ~0 at N=1.** A
  single agent adjudicating its own cached prefix is the plant grading itself
  ([`195 §4.4`](195_dos-and-the-kv-cache-the-forward-direction.md);
  [`136`](136_the-closed-loop-as-the-organizing-principle.md)'s closed-loop floor —
  a loop closed back onto its own author closes no loop). The trust×cache
  intersection is a real
  coordination win only at fanout, and most of the raw cache economics is the
  engine's job regardless. The defensible claim is the thin-but-genuine one:
  *the engine routes and evicts on the hash; DOS stamps which shared prefix is
  verified-reusable and safe across agents, and which is poisoned* — and that stamp
  is worth the most exactly where the interconnect tax is highest, which is the
  long-horizon fleet.

These limits do not weaken the frame; they place it. Token efficiency is
first-class for the cell where it bites: an un-authored spend signal × a
long-horizon fleet whose real cost is bandwidth. On that cell — and conceding the
rest — "spend well" is not a line on the invoice. It is a verdict the harness
steers on, and at scale it is the same verdict as "move the right bytes."

---

## 6. The synthesis (one paragraph)

Token efficiency belongs at the center of a coding harness for the same three
reasons verification does: it is a measurement the agent cannot author (work per
token, with the numerator pinned to a witness the environment wrote), it scales
with the horizon (waste compounds — re-reads, re-derivations, marker-spin — so an
end-of-run meter is both too late and read at peak temptation), and it is a
steering vector rather than a grade (the five-way spend split says *what kind* of
spend bought nothing, which is the heading correction). The market reached this
the expensive way in 2026 — token-maxxing died and "loop count to a verified
outcome" replaced "token rate," which is the `work/tokens` verdict arriving
industry-wide. Taken to the fleet-scale long horizon, the budget framing gives way
to a physical one: every token in context is a per-decode-step HBM-bandwidth tax,
each cold-paid prefix is a recompute, each KV transfer is an interconnect event, and
each eviction is content-blind — so *token efficiency becomes interconnect
efficiency*, and "spend well" becomes "move and keep warm only the bytes worth it."
DOS sits at the one seam this opens without crossing the silicon: it *adjudicates*
the spend (byte-clean once the engine's `cache_read` witness lands) and stamps which
warm prefix is verified-reusable versus poisoned — a trust×cache decision the engine
is structurally blind to — and hands that verdict to an engine that does the
moving. The kernel never manages the cache; it makes the spend legible and the warm
prefix trustworthy, and stops exactly at the bandwidth.

---

## 7. See also

- [`333_verification-as-steering-and-the-verification-first-harness.md`](333_verification-as-steering-and-the-verification-first-harness.md)
  — the control-loop reframe and the foundation-vs-bolt-on fork; §1 here is its
  token-economics transcription (efficiency is a steering vector, not a grade).
- [`335_tcp-for-agents-validating-the-reliability-analogy.md`](335_tcp-for-agents-validating-the-reliability-analogy.md)
  — the PDP-with-no-PEP finding; §4's "adjudicate, never manage" is the same
  bright line at the token/KV layer.
- [`195_dos-and-the-kv-cache-the-forward-direction.md`](195_dos-and-the-kv-cache-the-forward-direction.md)
  — the deepest hardware analysis in the repo: adjudicate the cache, never manage
  it; the `cache_read` witness (direction #1) §4 depends on; the trust×cache
  intersection §3.1/§4 builds the interconnect case on.
- [`263_token-effectiveness-verdict-plan.md`](263_token-effectiveness-verdict-plan.md)
  / [`300_token-spend-breakdown-and-efficiency-trend-plan.md`](300_token-spend-breakdown-and-efficiency-trend-plan.md)
  — the build plans behind [`efficiency.py`](../src/dos/efficiency.py) and
  [`spend.py`](../src/dos/spend.py), the live mechanism §1 reads.
- [`138_what-is-truth-the-throughline.md`](138_what-is-truth-the-throughline.md)
  — the byte-authorship invariant §1.1 reuses: the efficiency verdict is byte-clean
  for the same reason the truth verdict is.
- [`178`](178_prefix-deletion-and-the-kv-cache.md) — the backward cache sibling:
  tail-truncation is the only sound cut, and §3.2 reads that cut as a bandwidth
  optimization, not only a budget one.

> **External anchor.** The autoregressive-decode memory-bandwidth bound (decode
> reads the full KV cache from HBM every step, so long context is a per-token
> bandwidth tax, not only a billing multiplier) and the disaggregated-serving cache
> economics (Mooncake's KVCache-centric disaggregation, NVIDIA Dynamo's KV-aware
> routing, the ~0.1× cache-read vs ~12.5× recompute asymmetry) are the hardware
> facts §3 leans on — the physical reason "spend well" becomes "move the right
> bytes" at fleet scale.
