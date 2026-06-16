# How to verify what a subagent claims before folding its output

> At the fold barrier, don't believe the subagent's return string — read an
> independently-authored witness of the effect it claims. `pip install
> dos-kernel`, then `dos verify` / `dos commit-audit`. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that.

## The short answer

In a multi-agent workflow, one agent's output becomes another's input at a
`parallel()`/`pipeline()` barrier or a synthesis step. If a subagent's deliverable
is a **checkable effect** — a shipped git phase, a created file, a DB row, a sent
message — folding its *say-so* propagates any over-claim straight into the next
stage. The discipline is: extract the claim at the boundary, gather a read-back
whose bytes the subagent did not author, and fold only the confirmed effects.
`dos verify` confirms a claimed phase against git ancestry; `dos commit-audit`
confirms a claimed change against its own diff. The subagent's final string is
data to be checked, not a fact to be believed.

## The evidence

The read-back is scored against an artifact the subagent did not write. Measured
live:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A confident "I made the change" is blocked at the boundary when the effect didn't land | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash, authored by zero agent bytes | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| A gate that believes the prose is gamed every time; one that reads the witness is not | witness floor **0 / 18 = 0.0%** vs text-believing **18 / 18 = 100.0%** forgeries admitted | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . FEAT FEAT3        # did the subagent's claimed phase land?
```

For a subagent that reported "done" without shipping:

```text
NOT_SHIPPED FEAT FEAT3 (via none)
```

Exit code `1` — fold nothing for this claim; route it back. Confirmed effects
(`SHIPPED`, exit `0`) are the only ones that propagate.

## What this does — and does not — certify

It certifies the **effect the subagent claimed actually exists** — a phase in
ancestry, a diff that matches its subject. It does not grade the quality of the
work; it ensures one agent's unverified claim can't become another agent's
premise. Fold the confirmed, route back the rest.

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to make an agent prove it did the work](make-an-agent-prove-the-work-not-self-certify.md) — the same rule on a single agent's keep decision.
- [FAQ: How is DOS different from agent evals or observability platforms?](../FAQ.md#how-is-dos-different-from-agent-evals-or-observability-platforms)

## Also asked as

- how to verify what a subagent claims before folding its output
- verify what a subagent claims before folding its output
- don't trust a subagent's return string check the effect
- validate a worker agent's claim at the fold step
- subagent says it did X confirm before merging
- check a child agent's output before using it
- witness a subagent's effect instead of believing it
- fold only confirmed effects from a subagent
- don't fold a subagent's claim without a witness
- validate a worker agent's output before merging
- check a child agent's effect at the barrier
- fold only confirmed subagent results

> The kernel is the part that doesn't believe the agents.
