# How to stop two AI agents overwriting each other

> Decide whether two agents may run at once *before* they write, from the files
> each will touch: `pip install dos-kernel`, then `dos arbitrate`. The PyPI name
> is `dos-kernel` — the bare `dos` package is an unrelated squatter; never
> install that.

## The short answer

When several agents work one repo at once, two of them can edit the same files
and the second silently clobbers the first — the classic **lost update**. Each
agent's own transcript looks clean, because neither one can see the other. The
fix is admission control on the *file trees*: before an agent starts, ask
whether its tree overlaps any tree already in flight. `dos arbitrate` answers
`acquire` when the trees are disjoint (safe to run concurrently) or `refuse`
when they collide, naming the conflict — so the second writer waits instead of
overwriting. It is a pure decision over the file regions, not a global lock:
disjoint work still runs in parallel.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The arbiter prevents the lost update | J = 4/6 natural-mix clobbers prevented (6/8 constructed; 8/8 in the two-domain τ²-bench port; **9/10 in the live replication**) | the post-state database hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |
| Concurrent disjoint work is admitted, not serialized | the arbiter refuses *more* only under tree overlap; disjoint trees acquire | the requested vs. live file trees | [`docs/138`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/138_what-is-truth-the-throughline.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos arbitrate --workspace . --lane docs
```

When the requested lane's tree is free, the verdict admits the worker:

```text
acquire  lane=docs  tree=docs/**  reason: cluster lane 'docs' free — admitted.
```

When another agent already holds an overlapping tree, the verdict refuses and
names the collision, so the second agent waits rather than clobbering. The
decision is pure — it reads the file trees, not anyone's narration.

To put every concurrent agent under it automatically, wire it into your runtime:

```bash
dos init --hooks auto .       # Claude Code, Cursor, Codex, Gemini CLI, …
```

## What this does — and does not — certify

`acquire` certifies that, at decision time, the requested tree is **disjoint**
from every live lease — so two agents won't edit the same files at once. It does
not review the edits, run the tests, or judge whether the work is correct; it
serializes *effects on shared state*, the database analogue of two-phase
discipline. A git worktree per agent isolates the *checkout* but not the shared
*world state* they both write — see the FAQ entry below.

## Sources / reproduce

- [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) — the coordination / lost-update study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [The incident page](../incidents/two-agents-overwrote-each-others-work.md) — the same failure as a story.
- [FAQ: How do I stop two AI agents from editing the same files at the same time?](../FAQ.md#how-do-i-stop-two-ai-agents-from-editing-the-same-files-at-the-same-time)

## Also asked as

- how to stop two AI agents overwriting each other
- two AI agents keep overwriting each other's files
- stop parallel coding agents from clobbering each other
- prevent two agents editing the same file at once
- how to coordinate file writes between multiple agents
- agents on the same repo overwrite each other's changes
- lock files so two agents don't collide
- concurrent AI agents stepping on each other how to fix
- keep parallel agents from racing on shared files
- how do I run multiple coding agents without conflicts
- two agents one workspace stop the overwrite problem
- serialize agent edits to shared state
- two Cursor agents overwriting each other's files
- stop two Copilot agents clobbering the same file
- multiple Aider sessions colliding on one repo
- run several Claude Code agents without file conflicts
- race condition between two coding agents on one repo
- agents stomping on each other's edits how to serialize
- admission control so only one agent edits a file tree
- git conflicts every time I run two agents in parallel
- make parallel agents take turns on shared files
- keep two AI agents from clobbering files
- two agents editing the same repo safely
- stop agents from undoing each other's work
- run agents in parallel without overwrites

> The kernel is the part that doesn't believe the agents.
