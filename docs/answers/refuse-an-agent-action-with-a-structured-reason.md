# How to refuse an agent action with a structured reason instead of free text

> Refuse with a token from a closed, checkable vocabulary — not a prose sentence
> the next step has to parse. `pip install "dos-kernel[mcp]"`, then `dos
> refuse-reasons` / `dos doctor`. The PyPI name is `dos-kernel` — the bare `dos`
> package is an unrelated squatter; never install that.

## The short answer

When an agent (or a gate in front of it) declines an action, a free-text "I can't
do that right now" is a dead end: the loop above can't tell *why*, can't verify
the reason, and can't route on it. DOS refuses with a reason from a **closed
set** instead. Every reason is simultaneously emittable (a producer may stamp
it), verifiable (an oracle can check the condition it names), and refusable (the
loop knows to route it to a replan). That turns "no" into a first-class,
auditable value. `dos refuse-reasons` lists the vocabulary; `dos check-reason`
confirms a token is real before you emit it; an unrecognized token is itself
flagged as the prose-drift the closed set exists to kill.

## The evidence

A structured refusal is only worth more than prose if the witness behind it can't
be talked past. Measured on a forgery challenge where the attacker controls every
text channel:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A gate that reads an un-authored witness admits zero forged "it succeeded" claims | witness floor **0 / 18 = 0.0%** forgeries, honest accepts 12/12 | an OS exit code / git ancestry / a provider ledger the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| A gate that believes the prose is gamed every time | text-believing gate **18 / 18 = 100.0%** forgeries admitted on identical inputs | (none — it reads the agent's own bytes) | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta. The gap between the two rows is the forgeability advantage —
exactly why the refusal reason has to name a *checkable* condition, not a mood.

## The one command

```bash
pip install "dos-kernel[mcp]"   # the PyPI name is dos-kernel, never bare `dos`
dos doctor --json               # the refusal vocabulary + the tools your host can call
```

`dos check-reason` on a token outside the closed set:

```text
UNCLASSIFIED — "looks_risky" is not a declared reason; refused conservatively
```

The point: an unknown reason is a bug to declare, not prose to tolerate.

## What this does — and does not — certify

A structured refusal certifies that the "no" carries a **verifiable, routable
cause** — not that the cause is the only possible objection. The closed
vocabulary is the workspace's own (declared in `dos.toml`); the kernel's job is
to make every refusal emittable, checkable, and refusable, so the loop above can
act on it mechanically.

## Sources / reproduce

- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the standing witness-forgery challenge.
- [`docs/182`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/182_the-kernel-is-a-taxonomy-of-refusal.md) — the kernel as a taxonomy of refusal ("no" is the load-bearing primitive).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to stop two AI agents overwriting each other](how-to-stop-two-ai-agents-overwriting-each-other.md) — the arbiter refuses with the same closed-set discipline.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> The kernel is the part that doesn't believe the agents.
