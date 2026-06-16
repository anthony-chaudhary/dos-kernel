# How to block an out-of-lane file write before the agent makes it (PreToolUse)

> Decide admission at the write, from the file the agent is about to touch:
> `pip install dos-kernel`, then `dos arbitrate` wired into a PreToolUse hook. The
> PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that.

## The short answer

The cheapest place to stop a collision or an out-of-scope edit is *before* the
write happens — at the PreToolUse boundary, where the agent has named the file but
not yet changed it. Give each agent a lane (a declared region of the tree) and
ask `dos arbitrate` at that boundary: a write inside the agent's leased region is
allowed; a write into a region another agent holds, or outside the agent's
declared scope, is denied and the file is left untouched on disk. Wire it via
`dos init --hooks` and the verdict becomes a deny payload your runtime honors —
the same mechanism that stops an agent self-modifying the kernel, generalized to
any out-of-lane write.

## The evidence

The witness is the shared post-state, not either agent's account. Measured across
two τ²-bench domains:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The arbiter prevents the out-of-lane clobber two single-agent checks cannot | **J = 8/8** lost-update clobbers prevented (deterministic), **J = 8/8** again with live headless `claude -p` agents | the τ²-bench DB-hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |
| A pre-action gate refuses the breaching write the narration waved through | prevention **100% (6/6 true checkable violations)**, false-fire **0% (0/2)** | a world-state precursor read-back | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos init --hooks auto .       # wires arbitrate into the PreToolUse boundary
```

At the write, an out-of-lane target is denied and nothing lands:

```text
refuse — tree src/auth/** overlaps a live lease held by agent-1; write denied
```

The decision is pure — it reads the requested file tree, not the agent's intent.

## What this does — and does not — certify

It certifies that, at the write, the target is **inside the agent's leased region
and disjoint from every other** — so an out-of-lane or colliding write is stopped
before it exists. It does not review the edit's content; it gates *where* the
agent may write, deterministically, at the cheapest point to intervene.

## Sources / reproduce

- [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) — the coordination / lost-update study.
- [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) — the lane as a leased region-lock.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Lease-based file locking for parallel agents](lease-based-file-locking-for-parallel-agents.md) — the lease this gate enforces.
- [FAQ: How do I stop two AI agents from editing the same files at the same time?](../FAQ.md#how-do-i-stop-two-ai-agents-from-editing-the-same-files-at-the-same-time)

## Also asked as

- how to block an out-of-lane file write before the agent makes it PreToolUse
- block an out-of-lane file write before the agent makes it
- deny an agent file write at PreToolUse
- stop an agent writing outside its allowed paths
- pre-write guard for agent file edits
- intercept an out-of-scope agent write before it happens
- enforce a write boundary at the PreToolUse hook
- deny an agent write outside its lane before it happens
- pre-write boundary check for an agent
- intercept an out-of-scope edit at the hook
- stop an agent writing where it shouldn't pretooluse

> The kernel is the part that doesn't believe the agents.
