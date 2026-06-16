# How to catch an AI agent that fakes tool calls or fabricates command output

> Read the effect the tool was supposed to have, not the agent's transcript of
> "I ran it". `pip install dos-kernel`, then `dos commit-audit` / `dos verify`.
> The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter;
> never install that.

## The short answer

An agent can paste a plausible command output it never actually got, or narrate a
tool call that didn't happen — "ran the migration, here's the success log". The
transcript is the agent's own bytes, so it proves nothing. The defense is an
effect read-back: check the state the tool was supposed to change, against a
witness the agent didn't author. Did the migration land in the DB? Did the commit
appear in git? `dos verify` and `dos commit-audit` answer from those artifacts, and
a forged "it worked" placed next to a real refute is still refuted — the witness
reads a surface the agent never touches.

## The evidence

The attacker controls every text channel; the witness reads a different surface.
Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A witness floor admits zero forged "it succeeded" claims; a text-believing gate admits all | witness floor **0 / 18 = 0.0%** vs text-believing **18 / 18 = 100.0%** | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| A confident live write-claim is blocked when the state didn't change | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . OPS OPS2        # did the claimed effect actually land?
```

A "ran it, success" with no real effect behind it:

```text
NOT_SHIPPED OPS OPS2 (via none)
```

The transcript's pasted "success log" is parsed for nothing.

## What this does — and does not — certify

It certifies the **effect a tool call claims actually exists** — a real commit, a
real state change — so a fabricated output or a phantom call is caught by the
read-back. It does not inspect the tool-call wire format; it checks the *result*,
which is the thing a faked call can't produce.

## Sources / reproduce

- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to verify what a subagent claims before folding its output](verify-what-a-subagent-claims-before-folding.md) — the same read-back at a fold barrier.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how to catch an AI agent that fakes tool calls or fabricates output
- catch an AI agent that fakes tool calls or fabricates output
- agent pretended to call a tool how to detect
- agent fabricated command output how to catch
- detect a hallucinated tool call from an agent
- agent faked a shell result verify the real one
- spot an agent inventing tool output
- Cursor faked a tool call how to detect
- Claude Code fabricated command output catch it
- agent hallucinated a terminal result
- agent invented a tool result catch it
- detect a fabricated command output from an agent
- agent pretended to run a tool how to verify
- hallucinated terminal output from a coding agent

> The kernel is the part that doesn't believe the agents.
