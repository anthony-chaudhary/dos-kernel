# 304 — pre-commit ecosystem distribution: make the shipped hook actually work

> The repo has shipped a `.pre-commit-hooks.yaml` since the v0.22.0 seed — the
> file that lets any team add the claim-vs-diff gate to their repo with four
> lines of `.pre-commit-config.yaml`. But the shipped hooks run at the WRONG
> STAGE: `stages: [commit]` fires at git's pre-commit stage, which runs BEFORE
> the commit object exists. `dos commit-audit` defaults to `HEAD`, and at that
> moment `HEAD` is the PARENT of the commit being made — so the hook audits the
> wrong commit, off by one, and blocks an innocent commit while naming the
> previous one. This plan fixes the stage semantics, adds the one missing line
> of glue (a range adapter for pre-commit's env-var contract), and then lists
> the hook where the ecosystem looks for hooks. pre-commit is one of the
> largest dev-tool distribution channels; a working four-line integration is a
> distribution surface, not just a bug fix.

*Status: P1 ships with this plan. P2 is an external PR (merge timing is the
maintainer's). P3 is deferred until the README lane is free.*

## 0. The facts the design rests on

- **pre-commit stage timing.** Git's `pre-commit` hook runs before the commit
  is created; `post-commit` runs after (and cannot block); `pre-push` runs
  before the push and CAN block it. The only stages where `commit-audit` can
  read the commit it is meant to judge are `post-commit` (advisory) and
  `pre-push` (gating).
- **The range contract.** At `pre-push`, the pre-commit framework exposes the
  pushed range as two env vars: `PRE_COMMIT_FROM_REF` (the remote tip, or the
  all-zeros SHA for a new branch) and `PRE_COMMIT_TO_REF` (the local tip). An
  `entry:` line is exec'd WITHOUT a shell, so `$PRE_COMMIT_FROM_REF` never
  expands — reaching the range needs one line of Python glue.
- **Empty diffs must still run.** The forgery case the gate exists for —
  `git commit --allow-empty -m "implement the cache"` — changes ZERO files, and
  a pre-commit hook with no matching files is SKIPPED unless `always_run:
  true`. Without that flag the gate skips exactly the commit it was built to
  catch.
- **Passing hooks are silent.** pre-commit hides the output of a hook that
  exits 0 unless `verbose: true` — so a `--warn-only` hook without `verbose`
  reports nothing, ever.
- **Vendor names live in drivers.** The adapter speaks pre-commit's env
  contract by name, so it is a layer-4 driver (`dos.drivers.pre_commit`), the
  same split as `hook_dialects` / `notify_slack`. It contains zero
  adjudication: every verdict byte comes from the unchanged `dos commit-audit`
  CLI (a driver may import the CLI — the established consumer→consumer edge,
  cf. `drivers/watchdog.py`).

## P1 — stage-correct hooks + the range adapter (this repo)

1. `src/dos/drivers/pre_commit.py` — `main(argv) -> int`: read
   `PRE_COMMIT_FROM_REF` / `PRE_COMMIT_TO_REF`; both present and FROM not
   absent/all-zeros → audit `FROM..TO`; new-branch zero-SHA → fall back to
   auditing the tip (`TO`) only, conservative and documented; no env at all →
   `HEAD` (the post-commit case). Append passthrough flags (`--warn-only`,
   `--json`, `--docs-ok`, `--sweep`), delegate to `dos.cli.main`, return its
   exit code untouched — the verdict IS the exit code.
2. Rewrite `.pre-commit-hooks.yaml`:
   - `dos-commit-audit` — the gate. `stages: [pre-push]`, `always_run: true`,
     `pass_filenames: false`, entry `python -m dos.drivers.pre_commit`.
   - `dos-commit-audit-warn` — the advisory floor. `stages: [post-commit]`
     (HEAD is the new commit there), entry adds `--warn-only`, `verbose: true`
     so the report is visible, `always_run: true` (post-commit passes no
     files).
3. Pin it: `tests/test_pre_commit_hook.py` — the manifest parses; no hook runs
   at a stage where `HEAD` is the parent (the off-by-one regression pin); the
   gate/warn contracts above; driver env→ref mapping; exit-code passthrough;
   an end-to-end tmp-repo case where an `--allow-empty` over-claim exits 1.
4. Keep the example `rev:` pin fresh: add `.pre-commit-hooks.yaml` to
   `tests/test_docs_version_drift.py` `LIVE_ONBOARDING_DOCS` and
   `scripts/release_bump.py` `_DOC_BANNER_FILES`.

Done-condition: suite green; `pre-commit try-repo . dos-commit-audit
--hook-stage pre-push` runs the gate against this repo.

## P2 — list the hook where the ecosystem looks (external — GATED)

Checked 2026-06-12: pre-commit.com no longer runs an open `all-repos.yaml`
directory. Its hooks page is hand-picked, and a PR adding a tool is closed
without comment unless the tool is "already fairly popular (>500 stars)".
So P2 is gated on traction, not on work here. Two things still hold today:
the four-line consumer YAML works with no listing at all (the `repo:` URL is
the distribution), and the hooks page links a Sourcegraph query over every
public `.pre-commit-hooks.yaml` — this repo surfaces there organically now
that the manifest is pushed. Revisit when the star count clears the bar.

## P3 — surface docs (deferred: README lane busy at authoring time)

A short "use it with pre-commit" block in the README integrations section +
`docs/QUICKSTART.md`, mirroring the four-line consumer YAML in the manifest
header. Until a `vX.Y.Z` tag containing P1 exists, consumers must pin `rev:`
to a SHA on master; cutting the next release closes that gap.
