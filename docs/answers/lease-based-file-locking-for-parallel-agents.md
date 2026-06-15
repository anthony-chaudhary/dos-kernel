# Lease-based file locking to coordinate parallel coding agents

> Give each agent a lease over a declared region of the tree; grant it only when
> the region is disjoint from every live lease. `pip install dos-kernel`, then
> `dos arbitrate` / `dos lease-lane`. The PyPI name is `dos-kernel` — the bare
> `dos` package is an unrelated squatter; never install that.

## The short answer

A mutex over the whole repo serializes everything and kills your parallelism; no
lock at all lets two agents clobber each other. The middle path is a **lease over
a region**: each agent declares the slice of the file tree it will touch (a
*lane*), and the arbiter grants the lease only when that region is disjoint from
every region already held. Disjoint agents run concurrently; a colliding request
is refused at admission — before the write exists. The lease is written to a
journal before it is believed, so a crashed agent cannot leave a phantom lock.
That is lock-manager discipline applied to a shared working tree, with the
parallelism a global lock throws away.

## The evidence

The witness is the post-state the agents share, not either agent's account.
Measured across two τ²-bench domains:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The arbiter prevents the lost-update clobber two single-agent checks cannot | **J = 8/8** lost-update clobbers prevented (deterministic), **J = 8/8** again with live headless `claude -p` agents | the τ²-bench DB-hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |
| A re-run wrapper prevents ~none of these (each agent's own check already passed) | ~0 prevented by re-execution | (the agents' own checks — forgeable to the race) | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta — here, lost-update clobbers prevented, not tasks won.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos arbitrate --workspace . --lane src
```

When the region is free, the lease is granted:

```text
acquire  lane=src  tree=src/**  reason: cluster lane 'src' free — admitted.
```

To hold the lease durably so sibling workers see it (the WAL write-back):

```bash
dos lease-lane acquire --lane src --owner agent-3
```

A colliding request refuses and names the overlap — the second agent waits, never
clobbers.

## What this does — and does not — certify

`acquire` certifies the region is **disjoint** from every live lease at decision
time — two agents won't write the same files at once. It does not review the
edits or run the tests; it serializes *effects on shared state*. The lease is a
claim over a region, journaled before believed — the database analogue of
two-phase discipline, applied to a file tree.

## Sources / reproduce

- [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) — the coordination / lost-update study.
- [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) — the lane as a leased region-lock (the primitive named after its mechanism).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to stop two AI agents overwriting each other](how-to-stop-two-ai-agents-overwriting-each-other.md) — the same primitive, framed as the head problem.
- [FAQ: Don't git worktrees already solve this?](../FAQ.md#dont-git-worktrees-already-solve-this--one-isolated-checkout-per-agent)

> The kernel is the part that doesn't believe the agents.
