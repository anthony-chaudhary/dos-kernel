# DOS for GitLab CI — the claim-vs-diff gate, two ways

The GitLab population never sees a GitHub Action. CI is the cheapest ambient
surface there is: installed once for a mundane reason, it then witnesses every
merge request's claims from *outside* the loop that wrote them. This directory
gives GitLab the same two gates `verify-action/` gives GitHub —
`dos commit-audit` (does a commit's diff do the kind of thing its message
claimed?) and the optional `dos verify` (did a stamped phase actually ship?) —
with the kernel's **exit code as the job status**. Deterministic, author-neutral,
no LLM, no network beyond the pip install.

It witnesses claim-vs-diff, **not correctness** — run your test job for that;
this sits beside it.

There are two delivery shapes; pick by how a consumer finds and pins you.

## 1. CI/CD Catalog component (searchable, semver-pinned) — `templates/dos-verify.yml`

A published [CI/CD Catalog](https://docs.gitlab.com/ee/ci/components/) component:
searchable in the catalog UI, semver-resolvable, with a typed `spec:inputs`
interface. Reference it with one `include` block:

```yaml
include:
  - component: $CI_SERVER_FQDN/<catalog-project-path>/dos-verify@1.0.0
    inputs:
      fail_on: unwitnessed     # or "none" to observe-only
      dos_version: "==0.25.0"  # pin a release for reproducible CI
```

Inputs (all optional, with defaults): `dos_version` (pip version spec, empty =
latest), `fail_on` (`unwitnessed` blocks / `none` observes), `workspace` (repo
root, `.`), `plan` + `phase` (set both to also run the did-it-ship verdict),
`stage` (default `test`), `image` (default `python:3.12` — the full image
carries git; `-slim` does not).

> The component is *published* from a GitLab project that mirrors this repo (the
> catalog lives on a GitLab instance; this source-of-truth repo is on GitHub).
> The `@version` you pin is the git tag that project released to the catalog.

## 2. Raw remote include (zero setup, branch-pinned) — `dos-verify.gitlab-ci.yml`

No catalog project needed — `include` the file straight off the raw URL:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/gitlab-ci/dos-verify.gitlab-ci.yml'
    # pin a release tag instead of master for reproducible CI
```

Override per project via CI/CD variables: `DOS_VERSION`, `DOS_FAIL_ON`,
`DOS_WORKSPACE`, `DOS_PLAN`/`DOS_PHASE`. The component form takes the same knobs
as typed `inputs` instead.

## The one pitfall, shared by both: `GIT_DEPTH`

The audit reads git **ancestry**, not a shallow tip. Both forms force
`GIT_DEPTH: "0"` (a full clone). GitLab's default shallow clone (20–50 commits)
amputates the evidence, and a merge request with more commits than the depth
would audit against a hole — the same reason `verify-action` checks out with
`fetch-depth: 0`. Do not "optimize" it away.

Make the `dos-verify` job required (Settings → Merge requests → pipeline must
succeed) and GitLab becomes the enforcement point for the kernel's verdict: DOS
decides, your project settings block.
