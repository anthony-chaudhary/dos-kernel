# My recalled AI agent memory is stale or wrong — how do I re-verify it

> A recalled memory is a frozen self-report from a past session — re-check its
> claims against ground truth before trusting them. `pip install "dos-kernel[mcp]"`,
> then the `recall` tool re-verifies a memory at read time. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that.

## The short answer

When a saved memory is injected back into context, it arrives wearing the
authority of a fact — but it is the least trustworthy signal in the stack: a note
some past session wrote about a SHA, a flag, or a file path that may no longer be
true. The `recall` tool (an MCP tool; `dos doctor` lists what your install
exposes) re-probes each checkable claim in the memory against the repo *now* — a
working-tree grep for a code token, git merge-base ancestry for a SHA, git
history for a path — and returns `RECALL_FRESH`, `RECALL_STALE`, or
`RECALL_UNVERIFIABLE`. On anything but fresh, you present the memory hedged or
withhold it, never inject its raw body as confirmed. The defense is two-tier:
refuse the poison the extractor can bind, and strip fact-authority from the rest.

## The evidence

Labels are env-authored (a constructed git history). Measured over n=18 candidates:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| False content is kept from wearing FACT authority | **100%** stopped; fact-tier precision **100%** (every WITNESSED memory was actually true, leak=0) | a constructed git history the memory's author did not control | [`benchmark/memory_integrity/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/memory_integrity/RESULTS.md) |
| The cost side of distrust stays zero | **0** false refusals on true candidates; stale-catch on aged-true memories **100%** | git ancestry / working-tree read at recall | [`benchmark/memory_integrity/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/memory_integrity/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. The industry default (admit-all auto-extraction) admits 100% of
the poison; recall-alone catches only **43%**, and only after sessions may have
inherited the lie.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # confirm the recall tool is available
```

The `recall` tool on a memory whose cited SHA is no longer in the branch:

```text
RECALL_STALE — the deciding claim (commit abc123 shipped) is not in ancestry
```

## What this does — and does not — certify

It certifies that a memory's **concrete, checkable claims still hold** against git
and the working tree — a SHA in ancestry, a token present, a path in history. It
does **not** verify prose-shaped claims that name no artifact (those are admitted
only as dated claim or opinion, never as fact). The guarantee: a stale memory
can't be re-injected as a current fact on its own say-so.

## Sources / reproduce

- [`benchmark/memory_integrity/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/memory_integrity/RESULTS.md) — the bad-memory taxonomy + integrity benchmark.
- [`docs/80`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/80_mcp-server-surface.md) — the MCP tool surface.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to make an agent prove it did the work](make-an-agent-prove-the-work-not-self-certify.md) — the same distrust applied to a candidate change.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> The kernel is the part that doesn't believe the agents.
