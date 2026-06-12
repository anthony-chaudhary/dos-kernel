# 310 — WKA: the work-kind account

> **Status:** P1–P3 shipped (2026-06-12, oracle-verified: P1 `67bae27` /
> P2 `215c9f7` / P3 `2675847`; P4 future). The forcing question (operator
> `/goal`): *better subdivision of the loop's goals/stats — "shipped a pick" may
> be too forced a unit; think in this direction and prove out the value-first
> items.* This doc records the gap, the one primitive built to fill it, and the
> two wirings that make it visible (the severity gate and a CLI verb).

---

## 1. The gap — one axis carries all the credit

A dispatch-loop iteration's stats today collapse onto a single work axis:
**did a pick ship?** Every surface reads that one bit:

- `event_severity.classify_event` grades a dispatch-family event `SHIPPED`
  iff `picks_shipped > 0` (or the gate said LIVE) — otherwise the event falls
  through to `NOOP` unless it is a *blocker*. The module's own measurements
  say **67% of dispatch-loop archives shipped 0 picks** — and all of those
  read as "drained", whatever else the iteration did.
- The gate verdict (`LIVE / DRAIN / STALE-STAMP / BLOCKED / RACE`) is a
  statement about the **backlog** ("is there dispatchable work left?"), not
  about the **work this iteration landed**. But the headline conflates the
  two: "drained" is forced to mean both *backlog empty* and *nothing
  happened*.
- The kernel already subdivides work by **magnitude over time**
  (`productivity`: a trend of per-step deltas) and by **price**
  (`efficiency`: work / tokens). Nothing subdivides it by **kind**.

So real-but-not-pick-shaped work is invisible at the stats layer:

| What the iteration actually did | What the stats say today |
|---|---|
| landed 4 commits on its lane, closed no phase | "drained" / NOOP |
| reconciled 3 stale stamps (the drive-mode self-heal) | "drained" / NOOP |
| caught a false "done" claim (`reconcile` → QUIET_INCOMPLETE) | "drained" / NOOP |
| raised 2 operator decisions | "drained" / NOOP |
| flipped a HELD unit back to OFFERABLE | "drained" / NOOP |

The last-but-one row is the sharpest: **catching a lie is the kernel's own
product** — the single most distinctive thing a DOS-supervised iteration can
do — and the stats grade it zero.

"Shipped a pick" is also forced in the other direction: it grades a one-line
phase and a forty-commit phase identically, and (because the count rides the
archive subject) it can grade a *claim* rather than a verified ship.

## 2. The primitive — `work_account.classify_work`

A pure kernel leaf, the **composition sibling** of the loop-economics family:

| Verdict | Module | The question | The shape |
|---|---|---|---|
| `liveness()` | `dos.liveness` | did state move at all? | a binary, lifetime count |
| `productivity()` | `dos.productivity` | is the work-per-step rate fading? | a trend |
| `efficiency()` | `dos.efficiency` | did the tokens buy work? | a ratio |
| **`work_account()`** | **`dos.work_account`** | **what KINDS of work landed?** | **a typed account** |

`WorkAccount` is a frozen dataclass of non-negative counters, one per work
kind. Each counter is a fact the **caller reduces at the I/O edge from
evidence the worker did not author** (the docs/138 invariant, same as every
sibling):

- `verified_ships` — phases the ORACLE confirms closed (`dos verify` /
  `dos reconcile` VERIFIED). Never the claim.
- `claimed_ships` — phases the iteration *said* it closed (the self-report,
  carried so the over-claim gap `claimed − verified − caught` stays visible,
  never believed).
- `catches` — claims the oracle REFUTED (`reconcile` → QUIET_INCOMPLETE, a
  commit-audit drift). The kernel's own product, now counted as work.
- `advance_commits` — commits the git machinery recorded on the leased lane
  that closed no phase (the `git_delta` evidence `liveness` already reads,
  folded into the stats). Partial progress stops reading as zero.
- `grooms` — durable plan-state bookkeeping: stamps reconciled, findings
  closed/added, inbox promotions (the counters `EventState` already carries
  for the replan family, generalized).
- `unblocks` — units that flipped HELD/BLOCKED → OFFERABLE this iteration.
- `surfaced` — operator-decision entries raised.

`classify_work(account) -> WorkVerdict` names the **dominant kind** by a fixed
precedence — most operator-valuable first:

    SHIPPED > CAUGHT > ADVANCED > GROOMED > SURFACED > IDLE

- **SHIPPED** — ≥1 *verified* ship. (Claims alone never reach SHIPPED — the
  one place this leaf is stricter than today's `picks_shipped` headline.)
- **CAUGHT** — no ship, but ≥1 false claim refused. Ranked above ADVANCED
  because a refused lie is operator-actionable; commits are routine.
- **ADVANCED** — no ship/catch, but real commits landed on the lane.
- **GROOMED** — bookkeeping only (grooms + unblocks).
- **SURFACED** — raised decisions only.
- **IDLE** — every counter zero: the honest "nothing witnessed". IDLE is a
  statement about *this iteration's work*; DRAIN remains a statement about
  *the backlog*. An iteration can be DRAIN-and-GROOMED; the two axes no
  longer share one word.

`account_lead_token(account)` renders the composed headline — every non-zero
kind in precedence order, e.g.
`1 pick shipped · 1 false claim caught · 4 commits advanced` — so the commit
subject / report line carries the whole account, not the forced binary. Like
`subject_lead_token`, the renderer is a function so it cannot drift from the
classifier (they read the same facts).

`merge(*accounts)` folds per-iteration accounts into a loop-level account
(counter-wise sum), so a loop record can close with one honest composition
line instead of `N picks shipped` alone.

Advisory, PURE, no I/O, timeless, names no host. Quality is never graded
(Wall-3): the account says a commit landed, never that it was good.

## 3. Phases

- **P1 — the leaf.** `src/dos/work_account.py` (`WorkAccount`, `WorkKind`,
  `WorkVerdict`, `classify_work`, `account_lead_token`, `merge`) +
  `tests/test_work_account.py`. ✓ when the suite pins precedence, the
  claimed-vs-verified split, the renderer, and the merge fold.
- **P2 — the severity wiring.** `EventState` grows an optional
  `account: WorkAccount | None = None`. For the dispatch family,
  `classify_event` consults it: verified ships → SHIPPED (as today); a
  CAUGHT/ADVANCED/GROOMED/SURFACED account lifts the event from NOOP to
  NOTICE (real work no longer reads as a non-event; the push sink's default
  threshold is BLOCKED-NEW, so peers see no new noise). `subject_lead_token`
  renders the composed account headline when an account is present.
  `account=None` stays **byte-identical** to today — pinned by the existing
  suite passing untouched.
- **P3 — the CLI verb.** `dos work-account --verified-ships N
  --claimed-ships N --catches N --advance-commits N --grooms N --unblocks N
  --surfaced N [--json]`, the productivity/efficiency boundary shape. The
  verdict IS the exit code: `SHIPPED/ADVANCED/GROOMED/SURFACED 0 / CAUGHT 3 /
  IDLE 4`, contract-error 2; registered in `dos exit-codes`.
- **P4 — the loop-record fold (future).** The dos-dispatch-loop skill's
  Step 4 archive records a per-iteration account and the merged loop account;
  `dos top` / `dos notify` surface the composition line. Deferred until the
  P1–P3 surfaces have soaked.

## 4. What this is NOT

- **Not a new gate.** The gate verdict and `loop_decide` are untouched; the
  account is observability — it changes what the stats *say*, never what the
  loop *does*. (A later consumer may choose to read it; that is its decision.)
- **Not a self-report channel.** Every counter is defined by the witness that
  authors it; `claimed_ships` is carried precisely so the claim can be
  compared, never believed. An agent cannot narrate its way from IDLE to
  ADVANCED — commits are git's to count.
- **Not a quality grade.** CAUGHT says a claim failed adjudication; ADVANCED
  says bytes moved. Neither says the work was right — that stays with the
  JUDGE rung.
