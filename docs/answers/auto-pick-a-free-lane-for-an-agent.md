# How does an agent auto-pick a free, non-colliding lane to work in?

> A bare `dos arbitrate` (no `--lane`) asks the kernel to walk the workspace's autopick ladder and hand back a free lane whose file tree is disjoint from every live lease. `pip install dos-kernel`, then `dos arbitrate`. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

Don't let the agent pick its own lane by name and hope it doesn't collide — that is a self-report, and a self-report is not evidence. Ask the kernel instead. A bare `dos arbitrate` (no lane named) is an auto-pick request: the kernel walks the workspace's declared autopick order and returns the first lane whose region doesn't overlap any lease already held. Admission is decided by **tree-disjointness**, not by a name the agent chose — two workers run concurrently if and only if their file trees are disjoint.

The call is pure: state in, decision out. The live leases are gathered at the boundary (folded from the lane journal — the exact set `dos lease-lane live` reconstructs) and passed into the verdict; the verdict itself reads no disk and persists nothing. So the answer to "which free lane may I take?" comes from the journal of what other agents already hold, never from the requesting agent's account of the world. `dos pickable` and `dos enumerate` surface what is takeable before you ask.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| Bare `arbitrate` walks the autopick ladder for a free, tree-disjoint lane | mechanism (no benchmark number) | the lane journal of live leases — folded at the boundary, not authored by the requesting agent | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| Two workers run concurrently iff their file trees are disjoint — admission by region, not by name | mechanism | the live-lease set the arbiter is handed; the verdict is pure `classify(evidence, policy)` | [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) |

No J is claimed here: this is the admission mechanism, not a measured failure-block count. The lost-update study that *does* put a number on the collision-prevention lives in the sibling pages below.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos arbitrate --workspace .
```

With no `--lane`, the kernel auto-picks the first free disjoint lane and grants it:

```text
acquire  lane=docs  tree=docs/**  reason: cluster lane 'docs' free — admitted.
```

If every autopick lane is already held, the request is refused with a typed reason rather than a silent double-booking — the second agent waits, it does not clobber.

## What this does — and does not — certify

A bare-arbitrate grant certifies one thing: at decision time the returned lane's region was **disjoint** from every live lease, so taking it won't put two writers on the same files. It does not review the edits, run the tests, or judge whether the work that follows is correct — it serializes *effects on shared state*, nothing more. And the pure verdict persists nothing; to hold the lease durably so sibling workers actually see it, the durable verb (`dos lease-lane acquire`) writes the grant back to the journal.

## Sources / reproduce

- [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) — the `dos arbitrate` verb: a bare/auto-pick request walks the autopick ladder for a free, tree-disjoint lane; `dos pickable` / `dos enumerate` surface what's takeable.
- [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) — arbitrate is the admission syscall; two workers run concurrently iff their file trees are disjoint.
- [Lease-based file locking for parallel agents](lease-based-file-locking-for-parallel-agents.md) — the durable lease that holds the lane you auto-picked.
- [How to stop two AI agents overwriting each other](how-to-stop-two-ai-agents-overwriting-each-other.md) — the same primitive, framed as the head problem.
- [FAQ: Don't git worktrees already solve this?](../FAQ.md#dont-git-worktrees-already-solve-this--one-isolated-checkout-per-agent)

## Also asked as

- how does an agent automatically choose a lane that won't collide?
- pick a free non-overlapping region for a worker to edit
- auto-assign a coding agent to an unclaimed part of the repo
- bare `dos arbitrate` with no lane — what does it do?
- let the kernel hand a worker a free lane instead of naming one
- how do I find an open lane for the next parallel agent?
- self-service lane selection for a fleet of agents

> The kernel is the part that doesn't believe the agents.
