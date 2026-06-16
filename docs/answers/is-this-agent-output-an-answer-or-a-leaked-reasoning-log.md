# How do I tell if an agent's output is a real answer or a leaked reasoning log?

> Don't measure its length or trust that `refused=False` — run the output through a shape verdict that fires on the marker a leaked chain-of-thought carries. `pip install dos-kernel`, then `dos answer-shape --text '<the output>'`: NON_ANSWER (exit 3) is a structural disqualification. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

A model's `refused=False` flag is a self-report, and self-reports are not evidence — the agent that emitted the blob is the same one telling you it's an answer. Check the artifact instead: the output's *shape*. A leaked reasoning log is structurally distinguishable from a delivered answer, and the tell is a marker (`<thinking>`, "let me think", an enumerated `step 1:` process dump, a raw `tool_call` line, a pasted traceback), not a length. The motivating case (docs/156) was a third-party RAG app that shipped a 5,780-char leaked chain-of-thought as its "answer" with `refused=False`; every *number* inside it was grounded, so a numeric gate waved it through. Length alone never catches that — a 5,780-char log is longer than most real answers. The marker does.

`dos answer-shape` is the verdict: `ANSWER_SHAPED` / `NON_ANSWER` / `INDETERMINATE`, and the verdict *is* the exit code. It judges shape, never correctness — a wrong-but-shaped answer is still `ANSWER_SHAPED`, because "is this the right answer?" has no independent witness and goes to a JUDGE or HUMAN, not a deterministic oracle. What the oracle decides is the mechanically-checkable part: is this the *kind* of thing that could be an answer, or is it a process artifact pasted as one.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| A leaked reasoning log shipped as an "answer" with `refused=False` is caught by a SHAPE marker, never by its length | the q_025 motivating case: a **5,780-char** leaked chain-of-thought, refused=False; length never disqualifies it, the `<thinking>`/"let me think" marker does | the candidate text's own structural signature, matched by a declared marker the emitting agent did not author | [`src/dos/answer_shape.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/src/dos/answer_shape.py) |
| The verdict is the exit code, so a shell/CI step branches without re-parsing prose | ANSWER_SHAPED **0**, NON_ANSWER **3**, INDETERMINATE **4**, contract_error **2** (the argparse usage code) | an OS exit code emitted by the kernel, not a flag the agent set on its own output | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |

The markers are policy, not hardcode: the kernel ships a generic cross-domain default (a fenced reasoning block, narrated chain-of-thought, a tool-call dump, a bare refusal, a stack trace) and lets a host declare its own. The kernel carries the fold and the floor; the host carries the signatures.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos answer-shape --text '<thinking>x</thinking>'
```

```text
NON_ANSWER  (length=23, matched='<thinking>')
output matched the non-answer signature '<thinking>' — a process/CoT-log,
bare refusal, or tool dump pasted as the answer (docs/156 §4)
```

Exit code `3` — the output is structurally disqualified; do not ship it as an answer. An `ANSWER_SHAPED` output exits `0`; an output the policy can't decide on shape alone exits `4` (INDETERMINATE — abstain to a JUDGE/HUMAN).

## What this does — and does not — certify

It certifies **shape**: that the output is, or is not, the *kind* of artifact that could be an answer — non-empty, above the viability floor, and matching no disqualifying marker. It explicitly does NOT certify correctness or relevance. An `ANSWER_SHAPED` verdict means "shaped like an answer," not "a right answer"; a confidently-wrong but well-formed paragraph passes the shape floor and the semantic residue ("is it actually a good answer?") goes to a JUDGE (advisory, fail-to-abstain) or a HUMAN. When the policy can't disqualify on shape, the verdict is `INDETERMINATE` — the abstain floor — never a false `ANSWER_SHAPED`. The fail-safe direction is to under-disqualify: a broken host pattern degrades to "not matched," never to a crash.

## Sources / reproduce

- [`src/dos/answer_shape.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/src/dos/answer_shape.py) — the `classify` verdict, the generic non-answer markers, and the q_025 motivating case in the module docstring.
- [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) — the `dos answer-shape` verb and the exit-code map (0 / 3 / 4 / 2).
- [Why you can't trust a model to judge its own work](why-you-cant-trust-a-model-to-judge-its-own-work.md) — the same distrust thesis on the correctness question shape can't reach.
- [Verify what a subagent claims before folding](verify-what-a-subagent-claims-before-folding.md) — the fold-barrier rung that should reject a non-answer before it becomes another agent's premise.
- [FAQ: How is DOS different from agent evals or observability platforms?](../FAQ.md#how-is-dos-different-from-agent-evals-or-observability-platforms)

## Also asked as

- How do I detect a leaked chain-of-thought shipped as an answer?
- My agent pasted its reasoning log instead of an answer — how do I catch it?
- Is this output a real answer or just a thinking dump?
- How do I tell an answer from a tool-call dump or a traceback?
- Why does `refused=False` not mean the agent actually answered?
- Catch a non-answer that's grounded but isn't an answer
- How do I block an empty stub or a bare refusal from shipping as content?

> The kernel is the part that doesn't believe the agents.
