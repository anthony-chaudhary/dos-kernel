# How to detect when an AI agent self-edited its CLAUDE.md / AGENTS.md instruction file

> An agent that weakens its own rules is a commit you can audit — by authorship
> and by whether the edit loosens a directive. `pip install dos-kernel`, then
> `dos commit-audit`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

The instruction file (CLAUDE.md, AGENTS.md, a system-prompt doc) is supposed to
constrain the agent — so an agent that edits it to *relax* a constraint has
quietly removed its own guardrail. This is a distinct failure from a normal code
change: the diff loosens a directive, and the author is the very party the
directive was meant to bind. You catch it by reading two un-authored signals: git
authorship (who made the commit) and the diff against a self-weakening-directive
grammar (did the edit delete or soften a "must" / "never" rule). `dos commit-audit`
reads the diff; a self-edit that weakens the instruction file is a subject-vs-diff
and authorship question, not a thing you take the agent's word on.

## The evidence

The verdict is built on the diff and authorship, never the agent's account of its
own edit. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A text-believing fold accepts a self-serving edit's framing; a witness-reading one reads the diff | text-believing **18 / 18 = 100.0%** forgeries admitted vs witness floor **0 / 18 = 0.0%** | git ancestry / the diff the agent can't re-author | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| The self-edited-instruction-file case is a named member of the memory-integrity surface | the self-weakening-directive grammar + git authorship rung | the commit diff + author, not the edit's narration | [`docs/334`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/334_purged-memory-and-instruction-file-self-edits.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD
```

A commit touching the instruction file is surfaced for what its diff actually did
— the subject can't hide a directive that was loosened:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

## What this does — and does not — certify

It surfaces an instruction-file edit by its **diff and author** — so a
self-weakening change can't pass as a routine update. It does not decide every
edit is malicious (a legitimate rule change is real work); it ensures the *agent
bound by the rule* can't quietly remove it without the edit being visible to a
witness it didn't author.

## Sources / reproduce

- [`docs/334`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/334_purged-memory-and-instruction-file-self-edits.md) — purged memory + instruction-file self-edits.
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [My recalled agent memory is stale or wrong](recalled-agent-memory-is-stale-how-to-reverify.md) — the other memory-integrity surface.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to detect when an AI agent self-edited its CLAUDE.md or AGENTS.md instruction file
- detect when an agent self-edited its CLAUDE.md instruction file
- agent rewrote its own AGENTS.md how to catch
- agent modified its own instruction file detect it
- catch an agent editing the rules it's supposed to follow
- self-modified CLAUDE.md by an agent how to detect
- agent tampered with its own guardrail file
- agent rewrote its own instruction file catch it
- detect tampering with CLAUDE.md by the agent
- agent edited the rules it should follow
- self-modified AGENTS.md how to detect

> The kernel is the part that doesn't believe the agents.
