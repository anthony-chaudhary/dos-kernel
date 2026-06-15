# How do agents prove to each other that work actually landed (agent-to-agent trust)

> Hand a peer a verified status digest with no "claimed" field — only the
> witnessed effect. `pip install dos-kernel`, then `dos status` / `dos verify`.
> The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated
> squatter; never install that.

## The short answer

When one agent reports its status to another, the reporting agent authored the
report — so a peer that folds it inherits any over-claim. The fix is structural: a
peer-readable status digest built only from the kernel's VERIFIED rung, with no
`claimed` field by construction. A peer reading it cannot pick up a self-report it
is never handed — progress is git-VERIFIED, the held region comes from the lease,
liveness is read from the run's actual deltas. `dos status` folds those into one
record; `dos verify` is the underlying truth read. Agent-to-agent trust stops
being "I believe what you told me" and becomes "I read the same ground truth you
did".

## The evidence

The digest carries only what an un-authored witness confirms. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A confident peer claim is blocked when the shared state disagrees | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash, authored by zero agent bytes | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| A text-believing fold is gamed; a witness-reading one is not | text-believing **18 / 18 = 100.0%** forgeries admitted vs witness floor **0 / 18 = 0.0%** | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . PEER PEER2        # read the peer's claimed phase from git
```

`SHIPPED` (exit `0`) is a fact a peer can fold; `NOT_SHIPPED` is a claim to route
back, not inherit:

```text
SHIPPED PEER PEER2 a1b2c3d (via grep-subject)
```

## What this does — and does not — certify

It gives a peer a status whose **progress is git-verified, never self-reported** —
the digest structurally has no field for a claim. It does not certify the work is
correct; it ensures one agent's unverified word can't become another agent's
premise. Fold the witnessed, route back the rest.

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify what a subagent claims before folding its output](verify-what-a-subagent-claims-before-folding.md) — the same rule at the fold barrier.
- [FAQ: How is DOS different from agent evals or observability platforms?](../FAQ.md#how-is-dos-different-from-agent-evals-or-observability-platforms)

> The kernel is the part that doesn't believe the agents.
