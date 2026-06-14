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

## Proven: the job's exact script, run in the template's own image (#73)

The raw-include template's `before_script` + `script` were run verbatim inside
the `python:3.12` image it pins, installing the **published** `dos-kernel` from
PyPI (the real consumer path — no local checkout), against a scratch repo whose
last commit is the canonical over-claim: a `fix(calc): resolve the off-by-one`
subject whose diff touched only `README.md`. The exit code IS the verdict, so
this is what a GitLab runner would mark on the job.

**Merge request carrying the over-claim** (`$CI_MERGE_REQUEST_DIFF_BASE_SHA..HEAD`)
— the gate fires and the job FAILS:

```text
$ pip install dos-kernel          # → Version: 0.26.0, from PyPI
$ dos commit-audit --sweep <base>..HEAD
commit-audit sweep over 2 commit(s):
  checkable (made a concrete claim) : 1
  witnessed by their diff           : 0
  UNWITNESSED (claim vs diff)       : 1
  no checkable claim (abstained)    : 1
  DRIFT RATE (unwitnessed/checkable): 100.0%
  unwitnessed: <sha of the fix: that touched only README>
JOB_EXIT_CODE=1   → GitLab marks the job FAILED
```

**A clean range** (only a real `feat(calc): sub()` code commit) — no false
positive, the job passes:

```text
$ dos commit-audit --sweep <base>..<real-code-commit>
commit-audit sweep over 1 commit(s):
  checkable (made a concrete claim) : 0
  UNWITNESSED (claim vs diff)       : 0
  no checkable claim (abstained)    : 1
  DRIFT RATE (unwitnessed/checkable): 0.0%
JOB_EXIT_CODE=0   → GitLab marks the job passed
```

This proves the engineering core end-to-end: the published package installs in
the pinned image, the job script resolves the MR range, and the exit-code-as-
verdict blocks an over-claiming MR while passing a clean one. **The one step
this cannot self-witness is the gitlab.com UI run** — seeing the red/green job
in a real GitLab pipeline needs a push to a GitLab project (operator auth); the
above is the same script that runs there, exercised in the identical container.
