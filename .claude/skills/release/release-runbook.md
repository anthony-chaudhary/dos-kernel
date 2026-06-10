# Release runbook — DOS edge cases only

`SKILL.md` is the fast path. This file holds the defensive rules you only need
when something is weird. Load it when:

- `scripts/release_context.py` reports `drafted_release_past_tag` → read
  **Racing-agent recovery**.
- `version_files.drift` is true → read **Version-marker drift**.
- You're drafting release notes and want the full format contract → read
  **Release-notes format**.
- `active_leases` is non-empty (a live `/dispatch-loop`) → read **Lease auto-defer**.
- You passed `--lint-prefix`, or you're running the post-release commit-audit, and
  want the full witness rationale → read **Two witnesses on the commit subjects**.
- The pre-push leak gate refused a push, or you need its mechanics → read
  **Public-repo push gates**.
- The publish run failed or stalled (tag≠version, ci-green refusal, approval
  hold) → read **The publish pipeline**.

This is the DOS adaptation of `job`'s release runbook, stripped of the job-only
buckets (fanout-run artifacts, build-referenced Go/template traps, apply-audit
PNG policy, the four-version-file drift table). DOS has **two** *single-sourced*
version markers (plus the two plugin-manifest mirrors the bumper keeps in
lockstep) and **no** zip/screenshot surface. Its Go surface — the `dos-hook`
binaries — is CI-built into the per-platform wheels (docs/286, `publish.yml`) and
committed under `claude-plugin/bin/` for marketplace installs; neither is a
release asset this skill builds or uploads.

What DOS *kept and re-grounded* from job (docs/267): the **lease auto-defer** (job
reads a bespoke `active_leases` field; DOS reads its own kernel WAL —
`dos.lane_journal` folded the same way `dos top` does), the **index-race staging
discipline** (job uses a 944-line atomic-staging helper; DOS encodes the
patch→reset→apply recipe its own memories paid for, in the skill body), the
**opt-in commit-prefix lint** (`scripts/check_commit_prefix.py`, DOS-native — no
imported host noise-prefix taxonomy), and a **post-release `dos commit-audit`**
honesty witness job has no analogue for.

---

## Version-marker drift

DOS single-sources its version. Two markers must agree:

1. `pyproject.toml` — `version = "X.Y.Z"` (the **source of truth**; build + the
   `dos` package metadata read this).
2. `src/dos/__init__.py` — `__version__ = "X.Y.Z"` (the **fallback literal**,
   used only when running from a bare source checkout that was never
   `pip install`-ed; at runtime `__version__` normally comes from
   `importlib.metadata.version("dos")`).

`scripts/release_bump.py` touches both in one call and refuses (exit 1,
`drift_after_bump: true`) if they end up disagreeing. If `release_context.py`
reports `version_files.drift: true` *before* you bump, reconcile to the highest
semver — treat the higher value as the intended target, note the drift in the
final summary, continue. This drift is not hypothetical: it shipped once
(`__init__` said `0.1.0` while `pyproject` shipped `0.2.0`, so every `dos` CLI
command misreported its version from a source checkout). The comment in
`src/dos/__init__.py` records the scar; the bump script's drift guard exists to
catch it mechanically.

STOP only if the working tree is so tangled that no single coherent version can
be picked (see Racing-agent recovery).

---

## Racing-agent recovery (drafted-but-untagged state)

DOS runs concurrent automation — a scheduled agent auto-commits to `master`
mid-session (see the `project-dos-concurrent-automation` memory). Signals that
another agent is mid-release:

- `drafted_release_past_tag` is non-null in the context JSON.
- `version_files` already past `last_tag`.
- HEAD commit changes between two `git status` calls.

**Playbook — favour forward progress over perfect lineage.**

1. **Do not try to complete the other agent's release.** Leave
   `docs/releases/vX.Y.Z.md` drafts and the files in their changelog alone.
2. **Skip ahead to the next patch number.** If `0.3.0` is drafted-but-untagged
   and your change is orthogonal, bump to `0.3.1` (or `0.4.0` for a minor). Gaps
   in the tag sequence are acceptable.
3. **Keep the snapshot as tight as humanly possible.** Stage exactly the files
   your change touches + the 2 version markers + your new release notes.
4. **Re-run `git status` right before `git add`.** Another commit may have landed
   while you drafted. Drop any path no longer modified; re-apply any version bump
   they made on top of yours.
5. **Push only after verifying `git log` doesn't show an unexpected commit ahead
   of your tag.** If it does, rebase your single commit on top — do not
   force-push `master`.
6. **Note the race in your release body.** One-liner: "shipped in parallel with
   vX.Y.Z drafting vA.B.C".

**Do NOT:** overwrite the other draft's release file, bump to the same version
number, delete their untracked scratch, or revert their staged version bump. If
your changes and theirs touch the same file, STOP and tell the user.

---

## Untracked-file classifier

`scripts/release_context.py` auto-classifies untracked paths into `scratch` /
`release_drafts` / `tracked_docs` / `other`. **Default: everything in
`tracked_docs` and `other` is auto-in-scope** (subject to the Step 0 scope) and
gets committed.

The buckets:

**Scratch (auto-delete).** The script's `scratch` list. `rm -f` before staging.
DOS scratch conventions (mirror of `.gitignore`): `_scratch/`, `*.err`, `*.html`
(downloaded research / scraped prior-art dumps), `scripts/_*.py` (leading
underscore = short-lived probe), `.dos-workspace/`, root-level `*.png`, and
root-level `.<name>_<suffix>.{py,json,…}` probes.

**Tracked-docs subtree (auto-commit).** Anything under `docs/` that isn't a
release draft — the genericization plan series (`docs/7x_*-plan.md`),
`HACKING.md`, the vision/business docs, host-plan drafts, postmortems. Ships with
every release. **Note:** unlike `job`, a docs-only snapshot is a *legitimate*
DOS release — docs are first-class substrate deliverables. The `docs_only` key in
the context JSON is advisory, never a refusal.

**Other durable artifacts (auto-commit).** New modules under `src/dos/`
(especially `src/dos/drivers/<host>.py` — a new driver is the canonical way to
add host policy without touching the kernel), tests under `tests/`, the umbrella
CLI, `examples/`, `.claude/` skill or memory edits, config (`pyproject.toml`,
`.gitignore`). Auto-in-scope. Prefer a separate thematic commit over leaving
untracked.

**Gitignored / build artifacts (never commit).** `*.egg-info/`, `build/`,
`dist/`, `.pytest_cache/`, `.ruff_cache/`, `.venv/`, zips. `.gitignore` catches
these; anything leaking through belongs in `.gitignore` as a follow-up.

### Kernel-vs-tooling sanity check (DOS-specific)

Before folding a new `src/dos/*.py` into the snapshot, sanity-check that it
respects the layering in `CLAUDE.md`:

- A new module that names a host (`job`, `apply`, `tailor`, a host-specific lane)
  must live under `src/dos/drivers/`, **not** at the kernel top level. If you see
  a host name in a kernel-layer module, that's a layering violation — flag it,
  don't just ship it.
- The release `scripts/` themselves are dev tooling; they are never imported by
  `dos.*`. A `src/dos/*` module that imports from `scripts/` is a bug — the
  dependency arrow only ever points the other way.

These are the same litmus tests `CLAUDE.md` enforces; the release flow is a
natural checkpoint to catch a violation before it ships.

---

## Script policy

- `scripts/*.py` without a leading underscore — **tracked**, durable. The release
  tooling (`release_context.py`, `release_bump.py`, `stable_release_context.py`)
  lives here. These are dev tooling that operates ON the package, never imported
  BY it.
- `scripts/_*.py` — **gitignored**, scratch. Leading underscore = short-lived
  probe.

If a probe turns out useful, drop the underscore and commit it.

---

## Release-notes format (full contract)

`scripts/release_bump.py` does not generate release notes — you write
`docs/releases/vX.Y.Z.md` by hand. Target reader: someone who pins `dos==X.Y.Z`
in a downstream `pyproject.toml` a week from now and wants to know what changed
and whether it affects them, in under 30 seconds.

### Structure

```
---
version: X.Y.Z
date: YYYY-MM-DD
headline: "One short sentence, ≤120 chars, operator-facing outcome."
themes: ["arbiter", "oracle"]
highlights:
  - "One short phrase per highlight — ≤15 words, no semicolons."
  - "Lead with the user-visible change, not the file that moved."
  - "3-6 total; this is a TOC, not the body."
---

**TL;DR** — 1-2 sentences naming the user-visible change and who it affects.

## Section heading (one per major theme, matches a `themes:` slug when possible)

- **What changed** — one short sentence stating the outcome. No semicolons.
  - *Why:* one short line if the motivation isn't obvious.
  - *How:* key file(s) or mechanism — `src/dos/arbiter.py:arbitrate`, enum values.
  - *Impact:* only when there's an ABI change or behavior flip to act on.
- **Next bullet** — same shape.
```

### Front-matter rules

- `version` — bare semver, no leading `v`.
- `date` — today (YYYY-MM-DD).
- `headline` — single sentence, ≤120 chars, double-quoted. State the outcome
  ("the truth syscall now answers from git history with no plan present"), not a
  file-level changelog. If you can't fit 120 chars, you're packing two releases —
  split.
- `themes` — slug list. Reuse the DOS vocabulary aligned to the syscall ABI +
  layering: `oracle`, `wedge-reason` / `refusal`, `arbiter`, `run-id` /
  `lane-journal`, `config` / `seam`, `cli`, `drivers`, `reasons`, `decisions`,
  `docs`.
- `highlights` — 3–6 items, each ≤15 words, no semicolons, no nested clauses.
  Lead with what changed, not the file path.

### Body rules

- Open with a one-line `**TL;DR**` before any heading.
- Group by theme with `##` headings. Single-theme release can skip.
- Top-level bullets are one short sentence each. Lead with a bold summary, state
  the outcome, stop.
- Push detail into indented sub-bullets with `*Why:* / *How:* / *Impact:*` labels.
- One bullet per atomic change.
- File paths and identifiers stay backticked inline (`src/dos/oracle.py`,
  `WedgeReason`, `SubstrateConfig`). Keep file refs at the sub-bullet level.
- For an ABI-affecting change (verdict vocabulary, syscall signature,
  `SubstrateConfig` shape), always add an `*Impact:*` line — downstream consumers
  (`job` pins `dos` by git ref / `@vX.Y.Z` tag) read these to decide whether a
  bump is safe.

### Anti-patterns

- Headline >120 chars or joining unrelated things with `+` → split into highlights.
- Highlights with semicolons or inline file paths before the user-visible change
  → rewrite lead-first.
- Body bullets as 80-word paragraph walls naming every identifier → split into a
  one-line summary + sub-bullets.
- Implementation minutiae (exact regex, internal constant names) at top level →
  demote to `*How:*` or drop.

---

## Snapshot discipline

The snapshot is the set of paths you decided to commit, frozen immediately after
reading `release_context.py`. From that moment:

- Stage only paths in `{snapshot ∪ version markers}`. One `git add` call with
  explicit paths — never `git add -A/./-u`.
- If a file shows up in a later `git status` that is NOT in the snapshot, do not
  stage it. (Concurrent automation, editor autosaves, generated artifacts.)
- If a snapshot path was further modified since you read it, stage as-is. Only
  brand-new paths get ignored.
- Never stage gitignored paths (`*.egg-info/`, `build/`, `dist/`, `.venv/`,
  `_scratch/`, `*.html`, `.dos-workspace/`).

Rationale: releases take several minutes; DOS's concurrent automation creates or
modifies files mid-release. Those changes are not part of this release.

---

## Lease auto-defer (`active_leases`) — the DOS-native Step 1.6

`release_context.py` emits an `active_leases` list: the lane-journal WAL
(`dos.lane_journal`) folded to the live-lease set, exactly as `dos top` reads it.
Each entry is `{lane, lane_kind, tree, stale, holder, age_s, heartbeat_age_s,
ttl_s}`.

This is the substrate eating its own dog food: the release flow is itself one of
the concurrent agents the kernel referees, so before it writes the most contended
region (a version bump + a tag on `master`) it reads the same lease evidence
`dos arbitrate` admits against, and defers any path a live loop still owns.

- **Defer** every dirty snapshot path matching a `stale: false` lease's `tree`
  globs (exact path / `dir/` prefix / `dir/*` / `*_suffix.py`), into a
  `Lease-deferred (left dirty):` bucket. These ship next release once the loop
  drains — shipping a mid-flight edit is the bug the defer prevents.
- **Do NOT defer** a `stale: true` lease's region. Its heartbeat is past `ttl_s`
  (default 300s), so the holder died without releasing — fair game, the same
  stale-steal rule the kernel's lease arbiter applies.
- **Empty list** (`active_leases: []`) → no live loop; skip Step 1.6 entirely.
- The fold is read defensively: a missing/torn journal, or a checkout where `dos`
  isn't importable, yields `[]` — the auto-defer is advisory, never a gate, so it
  must not block a release.

The lease defer and the Step 0 scope **compose** (AND): a path must be in-scope and
not live-leased to be committed.

---

## Public-repo push gates (post-cutover, 2026-06-10)

This working repo IS the public `anthony-chaudhary/dos-kernel` — a push is
publication. Two gates stand at and after the push, both outside this skill:

**The machine-local pre-push leak gate** (`.git/hooks/pre-push`, untracked by
construction). It runs `scripts/leak_scan.py` — a *gitignored* local sync of the
canonical tracked scanner `../dos-private/tools/leak_scan.py`. The scanner's
pattern list names the very strings it forbids, so it must never ship; its
gitignore entry is load-bearing. The hook is **fail-closed**: a hit *or a missing
scanner* refuses the push.

- **On a hit:** scrub the leak (sibling-relative paths, neutral fixture roots —
  the `CLAUDE.md` authoring rule), `git commit --amend` (or a follow-up commit if
  the SHA already left this machine), re-push. If the pattern itself needs
  adjudicating, that happens on the canonical copy in `dos-private`, then re-sync
  the local one.
- **On a missing scanner:** `cp ../dos-private/tools/leak_scan.py scripts/` —
  never `--no-verify`. The fail-closed refusal is the gate working, not an
  obstacle.
- CI runs the same scan runs-if-present (`ci.yml`'s leak-scan job); in the public
  tree the file is absent, so that job no-ops green — the **local hook is the
  real gate**, and SECURITY.md's "Publication gate" section is its public stub.

**`ci.yml` on the master push:** leak-scan + lint unconditionally; the docs-aware
test matrix (full 3-interpreter × ubuntu grid + a windows leg for code; one
ubuntu + one windows leg on the primary Python for prose — README.md counts as
*code* here, it ships as the wheel's long_description); per-platform wheel build
+ the binary-format guard on code changes. `dos-gate.yml` (the repo-self DOS
gate: commit-audit + verify via the bundled verify-action) fires alongside it. A
red run on the release SHA blocks the publish (next section) — fix forward.

---

## The publish pipeline (`publish.yml` — what the tag push starts)

The `vX.Y.Z` tag push triggers the Trusted-Publishing pipeline (OIDC, no stored
token). Anatomy and failure modes:

- **build** — `scripts/build_wheels.py` → one wheel per OS/arch, each embedding
  its arch's `dos-hook` binary at `dos/_bin/` (docs/286), + a pure-source sdist;
  `twine check` over all; then asserts tag == `pyproject.toml` version.
  - *Tag≠version failure:* the markers weren't bumped before tagging. Do NOT
    re-point or delete the pushed tag — bump properly and cut the next patch
    version.
- **ci-green** — polls `gh run list --workflow ci.yml --commit <sha>` until a
  `success` exists; refuses on completed-without-success or no-run-ever. The
  witness rule: `ci.yml` cancels superseded master runs, so a tagged commit can
  carry no CI verdict — the gate goes and *reads* one rather than believing the
  tag. The common `/release` flow pushes master + tag together, so the poll
  usually just absorbs CI's runtime.
  - *Refusal:* get a green run on the exact SHA (`gh run rerun <id>`, or fix
    forward + next patch release), then re-run the publish workflow.
- **publish-pypi** — holds at the protected `pypi` environment for
  required-reviewer approval (the operator's click), then uploads. No
  `skip-existing` on real PyPI: re-uploading an existing version fails loudly —
  the answer is the next version, never replacement.
- **TestPyPI leg** — manual dispatch with `target=testpypi`; `skip-existing:
  true` there (TestPyPI is noisy). Registered + scratch-verified 2026-06-10.

Two distinct binary surfaces — don't conflate them: the **wheel's embedded**
`dos-hook` is CI-built fresh per publish; the **committed** `claude-plugin/bin/`
binaries serve marketplace (git-clone) installs and are rebuilt locally at
Step 5.5 only when `go/` changed.

---

## Two witnesses on the commit subjects (lint + commit-audit)

DOS witnesses release commit subjects twice, at two strengths — neither in job:

1. **`scripts/check_commit_prefix.py`** (Step 1.5, opt-in `--lint-prefix`) — the
   *syntactic* check, run before the commit. Warn-never-block, always exit 0.
   Recognizes `vX.Y.Z:` + the general `<area>:` shape (conventional-commit,
   `docs/NN §`, plain `area:`) and flags a leaked UTF-8 BOM. DOS-native: no imported
   host noise-prefix taxonomy (the kernel-imports-no-host rule applied to tooling).
2. **`dos commit-audit --sweep --warn-only <last_tag>..HEAD`** (Step 7.6) — the
   *semantic, ground-truth* witness, run after the commits land. Reads each
   subject's CLAIM against its own DIFF (forgeable message vs unforgeable bytes) and
   fires `CLAIM_UNWITNESSED` only where a concrete code/test claim and a
   contradicting diff coexist. Advisory; grades the *kind* of change, never
   *correctness* (Wall 3). Run from OUTSIDE the loop that wrote the commits — the
   docs/228 lesson: the only witness worth trusting is one the claimant can't forge,
   and on this repo that is git, not a commit subject taken at its word.

On this repo the plan board (since docs/293) does parse the prose plans, but its
⚠over-claim rows are dominated by the pre-seed evidence horizon (stamps amputated
by the 2026-06-10 fresh seed → NOT_SHIPPED via `none`, a conservative abstain —
see CLAUDE.md step 5), so the commit-audit sweep is the honesty witness the
release actually has: it adjudicates the release window's own commits.
