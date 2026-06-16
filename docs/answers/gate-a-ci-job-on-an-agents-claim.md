# How do I gate a CI job on whether an AI agent's claim is actually backed?

> Run a DOS verdict in CI and let its exit code be the gate: `pip install dos-kernel`, then `dos commit-audit` (or `dos verify`) over the commits about to land. The verdict IS the exit code — clean=0, unwitnessed=1, contract_error=2 — so the CI wrapper is deliberately thin. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

A PR description, a commit subject, a "done" comment — these are all written by the agent whose work you are checking, so none of them is evidence. The gate has to read the artifact the agent did not author. `dos commit-audit` reads a commit's subject *and* its own diff and asks whether the diff did the *kind* of thing the subject claimed; `dos verify` asks whether a declared phase actually shipped in git ancestry. Either way the verdict is the process exit code: `0` clean, `1` a claim its diff doesn't back (`CLAIM_UNWITNESSED` / `NOT_SHIPPED`), `2` a contract error. The CI step does nothing but install the kernel, run the verdict, and surface that exit code to the check status — your branch protection turns it into a merge gate.

The result is author-neutral and deterministic: a human's hollow claim is caught exactly like an agent's, with no LLM in the loop. A PR whose commit subject claims work its diff doesn't contain fails the check; a clean range passes; a `wip:`/`merge:` subject with no checkable claim abstains rather than firing a false block.

## The evidence

The gate reads what git wrote, never what the author wrote in the subject or the PR. The wrapper is thin on purpose — the load is in the kernel verdict, not the YAML:

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The CI step shells the kernel and propagates its exit code as the check status | exit codes: clean=0, unwitnessed=1, contract_error=2 | the commit diff git authored, read by `dos commit-audit` / `dos verify` | [`verify-action/action.yml`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/action.yml) |
| One `uses:` line wires the gate; branch protection turns the exit code into a merge block | a single required status check (`dos-verify`) | GitHub's required-check setting (the PEP), gated on a verdict it did not compute | [`verify-action/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/README.md) |
| The same gate exists for the population a GitHub Action never reaches | one `include:` line; `GIT_DEPTH: "0"` so the audit reads full ancestry | the merge-request range git resolved (`base..HEAD`), audited from outside the loop that wrote it | [`gitlab-ci/dos-verify.gitlab-ci.yml`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/gitlab-ci/dos-verify.gitlab-ci.yml) |

The verdict is computed by the kernel; the CI provider's required-check setting is what actually blocks the merge. DOS decides, your repo's settings enforce.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos commit-audit --workspace . "$BASE..$HEAD"
```

When the range carries a hollow claim — a `fix(calc): resolve the off-by-one` whose diff touched only `README.md`:

```text
commit-audit sweep over 2 commit(s):
  checkable (made a concrete claim) : 1
  witnessed by their diff           : 0
  UNWITNESSED (claim vs diff)       : 1
  no checkable claim (abstained)    : 1
  DRIFT RATE (unwitnessed/checkable): 100.0%
JOB_EXIT_CODE=1   → the CI job is marked FAILED
```

When the range is clean (only a real code commit, or only subjects with no checkable claim):

```text
commit-audit sweep over 1 commit(s):
  checkable (made a concrete claim) : 0
  UNWITNESSED (claim vs diff)       : 0
  no checkable claim (abstained)    : 1
  DRIFT RATE (unwitnessed/checkable): 0.0%
JOB_EXIT_CODE=0   → the CI job passes
```

In a workflow you don't run this by hand — `verify-action/` is one `uses:` line, the GitLab template is one `include:` line, and the exit code becomes the check status.

## What this does — and does not — certify

The gate catches the **hollow claim**: a `fix:` that touched only a README, an `--allow-empty "shipped"`, a "tests pass" commit that deleted the assertions. It does **not** witness **correctness** — `commit-audit` grades whether the diff did the *kind* of thing the message claimed, never whether the code is *right*. Keep your test job; this sits *beside* it, not in place of it. And it **abstains** (exit 0, never a false block) on a `wip:`/`merge:` commit or any subject with no checkable claim — its credibility is that it refuses to fire when it cannot ground a verdict.

One operational note carries the whole thing: the audit reads git **ancestry**, not a shallow tip. Check out with full history (`fetch-depth: 0` on GitHub, `GIT_DEPTH: "0"` on GitLab) or a deep enough MR audits against a hole.

## Sources / reproduce

- [`verify-action/action.yml`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/action.yml) — the GitHub composite action: shells `dos commit-audit` / `dos verify`, surfaces the exit code as the check status.
- [`verify-action/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/README.md) — how to wire the gate into a workflow (one `uses:` line, then make it a required check).
- [`gitlab-ci/dos-verify.gitlab-ci.yml`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/gitlab-ci/dos-verify.gitlab-ci.yml) — the GitLab CI template, for the population the GitHub Action never reaches.
- [`gitlab-ci/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/gitlab-ci/README.md) — the GitLab wiring doc, with the catalog-component and raw-include forms.
- [Does the commit message match what changed?](does-the-commit-message-match-what-changed.md) — the verdict this gate runs, explained at the single-commit level.
- [How to verify an AI agent actually did the work](how-to-verify-an-ai-agent-actually-did-the-work.md) — the same distrust thesis, applied by hand instead of in CI.
- [FAQ: How do I verify an AI agent actually did what it claims?](../FAQ.md#how-do-i-verify-an-ai-agent-actually-did-what-it-claims)

## Also asked as

- How do I make CI fail when an AI PR claims more than it changed?
- Can I block a merge if the commit message doesn't match the diff?
- How do I add a GitHub Action that checks an agent's claim against its commit?
- Is there a GitLab CI gate for AI-generated commits that over-claim?
- How do I gate a required status check on a verified-by-DOS verdict?
- Stop an agent from merging a "fix" that only touched the README — in CI?
- How do I enforce that a declared phase actually shipped before a merge?

> The kernel is the part that doesn't believe the agents.
