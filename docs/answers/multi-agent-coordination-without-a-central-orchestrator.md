# How to coordinate multiple AI agents without a central orchestrator (contracts, not a queue)

> Replace the central queue with a per-region admission contract every agent
> checks before it writes. `pip install dos-kernel`, then `dos arbitrate`. The
> PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that.

## The short answer

A central orchestrator that hands out tasks is a bottleneck and a single point of
failure. The decentralized alternative isn't "no coordination" — it's a contract
every agent honors at the write boundary. Each agent declares the region it will
touch and asks `dos arbitrate` for admission: disjoint regions acquire and proceed
in parallel, an overlapping one is refused. There's no queue and no coordinator —
the contract is a pure decision over the file regions, and any agent (in any
runtime) can ask it. Coordination becomes a router, not a queue: by-tree, not
by-name.

## The evidence

The witness is the shared post-state, measured with live headless agents:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The per-region contract prevents the lost-update clobber a central re-run wrapper cannot | **J = 8/8** lost-update clobbers prevented (deterministic), **J = 8/8** again with live headless `claude -p` agents | the τ²-bench DB-hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |
| Admission is a pure decision — disjoint work runs, overlap is refused | the arbiter refuses *more* only under overlap; disjoint regions acquire | the requested vs. live file trees | [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos arbitrate --workspace . --lane src/api
```

Each agent asks before it writes — no coordinator in the loop:

```text
acquire  lane=src/api  tree=src/api/**  reason: region free — admitted.
```

## What this does — and does not — certify

It certifies coordination by a **per-region contract** — disjoint agents run
without a central scheduler, collisions refuse at admission. It does not assign
work or balance load (that's an orchestrator's job, which DOS deliberately isn't);
it provides the safety contract a decentralized fleet writes against.

## Sources / reproduce

- [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) — the coordination / lost-update study.
- [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) — the lane as a leased region-lock.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Lease-based file locking for parallel agents](lease-based-file-locking-for-parallel-agents.md) — the lease primitive.
- [FAQ: Is DOS an agent orchestrator or framework?](../FAQ.md#is-dos-an-agent-orchestrator-or-framework)

> The kernel is the part that doesn't believe the agents.
