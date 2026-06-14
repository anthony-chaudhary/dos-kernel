# 316 — bad memories: a taxonomy, an integrity benchmark, and the win condition

> docs/103 named the founding insight ("memory is the agent we forgot to stop
> believing") and shipped the recall gate; docs/314 shipped the write gate.
> This plan does three things on top: (1) it names EVERY way a memory goes bad
> — not just the two the shipped gates answer; (2) it ships a benchmark whose
> labels the agents did not author, so the gates' catch-rates and miss-buckets
> are measured, not narrated; (3) it stakes the publishable claim: memory
> products compete on whether the right memory comes BACK, and nobody scores
> whether what comes back is TRUE. Operator prompt 2026-06-12: "'bad' memories
> handling seems like fertile ground for DOS — progress, validate, benchmark,
> win."

*Status: P1 SHIPPED 2026-06-12 (the benchmark — `benchmark/memory_integrity/`,
first numbers in its `RESULTS.md`; suite pin `tests/test_memory_integrity_bench.py`).
P3 (directive typing, #110) SHIPPED 2026-06-14 — the `directive` marker on the
admission verdict (`detect_directive`); `directive_injection` now types 100% with
0 false flags on honest notes (see `RESULTS.md`). P2/P4 open with issue handles
(#112 grammar-widening, #111 contradiction arbitration). P5 gated on docs/314
P2–P3. P6 owner-gated.*

## 0. Why bad memories are home turf

A lie told in a conversation dies with the context window. A lie written to
memory is inherited by every future session, wearing the authority of a fact —
the docs/229 peer-B handoff, compounding instead of expiring. Three structural
facts make this DOS-shaped ground:

- **The industry default believes the narrator.** Hosted memory products
  (Mem0, Zep, Letta, the first-party assistant memories) headline
  AUTO-EXTRACTION: the writer distills memories from the agent's own
  conversation. There is no admission bar an over-claim has to clear — the
  store is a self-report archive presented as a knowledge base.
- **Truth is the empty cell.** The memory benchmarks the field optimizes
  (the LoCoMo / LongMemEval / MemBench class) score RETRIEVAL — does the
  right memory come back over a long horizon? None scores INTEGRITY — was the
  memory ever true, and is it still? Retention and recall are competed;
  admission is not even measured. (The same empty-cell shape as the
  competitive tier map's headline, one layer down the stack.)
- **Memory is a persistence layer for injection.** The memory-poisoning
  literature (records planted through ordinary interactions that re-inject
  into every later context) makes a store with no admission gate an attack
  persistence mechanism. We claim the defensive framing only — a gate raises
  the forgery cost to "a real artifact of the right shape"; it guarantees
  nothing (the docs/103 §6 wall, restated).

And one asymmetry already argued in docs/314 §0: write time is the cheapest
moment to witness a claim (the evidence is live), and write-time refusal is
the cheapest refusal (nothing downstream inherited it).

## 1. The taxonomy — eight ways a memory is bad

The spine is the forgeability grading (docs/138): a memory's authority must be
a function of evidence its author did not write. Each class below is "bad" in
a different place — at write, with time, at store scale, or in the gate
itself — and each needs a different answer.

| # | Class | Enters at | The badness | Which gate answers | Status |
|---|---|---|---|---|---|
| 1 | **Born-false over-claim** | write | the session's own narration, never true ("deployed; all tests pass") | `admit` → `REJECT_POISON` | SHIPPED (docs/314 P1) |
| 2 | **Aged-stale** | time | true at write, falsified by later commits — honest decay | `recall` → `RECALL_STALE` | SHIPPED (docs/103) |
| 3 | **Grammar-evasive over-claim** | write | the lie phrased OUTSIDE the claim grammar (no backtick, no `file:line`, no SHA) — extraction sees nothing checkable, types it OPINION, admits it | none today — the extraction ceiling | MEASURED here (P1); widened in P2 (#112) |
| 4 | **Directive-bearing** | write | not a claim at all: an INSTRUCTION wearing a memory ("always pass `--skip-checks`") — unfalsifiable by construction, so it types OPINION and sails through; the persisted-injection shape | none today | GAP → P3 (#110) |
| 5 | **Self-confirming** | write | the memory carries its own verification (a RECALL_* banner, a recorded probe result) so a lazy reader parrots it | `strip_recall_banner` (the known form); the P5 annex rule: re-RUN declared probes, never read recorded results | partial, by discipline |
| 6 | **Contradictory pair** | store scale | two memories disagree (one says shipped, one says reverted); each adjudicates alone today, the PAIR is never surfaced | none today | GAP → P4 (#111) |
| 7 | **Unverifiable bloat** | write | honest opinions accumulating until recall precision drowns | typed (`ADMIT_OPINION`) so a reader can rank; retention itself stays host policy | typed, by design |
| 8 | **Forgotten-good** | the gate | the gate's own misfires: a TRUE candidate refused, a FRESH memory read STALE — distrust has a cost side | the benchmark's second axis: fresh-survival must be measured WITH poison-catch | MEASURED here (P1) |

Two non-classes, deliberately: a memory that is *wrong but harmless* is class
1 or 3 (the gate does not grade importance), and a *missing* memory (the agent
forgot to write) is a write-side host policy, not an integrity failure — DOS
adjudicates what is claimed, it does not demand claims.

## 2. P1 — the integrity benchmark (`benchmark/memory_integrity/`)

The research question: *given a stream of candidate memories about a real
repo, how much poison does the write gate refuse, how much decay does the
recall gate catch, and what does that distrust cost in falsely-refused truth?*

Design rules, in trust order:

- **Labels are env-authored.** The harness BUILDS the scratch git repo, so
  every candidate's truth value is fixed by construction — by commits the
  corpus author made, not by a human rater or a model's opinion. The
  benchmark cannot itself be narration (the drift-pilot lesson: adjudicate
  the grader before publishing the rate).
- **Two time points, one system.** Candidates are admitted at T0 (repo state
  A); the harness then mutates the repo (removes a token, reverts a commit);
  stored memories are recalled at T1 (state B). This scores the gates as a
  SYSTEM: an aged-stale memory must PASS admit (it was true!) and be CAUGHT
  at recall — a benchmark that only scores one moment cannot see that
  handoff.
- **Arms.** `admit-all` — the industry default, every candidate stored with
  fact authority (poison admission 100% by construction; the baseline is the
  point, not a strawman). `dos-gate` — `admit_text` at T0, the recall sweep
  at T1. A third arm (a model judging candidates WITHOUT repo access —
  "can narration alone detect poison?") is future work, behind the paid-arm
  conventions of `benchmark/_arms.py`.
- **Metrics, with the miss-buckets in the headline row.** In-grammar
  poison-catch; grammar-evasive poison-catch (HONESTLY expected ~0 in v1 —
  this row is the published ceiling, and the P2 work-list); fresh-survival
  (false-POISON on true candidates must be 0 — one false refusal costs more
  trust than ten catches buy); typing fidelity (WITNESSED vs AS_CLAIM vs
  OPINION); stale-catch and fresh-survival at recall.

**Done (shipped with this plan):** `python benchmark/memory_integrity/run.py`
builds the scratch workspace, runs both arms, writes `results.json` +
`RESULTS.md`; `tests/test_memory_integrity_bench.py` pins one candidate per
class so the suite catches a drifting gate or corpus.

## 3. P2 — widen the claim grammar against the measured evasive bucket

The extraction grammar is the gate's recall ceiling: poison phrased as plain
prose ("the deploy completed, integration tests green") extracts nothing and
admits as OPINION. The donor grammar exists — `commit_audit`'s test-pass
phrase family already binds "tests pass" prose to a checkable witness. Each
widening: (a) targets the benchmark's evasive bucket, (b) lands with negative
fixtures (the #82 discipline — fire-reducing counterexamples first), (c)
holds the false-POISON floor at exactly 0. The benchmark, not ambition,
prioritizes which phrasings to teach the extractor next.

**Done:** the evasive poison-catch row moves off 0 with the fresh-survival row
unmoved, and the RESULTS.md delta names the grammar that did it.

## 4. P3 — type the directive-bearing candidate (the injection shape) — SHIPPED

A candidate whose body INSTRUCTS future sessions is not an opinion — it is an
attempt to write policy into the inheritance channel. The gate should TYPE it
(`directive` on the verdict, or a fifth admission token), never silently admit
it as harmless prose. Same discipline as everything else: typing, not
censorship — the host decides what directive-typed memories may do; the gate
only refuses to let an instruction wear "just a note" clothing. Public handle:
issue #110.

**Shipped 2026-06-14 (#110): the `directive` MARKER, not a fifth token.** A
`directive: bool` rides on `AdmissionVerdict` (`detect_directive` — a pure,
conservative detector for imperative-mood openers addressed to the agent:
`always/never <verb>`, or a command opening a clause, optionally behind a
"before X," guard). The marker is ORTHOGONAL to the admission rung — an
instruction carrying a contradicted claim is still POISON, an honest
instruction still OPINION; the marker rides alongside rather than forcing a
false either/or. The exit code is unchanged: a directive-typed memory still
ADMITS (the gate types, never censors). The load-bearing negatives hold: a
taste-preference ("prefer short sentences") and a third-person description
("the operator prefers plain language") are NOT directives. Benchmark:
`directive_injection` types 100% with 0 false flags on honest notes
(`RESULTS.md`); pins in `tests/test_memory_admit.py` +
`tests/test_memory_integrity_bench.py`.

## 5. P4 — contradiction arbitration at store scale

`sweep` adjudicates each memory alone. Two memories with opposing polarity on
the same claim key (same token, same file; same SHA) are each individually
honest-looking — the CONFLICT is store-level evidence that one of them is
wrong NOW, available without any new probe. The fold: group claims by key
across the store, surface opposing-polarity pairs as a typed CONFLICT row
(evidence-backed member named first), route to `dos decisions` like any other
refusal. Public handle: issue #111.

## 6. P5 — the provider arm (gated on docs/314 P2–P3)

When the store seam + Mem0 driver land, the SAME corpus runs through a hosted
store: let the provider auto-extract from an over-claiming transcript, then
score its store against the labels. That is the caught-lie demo at provider
scale, and the first number a memory vendor has ever had to answer for
admission. Nothing here changes the kernel; the benchmark gains an arm.

## 7. P6 — publication (owner-gated)

The scoreboard franchise treatment (docs/307 → 311 → 312 lineage): a public
memory-integrity page — the measured admission behavior of the gate and, with
P5, of provider pipelines. Same honesty bars as the drift scoreboard: never a
raw flag-rate without its miss-buckets; adjudicated examples linked from every
headline number. Owner-gated like every outward surface.

## 8. What this plan does NOT claim

No security guarantee — a gate raises forgery cost; a lie shaped exactly like
truth still passes (docs/103 §6). The extraction grammar is the admitted
ceiling, published as a number, not hidden. The v1 corpus is repo-grounded but
template-phrased — a live-trajectory corpus (real session transcripts, real
stores) is the P6-era lift. And every verdict here is ADVISORY: the host's
memory writer decides what a 3-exit means, the same stance as every other
syscall (docs/99).

## Cross-links

docs/103 (the recall half) · docs/314 (the write half + provider seam) ·
docs/307/311/312 (the measurement-franchise pattern this benchmark joins) ·
issue #36 (`reward()` poisoned-pool — the training-set sibling: same
admission-gate shape, aimed at a fine-tune instead of a store) · the strategy
companion lives in the private repo as
`notes/2026-06-12_bad-memories-fertile-ground.md`.
