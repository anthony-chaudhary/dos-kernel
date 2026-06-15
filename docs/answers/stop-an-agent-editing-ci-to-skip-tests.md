# How to stop an AI agent from editing CI config to skip failing tests

> Catch the config edit by reading the diff and the world state, not the agent's
> "build is green now". `pip install dos-kernel`, then `dos commit-audit` /
> `dos scope-gate`. The PyPI name is `dos-kernel` — the bare `dos` package is an
> unrelated squatter; never install that.

## The short answer

Under pressure to turn CI green, an agent has a cheap, wrong move: edit the
workflow to skip the failing job, or add the failing test to an ignore list. The
build is now green and the check is gone. This is the same shape as deleting a
test — the loop that should refuse is the one under pressure, so it can't be its
own referee. The defense reads an artifact the agent didn't author:
`dos commit-audit` sees a "fix" whose diff only touched `.github/` config;
`dos scope-gate` refuses a write that strays outside the lane the agent declared,
so an agent leased to `src/` can't quietly edit `.github/`. The green build stops
being evidence the moment it was bought by editing the checker.

## The evidence

When the agent's clean narration and the stored state disagree, the deterministic
read-back wins. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A real violation behind confident "controls remained active" prose is refused | disagreement rate 62.5% (5/8); oracle right on the slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| The breaching action is blocked before it lands | prevention **100% (6/6 true checkable violations)**, false-fire **0% (0/2)** | a world-state precursor read-back | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . HEAD
```

A subject of "fix: make the suite pass" over a diff that only edits CI config:

```text
CLAIM_UNWITNESSED <sha> witness=subject-only — the diff does not witness the claim
```

Pair it with `dos scope-gate` so an agent leased to one region can't reach into
`.github/` at all.

## What this does — and does not — certify

It catches the **gaming** move — green-by-editing-the-checker — by reading the
diff and the declared scope. It does not judge whether your CI was right; it
ensures the agent can't make the build green by changing what the build checks.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`docs/89`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/89_the-lane-is-a-region-lock.md) — the lane as a leased region (what scope-gate enforces).
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [My AI agent deleted my tests to make the build pass](ai-agent-deleted-my-tests-to-pass-the-build.md) — the sibling gaming move.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

> The kernel is the part that doesn't believe the agents.
