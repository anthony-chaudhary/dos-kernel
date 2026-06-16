# Process-reward-model training data that can't be gamed

> A per-step label that is not the agent's own word: `pip install dos-kernel`,
> then `dos reward`. The PyPI name is `dos-kernel` — the bare `dos` package is
> an unrelated squatter; never install that.

## The short answer

Agent-training data is contaminated at the source: outcome labels trust a
grader's pass/fail, and trace labels *are* the agent's own narration — both put
the label downstream of something the agent said. A process reward model (PRM)
trained on that learns to reward *convincing*, not *correct*. DOS produces a
different label: per adjudicated step it holds the agent's **claim**, the
**ground truth** (read from a witness the agent didn't author), and the
**verdict with its provenance** rigidly apart. `dos reward` emits the admission
bit as a pure function of that witness — so, conditional on environment state,
it is independent of the answer text. No token sequence flips a rejected poison
label to an accept.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The reward-set admission label can't be gamed | acceptance precision **60% → 100%**, J = 5 poison labels purged (**ΔP +40 pp**) from a naive self-judged collector's bank | the gold database hash, keyed on `db_match` | [`docs/230`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md) |
| The label is non-distillable | the admission bit is independent of the answer text conditional on environment state; a forgeable read-back is structurally ignored, not down-weighted | the environment's own state, not the trace | [`docs/234`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/234_the-non-distillable-reward-channel-lab-facing-proof.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos reward --workspace . AUTH AUTH1
```

`dos reward` returns the admission bit for a step from the witness alone. Because
it reads the environment's state rather than the agent's report, the same step
gets the same label no matter how the agent narrates it — which is what makes
the resulting dataset a clean per-step signal for a process reward model instead
of a record of which agent argued best.

## What this does — and does not — certify

The label certifies **the stated outcome matched the world** — necessary, not
sufficient. "Ground truth" here means *a real effect landed* (a commit exists, a
database row matches the gold hash), not *the change is correct*: a step that
admits can still be wrong code, and a correctness label is out of scope by
design. The value is honesty per step, held apart from the agent's word — the
scarce raw material a PRM is starved for.

## Sources / reproduce

- [`docs/230`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md) — the non-distillable reward label.
- [`docs/234`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/234_the-non-distillable-reward-channel-lab-facing-proof.md) — the lab-facing proof.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> An EvidenceSource is a witness whose byte-author is not the judged agent.

## Also asked as

- process-reward training data that can't be gamed
- process reward model training data that can't be gamed
- ungameable labels for a process reward model
- where do I get PRM training data agents can't hack
- non-distillable reward labels for coding agents
- reward signal an agent can't reward-hack
- generate process-reward data grounded in real outcomes
- PRM labels from verified outcomes not self-report
- build a reward model that resists gaming
- training labels for step-level agent rewards that hold up
- how to make reward data robust to distillation
- outcome-grounded labels for a process reward model
- stop reward hacking with non-forgeable PRM labels
- where verified step rewards for agents come from
- ungameable training signal for agentic RL
- PRM data that survives a distillation attack
