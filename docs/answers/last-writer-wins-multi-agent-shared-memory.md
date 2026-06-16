# The last-writer-wins problem in multi-agent shared memory — how do I stop it

> Serialize the contended region with a lease, so the second writer is admitted or
> refused, never silently overwriting. `pip install dos-kernel`, then
> `dos arbitrate`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

When several agents share one store — a file, a DB, a memory — and there are no
contracts between them, the last write wins: agent B computes against a value
agent A is about to change, both write, and A's update is silently lost. Each
agent's own check passes, because each looked at a consistent state *for itself* —
the conflict is between them, in time. A re-run wrapper can't catch it (re-running
one agent re-passes its own check). The thing that does is admission control on the
contended region: `dos arbitrate` serializes writes to one region, so the
stale-against-pre-change write is refused at admission rather than discovered as a
lost update later.

## The evidence

The witness is the shared post-state, not either agent's account. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The arbiter prevents the lost-update clobber two single-agent checks cannot | **J = 8/8** lost-update clobbers prevented (deterministic), **J = 8/8** again with live headless `claude -p` agents | the τ²-bench DB-hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |
| A re-run wrapper prevents ~none — each agent's own check already passed | ~0 prevented by re-execution | (the agents' own forgeable checks) | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos arbitrate --workspace . --lane store
```

A write into a region another agent holds is refused, so the lost update can't
happen:

```text
refuse — region 'store' overlaps a live lease; admission denied
```

## What this does — and does not — certify

It certifies the contended region is **serialized** — the second writer is admitted
or refused, never silently last-wins. It does not merge concurrent edits or pick a
winner by content; it prevents the silent overwrite, the database analogue of
two-phase discipline applied to a shared store.

## Sources / reproduce

- [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) — the coordination / lost-update study.
- [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) — the lane as a leased region-lock.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Lease-based file locking for parallel agents](lease-based-file-locking-for-parallel-agents.md) — the lease mechanism.
- [FAQ: How do I stop two AI agents from editing the same files at the same time?](../FAQ.md#how-do-i-stop-two-ai-agents-from-editing-the-same-files-at-the-same-time)

## Also asked as

- the last-writer-wins problem in multi-agent shared memory
- last-writer-wins problem in multi-agent shared memory
- agents overwrite shared memory last write wins
- stop lost updates in multi-agent shared state
- concurrent agents clobber shared memory how to fix
- shared memory race between agents last writer wins
- coordinate writes to shared agent memory
- lost updates in multi-agent shared state
- agents overwrite shared memory last write wins fix
- concurrent writes to agent memory race
- coordinate shared-memory writes across agents

> The kernel is the part that doesn't believe the agents.
