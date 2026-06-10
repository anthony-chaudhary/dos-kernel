# DOS verify-action — the claim-vs-diff gate at the PR boundary

> **The verdict, where the money is.** DOS's `verify` / `commit-audit` already
> produce a deterministic, author-neutral verdict whose *exit code IS the answer*.
> This Action routes that verdict to the one boundary the market already pays for —
> the pull request — and lets your branch protection turn it into a merge gate.
> (Design: [`docs/225`](../docs/225_the-ci-gate-consumer-the-verdict-at-the-pr-boundary.md).)

A composite GitHub Action that fails a PR when a commit's **claim** is not backed by
its **diff** (`dos commit-audit`) or a declared phase did not ship (`dos verify`).

## Quick start

```yaml
# .github/workflows/dos-gate.yml
name: dos-gate
on: [pull_request]
jobs:
  claim-vs-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }          # commit-audit needs git ancestry
      - uses: anthony-chaudhary/dos-kernel/verify-action@master   # pin a release tag for reproducible CI
        with:
          mode: commit-audit               # the default
          fail-on: unwitnessed             # block CLAIM_UNWITNESSED (or: none = observe-only)
```

With `mode: commit-audit` and no `range`, it audits the PR's `base..head`. To gate a
declared phase instead:

```yaml
      - uses: anthony-chaudhary/dos-kernel/verify-action@master
        with:
          mode: verify
          plan: AUTH
          phase: AUTH2
```

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `mode` | `commit-audit` | `commit-audit` (claim-vs-diff over a range/ref) or `verify` (a `(plan, phase)` shipped). |
| `range` | PR `base..head`, else `HEAD` | For `commit-audit`: a ref or `A..B` range. |
| `plan` / `phase` | — | For `verify`: the plan/series + phase ids. |
| `fail-on` | `unwitnessed` | `unwitnessed` blocks a `CLAIM_UNWITNESSED` / `NOT_SHIPPED`; `none` is observe-only (reports to the step summary, never blocks). |
| `workspace` | `.` | Repo root to verify against. |
| `dos-version` | latest | Pin the install, e.g. `==0.23.3`. |
| `install-from` | — (PyPI) | Override the install source: a local path (`.` = the checked-out repo) or a VCS URL (`git+https://github.com/anthony-chaudhary/dos-kernel`). Optional — the default resolves from PyPI; set it to gate with an unreleased tree. |

> **The live example.** The kernel's own repo is this Action's first consumer:
> [`.github/workflows/dos-gate.yml`](../.github/workflows/dos-gate.yml)
> runs both modes on every push with `install-from: "."` — gating with the tree
> being pushed, not a registry copy — and the resulting check is the README's
> live **verified by DOS** badge: the repo gates itself with the gate it ships
> (docs/112 §4, trust posture 2). A consumer repo needs no `install-from` at all
> (the default installs from PyPI); set it only to pin a fork or an unreleased
> tree.

The per-commit verdict table is written to the job's **step summary**. The Action's
exit code is the gate; make it a **required check** in branch protection to enforce.

## What it does — and deliberately does NOT — catch

It catches the **hollow claim**: a `fix:` that touched only a README, an
`--allow-empty "shipped"`, a "tests pass" commit that deleted the assertions. The
verdict is **deterministic** (no LLM, un-gameable, exact) and **author-neutral** (a
human's hollow claim is caught exactly like an agent's).

It does **not** witness **correctness** — `commit-audit` grades whether the diff did
the *kind* of thing the message claimed, never whether the code is *right* (Wall 3,
[`docs/204`](../docs/204_the-four-walls-where-verification-runs-out.md) §3). **Keep
your test job** — this sits *beside* it, not in place of it. And it **abstains**
(exit 0, never a false block) on a `wip:`/`merge:` commit or any subject with no
checkable claim: its credibility is that it refuses to fire when it cannot ground a
verdict.

It is a **PDP**: DOS computes the verdict and sets the exit code; *GitHub's
required-check setting* is the PEP that blocks the merge. Enforcement is yours,
opt-in, and visible.

## Local pre-commit

The same verdict fires before the push via the bundled
[`.pre-commit-hooks.yaml`](../.pre-commit-hooks.yaml):

```yaml
repos:
  - repo: https://github.com/anthony-chaudhary/dos-kernel
    rev: v0.23.4                        # the latest release tag
    hooks:
      - id: dos-commit-audit            # block; or dos-commit-audit-warn for observe-only
```
