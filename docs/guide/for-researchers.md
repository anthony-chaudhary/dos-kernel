# For researchers

> ← Part of the [DOS README](https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md). The on-ramp joining the claims to their write-ups, the two formal invariants, and the literature.

Every number the README claims is graded the way the kernel grades agents: it
counts only if a witness the graded party didn't author backs it. This is the
on-ramp joining the claims to their write-ups, the two formal
invariants, and the literature — the depth lives under
[`benchmark/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark) and the paper.

**The claims register.** The proven rows from
[the evidence section](./why-a-referee.md#whats-proven-and-whats-still-a-bet), each with the
witness it was scored against and the write-up that reproduces it:

| Claim | Result | Witness | Write-up |
|---|---|---|---|
| the write-admission gate catches the over-claim | J = 10/120 "I shipped it" lies blocked, 0 honest writes refused, the same 8.3% over-claim rate on a mid-size and a top-tier model (15/258 over the full benchmark) | the env DB-hash the agent authored zero bytes of | [`agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| the arbiter prevents the lost update | J = 4/6 natural-mix clobbers prevented (6/8 constructed; 8/8 in the two-domain τ²-bench port; 9/10 in the live replication) | the post-state DB-hash neither agent authors | [`tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |
| mid-run "fixes" are flat-to-negative; witness-gated early-halt is the survivor | 0 false-abandons / 1,634 winners across 22 models (error-gated, K≥3) — and the same test falsifies the naive raw-repeat gate | each task's own oracle over a frozen replay corpus | [`giveup_cross_benchmark.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/giveup_cross_benchmark.py) · [the corpus ledger](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/toolathlon/_results/additivity_claims.md) |
| the reward-set admission label can't be gamed | acceptance precision 60% → 100%, J = 5 poison labels purged (ΔP +40 pp) from a naive self-judged collector's bank | the gold DB-hash, keyed on `db_match` | [docs/230](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md) |

**The two invariants.** The formal contribution under those numbers, stated
precisely:

1. **The non-forgeable witness**
   ([docs/138](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md)):
   every kernel verdict is a pure function `classify(evidence, policy)` whose
   evidence is bytes the adjudicated agent did not author — git ancestry, an
   environment's database state, the file tree, the clock. Byte-author ≠
   claimant is the load-bearing split: the agent may write anything it likes,
   and none of it is an input to the verdict.
2. **The non-distillable label**
   ([docs/230](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md),
   [docs/234](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/234_the-non-distillable-reward-channel-lab-facing-proof.md)):
   the reward-set admission bit (`dos reward`) is a pure function of that
   witness, so — conditional on environment state — it is independent of the
   answer text. No token sequence moves a `REJECT_POISON` to an `ACCEPT`, and a
   forgeable read-back is structurally ignored, not down-weighted.

**Reproduce it.** One runner fronts the suite: `python -m benchmark._run list`
inventories every benchmark with its arms, cost, and prereqs, and each proven
row above has a $0 offline arm
([`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md)).
Read the numbers under one rule: a **J** is a count of failures blocked off
ground truth, never a downstream outcome delta — "blocked 10 real over-claims"
is proven, "made the fleet 10% better" is a different sentence, and we don't
write it.

**Where it sits.** The lineage is deliberate, one line each: the kernel is a
reference monitor in the minimal-TCB tradition — a small, separate,
non-bypassable adjudicator outside the agents it judges; `resume` is the third
ARIES phase aimed forward — continue from the durable fossils, never from the
dead run's account of itself; the arbiter enforces serializability over shared
world-state regions, with the lost update as its target anomaly; and
`reward()` lands in the reward-hacking / scalable-oversight line — a
deterministic floor inside the training loop. The full argument is the paper,
*"Verification Is All You Need — But Not Where You Think"*
([`paper/releases/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/paper/releases)),
and the BibTeX is in the README's [Citation](https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md#citation) section.
