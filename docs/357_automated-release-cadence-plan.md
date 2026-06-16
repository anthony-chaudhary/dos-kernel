# docs/357 — the automated release cadence (decide → cut → tag-after-green → publish)

> **Status:** shipped + zero-touch live. `scripts/release_decide.py` (the
> decision + semver auto-rule), `scripts/release_cut.py` (the mechanical bump +
> notes + commit), and `.github/workflows/release-cadence.yml` (the cron) are in
> the tree with 24 witness tests green. The one piece NOT reachable from a code
> edit — the `pypi` Environment's required-reviewer toggle for true zero-touch
> upload — **has been removed by the operator (2026-06-16)**, so a cron tick now
> carries a release all the way to PyPI with no human in the loop (§4 keeps the
> record of that switch).
>
> **Phase stamp.** Phase `P1` (the decide→cut→tag-after-green→publish cadence)
> shipped as `90ea637`; `dos verify --workspace . docs/357 P1` resolves it via
> this commit's `(docs/357 P1)` trailer — the oracle does not read the `Status:`
> sentence above (it is a self-report, by design).

## Why this exists

Every release is a human running the `/release` skill: a person reads the
commits since the last tag, judges whether they are a coherent shippable unit,
picks the semver level, and cuts the tag. That manual gate **stalls**. The proof
this plan was born from: **240+ commits sat unreleased behind `v0.26.0`** while
the package version stayed at `0.26.0` — a backlog of shippable work the cadence
intent in the release skill ("ship whenever the gates are green; several per
week is healthy") never enacted, because nothing fired it.

The *pipeline* was already complete and well-gated — pushing a `vX.Y.Z` tag
fires `publish.yml`, which builds per-platform wheels, refuses any SHA without a
green `ci.yml` run on it, runs a TestPyPI rehearsal + install-smoke, and uploads
via OIDC Trusted Publishing. What was missing was the **cadence trigger**: the
periodic decision "is there releasable work, are the gates green, and if so cut
the tag." This plan adds exactly that, and nothing else — it does not touch the
publish pipeline's own gates.

## The shape — decide and cut are split, the workflow drives

Two scripts plus one workflow, all **dev/release tooling** (they operate ON the
package, are never imported BY `dos.*` — the kernel/tooling boundary in
`CLAUDE.md` stays intact). They reuse the existing release scripts rather than
re-deriving anything.

### 1. `scripts/release_decide.py` — the decision (automated `/release` Step 0+2)

Read-only. Emits a JSON verdict and an exit code (0 = release, 2 = hold, 1 =
error). It reuses `release_context.py` for the git+CI+drift digest, then applies:

- **The should-release predicate** — ALL must hold, else HOLD with the failing
  gate named in `blockers`:
  - at least one commit since the last tag (`NOTHING_TO_SHIP` otherwise);
  - the trunk CI base is green (`ci_on_head.status == "green"`) — a release cut
    on a red base inherits the red and the publish ci-green witness refuses it
    (docs/295 P1). An `unknown` state (gh offline) is a soft pass unless
    `--require-ci-green` is set;
  - every workflow file parses (`workflows_parse_ok.ok` — a broken one fails CI
    in 0s, the ci.yml#L300 class);
  - the version markers do not already disagree (`version_files.drift`).
- **The semver auto-rule** — the judgment a human makes in `/release` Step 2,
  encoded: classify each commit subject's conventional-commit prefix and take the
  HIGHEST level across the range. A `!` bang or a `BREAKING CHANGE` body marker →
  major; a `feat` → minor; everything else (fix/docs/build/chore/refactor/…, a
  bare `area:` prefix, an unknown subject) → patch. **Conservative by
  construction**: an unrecognized prefix counts as patch, never inflating the
  bump on a surprise.

### 2. `scripts/release_cut.py` — the mechanical cut (automated `/release` Steps 3–6)

Given a decided version, it does the deterministic git work and **nothing
irreversible** — no push, no tag:

1. `release_bump.py <version>` — the 7-marker lockstep bump. Its JSON report is
   the single source of truth for the file set the commit stages (never
   hardcoded — a future bump target is picked up automatically).
2. `build_plugin.py` — resync the generated `claude-plugin/skills/` mirror.
3. Draft `docs/releases/v<version>.md` if absent — front-matter matching the
   shape `release_context.prior_release_style` parses, body clustered by
   conventional-commit scope (machine-derived from the real range, honest about
   being auto-generated). **Never clobbers** an existing notes file (a racing
   skill/cadence run).
4. `release_dry_run.py --json` — the tag-last witness on the committed bytes. A
   non-zero verdict **resets the commit** so no version number is half-cut (the
   docs/295 fix-forward rule). `--skip-dry-run` lets the CI context run the
   suite itself instead.
5. `git commit` with an explicit **pathspec** built from the bump report + the
   notes — never `git add -A`, so a concurrent loop's in-flight edits are not
   swept into a release commit (the hot-tree discipline).

Output: a JSON manifest `{version, tag, commit_sha, paths, dry_run, …}`.

### 3. `.github/workflows/release-cadence.yml` — the cron

- `schedule: "17 6 * * 1,4"` (Mon+Thu 06:17 UTC — twice weekly, off-peak, a
  non-:00 minute so this repo's tick doesn't collide with the rest of a fleet)
  plus `workflow_dispatch` with a `dry_run` input (the live read-back arm).
- Repo guard `github.repository == 'anthony-chaudhary/dos-kernel'` (same as
  `publish.yml` — never auto-releases from a fork or the private archive).
- Decide → if HOLD, write the reason to the step summary and stop (the DOS
  silent-when-clear discipline; a too-frequent tick is a cheap no-op, not churn).
- If RELEASE → cut → leak-gate the commit → push master → **wait for ci.yml to
  go green on the new SHA** → mint + push the `vX.Y.Z` tag → create the GitHub
  release from the drafted notes. The tag push hands off to `publish.yml`.
- **Tag-after-green** (docs/295): a tag is a claim, CI is the witness; the tag is
  minted only once the witness is in. A red CI on the pushed commit tags
  *nothing* — the commit is on master, fix-forward applies, no version burned.
- Concurrency group (`cancel-in-progress: false`) so two ticks never race and a
  cut mid-flight finishes.

## §4 — the one switch not in this repo: true zero-touch to PyPI

The operator directive (2026-06-16) is that the cadence run **fully unattended
through to the PyPI upload** — automate the reversible decision *and* the
irreversible publish. Everything reachable from a code edit is shipped here. One
piece was **not** in any tracked file:

> **GitHub → repo Settings → Environments → `pypi` → remove "Required
> reviewers".**

> **DONE (operator, 2026-06-16).** The required-reviewer gate on the `pypi`
> environment has been removed. The path is now genuinely zero-touch: a cron
> tick decides → cuts → tags, and `publish.yml` builds, gates on its machine
> witnesses, and uploads to PyPI with **no human in the loop**. This §4 is kept
> as the record of the one web-config switch the automation depends on — if a
> future audit re-adds a reviewer to that environment, the cadence silently
> reverts to pausing for a click, and this is where to look.

That required-reviewer gate is what paused `publish.yml` for a human approval
click before the OIDC upload. It lives in GitHub *web* config, not a workflow
file, so a code edit cannot flip it — which is why it is named here rather than
wired. **Before** it was removed, the cadence was unattended up to the tag and
the publish paused for one click; **now** that it is removed, a cron tick carries
a release all the way to PyPI with no human.

This is honest about the trade and keeps the DOS discipline intact: removing the
HUMAN gate does **not** remove the MACHINE witnesses on the irreversible step.
`publish.yml` still refuses any SHA without a green `ci.yml` run (the ci-green
witness) and still runs the TestPyPI rehearsal + install-smoke read-back before
the real upload. The cadence automates *who pushes the button*, not *whether the
bytes were witnessed*.

## Proving it (DOS-style — evidence, not self-report)

The witnesses, weakest to strongest:

1. **Unit + workflow tests** (`tests/test_release_decide.py`,
   `tests/test_release_cut.py`, `tests/test_release_cadence_workflow.py`) — the
   semver auto-rule on synthetic ranges, the should-release gates on synthetic
   payloads, the file-set derivation, the notes drafter, and the workflow wiring
   (schedule + dispatch + repo guard + tag-after-green). 24 tests, green. Plus
   the existing `tests/test_workflow_yaml_parses.py` covers the new workflow.
2. **The live decision proof** — `release_decide.py --json` run against the real
   repo correctly *refuted* a release on the day this shipped: it HELD with
   `CI_BASE_RED` because the latest decisive trunk `ci.yml` run had failed. The
   cadence doing the right thing (not releasing on a red base) is the proof the
   gate works — a refute is a valid, recorded outcome, not a glossed pass.
3. **The dispatch dry-run** — after merge, `gh workflow run release-cadence.yml
   -f dry_run=true` decides + plans the cut and writes the verdict to the run's
   step summary (the env-authored read-back, not the script's self-report).
4. **`dos commit-audit`** on each cadence commit (subject vs diff) closes the
   loop — the kernel adjudicates its own cadence work.

## What this deliberately does NOT do

- It does not change `publish.yml` or its gates — the publish pipeline is
  complete; this only adds the trigger upstream of it.
- It does not remove the `pypi` required-reviewer gate (it can't — §4).
- It does not invent prose release notes — cadence notes are machine-derived
  from commit subjects, clustered by scope, and a human-drafted
  `docs/releases/v*.md` (from `/release`) is never overwritten.
- It does not force a release on a clock alone — every gate can turn a tick into
  a quiet no-op.
