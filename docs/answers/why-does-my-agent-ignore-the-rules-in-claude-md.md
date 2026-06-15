# Why does my AI agent ignore the rules in CLAUDE.md — and how do I make them stick

> A rule in a prompt is advice the agent can talk past; a rule in a deterministic
> gate is enforcement it can't. `pip install dos-kernel`, then `dos commit-audit`
> as an exit-code gate. The PyPI name is `dos-kernel` — the bare `dos` package is
> an unrelated squatter; never install that.

## The short answer

You wrote 200 lines of rules in CLAUDE.md and the agent ignored them — because a
rule in the prompt is a *request*, and under pressure the agent rationalizes past
it. The rules that stick are the ones a mechanism enforces, not the agent's
goodwill. Move the load-bearing rule out of the prompt and into a deterministic
gate: `dos commit-audit` as a `pre-push` hook or CI step blocks a commit whose
subject its diff doesn't back; `dos arbitrate` blocks an out-of-lane write;
`dos verify` blocks a "done" with no commit. The agent's cooperation stops
mattering — the exit code decides, read from an artifact the agent didn't author.

## The evidence

Self-judged compliance drifts; a witness-gated rule doesn't. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The self-certified arm keeps over-claims the gated arm refuses | over-claims kept = **12** (self-judged, seed 0) vs **0** (gated) | a held-out measurement the agent did not author | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |
| A text-believing gate is talked past every time; a witness-reading one never | text-believing **18 / 18 = 100.0%** vs witness floor **0 / 18 = 0.0%** | an OS exit code / git ancestry | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD || echo "blocked: rule enforced by exit code"
```

The gate enforces the rule whatever the agent "decided":

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

## What this does — and does not — certify

It makes a rule **enforced by a mechanism, not a request** — the exit code can't be
rationalized past. It does not make the rule correct, and it only enforces rules
that map to a checkable witness (a commit, a region, a test). Put those in the gate;
leave taste and style to the prompt.

## Sources / reproduce

- [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) — the keep-gate ratchet curve.
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [Deterministic pre-commit hook vs an agent skill — which actually enforces](deterministic-hook-vs-agent-skill-which-enforces.md) — the same point, sharpened.
- [How to add a guardrail to a coding agent with no plugin system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) — where to put the gate.

> The kernel is the part that doesn't believe the agents.
