# Deterministic pre-commit hook vs an agent skill — which one actually enforces the rule

> A rule in the prompt is advice the agent can ignore; a rule in a deterministic
> gate is enforcement it can't. `pip install dos-kernel`, then `dos commit-audit`
> as an exit-code gate. The PyPI name is `dos-kernel` — the bare `dos` package is
> an unrelated squatter; never install that.

## The short answer

A "skill", a CLAUDE.md rule, or a system-prompt instruction tells the agent what
to do — and the agent, under pressure, can talk itself past it. A deterministic
gate doesn't ask the agent; it reads an artifact the agent didn't author and
returns an exit code a pipeline acts on. The difference shows up exactly when it
matters: when the agent *wants* to skip the rule. `dos commit-audit` as a
`pre-push` hook, a CI step, or an aider `--test-cmd` blocks a commit whose subject
its diff doesn't back — regardless of what the agent intended. Put the guardrail
in the repo, not the prompt, and "enforced" stops depending on the agent's
cooperation.

## The evidence

Same proposer, same tasks — one arm follows its own judgment, the other is gated
by an un-authored measurement. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The self-certified arm keeps over-claims the gated arm refuses | over-claims kept = **12** (self-judged, seed 0) vs **0** (gated) | a held-out measurement the proposer did not author | [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) |
| A text-believing gate is gamed every time; a witness-reading one never | text-believing **18 / 18 = 100.0%** vs witness floor **0 / 18 = 0.0%** | an OS exit code / git ancestry the attacker never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD || echo "blocked: subject not witnessed by diff"
```

The exit code is the enforcement — wire it into `pre-push`, CI, or your agent's
test command, and a violating commit cannot proceed no matter what the agent
"decided".

## What this does — and does not — certify

It certifies the rule is enforced by a **mechanism, not a request** — the gate's
exit code, read from an un-authored artifact, can't be argued with. It does not
make the rule itself correct; it ensures a correct rule is actually applied,
including the moment the agent would rather skip it.

## Sources / reproduce

- [`benchmark/improve_ablation/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/improve_ablation/RESULTS.md) — the keep-gate ratchet curve.
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to add a guardrail to a coding agent with no plugin system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) — the exit-code tier in practice.
- [FAQ: Does DOS work with Claude Code, Cursor, Codex, Gemini CLI, or other agent runtimes?](../FAQ.md#does-dos-work-with-claude-code-cursor-codex-gemini-cli-or-other-agent-runtimes)

> The kernel is the part that doesn't believe the agents.
