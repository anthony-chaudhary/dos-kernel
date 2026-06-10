# The release dry run — tag last, adjudicate the commit, rehearse on TestPyPI

> **Status:** proposed — no phase shipped. Born from the 2026-06-10 v0.23.x
> release run, which burned three version numbers (v0.23.0–v0.23.2) on defects
> that were all detectable *before* the tag was minted. This plan is release
> **tooling** (`scripts/`, `.claude/skills/release/`, `publish.yml`) — zero
> kernel surface; the "no `scripts/` in the kernel" litmus is untouched.

## 1. The evidence — one night, three refusals

The publish pipeline's ci-green witness gate refused three tags in a row.
Every refusal was correct, and every defect predated its tag:

| Tag | Defect | Was it knowable pre-tag? |
|---|---|---|
| v0.23.0 | `ci.yml` unparseable (a plain YAML scalar containing `: ` in the new ci-ok job) — CI had been failing in 0 seconds on every push since the commit that introduced it, ~3 minutes before the tag | **Yes** — a local YAML parse of the workflow files (<1s), or one `gh run list` on the base commit |
| v0.23.1 | three stale `0.22.0` FTUE version literals; the drift guard caught them — on CI, after the tag | **Yes** — the same drift tests run locally against the release commit, pre-tag (~2s for the doc-coherence family) |
| v0.23.2 | hot-tree desync: a sibling session's `src/dos/skills/` edit was swept by a pathspec commit *past* the already-generated `claude-plugin/skills/` copy, and a freshly-committed lockstep test failed on its own registry | **Yes** — the suite run against the *committed bytes* in a clean worktree (the v0.23.3 cut did exactly this and went green first try) |

The economics make the lesson sharp: **a red caught before the tag costs a
commit; a red caught after the tag costs a version number.** PyPI never
reuses an X.Y.Z, and this repo never re-points a pushed tag — so every
post-tag red permanently burns a number and another full release ceremony.
Three in one night is the cost curve announcing itself.

## 2. The diagnosis — the tag was the first witness

In kernel vocabulary: the tag is a **claim** ("these bytes are releasable"),
and `publish.yml`'s ci-green gate is its **witness**. The current flow mints
and *publishes* the claim (the tag push is the publish trigger) before any
witness has run on the candidate bytes. The pipeline then gathers the
evidence and — correctly — refuses. That is the docs/138 invariant working at
the wrong point in time: the refusal is sound, but the claim was already
world-visible and the version number already spent.

The fix is the same move the kernel makes everywhere else: **gather the
evidence before the claim goes out.** Don't weaken the out-of-loop gate —
shift a copy of it left, into the release flow, between the commit and the
tag.

Two further sub-lessons from the night:

- **The working tree cannot be the dry-run subject.** The local full-suite
  run *did* fire during the v0.23.x line — and produced four false signals
  (hot-tree lease contention, a sibling's half-edited code/test pair) on top
  of the true ones. A gate that cries wolf trains the operator to ignore red.
  The trustworthy subject is the **committed bytes in a detached worktree** —
  the same isolation rule `dos-self-improve` applies to its candidates
  (docs/280): the tree being adjudicated must not be the tree being edited.
- **Generated pairs desync at the commit boundary.** A pathspec commit reads
  the working tree at commit time, so a generated artifact staged from
  *earlier* source bytes can ship out of sync with its just-edited source
  (the v0.23.2 skills pair). The worktree dry run catches this class after
  the fact; the staging-side fix (index surgery from known bytes, or
  regenerate-immediately-before-commit) prevents it. Both belong in the
  release skill.

## 3. The mechanism — the tag-last gate

### Phase 1 — preflight facts in `release_context.py` (cheap, pure reads)

Add two keys to the Step-1 payload the release skill already consumes:

- `workflows_parse_ok` — YAML-parse every `.github/workflows/*.yml`; carry
  per-file ok/error. Catches the v0.23.0 class in milliseconds, offline.
- `ci_on_head` — digest of `gh run list --commit <HEAD>` plus the most recent
  completed `ci.yml` conclusion on the branch. A 0-second failure or a red
  tip is visible *before* the release builds on it. (Advisory: a missing `gh`
  or no runs yet degrades to `unknown`, never blocks — fail-to-abstain.)

The skill's Step 2 grows one rule: if `workflows_parse_ok` is false or
`ci_on_head` is red, **fix forward first** — the release would inherit a
known-red base.

### Phase 2 — `scripts/release_dry_run.py` + the skill reorder

A new script, run between the release commit and the tag:

```
python scripts/release_dry_run.py <ref>   # exit 0 = tag it; 1 = fix forward
```

1. YAML-parse all workflow files at `<ref>` (the Phase-1 check, re-aimed at
   the candidate bytes).
2. `git worktree add --detach <tmp> <ref>` and run the suite there —
   full by default; `--fast` runs the curated release-perturbation set (the
   doc-coherence family the release itself touches: version-drift, README
   assembly, plugin-manifest sync, canonical-example lockstep, install-drift).
3. Emit a JSON verdict (`{parse: …, suite: {passed, failed, names}}`); clean
   up the worktree.

Skill changes:

- New **Step 5.8 (dry-run gate)** after the commit and binary rebuild, before
  the tag: run the script; tag only on exit 0. A red here is a follow-up
  commit and a re-run — **no version is minted, nothing is pushed, nothing is
  burned.**
- **Step 7.5 shrinks**: the post-push local suite run becomes redundant fast
  feedback (CI on the pushed SHA remains the witness the publish gate
  consumes — unchanged, defense in depth).
- **Step 5 staging discipline** gains the generated-pair rule: a generated
  artifact (`claude-plugin/skills/`, `README.md`) is staged either by
  regenerating it immediately before the commit in the same breath, or via
  index surgery from the same source bytes the commit will carry (the
  `git show HEAD:… | … | git hash-object -w` pattern that cut v0.23.3 clean
  on a hot tree).

**Known coupling, documented not hidden:** the worktree suite imports `dos`
through the machine's editable install (a `.pth` meta-path finder PYTHONPATH
cannot shadow), so `dos.__version__` reads the *main* tree's markers. In the
release flow this is benign by construction — the dry run fires after the
bump is committed, when main tree and `<ref>` agree on the version. A
`--venv` mode (fresh venv, `pip install <worktree>`) is the full-isolation
upgrade if the coupling ever bites; deliberately deferred.

### Phase 3 — `publish.yml` knows the gate exists (optional tightening)

None required — the pipeline already refuses an unwitnessed SHA. Optionally,
the publish run's summary can note whether a dry-run verdict was recorded
(e.g. a `release-dry-run: pass` trailer in the tag annotation), making the
shift-left visible in the audit trail. Cosmetic; not load-bearing.

## 4. TestPyPI — the publish rehearsal (the secondary thread)

The dry run above witnesses the *tree*; TestPyPI witnesses the *publish*.
They cover disjoint defect classes — a green suite says nothing about wheel
metadata, README rendering on a registry page (the relative-links 404 class
shipped exactly this way), or the OIDC upload path.

Today the TestPyPI leg is manual-dispatch only (registered + scratch-verified
2026-06-10) and is **skipped** on tag runs. Proposal:

### Phase 4 — auto-rehearse on every `v*` tag

- On a tag push, `publish.yml` runs the TestPyPI leg **automatically, before
  the `pypi` environment hold** (no human gate; `skip-existing: true` already
  set). The operator arriving at the approval click then has three witnesses:
  green CI on the SHA, `twine check` on the artifacts, and *the same
  artifacts already accepted by a real registry*.
- Add an **install-smoke job** downstream of the TestPyPI upload:
  `pip install --index-url https://test.pypi.org/simple/ --extra-index-url
  https://pypi.org/simple/ dos-kernel==X.Y.Z` on a clean runner, then
  `python -c "import dos"` + `dos doctor`. (The `--extra-index-url` matters:
  TestPyPI does not mirror dependencies; without it the smoke fails on
  PyYAML, a false red.)
- **Soft gate semantics** (fail-to-abstain, docs/86 discipline): a TestPyPI
  outage / quota error marks the rehearsal `ABSTAINED` and the run proceeds
  to the hold with that fact in the summary — the operator decides. A
  *defect* surfaced by the rehearsal (bad metadata, failed install-smoke) is
  a real red: the run stops before the hold, and the fix-forward costs only
  the next patch number it would have cost anyway — but now with the cause in
  hand before PyPI is touched.
- Version-number economics on TestPyPI are free: `skip-existing` makes
  re-runs idempotent, and a burned TestPyPI version costs nothing.

### Non-goals for TestPyPI

- **Not a pre-tag step.** The pipeline is tag-triggered by design; rehearsing
  before a tag exists would need a parallel trigger path for marginal gain —
  the artifact-level defects it catches are rare and always patch-fixable.
  The manual-dispatch leg remains for ad-hoc pipeline validation.
- **Not a consumer instruction.** No README/INSTALL doc ever points a user at
  TestPyPI; it is an internal rehearsal surface only.

## 5. Phases, in shipping order

| Phase | Deliverable | Catches (from §1) | Cost |
|---|---|---|---|
| P1 | `release_context.py`: `workflows_parse_ok` + `ci_on_head` keys; skill Step-2 rule | v0.23.0 class | ~20 lines, no new deps |
| P2 | `scripts/release_dry_run.py` + skill Step 5.8 (tag-last) + Step-5 generated-pair staging rule | v0.23.1 + v0.23.2 classes | ~100 lines; +4 min per release (full) or seconds (`--fast`) |
| P4 | `publish.yml`: auto TestPyPI leg + install-smoke before the `pypi` hold, soft-gated | registry/metadata/rendering class | ~2 min pipeline time, zero human cost |
| P3 | tag-annotation dry-run trailer (cosmetic) | — | optional, last |

Each phase is independently shippable and independently verifiable
(`dos verify` on this plan's phases once stamped). The out-of-loop publish
gate is never weakened — every change here adds a witness earlier; none
removes one later.
