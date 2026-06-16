# How does DOS fit into my CI/CD pipeline?

> DOS is not a CI/CD platform — it's the **trust floor** you add *inside* your
> pipeline. It verifies that a claim landed (`dos verify`), that a commit's message
> matches its diff (`dos commit-audit`), and that concurrent agents don't collide
> (`dos arbitrate`) — at the merge/gate boundary, as an exit code your branch
> protection already knows how to require. `pip install dos-kernel` (the PyPI name is
> `dos-kernel`; the bare `dos` is an unrelated squatter — never install that).

## The short answer

A CI/CD pipeline triggers work, builds, tests, gates, and deploys. DOS does **not**
own triggering, building, or deploying — those belong to your CI platform and deploy
engine. What DOS adds is the one thing a pipeline can't get from a green checkmark:
**a verdict the agent that did the work could not have forged.** A suite is green
because the tests it ran passed — not because the feature shipped, not because the
commit's `fix:` subject is true, not because a parallel agent didn't overwrite it.
DOS routes each of those to a deterministic exit code you drop in beside your test
job:

- **`dos commit-audit`** — does each commit's subject claim match its own diff? Catches
  the `--allow-empty "implement cache"`, the README-only `fix:`, the "tests pass" that
  deleted the assertions. Author-neutral; abstains (never blocks) on a `wip:`/`merge:`
  commit with no checkable claim.
- **`dos verify PLAN PHASE`** — did the phase actually land a commit, from git
  ancestry? "CI green" never stands in for "shipped" again.
- **`dos arbitrate`** — may two agents/jobs run concurrently, or do their file-trees
  collide? The merge-queue guarantee at the file-tree level.

It runs *after* the agent's loop ends, on a commit already written, and only blocks a
*merge* — so it can't perturb a passing run. That's why it's safe to require.

## The one integration

The package ships a composite GitHub Action and a pre-commit hook — no glue beyond
the exit code:

```yaml
# .github/workflows/dos-gate.yml in YOUR repo
jobs:
  claim-vs-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }     # commit-audit needs ancestry
      - uses: anthony-chaudhary/dos-kernel/verify-action@v1
        with:
          mode: commit-audit
          fail-on: unwitnessed         # block a commit whose claim its diff doesn't back
```

Or locally, before the push:

```bash
pip install dos-kernel
dos commit-audit HEAD        # exit 0 = clean/abstain, 1 = claim unwitnessed
dos verify --workspace . FEAT FEAT4   # did the feature's phase land? exit 1 if not
```

This repo runs its own gate on itself — the
[`verified by DOS` workflow](https://github.com/anthony-chaudhary/dos-kernel/blob/master/.github/workflows/dos-gate.yml)
is the badge, and the badge is the kernel's exit code over git ancestry it checked out
itself.

## Where each CI/CD concept lands

DOS sits *inside* a step the host triggered, *beside* the test/scan/build jobs, and
*at* the merge gate. The full map of ~140 industry CI/CD concepts onto DOS primitives —
what's native, what's a thin gap, and what's deliberately out of scope (canary routing,
runner autoscaling, the blue-green switch — those stay with your deploy engine) — is in
[docs/359, the CI/CD concept coverage map](../359_ci-cd-concept-coverage-map.md). A few
non-obvious ones:

| You want… | DOS primitive |
|---|---|
| Merge queue / deploy lock | `dos arbitrate` / `dos lease` (lane disjointness) |
| Circuit breaker / retry policy | `dos breaker` (failure-class counting) |
| Build provenance / attestation (SLSA) | `dos attest` / `dos verify-receipt` (portable signed receipt) |
| Drift detection / reconciliation | `dos reconcile` (claim × oracle, fail-closed) |
| Audit log / pipeline metrics | `dos observe` (the verdict journal) / `dos trace` |
| Coverage gate | `dos coverage` / `dos test-witness` (red→green) |

## What this does — and does not — certify

It certifies **presence and honesty**: the phase landed a commit, the commit's claim
matches its diff, a test actually exercised the change. It does **not** judge whether
the code is *correct* — keep your test and scan jobs; the DOS gate sits beside them,
not in place of them. Over-claiming would re-import the forgeability the kernel exists
to refuse.

## Sources / reproduce

- [docs/359 — the CI/CD concept coverage map](../359_ci-cd-concept-coverage-map.md) — every concept, routed to its DOS primitive or named out of scope.
- [docs/225 — the CI-gate consumer](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/225_the-ci-gate-consumer-the-verdict-at-the-pr-boundary.md) — why the PR boundary is the value-capture consumer.
- [CI passed but the feature isn't there — how do I catch that](ci-passed-but-the-feature-isnt-there.md) — the green-build-missing-feature gap.
- [How do I add a guardrail to a coding agent with no plugin system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md) — the exit-code-as-gate pattern.
- [The verify-action README](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/README.md) — the composite Action's inputs.
- [README](../../README.md) · [FAQ](../FAQ.md)

> The kernel is the part that doesn't believe the agents.
