# 290 — The agent-view harness, made durable: a litmus tier in the suite + a replayable skill

> **Status:** Phase 1 shipped (2026-06-10) — `tests/test_agent_surface.py` (AV1–AV5
> + the AV6 pins) with the AV6 session guard in `tests/conftest.py`; every litmus
> proven against its defect by a sacrificial red/green probe (evidence in the
> Phase 1 section below). Phases 2–3 open. Successor to the one-off instrument in
> `docs/reports/2026-06-10_agent-view-ab.md` (Arm A 2/4 → Arm B 4/4 at `ca931a5`,
> independently replicated in `aa60cf2`).

## The problem

The cold-clone agent-view A/B answered a real question — *does this repo's
agent-facing surface actually serve a stranger's agent?* — but as built it is a
one-off: the four task prompts live outside the repo (a sibling `.dosview\logs\`
scratch dir),
the grading rubric lives in a dated report's prose, and the grading itself was
performed by hand twice (two sessions, happily agreeing — but agreement between
two hand-graders is a luxury, not a mechanism). Nothing stops the seven defect
classes it found (D1–D8) from quietly regressing tomorrow.

## The thesis — split it along the trust ladder

The instrument has two parts with opposite economics, and they belong on
different rungs:

1. **What the A/B *taught* us is deterministic.** Each defect class reduces to a
   checkable invariant of the repo's bytes ("the committed settings carry no
   hooks", "the documented install line provides pytest", "running the suite
   modifies no tracked file"). Those are ORACLE-rung facts: free, exact,
   suitable as always-on suite tests. **This tier catches the regression.**

2. **What the A/B *measures* is nondeterministic and costs money.** A real
   headless agent journey (~$1/task, minutes of wall, an LLM in the loop) can
   never be a CI gate — it is JUDGE-rung: advisory, run on demand, valuable
   precisely because it exercises the live surface end-to-end. **This tier
   measures the experience** and finds the *next* defect class the litmus tier
   doesn't know about yet (the way the Arm B run itself surfaced D8).

Deterministic-first, expensive-adjudicator-on-the-residue, advisory-only — the
same discipline as `dos.judges`, applied to the repo's own front door.

## Phase 1 — the litmus tier: `tests/test_agent_surface.py`

One test per defect class, in the existing genre of `test_skill_pack_*.py` /
`test_install_drift.py` (grep + ground-truth assertions, no network, no agent):

| ID | Pins (defect) | Assertion |
|---|---|---|
| AV1 | D1 | The committed `.claude/settings.json` parses and has **no `hooks` key** (permissions only); `.claude/settings.local.json` matches a `.gitignore` rule. A cold machine must have nothing to error on. |
| AV2 | D2 | In AGENTS.md + CLAUDE.md, any fenced block that runs `pytest` is preceded (same block) by an install line whose extras **actually provide pytest** — resolved against `pyproject.toml`, not hard-coded (the `install_facts.py` pattern; extend that single-source rather than duplicating it). |
| AV3 | D3 | The lint command AGENTS.md documents **equals the blocking lint CI runs** (parse the workflow YAML; compare argv, not prose). |
| AV4 | D5 | The "When the user asks you ABOUT DOS" table exists in AGENTS.md, CLAUDE.md carries the pointer block, and every consumer move it names is **live**: each `dos` verb resolves in the CLI parser (`quickstart`, `init --hooks`, `doctor`, `verify`), each referenced file exists (`docs/INSTALL.md`, `claude-plugin/README.md`, `docs/QUICKSTART.md`). |
| AV5 | D6 | The documented suite size ("~3,900") is within ±15% of the **collected** test count (pytest `--collect-only -q` at session start is too slow to repeat — derive once via a session fixture). A hand-kept number rots; a banded number is honest. |
| AV6 | D8 | **Running the suite is effect-free on tracked files.** A session-scoped conftest fixture snapshots the set of modified tracked paths (`git status --porcelain`, tracked entries only) before the session and re-derives it at session end; assert no *new* entries. Delta-of-sets, not absolute cleanliness — the hot tree is legitimately dirty with concurrent work; the suite must only add nothing. |

Notes. AV6 is the strongest and the most kernel-shaped (evidence snapshot at the
boundary, pure comparison) — it would have caught D8 the day
`test_go_hook_parity.py` gained its corpus regen. D4 and D7 already have their
own pinning tests (the hermes skip-probe; `test_vendor_agnostic_kernel`) and
need no duplicate here.

### Phase 1 — shipped (2026-06-10): the red/green witness

A litmus that has never seen its defect is an unproven guard, so each one was
proven the sacrificial-probe way: the defect re-introduced in the working tree,
the test shown RED, the tree restored byte-exact (`git checkout HEAD -- <file>`),
the test shown GREEN. The evidence below is from the real probe runs (pytest
node ids + the failure headline; "exit" is the pytest process exit code):

| Litmus | Probe (the defect, reintroduced) | RED | GREEN after restore |
|---|---|---|---|
| AV1 (D1) | a non-cold-safe `dos hook …` command (no trailing `\|\| true`) added to the committed `.claude/settings.json` | `FAILED …::test_av1_committed_settings_hooks_are_cold_safe` — *"carries hook command(s) that are NOT cold-safe … they error on every Stop"* (exit 1) | `2 passed` (exit 0) |
| AV2 (D2) | AGENTS.md's build-block install degraded to bare `pip install -e .` | `FAILED …::test_av2_every_pytest_block_installs_pytest_first[AGENTS.md]` — *"a fenced block runs pytest without first installing an extra that provides it (need one of ['dev'] …)"* (exit 1) | `2 passed` (exit 0) |
| AV3 (D3) | AGENTS.md's documented lint widened to `ruff check .` | `FAILED …::test_av3_documented_lint_equals_ci_blocking_lint` — *"AGENTS.md documents 'ruff check .' but CI's blocking lint is 'ruff check src/dos src/dos_mcp'"* (exit 1) | `1 passed` (exit 0) |
| AV4 (D5) | the consumer table's `dos quickstart` move renamed to the dead verb `dos quickdemo` | `FAILED …::test_av4_every_consumer_move_resolves_in_the_cli_parser` — *"the consumer table tells agents to run `dos quickdemo`, but the CLI parser has no such command"* (exit 1) | `3 passed` (exit 0) |
| AV5 (D6) | AGENTS.md's suite-size sentence rotted to "~1,200 tests" | `FAILED …::test_av5_documented_suite_size_is_in_band` — *"AGENTS.md documents ~1,200 tests but the suite collects 4,055 (±15% band: 3,447–4,663)"* (exit 1) | `1 passed` (exit 0) |
| AV6 (D8) | a throwaway probe test appended a line to the tracked `LICENSE` mid-session (the D8 shape: a test mutating a committed file) | `ERROR at teardown … _suite_is_effect_free_on_tracked_files` — *"this pytest session left tracked files modified that were clean when it started: ['LICENSE']"* (`1 passed, 1 error`, exit 1) | probe deleted + `LICENSE` restored → `2 passed` (exit 0) |

Where the as-built shape deviates from the table above, deliberately:

- **AV5's count is free in a full run.** `tests/conftest.py` records
  `len(session.items)` at collection-finish, so CI pays nothing; only a partial
  run (a path/`-k`/`-m` selection — e.g. these probes) pays one
  `--collect-only -q` subprocess. Measured at ~6s, the plan's "too slow to
  repeat" caution was overstated — the fallback is cheap enough to run per
  partial session rather than memoize across sessions.
- **AV2 grew no new `InstallFacts` field.** The extras-providing-pytest set is
  resolved through `install_facts._project_table()` — the one reader that knows
  where the install facts live in `pyproject.toml` — so the single source is
  extended at its read seam without touching `scripts/`.
- **AV6's enforcement lives in `tests/conftest.py`, not the test module**, so it
  arms EVERY pytest session, including one that never collects
  `test_agent_surface.py`; the module pins the pure delta classifier and that
  the guard is registered (session-scoped, autouse). Abstains (never fails)
  when git itself is unavailable — absent evidence is not evidence of a
  violation.

Full-suite verdict on the shipped tree (foreground, 2026-06-10): two runs, both
`1 failed, 4046 passed, 8 skipped` (~5–6 min each), the one red identical both
times — the pre-existing live-WAL contamination flake
(`test_arbitrate_default_loads_live_wal_no_double_book`, documented as reddening
only while a concurrent host session writes its journal mid-suite; such a
session was demonstrably live during both runs). Adjudication: the nodeid passes
in isolation and its whole module passes alone (`31 passed`), and this tier's
diff touches neither the arbiter nor the WAL. This tier's 11 tests passed in
both full runs, and the AV6 guard rode both runs end-to-end reporting the suite
effect-free on tracked files.

## Phase 2 — prompts and grader move into the repo

- **Journeys as fixtures:** `tests/agent_view/journeys/{t1_orient,t2_install,t3_adopt,t4_test}.txt`
  — byte-copies of the `.dosview` prompts. Versioned, shared, byte-identical
  across all future arms by construction.
- **The grader as code:** `scripts/agent_view_grade.py` — input: a clone dir + the
  harness JSON; output: a typed per-journey verdict (points, evidence strings)
  as JSON, exit code = pass/fail. It gathers the read-backs at the boundary
  (does PROOF/ADOPT/TESTS.md exist; run *their* venv's `dos doctor`/`dos verify`;
  pytest counts byte-matched against an independent run) and classifies purely —
  the `classify(evidence, policy)` shape, living in `scripts/` because it
  operates ON the package (the kernel never imports it; same litmus as the
  release tooling).
- **The anti-gaming rule that makes in-repo prompts safe:** the agent under test
  can *read its own grading rubric* inside its clone. Therefore **a rubric point
  may exist only if it is verifiable by an independent read-back the agent
  cannot author** — an artifact my re-run corroborates, never a self-report. (A
  fabricated TESTS.md dies on the byte-match against the grader's own suite
  run; that is the docs/138 invariant doing the work.) Points that fail this
  rule are dropped, not hand-graded.
- **A gated live smoke** (the `DOS_TEST_BUILD_WHEELS=1` precedent):
  `DOS_TEST_AGENT_VIEW=1` + a `claude` CLI on PATH runs **T1 only** (~$0.20,
  ~30s) in a fresh clone and asserts the grader scores ≥2/3. Skipped by
  default, never in CI's default lane.

## Phase 3 — the skill: `.claude/skills/agent-view-ab/`

Repo-local dev tooling (the `/release` tier — operates on the package, not part
of it). The screenplay:

1. **Preflight** — clean question: is HEAD what you mean to measure? Is the
   runner CLI available? State the cost (~$4/arm) and get the operator's ack.
2. **Run an arm** — one fresh clone per journey, record each clone's HEAD,
   launch the headless runner with the Phase-2 prompt files, save the harness
   JSON. The runner command comes from config/env (the `$DOS_LLM_JUDGE_CMD`
   precedent), so the vendor is *data*, not skill text.
3. **Grade** — shell `scripts/agent_view_grade.py` per journey; the skill never
   hand-scores.
4. **Report** — fill a dated `docs/reports/` file from a template (Method,
   pre-registered metrics, per-journey table, residuals), commit by pathspec.
5. **Verifier seat (grade-only mode)** — re-derive grades from an existing run's
   artifacts without re-spending. This codifies what the second session did by
   hand in `aa60cf2`: replication should cost a command, not an afternoon.

A future Phase 4 — promoting the screenplay to a shipped generic SKP skill
("cold-clone audit" for any repo: orient/install/integrate/test are universal
journeys) — is deliberately deferred until a second host wants it. The shipped
skill pack's litmus (names no host) is satisfiable, but speculative
generalization is how skills rot.

## Hazards and non-goals

- **Never gate CI on the paid tier.** Nondeterministic + billed = advisory by
  construction. The litmus tier is the gate; the live tier is the instrument.
- **Model pinning.** Arms are comparable only within a model
  (`claude-opus-4-8` today). The harness JSON already records the model; the
  report template must surface it, and a model change resets the baseline.
- **Goodhart, owned.** Writing docs to ace these four journeys is the *intended*
  optimization — the journeys are the product surface. The exposure is rubric
  gaming, and the anti-gaming rule above (read-back-only points) is the
  mitigation; when adding journeys, add their read-backs first.
- **Wall-clock stays the weakest metric** on a shared box; the grader weights
  artifact success > turns > cost > seconds (the report's pre-registered order).
- **No kernel involvement.** Everything here is tests + `scripts/` + a skill.
  `dos doctor --check` does not learn about AGENTS.md — the agent surface is
  this repo's policy, not kernel mechanism.

## Ship order

Phase 1 first (it is pure suite work and immediately guards D1/D2/D3/D5/D6/D8
against regression), Phase 2 second (it makes any future arm cheap and
re-gradable), Phase 3 when the next arm is actually wanted.
