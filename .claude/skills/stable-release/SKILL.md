---
name: stable-release
description: Promote an already-shipped rolling release (vX.Y.Z) of the DOS kernel to a named stable channel — gated on a green kernel suite + a green third-party CI run on the candidate + a clean truth syscall + a soak window. Writes an evidence file and adds a stable/<codename> git tag on the same commit. Does NOT bump versions or build new artifacts.
disable-model-invocation: false
user-invocable: true
allowed-tools: Read, Edit, Grep, Glob, Bash, Write
argument-hint: "<codename> [--from vX.Y.Z] [--window-days N] [--force-promote]"
output_root: none
---

# Stable Release — a named channel on top of rolling `/release` (DOS)

`/release` ships a rolling tag (`vX.Y.Z`) whenever DOS lands user-visible change.
That tag promises "this commit merged" — it does **not** promise "the substrate
was provably trustworthy here." This skill adds a second, less-frequent channel
that does make that promise.

A stable release is a **promotion of an already-shipped commit**, not a new
build. No new version is minted. No new wheel or zip is produced. The gate is
read at promote time, frozen into an evidence file, and a second annotated tag
(`stable/<codename>`) is pinned on the same commit.

> **This is the DOS adaptation of `job`'s stable-release skill.** job gates on
> apply-loop hero metrics (silent-failure share, PSV verified-success rate,
> funnel-stage regressions) and KEEP-slot baselines from `execution-state.yaml` +
> `baselines.yaml`. **None of that exists in DOS** — DOS is a domain-free trust
> substrate, not an apply pipeline; it has no runtime funnel, no hero metric, no
> baseline ledger. So the gate is re-grounded on the only "known-good" signals
> DOS actually has: a green kernel suite, a clean truth syscall, and a soak
> window. See "The gate" below.

**Trunk is `master`.** The stable tag is pinned on a commit reachable from
`master`; the push targets `master`'s remote.

**The kernel-vs-tooling boundary.** The gate context script
(`scripts/stable_release_context.py`) and this skill are **dev/release tooling
that operates ON the package** — never imported BY `dos.*`. The
"kernel imports no host, no `scripts/`" litmus in `CLAUDE.md` stays intact.

## Relationship to other systems

| System | Owns | Stable-release reads | Stable-release writes |
|---|---|---|---|
| `/release` (`scripts/release_bump.py`) | rolling `vX.Y.Z` tag, version markers | the latest `vX.Y.Z` as promotion candidate | nothing — never re-bumps |
| the kernel suite (`pytest`) | substrate correctness | exit code (green == eligible) | nothing |
| the truth syscall (`dos verify`) | ground-truth adjudication | exit + verdict (well-formed == eligible) | nothing |
| git tags | the channel itself | `stable/*` collision check + previous-stable | one new `stable/<codename>` tag |

The spec deliberately introduces **no** new state machine, database table, or
pipeline event. It reuses git tags + the suite + the syscall.

## When to invoke

The user (or a scheduled cron) invokes this skill. Typical triggers:

- After a `/release` that landed a meaningful kernel change and you want a
  rollback anchor that says "the substrate was provably good here."
- Quarterly hygiene — pick the highest `vX.Y.Z` that satisfies the gate.
- After a regression rollback, to mark the recovered state.

There is **no calendar cadence**. Promotion is event-driven. The gate either
passes today or it doesn't.

## Codename convention

Operator picks one short word at promote time, prefixed by year-month:
`stable/2026-06-aardvark`. Memorable, sortable, and rollback becomes meaningful
(`git checkout stable/2026-06-aardvark` says "the substrate was good here" in a
way `git checkout v0.3.0` never claimed).

Validate: `^\d{4}-\d{2}-[a-z][a-z0-9-]{2,20}$` (the context script enforces this).
Reject ambiguous reuses by checking `git tag -l 'stable/*-<codename>'`.

---

## Step 1 — Pre-digest the gate context

One helper collapses every gate read into one JSON payload:

```bash
python scripts/stable_release_context.py --codename <codename>
#   --from vX.Y.Z       pin the candidate (default: latest semver tag)
#   --window-days N     min soak age for the candidate tag (default 3)
#   --skip-pytest       smoke / dry inspection only (do NOT use for a real promote)
#   --force-promote     report blockers but mark all_green-eligible (records rationale)
```

The script returns:

```json
{
  "candidate_tag": "v0.3.0",
  "candidate_sha": "abc123…",
  "codename": "2026-06-aardvark",
  "window_days": 3,
  "previous_stable": {"tag": "stable/2026-05-zephyr", "sha": "…"},
  "tag_collision": false,
  "idempotency": {
    "tag_exists": false, "tag_sha": null, "tag_matches_candidate": null,
    "evidence_file_exists": false, "evidence_path": "docs/stable-releases/2026-06-aardvark.md"
  },
  "gate": {
    "pytest_suite_green": {"name": "...", "pass": true, "exit_code": 0, "summary_tail": "..."},
    "ci_green":           {"name": "...", "pass": true, "verdict": "GREEN", "advisory": false, "reason": "..."},
    "dos_verify_clean":   {"name": "...", "pass": true, "exit_code": 1, "verdict": {...}},
    "tag_age":            {"name": "...", "pass": true, "age_days": 5.2, "window_days": 3}
  },
  "summary": {"all_green": true, "blockers": [], "forced": false}
}
```

If `summary.all_green` is `false`, the skill **stops** and reports the failing
rows — exactly which gate row blocked promotion and its reading. This is the
entire point of the skill; never paper over a red gate.

> **`dos_verify_clean` exits 1 on a healthy no-plan repo — that is a PASS.** The
> truth syscall's exit code carries the *ship verdict* (0 shipped / 1 not), not
> execution health. The gate probes with a sentinel `(plan, phase)` that never
> shipped, so a healthy syscall returns `shipped=false, source="none"` and exit
> 1; the script treats a well-formed verdict dict + exit ∈ {0,1} as the pass. A
> crash (non-{0,1} exit, traceback, no JSON) is the only failure. Don't "fix"
> this by demanding exit 0.

## Step 2 — Decide the promotion candidate

By default, promote the most recent `vX.Y.Z` tag that has soaked ≥ `window_days`
(so the gate had time to observe it). If `--from vX.Y.Z` is passed, use that.

Refuse to promote (the context script flags these as blockers):

- A tag already promoted (a `stable/*` tag points at the same SHA → `tag_collision`).
- A tag younger than `window_days` unless `--force-promote` is set with a written
  rationale in the evidence file.

If `candidate_tag` is `null`, there is **no `vX.Y.Z` tag yet** — DOS has not cut
a rolling release. Stop and tell the operator to run `/release` first; a stable
promotion has nothing to promote.

## Step 2.5 — Idempotent re-run (a partial-failure replay is SAFE)

A promotion writes two durable side-effects in sequence — the `stable/<codename>`
tag (Step 5) and the `docs/stable-releases/<codename>.md` evidence file (Step 4 +
its commit in Step 6). If a prior run was interrupted between them (operator
Ctrl-C, a `git push` that failed), **re-running with the same codename must be
safe** — detect what already exists and skip it, never error or write a duplicate.
This is the idempotency property the userland app bakes into its promote
orchestrator; DOS reads it from the context script's `idempotency` block and the
skill body acts on it (DOS keeps the promotion script-light, so this is data the
skill obeys, not a wrapper).

Read `idempotency` from Step 1 and branch:

- **`tag_matches_candidate: true`** (the `stable/<codename>` tag already exists and
  points at the candidate SHA) → the tag step already succeeded. **Skip Step 5**
  (do not re-tag — a stable tag is never re-pointed); record it on an
  `idempotent_skips` list for the final summary. This is the clean replay case, NOT
  a `tag_collision` blocker (the context script already excludes a same-codename tag
  from the collision check).
- **`evidence_file_exists: true`** → the evidence file is already written. Re-read
  it; if its gate snapshot matches today's, **skip re-writing Step 4** and its Step 6
  commit (note the skip). If it's stale/partial, finish writing it and commit — the
  file is the artifact, completing it is the point.
- **`tag_exists: true` AND `tag_matches_candidate: false`** → the codename already
  tags a **different** commit (the word was reused). This is a hard **blocker** the
  context script already raised — **stop**; a stable tag is immutable, so pick a
  fresh codename and add a `## Supersedes` note in the new evidence file.
- **All false** → a normal first run; proceed through Steps 4–6 as written.

The load-bearing property: every side-effect detects its own already-done state, so
the skill can be re-run after any partial failure and converges to one tag + one
evidence file. Report every skip under `idempotent_skips` in the final summary so
the operator sees what the replay reused versus wrote.

## Step 3 — The gate (the load-bearing step)

The gate is four rows, all from the context script. Each is a real check, not a
remembered number:

| Row | Source | Pass condition |
|---|---|---|
| `pytest_suite_green` | `python -m pytest -q` | exit 0 — the kernel suite is green *on the promoting machine*. The substrate's entire value is deterministic, correct verdicts; a red suite means it can't anchor a stable channel. |
| `ci_green` | the CI/Checks oracle (`dos.drivers.ci_status`) on the candidate commit | **not RED** — the third-party CI record, not a local self-report. GREEN passes; **RED blocks** (a real, accountable failure, e.g. a Windows/py3.11 matrix leg the promoter's box never ran); PENDING/NO_SIGNAL are *advisory passes* (the local suite row stays load-bearing). See the asymmetry note below. |
| `dos_verify_clean` | `dos verify` (sentinel probe) | well-formed verdict dict + exit ∈ {0,1} — the truth syscall executes cleanly (see the exit-code note above). |
| `tag_age` | committer date of the candidate tag | age ≥ `window_days` — the candidate soaked, not a just-cut tag promoted in the same breath. |

> **`ci_green` is asymmetric — it can only ADD a blocker, never remove one.** It is
> the substrate eating its own distrust (docs/93): `pytest_suite_green` is a
> self-report from the very machine cutting the promotion, so `ci_green` reads the
> verdict the CI system recorded on infrastructure the promoter doesn't control.
> Only **RED** blocks (CI proves the build is broken — the row's whole reason to
> exist, catching matrix-leg failures invisible locally). **PENDING** (CI not
> finished) and **NO_SIGNAL** (`gh` unwired/unauthed, or the candidate was never
> pushed) are advisory passes — hard-blocking on them would make the gate
> unrunnable offline, contradicting the oracle's fail-safe "never fabricate a
> verdict" discipline. The local pytest row remains the load-bearing suite gate; CI
> is purely additive signal. Use `--skip-ci` for an offline smoke. If `ci_green` is
> PENDING and you want CI-verified promotion, wait for the run to finish and re-run
> the gate rather than `--force-promote`.

This row is itself the "richer known-good history across consumers" the gate was
always designed to grow into: as more signals appear (an N-day clean-`dos verify`
soak, a consumer-side loop reporting back), additional rows thread into
`stable_release_context.py` the same way — **without touching the kernel.** That is
the package's own modularity rule: policy (the gate) is data + a driver-shaped
script; mechanism (the kernel) stays untouched.

## Step 4 — Write the evidence file

Single markdown file at `docs/stable-releases/<codename>.md` (create the dir on
the first promotion). Frontmatter is the gate JSON snapshot; body is
human-readable.

```markdown
---
codename: 2026-06-aardvark
underlying_version: v0.3.0
candidate_sha: abc123…
promoted_at: 2026-06-15
window_days: 3
previous_stable: stable/2026-05-zephyr
gate:
  pytest_suite_green: {pass: true, exit_code: 0}
  ci_green: {pass: true, verdict: GREEN, advisory: false, repo: anthony-chaudhary/dos-kernel}
  dos_verify_clean: {pass: true, exit_code: 1, source: none}
  tag_age: {pass: true, age_days: 5.2, window_days: 3}
---

# Stable promotion — 2026-06-aardvark

Promotes `v0.3.0` (commit `abc123…`).

## What's known-good here

- Kernel suite green (`pytest -q`, exit 0) at this commit.
- Truth syscall executes clean (`dos verify` → well-formed verdict).
- Candidate soaked 5.2 days (≥ 3-day window).

## Rollback target

`git checkout stable/2026-06-aardvark` returns to this exact commit + a
provably-green substrate.

## Gate evidence (mechanically generated; do not edit)

[full gate table from the context JSON]
```

This file is the artifact `/stable-release` exists to produce. Everything else is
bookkeeping. **Use the date from the operator / the system clock provided in
context — do not invent a timestamp.**

## Step 5 — Tag

```bash
git tag -a stable/<codename> <candidate_sha> -m "stable/<codename> — promoted from v0.3.0"
git push origin stable/<codename>
```

**Idempotent skip (Step 2.5):** if `idempotency.tag_matches_candidate` was `true`,
the tag already exists on the right commit — **do not run these commands**; record
`idempotent_skips: stable/<codename> tag (already present)` and move on. If only the
local tag exists but the push didn't land (a rare interrupt), re-running
`git push origin stable/<codename>` is safe and idempotent.

Never delete or re-tag a stable tag. Mistakes get a new codename + a
`## Supersedes` section in the next evidence file.

## Step 6 — Commit the evidence file

The evidence file is a real doc; commit it (it is not the promoted commit — the
tag points at the *candidate*, the evidence lands as a fresh commit on `master`):

Stage and commit **by explicit pathspec** (`-- <path>`) — `master` here is a
concurrently-written tree, and a bare `git commit` would sweep another session's
staged content under this message (the same hot-tree discipline `/release` Step 5
follows):

```bash
git add docs/stable-releases/<codename>.md
git commit -m "docs/stable: promote v0.3.0 to stable/<codename>" -- docs/stable-releases/<codename>.md
git push origin master
```

**Idempotent skip (Step 2.5):** if `idempotency.evidence_file_exists` was `true`
and the file's gate snapshot already matches today's, the evidence commit likely
already landed — check `git log` for the `docs/stable: promote … <codename>` commit;
if present, **skip this step** and record `idempotent_skips: evidence file +
commit (already present)`. Only write + commit if the file is missing or stale.

## Step 7 — Final summary

Print:

- Codename + underlying tag + commit SHA (short)
- Gate result table (one row per check, reading vs pass/fail)
- Evidence file path
- Stable tag + push status
- **`idempotent_skips`** (if any) — each side-effect a re-run reused rather than
  re-created (the tag, the evidence file, the evidence commit), so the operator
  sees this was a replay, not a fresh promotion.
- Previous stable, for the rollback hint

---

## Supporting script

**`scripts/stable_release_context.py`** produces the Step 1 JSON. It runs the
suite, probes the truth syscall, computes tag age, checks for a `stable/*`
collision, and reports the `idempotency` block (Step 2.5 — whether this codename's
tag + evidence file already exist) so a partial-failure re-run is safe. It is
dev/release tooling — never imported by `dos.*`. There is no heavier orchestrator
(job's `stable_release_promote.py` / `agents.sr.*`): DOS's gate is small enough
that the skill body drives the tag + evidence-file steps directly, and keeping it
script-light avoids adding surface the kernel ethos discourages. The idempotency is
therefore **data the skill obeys**, not a wrapper that enforces it — the same
mechanism/policy split the kernel keeps everywhere.

## Anti-patterns (don't do these)

- **Don't bump versions.** This promotes an existing tag. If you're editing
  `pyproject.toml` or `src/dos/__init__.py`, you're in the wrong skill (use
  `/release`).
- **Don't build new artifacts.** No new wheel, no new zip. The git tag IS the
  artifact in DOS.
- **Don't `--skip-pytest` for a real promotion.** That flag is for dry inspection
  only; the green suite is the load-bearing gate row.
- **Don't `--skip-ci` to dodge a RED CI run.** The flag is for offline smokes. If
  `ci_green` is RED, the build is broken on a leg your machine didn't run — fix it,
  don't skip it. (PENDING/NO_SIGNAL already don't block; you only reach for
  `--skip-ci` to silence them, which is fine for a dry run and wrong for a real one.)
- **Don't override a red gate without writing why.** `--force-promote` is allowed,
  but the evidence file must contain a `## Force-promote rationale` section
  explaining which row failed, why it was overridden, and who approved it.
- **Don't promote two stable tags in one window** without a `--force-promote`
  rationale.
- **Don't fold the rolling channel into the stable channel.** Two tags, two
  audiences: rolling = "latest code," stable = "last provably-good substrate."

## What this skill consciously punts

- **A stable install pointer.** job flips `stable_current.txt` via
  `install_versioned.py`; DOS has no versioned-install snapshot at all (it's a
  pip package), so there is no pointer to flip. `pip install dos-kernel==<underlying>`
  pins the promoted version directly — note the dist name is `dos-kernel`, not
  the bare `dos` (an unrelated PyPI package).
- **GitHub Releases entry for stable tags.** The evidence file + annotated tag are
  the canonical artifacts. Revisit if operators ask.
- **Auto-promotion via cron.** Easy to add later (`/loop` + this skill), but the
  first few promotions should be operator-driven so the gate's calibration gets
  validated by hand.
- **Richer gate rows** (multi-day soak, consumer feedback). Threaded into the
  context script when DOS has the history to support them — not before.
