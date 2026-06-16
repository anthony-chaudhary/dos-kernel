# How do I tell if my AI-generated tests are real or just lying to me

> A "real" test fails when the code is wrong. You find out whether an
> AI-generated test is real — not lying — by running it against the code
> *without* the change and requiring it to fail: `pip install dos-kernel`, then
> `dos test-witness`. The PyPI name is `dos-kernel` — the bare `dos` package is
> an unrelated squatter; never install that.

## The short answer

The honest test of a test is one bit: **does it fail on a broken version of the
code?** People do this by hand with mutation testing — flip a `+` to a `-`,
re-run, see if the test catches it. The cheap one-mutation version is "would this
test fail if the function returned a hardcoded value?" If not, the test is
decorative.

`dos test-witness` mechanizes that bit. It takes the new test's outcome on the
tree *without* the change (baseline) and *with* it (candidate). A real test is
red→green: `DISCRIMINATES`, exit 0. A test that's green both ways is `VACUOUS`,
exit 3 — green theater. A test green before and red after is `REGRESSIVE`. The
verdict is the exit code, so "is this test real?" becomes a gate, not a judgment
call.

## The evidence

When the agent's confident "tests look good" and the world's stored state
disagree, the deterministic read-back wins. Measured:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A real failure behind confident clean prose is refused | disagreement rate **62.5% (5/8)**; oracle right on the disagreement slice **5 / 5 (100%)** | the stored world-state effect, not the trajectory prose | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |
| Judge accuracy vs the deterministic floor on the same slice | judge **37.5%**, oracle **100%** | a non-forgeable read-back, not the narration | [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) |

A **J** is a count of failures blocked off ground truth, never a downstream
outcome delta; the rate is a directional result on a faithful-minimal scenario
set, not a universal hit rate.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos test-witness --baseline fail --candidate pass
```

A real test — fails on the unchanged code, passes once the change is in:

```text
DISCRIMINATES, exit 0  (the witness)
```

A lying test — green no matter what:

```text
dos test-witness --baseline pass --candidate pass  →  VACUOUS, exit 3  (witnesses NOTHING)
```

A narrated red→green doesn't count: pass `--forgeable` and the agent's own
"it failed before, passes now" collapses to `ABSTAIN`, exit 6. The only path to
`DISCRIMINATES` is a test that actually failed on the tree it didn't touch.

## What this does — and does not — certify

This answers "is the test real?" — does it discriminate a correct tree from an
incorrect one. It does **not** answer "is it the *right* test?" — whether the
assertion captures the behavior you meant. That residue goes up the ladder to a
JUDGE or a HUMAN. It is the cheap, deterministic floor under the more expensive
question, and it catches the lie that matters most: a test that can't fail.

## Sources / reproduce

- [`benchmark/constraintviol/RESULTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/constraintviol/RESULTS.md) — the world-state floor under a gameable judge.
- [`benchmark/BENCHMARKS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/benchmark/BENCHMARKS.md) — every benchmark, with a $0 offline arm.
- [How to stop an AI agent from making fake tests](stop-ai-making-fake-tests.md) — the hub page for this family.
- [Mutation testing vs test-witness for AI tests](mutation-testing-vs-test-witness-for-ai-tests.md) — how the two relate.
- [Why you can't trust a model to judge its own work](why-you-cant-trust-a-model-to-judge-its-own-work.md) — why re-reading the test isn't evidence.
- [FAQ: Can't the agent just game the verdict?](../FAQ.md#cant-the-agent-just-game-the-verdict)

## Also asked as

- how do I tell if my AI-generated tests are real or just lying
- how do I tell if my AI-generated tests are real
- are my AI tests real or just lying to me
- check whether AI-written tests actually verify behavior
- tell real AI tests from fake ones
- are these agent tests genuine or hollow
- validate that AI-generated tests do real work
- are my AI tests genuine or hollow
- check whether AI-written tests verify behavior
- validate that agent tests do real work

> The kernel is the part that doesn't believe the agents.
