---
name: release
description: Cut a versioned release of the DOS kernel — bump the version, draft release notes, commit, tag, push to master, and create a GitHub release. The tag push triggers the gated PyPI publish pipeline (publish.yml); the skill surfaces the run and its approval gate.
disable-model-invocation: false
user-invocable: true
allowed-tools: Read, Edit, Grep, Glob, Bash, Write
argument-hint: "[summary of changes] [--scope <theme-token>... (recommended)] [--whole-tree] [--lint-prefix]"
output_root: none
---

# Release — the DOS kernel

Semver: `major.minor.patch`. Patch = bug fix, minor = new feature, major = breaking.

> **DOS is a substrate, not an app.** This skill is the DOS-context adaptation of
> the `job` release skill. It is deliberately *thinner*: DOS single-sources its
> version from `pyproject.toml` (one marker, not four), ships **no** release-asset
> zip archive, screenshots, versioned-install snapshot, plan-state regeneration,
> apply-loop gate, or fanout/dispatch manifest. The dist artifacts — one wheel per
> OS/arch, each embedding its native `dos-hook` fast-path binary at `dos/_bin/`
> (docs/286), plus a pure-source sdist — are built by CI (`publish.yml` via
> `scripts/build_wheels.py`), **never by this skill locally**. The verification
> step is the kernel test suite + the truth syscall, not an apply pipeline. If you
> find yourself reaching for any of those job-only ceremonies, you're in the wrong
> repo's skill.
>
> **One exception — the plugin's native hook binaries.** The Claude Code plugin
> (`claude-plugin/`) bundles the compiled `dos-hook` fast-path binaries (docs/125
> GHF4), committed into `claude-plugin/bin/` so a marketplace install (a git clone)
> is a direct install. They are NOT a release asset and NOT in the wheel — they live
> in the git tree. Step 5.5 rebuilds them so a release never ships a stale binary
> against changed Go source.

**Trunk is `master`** (there is no `main` branch in DOS). Every `git push` in
this skill targets `master`. Master-direct is the deliberate branching model for
now (a trunk+lane-branch PR cutover is planned, evidence-triggered — not a `dev`
branch); this skill assumes it.

**This repo is PUBLIC and the tag is a publish trigger (the 2026-06-10 cutover).**
Three consequences every release run must respect:

1. **A push IS publication.** `origin` is the public `anthony-chaudhary/dos-kernel`.
   The machine-local pre-push hook (`.git/hooks/pre-push`) runs the leak scanner
   (`scripts/leak_scan.py`, a *gitignored* sync of the canonical
   `../dos-private/tools/leak_scan.py`) and is **fail-closed**: a hit — or a
   missing scanner — refuses the push. On a refusal: scrub and amend, never
   `--no-verify` ("a leak is a refusal, not a warning"). Details in the runbook's
   **Public-repo push gates** section.
2. **Pushing the `vX.Y.Z` tag starts the PyPI publish pipeline.** `publish.yml`
   fires on every `v*` tag: it builds the per-platform wheels + sdist, asserts
   tag == `pyproject.toml` version, refuses to upload any SHA without a green
   `ci.yml` run on it (the **ci-green witness gate** — it polls while CI
   finishes), then pauses at the protected `pypi` environment for the operator's
   approval before the OIDC Trusted-Publishing upload. So Step 6's tag push is
   also the publish request, and Step 7.4 is "surface/approve the pipeline,"
   not "run twine."
3. **Release notes are public documents.** `docs/releases/vX.Y.Z.md` ships to the
   world (and into the GH release body). No dev-machine absolute paths, hostnames,
   or personal identifiers; private-SUBJECT prose (operator process, fleet audits,
   launch mechanics) is born in `dos-private`, never here — the `CLAUDE.md`
   route-privacy-at-authoring-time rule, applied to the notes you draft in Step 3.

## Cadence — two channels, one rule

- **Rolling `vX.Y.Z` (this skill): ship whenever the gates are green.** A
  coherent, user-visible unit of work + a green suite is enough to cut a tag —
  several per week is healthy, not churn. The cost is low by design: every hard
  gate is mechanical and already wired (pre-push leak scan, `ci.yml` on the push,
  the publish ci-green witness, one human approval click for PyPI). Don't batch
  work waiting for a "big enough" release — the oracle reads git, and unshipped
  work is `NOT_SHIPPED`.
- **Stable `stable/<codename>` (`/stable-release`): promote deliberately.** The
  infrequent counterweight — a promotion of an already-soaked rolling tag (green
  suite + green third-party CI + clean truth syscall + soak window), producing
  the evidence file + the tag consumers pin in production
  (`pip install dos-kernel==<underlying>`). Rolling moves fast *because* stable
  exists; stable can afford its soak window *because* rolling carries the urgency.
- **Semver carries the compatibility promise; the channel carries the trust
  promise.** Major = breaking ABI (verdict vocabulary, syscall signature,
  `SubstrateConfig` shape). A major bump deserves a stable promotion soon after
  it soaks, so consumers get a provably-good anchor on the new ABI.

**Git authorization.** Invoking this skill is the user's explicit authorization
to run `git add`, `git commit`, `git tag`, and `git push origin master`/`vX.Y.Z`
as specified in Steps 5–6. The "never commit/push unless asked" default does NOT
apply here — committing and pushing IS the skill's job, and stopping mid-flow to
re-confirm each git mutation is the wrong behavior. Confirmation is still
required for anything destructive the steps don't list (force-push, history
rewrites, branch deletion, `git reset --hard`).

**The kernel-vs-tooling boundary.** The release scripts (`scripts/release_*.py`)
and this skill are **dev/release tooling that operates ON the package** — they
are never imported BY `dos.*`. Editing them never touches the kernel's import
graph, so the "kernel imports no host, no `scripts/`" litmus in `CLAUDE.md`
stays intact. Keep release logic here, not in `src/dos/`.

**Fast path** (most releases): Steps 1–7.5 below. Edge cases (racing agents,
version drift, release-notes nuance) live in [`release-runbook.md`](release-runbook.md)
— load it only when a step tells you to.

**This skill is the deliberate act; the always-on nudge is the reminder.** A
second, advisory `Stop` hook (`scripts/git_hygiene.py`, wired in
`.claude/settings.json`) runs at every stopping point and prints a one-line
*"N uncommitted files on master, K commits not pushed — commit the lane or run
/release"* whenever the tree is dirty or the branch is ahead of its upstream. That
nudge is **advisory-only — it never blocks, commits, or pushes** (the docs/274
rule: a blocking Stop hook force-loops interactive sessions). Its whole job is to
keep "ship the finished work" the default reflex; **this `/release` skill is what
the nudge points you to** when the dirty work is a coherent, release-worthy unit.
The two compose: the hook makes the obligation visible at every turn-end, the skill
discharges it. The hook's lease-aware fold (it subtracts paths a live
`/dispatch-loop` legitimately owns) is the *same* `dos.lane_journal` fold Step 1.6
below uses, so the nudge never nags about a path this skill would itself defer.

**Hot-tree safety (the DOS-native bits).** This repo's working tree is routinely
written by several concurrent agents at once (the `project-dos-multi-session-hot-tree`
discipline). Two steps below exist for that: **Step 1.6** auto-defers any dirty
path a live lane lease still owns — read from DOS's own lane journal, the same
fold `dos top` renders — so the release never ships a loop's mid-flight edit. And
the staging rules in **Steps 1.5 + 5** are index-race-safe by construction (commit
by explicit pathspec; never a bare `git add -A`), because a bare add/commit on this
tree grabs another session's staged or unstaged content (five separate memories
record that scar). Both are dogfood: the release flow is itself one of the agents
the kernel referees, so it reads the kernel's evidence before it writes the most
contended region (a tag on `master`).

---

## Step 0: Scope (default — derive and suggest a scope unless `--whole-tree`)

**A scoped release is the default.** DOS's working tree routinely carries
in-flight WIP — the plan series (`docs/NN_*-plan.md`), a concurrent loop's
mid-theme edits — that is not necessarily part of *this* release's theme.
(Strategy/business prose no longer lives in this tree at all — it routes to
`dos-private` at authoring time; if you see it dirty here, that's a misroute to
flag, not to ship.)
A whole-tree release sweeps all of it into one `chore` blob. So on a bare
`/release`, **derive a recommended scope and proceed scoped** — don't default to
whole-tree.

### Bare `/release` (no `--scope`, no `--whole-tree`): derive-then-proceed

1. From the Step 1 payload, derive a recommended scope: the **dominant work
   theme(s)** of the dirty tree + `commits_since_tag[].subject`. Usually 1–2
   tokens (e.g. `stamp-convention`, `arbiter`, `decisions`, `oracle`).
2. **State the suggestion before cutting**, one line:
   `Suggesting scoped release: --scope arbiter (overrides: --whole-tree, or --scope <other>)`.
3. **Proceed with that scoped snapshot** — do not block waiting for confirmation.
   The operator can redirect in their next turn; absent a redirect, the scoped
   release is correct and forward motion wins.
4. If the dirty tree is genuinely **one coherent theme**, the derived scope
   covers the whole tree anyway — say `Suggested scope covers the full dirty
   tree` and proceed.

**`--whole-tree`** is the explicit opt-out: every dirty path (modulo the
auto-deferred scratch/release-draft buckets) is in-scope, no scope derivation.

**`--scope <theme-token>` (explicit, repeatable)** pins the scope: the release is
limited to paths belonging to one or more named themes; every dirty path that
does not match is left uncommitted.

A `--scope` token (derived or explicit) is matched case-insensitively as a
substring against **both** a path and the subject of each `commits_since_tag`
entry. A path is in-scope if it matches any token directly, or sits under a
directory a matching commit touched.

**Scoped-release rules** (apply to a derived scope and an explicit `--scope`):

- The Step 2 snapshot is `(modified + untracked.tracked_docs + untracked.other)`
  **intersected with the scope match**, minus the auto-deferred buckets.
- Step 1.5 clusters **only in-scope paths** into thematic commits. Out-of-scope
  dirty paths are neither staged nor committed — list every one in the final
  summary under `Out-of-scope (left dirty): …`.
- If the scope match is **empty** — stop and report: nothing in this theme to
  release. Do not fall back to a whole-tree release.
- Bump level, release-notes, version bump, tag, push, and the GH-release ceremony
  are unchanged — a scoped release still cuts a real version tag.

## Step 1: Pre-digest context

Run `scripts/release_context.py`. One JSON payload covers git state + version
drift + per-file diff previews + the prior-release style cue. Read it once; do
NOT re-run `git log`, `git status`, `git diff`, or read `.gitignore` /
individual `docs/releases/v*.md` / untracked source files.

```bash
python scripts/release_context.py --limit-commits 50
```

**Read the JSON inline from the Bash tool result — do NOT redirect to a file.**

**Hard cap on `git log` polling.** The single `release_context.py` read is the
only sanctioned `git log` for this skill. Never run `git log <same-pathspec>`
more than 3 times in one session. Wait on the spawning Bash `<task-notification>`
rather than polling a ref on a wall-clock cadence.

**JSON keys → consumer step:**

| Key | Consumed by |
|---|---|
| `default_branch` | Step 6 push target (`master`) |
| `last_tag` + `commits_since_tag[].subject` | Step 2 bump-level |
| `commits_since_tag[].files` (numstat) | Step 3 per-commit bullets |
| `modified` + `untracked` (buckets) | Steps 1.5/2 snapshot + Step 5 stage |
| `modified_diff_previews` | Steps 1.5/3 — DO NOT re-run `git diff <file>` |
| `untracked_doc_previews` | Steps 1.5/3 — DO NOT re-Read untracked `.py`/`.md` |
| `prior_release_style` | Step 3 front-matter/heading shape — DO NOT Read prior `v*.md` |
| `version_files` | Step 4 input + drift signal (pyproject vs `__init__` fallback) |
| `drafted_release_past_tag` | racing-agent signal → load runbook |
| `clean_tree` | Step 2 stop-if-nothing-to-release |
| `docs_only` | Step 2 — **advisory** flag (a docs-only release is legit in DOS) |
| `phantom_diffs` | Step 1.5 — YAML/JSON files that round-tripped; do not stage blindly |
| `active_leases` | Step 1.6 — auto-defer dirty paths a live `/dispatch-loop` lane still owns |

Load [`release-runbook.md`](release-runbook.md) **only if**:

- `drafted_release_past_tag` is non-null → another agent is mid-release.
- `version_files.drift` is true → `pyproject.toml` and the `__init__.py` fallback
  literal disagree.

**Untracked artifacts are auto-in-scope by default** (subject to Step 0 scope).
`untracked.tracked_docs` (anything under `docs/`), `untracked.other` (new modules,
tests, configs, `.claude/` skill/memory edits), and `modified` all get staged
with the snapshot. Only `scratch` and `release_drafts` are auto-deferred.

> **Scoped release (the default):** the auto-in-scope default is *replaced* by
> the Step 0 scope match. Whole-tree auto-in-scope applies only when
> `--whole-tree` was passed.

## Step 1.5: Pre-release WIP snapshot (only if working tree is dirty)

Skip if `clean_tree: true`. Otherwise commit the in-flight WIP *before* drafting
the release, so each thematic change lands as its own `git log` entry and the
release commit carries only version-marker churn + the release-notes file.

**Invoking `/release` is your authorization to make these WIP commits too** —
same scope as Steps 5–6. Don't stop to re-confirm each thematic commit.

### Inputs

Re-read the release-context JSON. All of `modified`, `untracked.other`, and
`untracked.tracked_docs` are in-scope (subject to Step 0 scope) and get
committed. `untracked.scratch` → `rm -f`. `untracked.release_drafts` → leave
alone (Step 3 owns the new notes file).

### Concurrent-agent + phantom-diff guard (read first)

For every path in `modified`, inspect its `modified_diff_previews[path]` value
(already in Step 1 — do NOT shell out to `git diff` per file). Missing key → the
diff was empty or line-ending-only; leave it unstaged. Value with ONLY `-` lines
for content already in HEAD → a concurrent agent committed underneath you; do not
stage it, note in the summary.

**Check `phantom_diffs`.** DOS's state plane is git-native YAML (`dos.toml`,
`execution-state.yaml`). Any path listed in `phantom_diffs` parses identically to
HEAD but shows a large textual diff — a writer round-tripped + reformatted it.
Do NOT stage a phantom-diff path; restore it from HEAD (or fix the writer) and
note it in the summary.

### Group into thematic commits

Consult `untracked_doc_previews` and `commits_since_tag[].subject` to cluster
dirty paths into 2–5 commits that each map to one work theme. Conventions:

- Match the prefix style of recent commits (DOS uses conventional-commit-flavoured
  subjects — `feat(efficiency): …`, `docs(CLAUDE): …`, `fix(loop_decide): …` — plus
  plain `area:` like `paper:` / `release:` and `docs/NN …:` plan refs). The
  `commits_since_tag` list is the cheat-sheet.
- **Stage by explicit pathspec, one `git add <path>…` per commit — never
  `git add -A/./-u`.** On this concurrently-written tree a blanket add sweeps in
  another session's in-flight edits (the `feedback-commit-pathspec-on-shared-tree`
  scar). Stage exactly the files of this theme.
- **Commit by pathspec too: `git commit -m "<subject>" -- <path>…`** (the `-m`
  BEFORE the `--`). A bare `git commit` with no pathspec commits whatever is staged
  — including a *concurrent* session's staged content — under your message
  (`feedback-shared-index-bare-commit-grabs-concurrent-staged`). Naming the paths
  scopes the commit to your lane.
- **Caveat — a pathspec commit reads the WORKING TREE, not the index** for the
  named paths. So `git commit -- <path>` on a file that a sibling session is *also*
  editing unstaged will still sweep their hunks (`feedback-pathspec-commit-pulls-working-tree`).
  For such a hot, interleaved file: stage your hunks as a patch, reset, re-apply
  (`git diff -- <f> > /tmp/p; git checkout HEAD -- <f>; git apply /tmp/p`) or simply
  hold the file out of this release and note it — do not hand-split with `git add -p`
  (no tty here). The Step 1.6 lease auto-defer removes the common case (whole files
  under a leased dir) before you reach this.
- No `Co-Authored-By` line.
- If a pre-commit hook runs the suite, let it run; fix on failure, never
  `--no-verify`.
- **Windows note:** when a subject carries backticks / pipes / `§`, pass it via a
  message file (`git commit -F msg.txt -- <path>`), and write that file UTF-8
  **without a BOM** — `Out-File utf8` / `Set-Content utf8` emit a BOM that lands in
  the commit subject (`feedback-pathspec-commit-pulls-working-tree`). The
  `--lint-prefix` check below flags a leaked BOM.

### Best-effort, don't stop

Bias is forward motion. Same path across two themes → group with the theme that
owns more of its change-set; mention the split. Unclassifiable file → fold into
the closest thematic commit (or a `chore: <area>` catch-all). More than 5 themes
→ fold the smallest two together.

After this step the tree is clean except (optionally) any path you noted as
stale-vs-HEAD or phantom.

### Optional: `--lint-prefix` (opt-in commit-subject sanity check)

If the operator passed `--lint-prefix`, run `scripts/check_commit_prefix.py`
against each thematic-commit subject you just wrote (and, if you like, the Step 5
release-commit subject). The lint is **warn-only — it never blocks; exit is always
0**:

```bash
python scripts/check_commit_prefix.py --subject "<your subject>"
```

On a known prefix the lint is silent; on an unknown one it prints
`lint: unknown prefix '<x>'` to stderr; on a leaked UTF-8 BOM it prints a BOM
warning. Surface any warning in the final summary as an `observation` — do **not**
rename the commit, do **not** amend, do **not** stop. The asymmetric goal is to
catch a *new* commit that accidentally lands with a malformed / prefix-less subject;
the warning is the signal, the operator is the gate. Skip this step entirely when
`--lint-prefix` was not passed (the prefix shapes already cluster cleanly enough
that strict enforcement would be ceremony). This is the cheap *syntactic* witness;
the *semantic* one (`dos commit-audit`, "does the subject's claim match its diff?")
runs post-commit in Step 7.6.

## Step 1.6: Auto-defer paths a live `/dispatch-loop` owns (DOS-native)

`/release` is safe to run **while one or more `/dispatch-loop`s are active** — it
does not wait and does not refuse. It auto-scopes itself away from the paths those
loops are still writing, so a bare `/release` is always a non-event on a hot tree.

This is the **DOS-native** form of the auto-defer: the `active_leases` key in the
Step 1 payload is DOS's **own kernel WAL** (`dos.lane_journal`) folded to the
live-lease set — the exact same fold `dos top` renders to show which lanes are held
right now. So the release flow reads the same evidence `dos arbitrate` admits
against, and defers a leased region the way the arbiter would refuse a contended
lane. (Skip this step if `active_leases` is empty — no live loop, whole-tree as
normal.)

Each entry is one live lane lease: `{lane, lane_kind, tree, stale, holder, age_s,
heartbeat_age_s, ttl_s}`. The `tree` field is the lease's glob-region list.

- For every entry with `stale: false`, move each dirty path in the Step 2 snapshot
  that matches one of that lease's `tree` globs into an
  **`auto-deferred (active lease: <lane>)`** bucket — excluded from the snapshot.
  Glob match is the same shape `dos arbitrate` uses: an exact path, a `dir/` prefix,
  or a `dir/*` / `*_suffix.py` wildcard.
- A `stale: true` lease is reported but **not** deferred — its heartbeat is past the
  TTL, so the loop died without releasing and its region is fair game (the same
  stale-steal rule the kernel's lease arbiter applies).
- The version-marker files (`pyproject.toml`, `src/dos/__init__.py`, the plugin
  manifests) are never inside a lane tree, so the version bump + tag + push always
  proceed — the release still cuts a real version tag, it just carries a narrower
  commit set. This is the **same outcome as a `--scope` release** (Step 0): reuse
  that path, no new tag/version logic.
- List every lease-deferred path in the final summary under
  `Lease-deferred (left dirty): …` with the owning lane, so the operator knows those
  changes ship in the *next* release once the loop drains.

The scope match (derived by default in Step 0, or explicit via `--scope`) and the
lease auto-defer **compose**: a path must be in-scope **and** not owned by a live
lease to be committed. A leased path's changes are mid-flight, not done — shipping
them now would be the bug.

## Step 2: Decide bump level + snapshot files

From the JSON:

- **Bump level**: patch unless a commit subject (or the user's summary) indicates
  a new feature (minor) or breaking change (major). New kernel syscall / new
  driver / new CLI surface = minor. Bug fix / docs = patch. Breaking ABI change
  (verdict vocabulary, `SubstrateConfig` shape, syscall signature) = major. Just
  pick — don't ask.
- **`docs_only` advisory**: if `docs_only.docs_only` is true, this is a
  **legitimate** release in DOS (the plan series and HACKING.md are first-class
  substrate deliverables — unlike `job`, DOS does **not** refuse a docs-only
  release). Pick `patch` and a docs theme, and proceed.
- **Snapshot**: `modified + untracked.tracked_docs + untracked.other`, minus
  `scratch`/`release_drafts`, **intersected with the Step 0 scope** (full set
  only on `--whole-tree`), **minus any path Step 1.6 deferred under a live lease**.
  Write the list down; it's frozen from here.
- **Clean tree** (`clean_tree: true`, no commits-since): nothing to release —
  stop.
- **Clean tree with commits-since-tag**: snapshot is empty; only version files +
  release notes get staged.

Delete any `untracked.scratch` entries with `rm -f` before proceeding. Never
stage `*.zip`, `*.exe`, `*.html` research scrapes, `_scratch/`, or `.dos-workspace/`.

## Step 3: Draft the release file

Compute the new version, then write `docs/releases/vX.Y.Z.md`. (Create the
`docs/releases/` directory on the first-ever release — DOS has none yet.) Mirror
the shape in `prior_release_style` when present; otherwise use the minimum-viable
shape below. Source per-bullet content from `commits_since_tag[].files`,
`modified_diff_previews`, and `untracked_doc_previews` — all already in the Step 1
payload, no extra Reads.

```
---
version: X.Y.Z
date: YYYY-MM-DD
headline: "One short sentence, ≤120 chars, operator-facing outcome."
themes: ["arbiter"]
highlights:
  - "3–6 items, each ≤15 words, no semicolons. Lead with the user-visible change."
---

**TL;DR** — 1–2 sentences on what shipped and who should care.

## Section heading (one per major theme)

- **What changed** — one short sentence, no semicolons.
  - *Why:* one line if motivation isn't obvious.
  - *How:* `src/dos/file.py:func` or mechanism.
```

DOS theme slugs (reuse when possible, matching the syscall ABI + layering in
`CLAUDE.md`): `oracle` (verify), `wedge-reason` / `refusal` (refuse), `arbiter`
(lease/arbitrate), `run-id` / `lane-journal` (correlation spine), `config` /
`seam` (SubstrateConfig), `cli`, `drivers`, `reasons`, `decisions`, `docs`.

Full rules + anti-patterns in [`release-runbook.md`](release-runbook.md#release-notes-format-full-contract).
Load only when the change spans 3+ themes.

## Step 4: Bump the version markers

One command updates all five markers in lockstep — `pyproject.toml` (source of
truth), the `src/dos/__init__.py` fallback literal, the Claude Code plugin
manifest (`claude-plugin/.claude-plugin/plugin.json`), the marketplace plugin
entry (`.claude-plugin/marketplace.json`), and the **FTUE doc banners + skill-pack
samples** (`README.md`, `docs/QUICKSTART.md`, `docs/HACKING.md`,
`examples/playbooks/01_onboard-a-repo.md`, and the `dos_version`/`DOS v…` literals
in `src/dos/skills/*`):

```bash
python scripts/release_bump.py X.Y.Z
python scripts/build_plugin.py        # resync the bundled skill copies the sweep touched
```

The bumper prints a JSON report. If any target reports `"ok": false`, read the
reason and fix by hand. **If `drift_after_bump` is true**, the *lockstep* markers
disagree — reconcile all to the same value before committing (this is the exact
failure the `__init__.py` comment + `tests/test_plugin_manifest.py` record: a
source-checkout CLI misreporting its version, or the plugin bundle fronting a
stale one — the plugin drifted to 0.13.0 behind a 0.15.0 package exactly because
the bumper once didn't know about these files). The `docs` target is keyed on the
old→new pair (it rewrites only literals naming the old version) and is NOT part of
the drift guard; it drifted on v0.19.0 (the FTUE docs stayed at 0.18.0, hidden by
stale install metadata — 2026-06-10 audit) which is why the sweep now exists. The
`build_plugin.py` re-run after the bump resyncs the generated
`claude-plugin/skills/` mirror of the swept `src/dos/skills/` samples, so the
byte-level `test_plugin_manifest` skill-sync test stays green. Skip a target with
`--skip pyproject` / `--skip init` / `--skip plugin` / `--skip marketplace` /
`--skip docs`.

> **A `pip install -e . --no-deps` before the suite/`dos doctor` step** keeps the
> editable install's metadata current, so `dos.__version__` reports the freshly
> bumped value rather than the prior release's — without it, the version-drift
> guard (`tests/test_docs_version_drift.py`) reads stale metadata and can pass
> against stale docs, the exact mask that hid the v0.19.0 drift.

## Step 5: Commit

Stage only the snapshot + the version-marker files + the FTUE doc/skill files the
sweep touched + the new release notes, with every path explicit, then commit
**scoped by the same explicit pathspec** — the race-safe form on this hot tree
(Step 1.5 conventions). Use the bumper's JSON report (`targets.docs.files_swept`)
as the authoritative list of which doc/skill paths actually changed — stage only
those, plus the regenerated bundle copies under `claude-plugin/skills/`:

```bash
git add pyproject.toml src/dos/__init__.py \
        claude-plugin/.claude-plugin/plugin.json .claude-plugin/marketplace.json \
        <doc/skill paths from targets.docs.files_swept ...> \
        claude-plugin/skills/ \
        docs/releases/vX.Y.Z.md <snapshot paths...>
git commit -m "vX.Y.Z: <one-line summary>" -- \
        pyproject.toml src/dos/__init__.py \
        claude-plugin/.claude-plugin/plugin.json .claude-plugin/marketplace.json \
        <doc/skill paths from targets.docs.files_swept ...> \
        claude-plugin/skills/ \
        docs/releases/vX.Y.Z.md <snapshot paths...>
```

**Why the pathspec on `git commit` (not just on `git add`):** a bare `git commit`
commits whatever is in the index — and on this tree a concurrent session may have
staged its own files between your `git add` and your commit, which a bare commit
would then ship under *your* `vX.Y.Z:` subject
(`feedback-shared-index-bare-commit-grabs-concurrent-staged`). Naming the paths on
the commit scopes it to your release. (The pathspec re-reads the working tree for
those paths, which is exactly what you want here — your snapshot paths are yours;
the Step 1.6 lease defer already removed any path a sibling loop owns. If one of
your snapshot paths is *also* being edited unstaged by a sibling, that's the hot
interleaved-file case — handle it with the patch→reset→apply recipe from Step 1.5
or hold the file out and note it.)

Before `git add`, re-run `git status --porcelain` and compare against the
snapshot. Any path that appeared since Step 1 and isn't in the snapshot → leave
it alone, note in the final summary. Full rules in runbook "Snapshot discipline".

Do NOT add a `Co-Authored-By` line. Do NOT stage gitignored paths.

## Step 5.5: Rebuild the bundled plugin binaries (only if Go source changed)

The Claude Code plugin ships its native `dos-hook` fast-path binaries **committed**
in `claude-plugin/bin/` (docs/125 GHF4) so a marketplace install is a direct install
— a clone already has the binary for the user's arch. So a release that touched the
Go source (`go/**`) must re-commit fresh binaries, or the published plugin ships a
stale fast-path. Skip this step entirely if the release did not touch `go/`.

```bash
# Only if `go/` changed in this release AND the Go toolchain is on PATH:
python scripts/build_hook_binary.py        # rebuilds the full amd64+arm64 × lin/mac/win matrix
git status --porcelain claude-plugin/bin/   # did any binary actually change?
```

If — and only if — a binary changed, commit it by explicit pathspec (same race-safe
form as Step 5), e.g.:

```bash
git add claude-plugin/bin/dos-hook-*
git commit -m "build(dos-hook): rebuild bundled native binaries for vX.Y.Z" -- claude-plugin/bin/dos-hook-*
```

- **No Go toolchain on this machine?** Skip and note it: the committed binaries stay
  as-is (still valid for an unchanged `go/`); a CI matrix or a follow-up rebuild
  refreshes them. `tests/test_hook_binaries_bundled.py` still passes (presence +
  tracking), but a `go/`-changed release without a rebuild ships a stale fast-path —
  call that out in the summary.
- The binaries are large (~24 MB total) and reproducible (`-trimpath`, `CGO_ENABLED=0`):
  an unchanged `go/` rebuilds to byte-identical output, so `git status` shows nothing
  and there is nothing to commit — the common case.

## Step 6: Tag + push

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin master
git push origin vX.Y.Z
```

If `git push` rejects because the remote has commits you don't have (another
agent pushed), rebase your single commit on top — do NOT force-push master.

> If you cut this release from a feature branch (the git status may show e.g.
> `feat/<x>`), confirm with the operator before pushing the branch to `master`,
> OR push the branch + tag and open a PR instead. The default trunk is `master`
> (see the `feedback-stay-on-master-by-default` memory) — return to it after the
> branch merges. Do not silently retarget a push.

**What the push sets in motion (post-cutover — check, don't assume):**

- The **pre-push leak gate** runs first, locally, on both pushes (fail-closed —
  see the public-repo note up top). A refusal means scrub + amend, never
  `--no-verify`; a missing scanner means re-sync it from
  `../dos-private/tools/leak_scan.py`.
- The `master` push fires **`ci.yml`** (leak-scan + lint always; the docs-aware
  test matrix — full 4-leg grid for code, 2-leg for prose; per-platform wheel
  build + binary-format guard on code changes) and the repo-self DOS gate
  (**`dos-gate.yml`**: commit-audit + verify via the bundled verify-action).
- The tag push fires **`publish.yml`** (Step 7.4): it waits for a green `ci.yml`
  run on this exact SHA, then holds at the `pypi` environment for the operator's
  approval. If CI goes RED on the tagged SHA, the publish refuses — fix forward
  with a follow-up commit and cut the next patch release; **never re-point or
  delete the pushed tag.**

Read the verdicts rather than assuming them:

```bash
gh run list --commit "$(git rev-parse HEAD)"     # ci.yml + dos-gate.yml on the release SHA
gh run list --workflow publish.yml --limit 1     # the publish run (ci-green poll / approval hold)
```

## Step 7: GitHub release

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes "$(cat docs/releases/vX.Y.Z.md)"
```

The GH release carries **no uploaded assets** — the dist ships through PyPI, not
as GH-release attachments. The dist is **no longer pure-Python** (docs/286):
`publish.yml` builds one wheel per OS/arch, each embedding its native `dos-hook`
fast-path binary at `dos/_bin/`, plus a pure-source sdist — all CI-built; this
skill builds nothing locally. The install path is `pip install dos-kernel`
(**live on PyPI since 2026-06-10**; `dos-kernel==X.Y.Z` pins this tag), or
`pip install -e .` from a checkout. The **distribution name is `dos-kernel`** —
**not** `dos`, which on PyPI is an unrelated package (the import name stays
`dos`). There is no `release.py`, no archive prune, no versioned install
snapshot, no screenshots. (The `claude-plugin/bin/` binaries are NOT a GH-release
asset either — they ship committed in the git tree, rebuilt at Step 5.5; they are
a *separate* surface from the wheel's embedded binary.)

### Step 7.4: Surface the PyPI publish run (the pipeline is already in flight)

`dos-kernel` is **live on PyPI** (first published 2026-06-10 via the pending
trusted publisher that claimed the name — `publish.yml`'s header records the
one-time setup). Publishing is no longer a local `twine` act: the Step 6 tag push
**already triggered** `publish.yml`, which

1. builds the per-platform wheels + sdist (`scripts/build_wheels.py`) and
   `twine check`s every artifact,
2. asserts the tag matches `pyproject.toml`'s version (a mismatch fails the run
   — the bumper scar, now also enforced server-side),
3. refuses to upload until a green `ci.yml` run exists on the exact tagged SHA
   (the **ci-green witness gate**: "I tagged it" is a forgeable claim; a
   completed CI run on those bytes is not), and
4. **pauses at the protected `pypi` environment for required-reviewer approval**
   — the deliberate human hand on the one-way step.

The skill's job here: surface the run and its state, and tell the operator it is
waiting on their approval (approval happens in the GitHub UI — the `pypi`
environment review):

```bash
gh run list --workflow publish.yml --limit 1     # the run on this tag
gh run watch <run-id>                            # optional: follow the ci-green poll
pip index versions dos-kernel                    # after approval: confirm X.Y.Z is live
```

- **TestPyPI dry-run** = manual dispatch (Actions → Publish to PyPI → Run
  workflow → `target=testpypi`); validates the whole OIDC path, `skip-existing`
  is on there. The leg was registered + scratch-verified 2026-06-10.
- The **manual local fallback** (`python scripts/build_wheels.py` + `twine
  upload` with a real token) exists only for a broken-pipeline emergency — it
  needs the Go toolchain for the per-platform wheels and has no OIDC. Prefer
  fixing the pipeline.
- Pin consumers with `dos-kernel==X.Y.Z`; never the bare `dos` (a squatter).
- PyPI rejects a re-upload of an existing X.Y.Z — a botched publish means cutting
  the next patch version, never force-replacing.

## Step 7.5: Verify (mandatory — the DOS analogue of an apply-loop smoke)

DOS's "does it work" gate is the kernel suite + the truth syscall, run against
the just-committed tree:

```bash
python -m pytest -q
dos doctor --workspace .
```

- `pytest -q` must be green. If it fails, the release commit + tag already
  happened — do **not** roll them back; land a follow-up fix commit and note the
  failure + fix in the final summary. (A red suite that predates your change and
  is unrelated — see the `project-dos-concurrent-automation` memory's note about
  pre-existing failing tests — is reported, not fixed here.)
- `dos doctor` should print the active workspace + lane taxonomy without error,
  confirming the package imports and the CLI entry point resolves at the new
  version.

This verification runs against the **editable working tree** (`pip install -e .`)
— DOS has no production snapshot to advance, so there is nothing further to flip.

This local run is the *fast feedback*, not the witness: `ci.yml` re-runs the same
suite on the pushed SHA across the full matrix, and the publish gate consumes
**that** verdict, not this one. A locally-green/CI-red release SHA means a
matrix-leg failure your box never ran — fix forward (follow-up commit + next
patch release); the publish run will refuse the red SHA on its own.

## Step 7.6: Commit-audit the release (advisory — dogfood the honesty witness)

A release is a batch of fresh commits, and `CLAUDE.md`'s "Committing — close the
loop" contract says to witness them from **outside** the loop that wrote them with
the kernel's own honesty oracle. `dos commit-audit` reads each commit's *subject*
against its own *diff* — the forgeable claim vs the unforgeable bytes — and is the
semantic complement of the syntactic `--lint-prefix` check from Step 1.5.

Run it over the commits this release just landed (the WIP thematic commits + the
release commit):

```bash
dos commit-audit --sweep --warn-only --workspace . <last_tag>..HEAD
#   <last_tag> = the prior tag (release_context `last_tag`); the range is exactly
#   the commits this /release added. Read-only. `--warn-only` makes it advisory by
#   construction (always exit 0, prints the drift rate + any flagged commits) so it
#   can NEVER gate the release — drop `--warn-only` only for a manual sharper check.
```

- **No flagged commits** → say a one-line "commit-audit clean" in the summary.
- **One or more `CLAIM_UNWITNESSED`** → a commit subject asserts a concrete
  code/test claim its own diff contradicts. The release commit + tag **already
  happened** — do **not** roll them back. Note the flagged commit(s) in the final
  summary so the operator can land a follow-up correction. (commit-audit grades the
  *kind* of change, never *correctness* — the Wall-3 line; it fires only where a
  concrete claim and a contradicting diff coexist, and ABSTAINs otherwise.)
- **Exit 2** (only if you drop `--warn-only`) → an unreadable ref (e.g. `last_tag`
  was null on a first-ever release); fall back to `dos commit-audit --warn-only HEAD`
  for just the release commit, or skip — it's advisory.

Why `commit-audit`, not `dos plan --once`: since docs/293 the plan board DOES
parse this repo's prose plans (`dos.toml [plan]` declares the dialect), but its
⚠over-claim rows are dominated by the evidence horizon — plans stamped before
the 2026-06-10 fresh seed answer NOT_SHIPPED via `none` (a conservative abstain,
not dishonesty; see CLAUDE.md step 5) — so the board is the wrong release
witness here. This repo's release-window claims live in **commit subjects**, so
the commit-audit sweep is the witness that actually adjudicates them. This step
is **advisory** — it never gates the release; it surfaces drift for the
operator, exactly as the contract intends.

## Final summary

Print:

- Version: old → new
- Release tag + commit sha (short)
- GitHub release URL
- Push target (`master`) + whether a branch/PR detour was taken
- **Publish pipeline** (Step 7.4): the `publish.yml` run URL + its state —
  polling ci-green / **holding for `pypi`-environment approval** (tell the
  operator the approval is theirs to click) / uploaded (confirmed via
  `pip index versions dos-kernel`) / refused (and why)
- **Leak gate**: clean, or what the pre-push hook refused + what was scrubbed
  and amended
- Verify: `pytest -q` result (passed / N failed) + `dos doctor` ok
- **Commit-audit** (Step 7.6): `commit-audit clean` or the flagged commit(s) + a
  note that a follow-up correction is needed (the release was NOT rolled back).
- **Scope** (the default): how it was chosen — `Scope: arbiter (derived)` /
  `Scope: arbiter (--scope)` / `Scope: whole-tree (--whole-tree)`. Then
  `Out-of-scope (left dirty): <path>, …` — every dirty path deliberately not
  committed, so the operator knows what still needs handling.
- **If any `/dispatch-loop` was live (Step 1.6):** `Lease-deferred (left dirty):
  <path> — <lane> lane` — every dirty path not committed because a live lease owns
  it; those changes ship in the next release once the loop drains.
- Any drift warnings (`version_files.drift`), phantom-diff paths skipped, or
  snapshot-excluded paths the user should know about.
- If `--lint-prefix` was passed: any `lint: unknown prefix` / BOM warnings as
  `observation` findings (the commit was NOT renamed).
- If `docs_only` fired: note it was a docs-only release (legitimate in DOS).
