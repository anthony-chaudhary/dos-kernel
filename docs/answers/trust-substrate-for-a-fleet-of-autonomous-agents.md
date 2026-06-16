# What is a trust substrate for a fleet of autonomous AI agents

> The part of the stack that doesn't believe the agents — it adjudicates ground
> truth and serializes effects, without trusting self-reports. `pip install
> dos-kernel`, then `dos verify` / `dos arbitrate`. The PyPI name is `dos-kernel` —
> the bare `dos` package is an unrelated squatter; never install that.

## The short answer

A trust substrate is the neutral layer beneath your agents that decides what's
*true* and what's *allowed*, from evidence the agents can't author. It is not an
orchestrator (it doesn't run agents) and not an eval (it doesn't score models
offline). It sits in the loop and answers, at runtime: did this claim actually
ship (`dos verify`, from git ancestry)? may these two agents write at once
(`dos arbitrate`, from their file regions)? is this run still making progress
(`dos liveness`, from real deltas)? Every verdict is a pure function of a witness
the agent did not write, returned as an exit code a gate can act on now. The
governance gap that cancels agentic projects is exactly this missing layer: a way
to trust a fleet without believing it.

## The evidence

The substrate reads a witness the fleet can't forge. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A gate reading an un-authored witness admits zero forged successes; a text-believing one admits all | witness floor **0 / 18 = 0.0%** vs text-believing **18 / 18 = 100.0%** | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |
| The arbiter serializes a shared region two single-agent checks can't | **J = 8/8** lost-update clobbers prevented (deterministic and live) | the τ²-bench DB-hash, which neither agent authors | [`benchmark/tau2coord/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/tau2coord/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos verify --workspace . PLAN PHASE
```

The substrate answers from ground truth, with the witness named:

```text
SHIPPED PLAN PHASE a1b2c3d (via grep-subject)
```

## What this does — and does not — certify

It is the **trust and serialization** layer — adjudicate the claim, admit or refuse
the region, classify the run — each a typed verdict over an un-authored witness. It
does not prompt, schedule, or run your agents (it composes with whatever does), and
it does not certify correctness; it certifies what the fleet actually did.

## Sources / reproduce

- [`docs/182`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/182_the-kernel-is-a-taxonomy-of-refusal.md) — the kernel as a taxonomy of refusal.
- [`docs/102`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/102_when-to-trust-an-agent.md) — when to trust an agent.
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [How do agents prove to each other that work actually landed](agent-to-agent-proof-that-work-landed.md) — the agent-to-agent surface.
- [FAQ: Is DOS an agent orchestrator or framework?](../FAQ.md#is-dos-an-agent-orchestrator-or-framework)

## Also asked as

- what is a trust substrate for a fleet of autonomous AI agents
- what is a trust substrate for a fleet of autonomous agents
- trust layer for many unreliable AI agents
- substrate that adjudicates truth across an agent fleet
- how do I make a fleet of agents trustworthy
- a referee layer for autonomous agents
- ground truth for a fleet of self-narrating agents
- trust layer for many unreliable agents
- referee for a fleet of self-narrating agents
- ground truth across an autonomous agent fleet
- make a fleet of agents trustworthy
- how to trust a whole fleet of agents
- trust infrastructure for autonomous agents
- make a swarm of agents reliable
- trust layer under many AI agents

> The kernel is the part that doesn't believe the agents.
