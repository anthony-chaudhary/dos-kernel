# docs/317 — duplicate plan numbers must not cross-witness: slug-or-nothing stamps + the duplicate-number lint

> **Status:** P1 executed 2026-06-12 (`de45c1d`, re-stamped under the full
> slug in `c956d2a`); P2 executed 2026-06-12 (`8aea2c6`, carries `Fixes #80`
> — P1+P2 together meet the issue's done-condition). P3 not started.
> Tracking handle: issue
> [#80](https://github.com/anthony-chaudhary/dos-kernel/issues/80). The
> adjudicator for each phase is
> `dos verify docs/317_duplicate-plan-number-disambiguation-plan Pk`, never
> this sentence.
>
> **P1 fired live the day it shipped.** Between P1's commit and its verify,
> two sibling in-flight plans appeared on disk also numbered 317 — the new
> rule refused the bare `(docs/317 P1)` stamp as ambiguous (correct), and
> the slug-carrying re-stamp resolved it. The number stays with this plan
> (first wearer); the untracked siblings renumber before committing. Note
> the index reads the ON-DISK glob, not git — so an uncommitted stray plan
> makes the head ambiguous for everyone until it is renumbered or removed.
> That is the conservative direction, chosen on purpose.

## The problem — one number, two plans, one witness pool

Two concurrently-working agents each minted `docs/306` on 2026-06-12: the
conformance-suite plan (committed first) and the work-kind-account plan
(committed later the same day). Both then stamped commits with
`(docs/306 Pk)` trailers — and the ship oracle's grep rung keys the stamp on
the plan NUMBER, not the slug. So the two plans witnessed EACH OTHER's
phases:

```
$ dos verify --workspace . docs/306_conformance-suite-and-judge-tester-plan P1
SHIPPED ... P1 67bae27 (via trailer)     # <- the work-kind-account P1 commit
$ dos verify --workspace . docs/306_work-kind-account-plan P1
SHIPPED ... P1 67bae27 (via trailer)     # <- same witness, both plans
```

Why this matters: the truth syscall's whole claim is that a phase verdict
comes from evidence the claimant did not author. A number collision lets one
loop's stamps satisfy another loop's claims — an accidental forgery channel
between honest concurrent agents, and a deliberate one for a dishonest agent
(stamp `(docs/NN Pk)` for any NN you want closed). The verdict for a given
(plan, phase) also silently CHANGED when the second plan landed — the same
query resolved to different commits before and after, with no signal.

The collision was resolved operationally (the junior plan renumbered
306 → 310, its phases re-stamped), but the shape recurs: the repo carries a
latent duplicate today — `docs/295_release-dry-run-tag-last-and-the-testpypi-rehearsal-plan.md`
and `docs/295_the-transport-pipe-wedge-stdin-hygiene-for-evidence-subprocesses.md`
share the `docs/295` stamp handle — and two loops can leapfrog into a fresh
collision any day they both mint a number.

## Where the leak lives (mechanism, today's code)

- `phase_shipped._series_variants("docs/306_work-kind-account-plan")` returns
  the full id PLUS its number head `docs/306` — the underscore-basename
  bridge (docs/289) that lets a short trailer stamp witness a full-id query.
- The trailer rung (Pass 1a′) builds its series alternation from BOTH
  spellings. So a `(docs/306 P1)` trailer — stamped by EITHER plan — matches
  a query for EITHER plan. The release-prefix and body rungs inherit the
  same head spelling through the variant set.
- Nothing reads "how many declared plans share this number." The oracle's
  plan-doc map (`oracle.default_plan_doc_map`) keys on the full series id;
  `dos lint` checks lanes and reasons only.

## The fix's one rule

The same direction every kernel gate takes: **the fix may only refuse MORE —
it must never witness a stamp it could not witness before** (the
`admissible_under_floor` discipline, docs/113, applied to the stamp
grammar). The duplicate-number facts are gathered at the boundary (the same
plans glob the workspace already declares, `cfg.paths.plans_glob`) and
handed into the pure matchers as data — I/O at the boundary, data to the
core. A workspace with NO plan docs builds an empty index and the behavior
is byte-identical: `verify` still needs no plan
(`tests/test_verify_no_plan.py` stays green by construction).

### P1 — slug-or-nothing when the number is shared (the oracle half)

- **Boundary:** derive a shared-number index `{number_head: [basenames]}`
  from the same glob walk `default_plan_doc_map` already performs; a head
  with ≥ 2 basenames is AMBIGUOUS. Thread it into `phase_shipped`'s
  `_Matchers` the way the stamp convention already travels (CLI, library
  `cfg=`, and the subprocess bootstrap — the design-law-2 funnel, one
  resolve per entrypoint).
- **Pure rule:** when the QUERY's number head is ambiguous,
  `_series_variants` drops the bare-head spelling. The trailer/release/body
  alternations then accept only the full slug: a `(docs/306 P1)` stamp
  witnesses NOTHING while two docs/306 exist; a
  `(docs/306_work-kind-account-plan P1)` stamp witnesses only its own plan.
- **Typed ambiguity, never a silent pick:** a query BY the bare head
  (`dos verify docs/306 P1`) while the head is ambiguous answers
  `shipped=false` with a distinct source (`ambiguous-number`) naming both
  basenames — the operator learns WHY the abstain happened, in the verdict,
  not a log.
- **Done-condition:** a new `tests/test_duplicate_plan_numbers.py` builds a
  throwaway workspace with two same-numbered plan files and pins: plan A's
  phase refuses a commit stamped with the bare head; a slug-carrying stamp
  still witnesses its own plan and only its own plan; the bare-head query
  returns the typed ambiguity; a workspace with zero/one plan per number is
  byte-identical to today. Witness: that file green +
  `tests/test_verify_no_plan.py` green +
  `dos verify docs/317_duplicate-plan-number-disambiguation-plan P1`.

### P2 — the duplicate-number lint (the declaration half)

- `dos.config_lint` gains a finding kind `PLAN_NUMBER_DUPLICATE`: two files
  matched by the workspace's plans glob whose basenames share the leading
  number. The finding names BOTH basenames (the disjoin move is a rename, so
  the message must say which two files collide). Severity: warning — the
  verdicts stay truthful per plan once P1 lands; the duplicate is dead-policy
  shaped, not corruption.
- The declared plan basenames are a new OPTIONAL input to `lint()`, gathered
  at the CLI/doctor boundary like every other fact — `lint()` stays pure and
  callable with today's two arguments.
- Folded into `dos lint` and `dos doctor --check` exactly like the existing
  findings. Note the scope is the GLOB the workspace declares: the docs/295
  pair above collides in stamp-handle space, but its second file is not
  `*-plan.md`-suffixed — whether lint sees it follows the workspace's own
  `plans_glob`, the kernel invents no wider walk.
- **Done-condition:** a lint fixture with two same-numbered plan files emits
  the finding naming both; a clean fixture emits nothing; `dos lint --json`
  carries the new kind. Witness: the config-lint suite green +
  `dos verify docs/317_duplicate-plan-number-disambiguation-plan P2`.

### P3 — surface + process (catch the NEXT collision the day it lands)

- `dos plan --once` renders one ⚠ DUPLICATE-NUMBER row per ambiguous head
  (reusing P2's pure check) — the board a human already reads is where the
  collision should appear, the day it lands.
- Give the renumber playbook a permanent public home (a section in
  `docs/HACKING.md`): `git mv` the junior plan → update in-tree references →
  one renumber commit naming both plans and the cause → one `--allow-empty`
  re-stamp commit per shipped phase carrying the original ship SHA → re-run
  `dos verify` on BOTH plans. Today that playbook lives in an issue comment
  and an agent's memory — neither is where the next collision's operator
  will look.
- **Done-condition:** a board-render test pins the duplicate row; the
  HACKING.md section exists. Witness:
  `dos verify docs/317_duplicate-plan-number-disambiguation-plan P3`.

## What this does NOT fix (named residue)

History is immutable. Three commits still carry bare `(docs/306 Pk)`
subjects from before the renumber; with only ONE docs/306 left on disk the
head spelling is unambiguous again, so those commits witness the surviving
(conformance) plan even though the work was the other plan's. The verdict is
truthful today only because both plans' P1–P3 genuinely shipped — exactly
the coincidence issue #80 names. P1's rule keys on the DECLARED set, so it
cannot see a duplicate that was renumbered AWAY. A historical resolver
(prefer slug-carrying stamps whenever ancestry contains a same-number
sibling) would need a history walk per query and is deliberately out of
scope: the P2 lint and P3 board shrink the window in which two plans
coexist, which bounds how much ambiguous history can accumulate.
