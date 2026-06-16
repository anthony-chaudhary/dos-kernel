# How to prevent context poisoning from an AI agent's own prior outputs

> Don't let a past self-report re-enter as a fact — re-verify each recalled claim
> against ground truth, and refuse the bindable poison at birth. `pip install
> "dos-kernel[mcp]"`, then the `recall` tool. The PyPI name is `dos-kernel` — the
> bare `dos` package is an unrelated squatter; never install that.

## The short answer

Context poisoning is a wrong claim from a past turn — a hallucinated SHA, a false
"the flag is X", a fabricated fact — getting written to memory and re-injected
later wearing the authority of a fact. The defense is two-tier and both tiers read
ground truth, not the claim. At write time, a false claim the extractor can *bind*
(a checkable SHA/flag/path) is refused at birth; what it can't refuse, it strips of
fact authority (admitted only as dated claim or opinion). At read time, the
`recall` tool re-probes each checkable claim against git and the working tree and
returns fresh / stale / unverifiable — so a poisoned memory can't re-enter as a
current fact on its own say-so.

## The evidence

Labels are env-authored (a constructed git history). Measured over n=18 candidates:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| False content is kept from wearing FACT authority | **100%** stopped; fact-tier precision **100%** (leak=0) | a constructed git history the memory's author did not control | [`benchmark/memory_integrity/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/memory_integrity/RESULTS.md) |
| Recall-alone (the industry default) catches the poison late, after sessions inherit it | late recall-only catch on false **43%**; admit-all baseline admits **100%** of the poison | git ancestry / working-tree read at recall | [`benchmark/memory_integrity/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/memory_integrity/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the recall tool is available
```

A recalled memory whose cited fact no longer holds:

```text
RECALL_STALE — the deciding claim is not in ancestry; do not inject as a fact
```

## What this does — and does not — certify

It certifies a recalled memory's **checkable claims still hold** against ground
truth — and refuses the bindable poison before it's stored. It does not catch
prose-shaped poison that names no artifact (that's typed as opinion, not refused);
the guarantee is that no checkable lie re-enters context as a fact.

## Sources / reproduce

- [`benchmark/memory_integrity/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/memory_integrity/RESULTS.md) — the bad-memory taxonomy + integrity benchmark.
- [`docs/80`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/80_mcp-server-surface.md) — the MCP tool surface.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [My recalled agent memory is stale or wrong — how do I re-verify it](recalled-agent-memory-is-stale-how-to-reverify.md) — the recall verb in depth.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to prevent context poisoning from an AI agent's own prior outputs
- prevent context poisoning from an agent's own prior outputs
- agent feeds its own bad output back into context
- stop an agent poisoning itself with prior mistakes
- context poisoning loop in an autonomous agent
- agent's own hallucination contaminates later steps
- break the self-poisoning feedback loop in an agent

> The kernel is the part that doesn't believe the agents.
