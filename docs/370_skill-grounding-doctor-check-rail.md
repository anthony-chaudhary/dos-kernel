# docs/370 — skill-grounding as a first-class `dos doctor --check` rail

> **Status:** SHIPPED. Closes #201.
>
> A third `dos doctor --check` rail asks the question `skill_grade.check_verdict`
> already answers — *do the skills this workspace ships ground their own
> belief-bits, or self-certify them?* — over the workspace's OWN skills, honoring
> the existing honesty floor. INFO-severity by default (surface, don't block);
> `--skill-strict` opts into a gating verdict.
>
> **Phase stamp.** Phase `P1` (the skill-grounding rail) shipped as `c743791`;
> `dos verify --workspace . docs/370 P1` resolves it via this commit's
> `(docs/370 P1)` trailer (the oracle does not read the `Status:` line — by
> design, that sentence is a self-report).

## The gap

`dos doctor --check` runs two rails today:

- **`config_lint`** (docs/227) — dead lanes / dead reasons in the taxonomy.
- **the wiring-drift rail** (#190, `--wiring`) — a runtime whose DOS hook block
  silently unwired.

Neither asks the question the kernel's one rule, aimed at a skill, makes
checkable: *does a shipped skill set its "done / safe / found" bits from a
read-back the agent did not author, or from its own say-so?* That question has a
verb already — `dos skillify --grade --check`, which wraps
`skill_grade.check_verdict` with a sound floor (`_CHECK_MIN_SCORED = 8`, default
fraction `0.6`, sparse → skip-not-fail). It is the right gate. It was just on a
separate verb the standard health check never invoked, so an operator running
`dos doctor --check` in CI never saw a self-certifying skill they shipped.

## What shipped

A third rail in `cmd_doctor`, computed by a pure helper
`_skill_grounding_findings(cfg, strict=…)` next to `_state_health_findings` and
`_wiring_drift_rows`:

1. Resolve the workspace's OWN skills — `cfg.paths.root / ".claude" / "skills"`,
   every `SKILL.md` under it. NOT the wheel's generic pack (`src/dos/skills/`):
   those are first-party and already pinned green by `tests/test_skill_grade.py`
   (`test_real_grounded_skills_are_not_falsely_failed`). The reframe in #201 is
   right that the host's OWN skills are the ones whose trust-seam no one audited —
   so those are exactly what this rail grades.
2. Grade each with `skill_grade.grade_skill` against the `dos doctor` facts (the
   verb names as data, never a literal), then `check_verdict`.
3. A `fail` verdict → one finding string naming the skill and carrying the
   `why` (the re-derivable reason: `grounding NN% < 60% floor (K review
   candidate(s) over M bits)`). A `skip` or `pass` → no finding (the honesty
   floor: a sparse / pure-prose / all-grounded skill is never flagged —
   `grounded_fraction is None` is N/A, never a 0).

The rail is **fail-soft**: any error resolving or reading a skill is swallowed
(a report row must never break `doctor`), exactly as the #200 footer hint is.

## The three open questions, resolved

1. **Severity — `info` by default, `--skill-strict` to gate.** An ungrounded
   shipped skill is surfaced (the operator sees the named skill + reason) but does
   NOT fail `--check` / block CI by default — a workspace may legitimately ship a
   pure-prose skill, and `skill_grade`'s own floor already declines to judge those
   (so the only skills that reach a `fail` are dense self-certifiers). The
   default lean in the issue was `info`; this honors it. `dos doctor --check
   --skill-strict` promotes the finding to a gating one for a workspace that wants
   its skills' grounding enforced. This mirrors `config_lint`'s severity model:
   `info` is surfaced-but-cosmetic (docs/227 §4), `error`/`warn` gate.

2. **Scope — the host's OWN skills, not the shipped generic pack.** The reframe
   argues *especially* the host's own skills; the generic pack is first-party and
   continuously graded by the suite. So the rail reads
   `cfg.paths.root / ".claude" / "skills"` — resolved from the workspace root the
   way `dos doctor --json` reports it, never a literal path. A host that ships no
   `.claude/skills/` produces no finding (nothing to grade).

3. **Double-surfacing — the rail wins, the footer hint stands down.** Issue #200
   added a doctor footer line (`workspace skills    N  (dos skillify --grade …)`)
   pointing at the verb. When `--check` runs, the rail now DOES the grading, so
   the footer hint would be redundant noise. The fix: suppress the footer hint
   line when `check_requested` is true. Outside `--check` (a bare `dos doctor`)
   the footer hint stands — it is the right nudge when no rail ran. An operator
   sees the verdict once: the rail under `--check`, the hint otherwise.

## Done condition (met)

`dos doctor --check` in a workspace with a clearly self-certifying skill (≥8
scored bits, grounded fraction < floor) emits a finding naming that skill with a
re-derivable reason; the config_lint / wiring rails are unaffected; a pure-prose
or all-grounded skill produces no finding. Pinned by
`tests/test_skill_grounding_rail.py`.

## Layering

The rail is a layer-3 CLI helper (it reads files and shells the pure
`skill_grade` core; mints no verdict of its own — `check_verdict` does). It names
no host: the skills dir is `cfg.paths.root / ".claude" / "skills"` (the workspace
root from config), the verb names are `dos doctor` facts. A shipped generic skill
names no host — and this rail grades the HOST's skills, surfacing exactly the
ones the generic-pack litmus never covers.
