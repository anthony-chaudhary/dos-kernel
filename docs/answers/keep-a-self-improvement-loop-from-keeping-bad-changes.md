# How to keep an AI self-improvement loop from keeping bad changes

> Keep a candidate only when a witness the candidate's author didn't write
> confirms a real gain — otherwise revert. `pip install dos-kernel`, then `dos
> improve`. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated
> squatter; never install that.

## The short answer

A self-improving loop that grades its own candidates ratchets in the wrong
direction: it keeps the changes it *claims* help, banks the over-claims, and
conditions the next round on them. The one rule that fixes it: a candidate is KEPT
only if an independent witness confirms it improved — the test suite green on a
clean worktree, the truth syscall clean, and a strictly-measured metric gain.
Otherwise it's REVERTED. `dos improve` makes that the typed keep/revert decision,
applied in an isolated worktree so the kernel adjudicating a candidate is never the
kernel being rewritten; a run of non-keeps trips a breaker that escalates to a
human. The loop can't keep a change by asserting it's better.

## The evidence

Same proposer, same mutation stream, same budget — one rule apart. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Self-judged keeping banks over-claims; witness-gated keeping does not | over-claims kept = **12** (self-judged arm, seed 0) vs **0** (witness-gated) | a held-out measurement the proposer did not author | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |
| The gain holds across seeds, not one lucky run | mean witnessed final gain **A − B = +1553.6 mbits/char** over 10 seeds | fresh held-out windows, measured per seed | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. An "over-claim kept" is a cycle whose claimed improvement the
fixed-seed record refutes — the poison the gate exists to refuse.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos improve --workspace . --work measured.json
```

A candidate whose witnessed gain doesn't clear the floor is reverted, not kept:

```text
REVERT — claimed gain unconfirmed by the witness; candidate not kept
```

## What this does — and does not — certify

It certifies each kept candidate is backed by a **measured, un-authored gain** —
so the loop ratchets up, not sideways. It does not capture every dimension of
"better" the metric ignores; it closes the specific failure where a loop keeps a
change because it said so. A run of non-keeps escalates to a human rather than
spinning.

## Sources / reproduce

- [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) — the ratchet curve and the bait scoreboard.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to make an agent prove it did the work](make-an-agent-prove-the-work-not-self-certify.md) — the same keep-gate, framed as the self-certify problem.
- [Reward hacking in LLM coding agents](reward-hacking-in-llm-coding-agents.md) — the training-loop sibling.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to keep an AI self-improvement loop from keeping bad changes
- keep a self-improvement loop from keeping bad changes
- stop an auto-improve loop from accepting regressions
- gate a self-improving agent on a real measured gain
- RSI loop keeps bad edits how to prevent
- only keep an agent's change if a witness confirms it improved
- revert bad changes in a self-improvement loop automatically
- auto-improve loop accepted a regression stop that
- only keep an agent change if a witness confirms a gain
- revert bad edits in a self-improving loop
- gate RSI on a measured improvement not the agent's word

> The kernel is the part that doesn't believe the agents.
