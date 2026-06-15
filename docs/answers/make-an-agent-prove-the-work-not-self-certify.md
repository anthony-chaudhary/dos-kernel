# How to make an AI agent prove it did the work instead of self-certifying done

> Separate the worker from the judge: the agent proposes, a witness it didn't
> author decides. `pip install dos-kernel`, then `dos improve` / `dos verify`.
> The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated
> squatter; never install that.

## The short answer

An agent that grades its own work will keep the changes it *claims* are good, not
the ones that *are* good — the contestant cannot be the referee. The fix is to
make "done" mean a checkable effect, decided by a witness the agent did not
write. `dos improve` lets a self-improving loop KEEP a candidate only when an
independent measurement confirms a real gain (suite green on a clean tree, truth
syscall clean, a strictly-measured metric improvement); otherwise it REVERTS.
`dos verify` does the same for a phase claim — `SHIPPED` only when git ancestry
backs it. In both, the agent's own estimate of its success never enters the
verdict.

## The evidence

The same proposer, same mutation stream, same budget — one rule apart: whether
the keep decision reads an un-authored witness or the agent's own estimate.

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Self-certified keeping banks over-claims; witness-gated keeping does not | over-claims kept = **12** (self-judged arm B, seed 0) vs **0** (witness-gated arm A) | a held-out measurement the proposer did not author | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |
| The gap holds across seeds, not one lucky run | mean witnessed final gain **A − B = +1553.6 mbits/char** over 10 independent seeds | fresh held-out windows, measured per seed | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. An "over-claim kept" is a cycle the arm kept whose claimed
improvement the fixed-seed record refutes — the poison the gate exists to refuse.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos improve --workspace . --work measured.json
```

A candidate whose witnessed gain doesn't clear the floor:

```text
REVERT — claimed gain unconfirmed by the witness; candidate not kept
```

Exit code non-zero. KEEP requires the un-authored measurement to agree; the
agent's own "this is better" buys nothing.

## What this does — and does not — certify

`dos improve` certifies a **measured** improvement against a held-out witness, not
that the change is good in every way the metric doesn't capture. The point is
narrower and load-bearing: the agent cannot keep a change by asserting it
improved — only the measurement can, and a run of non-keeps escalates to a human.

## Sources / reproduce

- [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) — the ratchet curve and the bait scoreboard.
- [`docs/102`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/102_when-to-trust-an-agent.md) — when to trust an agent (trust commitments, not reports).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify an agent actually committed code](how-to-verify-an-ai-agent-actually-committed-code.md) — the phase-level version of the same rule.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> The kernel is the part that doesn't believe the agents.
