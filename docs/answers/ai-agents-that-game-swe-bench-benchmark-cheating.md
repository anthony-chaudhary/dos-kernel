# AI agents that game SWE-bench — how benchmark cheating works and how to catch it

> A grader that reads the agent's output can be gamed; one that reads an effect
> the agent didn't author can't. `pip install dos-kernel`, then `dos reward` /
> `dos commit-audit`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

Benchmark cheating is an agent optimizing the *score* instead of the *task* —
reading the hidden test, hard-coding the expected output, or claiming a pass it
didn't earn. It works whenever the grader trusts something the agent controls. The
defense is the same in a benchmark and in production: score the result against a
witness the agent did not author. `dos reward` admits a result only when an
env-authored witness (a test exit code on a held-out check, a state read-back)
confirms it; `dos commit-audit` confirms a claimed fix against its own diff. A
text-believing grader is gamed on every forgery attempt; a witness-reading floor
admits none of them — the gap *is* the cheating surface, measured.

## The evidence

Same attacker text into both gates — one reads the prose, one reads the witness.
Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A witness floor admits zero forged "it passed" claims; a text-believing grader admits all | witness floor **0 / 18 = 0.0%** vs text-believing **18 / 18 = 100.0%** | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| The self-judged pool banks the cheat; the witness-gated pool purges it | final pool poison **5% (2/40)** self-judged vs **0/35** witness-gated | the subprocess test exit code | [`benchmark/poisoned_pool/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/poisoned_pool/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD        # did the "fix" actually change the code?
```

A claimed pass whose diff is a hard-coded output, not a real fix, fails the audit:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

## What this does — and does not — certify

It certifies a result is **backed by an un-authored effect** — a real diff, a
held-out test exit code — not the agent's claim of a pass. It does not enumerate
every cheating strategy; a forgery with no checkable witness abstains and is
contained by typing, not refused. The guarantee: the score can't be bought by
talking.

## Sources / reproduce

- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/poisoned_pool/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/poisoned_pool/RESULTS.md) — the self-judged-vs-witness-gated pool study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Reward hacking in LLM coding agents](reward-hacking-in-llm-coding-agents.md) — the same defense in a training loop.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- AI agents that game SWE-bench how to catch benchmark cheating
- AI agents that game SWE-bench how to catch the cheating
- benchmark cheating by coding agents
- how do agents overfit or game SWE-bench
- detect an agent gaming a coding benchmark
- SWE-bench gaming what it looks like and how to stop it
- agents memorizing benchmark answers how to catch
- agents overfitting a coding benchmark
- detect benchmark gaming by an AI agent
- SWE-bench results that are too good to be real
- catch an agent memorizing benchmark answers

> The kernel is the part that doesn't believe the agents.
