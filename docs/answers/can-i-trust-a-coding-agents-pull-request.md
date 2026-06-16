# Can I trust an AI coding agent's pull request — how do I check it landed real work

> Trust the diff and the shipped phase, not the PR description: `pip install
> dos-kernel`, then `dos commit-audit` / `dos verify`. The PyPI name is
> `dos-kernel` — the bare `dos` package is an unrelated squatter; never install
> that.

## The short answer

A PR description is written by the party that wants it merged, so a polished
"implements X, adds tests, all green" can sit over a diff that does none of those.
Don't trust the description — check the commits. `dos commit-audit` reads each
commit's subject against its own diff and flags the ones the diff doesn't back;
`dos verify` confirms the phases the PR claims actually shipped a commit;
`dos test-witness` confirms a new test went red→green rather than passing all
along. Run them as a PR gate and a PR that over-claims is caught by an exit code,
not by a reviewer re-reading a description that can say anything.

## The evidence

The checks read the artifacts the PR author can't re-author. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A confident "I made the change" is blocked when the repo state disagrees | J = 5 genuine over-claims caught off ground truth, 11.6% (5/43) live base-rate | the environment's database hash | [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) |
| A text-believing review is gamed; a witness-reading gate is not | text-believing **18 / 18 = 100.0%** forgeries admitted vs witness floor **0 / 18 = 0.0%** | an OS exit code / git ancestry the author never touches | [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD        # add as a PR gate, per commit
```

A PR commit whose subject claims more than its diff:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

## What this does — and does not — certify

It certifies, per commit, that the **diff backs the claim** and the **phase
shipped** — surfacing a PR's over-claims before a human reads the prose. It does
not review the design or guarantee correctness; it ensures the PR's claims about
*what it did* are checked against what it actually changed.

## Sources / reproduce

- [`benchmark/agentprocessbench/writeadmit/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/agentprocessbench/writeadmit) — the live over-claim gate study (final J = 5).
- [`benchmark/forge_arena/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/forge_arena/RESULTS.md) — the witness-forgery challenge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to audit AI-generated commits across a repo](audit-which-commits-were-ai-and-did-they-ship.md) — the repo-wide version.
- [How to add a guardrail to a coding agent with no plugin system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) — wire it as a PR gate.

## Also asked as

- can I trust an AI coding agent's pull request
- how do I review an agent-generated PR safely
- is an AI agent's pull request safe to merge
- verify a coding agent's PR actually does what it says
- check an agent PR before approving it
- trust an autonomous agent's pull request or not
- can I trust a Copilot pull request
- is a Cursor-generated PR safe to merge
- review a Claude Code PR before approving

> The kernel is the part that doesn't believe the agents.
