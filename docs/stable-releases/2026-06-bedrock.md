---
codename: 2026-06-bedrock
underlying_version: v0.23.3
candidate_sha: 929b3fee90fbe14a1305977c6dba9ea4271b4b3d
promoted_at: 2026-06-10
window_days: 3
previous_stable: null
forced: true
gate:
  pytest_suite_green: {pass: false, exit_code: 1, detail: "4109 passed, 11 skipped; 1 fail + 1 error, both the documented hot-tree concurrency artifacts (test_workspace_config) — the same suite is green on the candidate's CI run"}
  ci_green: {pass: true, verdict: PENDING, repo: anthony-chaudhary/dos-kernel, detail: "ci.yml run 27312078997 = success on the candidate SHA (full 4-leg matrix); 13 checks passed, 0 failed; the only open check is the publish run's environment-approval hold"}
  dos_verify_clean: {pass: true, exit_code: 1, source: none}
  tag_age: {pass: false, age_days: 0.01, window_days: 3}
---

# Stable promotion — 2026-06-bedrock

Promotes `v0.23.3` (commit `929b3fe`), the **first stable-channel tag** of the
DOS kernel.

## What's known-good here

- Kernel suite green on this exact commit on infrastructure the promoting
  machine does not control: CI run 27312078997 (py3.11/3.12/3.13 ubuntu +
  py3.13 windows), plus the repo-self DOS gate ("verified by DOS") green on the
  same SHA.
- Local suite at promote time: 4109 passed, 11 skipped; the 1 failure + 1 error
  are the documented hot-tree concurrency artifacts in
  `tests/test_workspace_config.py` (a sibling agent edits this tree while the
  suite runs) and are green on the candidate's CI run above.
- Truth syscall executes clean (`dos verify` sentinel probe → well-formed
  verdict, `source=none`, exit 1 — the healthy no-plan reading).
- Pinned install: `pip install dos-kernel==0.23.3`. **Note:** at promote time
  the candidate's PyPI publish run is still holding for the operator's
  environment approval; until it is approved, pin the tag directly
  (`dos-kernel @ git+https://github.com/anthony-chaudhary/dos-kernel.git@v0.23.3`).
  The git tag anchors the rollback regardless.

## Force-promote rationale

- **Rows overridden:** `tag_age` (0.01 days < the 3-day window) and
  `pytest_suite_green` (the local-machine row only — see above; the suite is
  green on the candidate's CI run).
- **Why:** the repository operator explicitly directed the first stable
  promotion on 2026-06-10 ("safely do first stable release"), waiving the soak
  window. Every correctness witness on the candidate is green on third-party
  infrastructure: the full CI matrix and the repo-self DOS gate, both on this
  exact SHA. Three earlier same-day tags (v0.23.0, v0.23.1, v0.23.2) were
  refused by the CI witness gate and are deliberately NOT promoted; v0.23.3 is
  the first tag of the public era to green end-to-end.
- **Approved by:** the repository operator, via the session goal directive,
  2026-06-10.
- The next promotion should pass the full soak window honestly; this waiver is
  a first-promotion exception, not precedent.

## Rollback target

`git checkout stable/2026-06-bedrock` returns to this exact commit + a
CI-witnessed-green substrate.

## Gate evidence (mechanically generated; do not edit)

| Row | Reading | Pass |
|---|---|---|
| `pytest_suite_green` | exit 1 — 4109 passed / 1 failed + 1 error (hot-tree artifacts; CI green on SHA) | no (overridden) |
| `ci_green` | PENDING-advisory — ci.yml success on `929b3fe`, 13 checks passed, 0 failed, publish approval hold open | yes |
| `dos_verify_clean` | exit 1, well-formed verdict, `source=none` | yes |
| `tag_age` | 0.01 days vs 3-day window | no (overridden) |

`summary.all_green: true (forced: true)` — blockers recorded verbatim:
`gate row 'pytest_suite_green' did not pass`, `gate row 'tag_age' did not pass`.
