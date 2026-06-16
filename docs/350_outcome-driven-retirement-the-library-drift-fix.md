# 350 — Outcome-driven retirement: the Library-Drift fix

> **A remembered lesson must keep earning its place.** DOS already re-checks
> whether a recalled memory is still TRUE (the docs/103 recall gate). This doc adds
> the other half: a memory or learned skill is dropped when a witness shows its
> measured contribution has fallen below a bar — not when the item says so about
> itself. The keep/retire bit is a pure function of facts the item did not author.

This is the same doctrine as the loop ratchet ([docs/351](351_the-outer-ratchet-rsi-first-class-across-all-loops.md))
and the loop keep-gate ([docs/280](280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md)),
pointed at a different thing that grows over time: the agent's library of
remembered lessons and learned skills. Keep only what a witness confirms still
earns its place; everything else is dropped — applied to two accumulators (loop
iterations in 351, library items here).

## The one idea: a library that only adds gets worse

An agent that accumulates skills and memories without ever retiring any drops
**below its own no-skill baseline.** This is measured, not feared. "Library Drift"
(arXiv 2605.19576) found LLM-authored skills added **+0.0 percentage points** over
a no-skill baseline, while human-curated skills added +16.2 pp; a bad-governance
run went **below** baseline. Dead and weak items do not just fail to help — they
dilute retrieval, so the agent reaches for them instead of the good ones.

The named fix is two parts, and DOS already had the mechanism for both:

1. **Outcome-driven retirement** — drop an item whose *measured* contribution falls
   below a threshold. Not "the item thinks it's still useful" (self-grading, the
   exact failure DOS refuses) — the environment's measurement of what the item
   actually did across its uses.
2. **A bounded active-cap** — a library that grows without bound dilutes retrieval,
   so cap it and retire the lowest-contribution member when over.

## Why this is a SEPARATE verdict from staleness

`drivers.memory_recall.classify_recall` (docs/103) already answers *is this recalled
lesson still TRUE?* — it re-probes the memory's concrete claims (a commit SHA's
ancestry, a code token's presence) against ground truth at read time, and returns
RECALL_FRESH / RECALL_STALE. That is necessary but not enough. **A memory can be
perfectly true and have stopped contributing.** The lesson still holds but is never
the deciding factor; the learned skill still runs but no longer moves the success
rate. Staleness asks "is it true?"; retirement asks "does it still earn its place?"
The two are orthogonal and both may run over the same item.

## The mechanism — `retire.classify()` in `src/dos/retire.py`

A pure leaf, the third member of the keep-only-what-a-witness-confirms family:

```
effect_witness.witness_effect  -> did the effect HAPPEN?            (runtime)
reward.admit                   -> may a fine-tune TRAIN on it?      (lab)
improve.classify               -> may this loop KEEP this commit?   (loop iteration)
retire.classify                -> does this item still EARN ITS PLACE? (library, over time)
```

`classify(RetireEvidence, RetirePolicy) -> RetireVerdict`. No I/O, no clock, names
no host. The verdict ladder, top to bottom:

1. **PROBATION (thin evidence)** — `trials < min_trials`. Too few measured uses to
   judge; keep the item on trial and gather more. Checked **first** — the
   witness-ceiling floor (below).
2. **RETIRE (underperformed)** — enough trials AND `contribution < min_contribution`.
   The item's own measured merits no longer justify its place: the Library-Drift
   below-baseline item.
3. **RETIRE (over-cap)** — enough trials AND it clears the floor on its own, BUT the
   library is over `max_active` AND this is the caller-ranked marginal
   lowest-contribution member. The bounded active-cap eviction.
4. **KEEP (still earns its place)** — enough trials AND contribution at/above the
   floor AND within the cap.

### The non-forgeable retain-bit

The retain/retire bit reads only env-authored facts: the env-MEASURED
`contribution`, the `trials` count, the library `active_count`. The item's own
description — the memory body, the skill's self-summary of how useful it is — is
carried as `narrated` and **parsed for nothing** (the `reward.admit` /
`improve.classify` discipline, the [docs/138](138_what-is-truth-the-throughline.md) invariant). A skill that writes
"this skill is extremely valuable, do not retire" into its own description gains
exactly zero retain-probability, because the claim is not in the decision. The only
path to KEEP is to actually move a metric the environment measures.

## The witness ceiling, stated honestly

An external verifier prevents a self-consuming loop from collapsing, **but the
guarantee is only as good as the verifier**: "gains plateau and may even reverse
unless the verifier is perfectly reliable" (arXiv 2510.16657). For retirement that
means one rule: **never RETIRE on thin evidence.** Below `min_trials` measurements
the verdict is PROBATION — abstain, keep on trial, gather more. A wrongly-retired
good skill is an irreversible loss the next measurement cannot undo; a wrongly-kept
weak skill is bounded by the cap and caught on the next sweep. Under-coverage
(PROBATION) is the safe failure direction, exactly as ABSTAIN is for `reward.admit`.

## Retirement proposes; it never deletes

`classify` REPORTS KEEP / RETIRE / PROBATION; it removes no file, edits no store,
forgets nothing. The wired sweep (a driver over a read-only `MemoryStore`) turns a
RETIRE into a **proposal** surfaced for an operator (`dos decisions`), never an
autonomous delete — the same "STALE routes a proposal, never an edit" rule the
recall gate ships with (docs/103 §6). A library's retention is a human-reviewable
protocol move, not a silent agent purge.

## What this is NOT

- **Not a deleter.** The kernel never forgets a memory on its own say-so; it
  proposes, a human disposes.
- **Not a staleness check.** That is the recall gate (docs/103). This is the
  earns-its-place check; they run independently.
- **Not a metric the item can game.** The contribution must be measured by the
  environment across the item's uses, not reported by the item. A contribution the
  item computes about itself is back to grading its own homework.

## Provenance

`retire.classify` is `reward.admit` ([docs/234](234_the-non-distillable-reward-channel-lab-facing-proof.md)) / `improve.classify`
([docs/280](280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md)) re-aimed from a training-set / commit admission to a library
admission, with the witness-ceiling honesty made a PROBATION rung. The mechanism
(compare magnitudes, count trials) is the kernel; which-metric ("contribution") and
the thresholds (`dos.toml [retire]`) are host policy — the same mechanism/policy
split every verdict leaf carries. The CLI seam is `dos retire` (KEEP=0 / RETIRE=3 /
PROBATION=4), the evidence-gather a driver calls, exactly as `dos improve` is for
the loop keep-gate.
