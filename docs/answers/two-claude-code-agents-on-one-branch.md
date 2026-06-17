# Two Claude Code agents on one branch keep clobbering each other — how do I fix it

> Lease the file regions and decide admission before each write — disjoint agents
> run, colliding ones wait. `pip install dos-kernel`, then `dos arbitrate` wired
> into the runtime. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

Run two Claude Code (or Cursor, or Codex) agents on one branch and they will edit
the same files and clobber each other — each one's transcript looks clean because
neither can see the other. The fix isn't "give each its own worktree" (that
isolates the checkout but not the shared world state they both write); it's
admission control on the file trees. Give each agent a lane and ask `dos arbitrate`
before it starts: disjoint trees acquire and run concurrently, a colliding request
is refused so the second agent waits instead of overwriting. Wire it via
`dos init --hooks` and every agent on the branch is under it automatically.

## The evidence

The witness is the shared post-state, not either agent's account. Measured with
live headless agents:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The arbiter prevents the lost-update clobber two single-agent checks cannot | **J = 8/8** lost-update clobbers prevented (deterministic), **J = 8/8** again with live headless `claude -p` agents | the τ²-bench DB-hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |
| A re-run wrapper prevents ~none — each agent's own check already passed | ~0 prevented by re-execution | (the agents' own forgeable checks) | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta — lost-update clobbers prevented, not tasks won.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos init --hooks auto .       # wires arbitrate into Claude Code, Cursor, Codex, …
```

When two agents' regions overlap, the second is refused and waits:

```text
refuse — tree src/api/** overlaps a live lease held by agent-1; admission denied
```

## What this does — and does not — certify

It certifies that two agents on one branch won't **write the same files at once** —
disjoint work runs in parallel, collisions are serialized at admission. It does not
merge their work or review it; it prevents the silent clobber, the database
analogue of two-phase discipline applied to your tree.

## Sources / reproduce

- [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) — the coordination / lost-update study.
- [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) — the lane as a leased region-lock.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to stop two AI agents overwriting each other](how-to-stop-two-ai-agents-overwriting-each-other.md) — the head version.
- [FAQ: Don't git worktrees already solve this?](../FAQ.md#dont-git-worktrees-already-solve-this--one-isolated-checkout-per-agent)

## Also asked as

- two Claude Code agents on one branch keep clobbering each other
- running two Claude Code instances on the same branch
- two agents same branch how do I stop the conflicts
- coordinate two Claude Code agents on one repo
- parallel Claude Code agents overwrite each other
- two coding agents one git branch collision fix

> The kernel is the part that doesn't believe the agents.
