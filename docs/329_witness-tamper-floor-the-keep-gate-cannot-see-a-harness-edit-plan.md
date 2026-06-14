# 329 — The witness-tamper floor: the keep-gate cannot see a candidate that weakens its own harness

> **Status:** 📐 **DESIGN — plan first, no code yet** (2026-06-13). Closes the
> design half of #35. Implementation ships under this plan's phases, each
> adjudicated by `dos verify --workspace .
> docs/329_witness-tamper-floor-the-keep-gate-cannot-see-a-harness-edit-plan
> "Phase N"` — never by a `> **Status:**` sentence. Until P1 lands the phases
> answer `NOT_SHIPPED via none` (the evidence horizon, not a lie).

## The one idea

The keep-gate ([[280]], `dos.improve`) keeps a candidate self-improvement only
when three witnesses the candidate's author did not write agree: the suite is
green, the truth syscall is clean, the env-measured metric strictly improved.
Every one of those witnesses is gathered by **running something that lives in
the same tree the candidate just edited** — the suite's configuration, the
host's metric-measurement script, the `dos.toml [improve]` table itself.

So a candidate whose diff *weakens the harness* — skips a test class, inflates
what the metric reader counts, relaxes a threshold — then measures
green-and-improved **on its own tree** and is KEPT. Each individual witness is
still env-authored (the runner ran, git diffed, the oracle spoke), but the
candidate chose *what the runner runs*. The keep-bit is non-forgeable in the
[[230]]/[[234]] sense — no string the loop writes moves REVERT→KEEP — and yet
the loop moved the verdict anyway, by editing the measuring instrument instead
of the claim.

> **This is `SELF_MODIFY` generalized.** The admission guard
> (`dos.self_modify`) already refuses a *lease* whose tree touches the kernel's
> own running code — "a live loop must not rewrite the kernel adjudicating it."
> The same hazard, one layer in: a *candidate* must not rewrite the witnesses
> adjudicating it. The fix is the same shape — one env-authored bit, a
> refuse-first floor, tighten-freely / loosen-signed — moved from the lease
> boundary to the keep-gate.

## 0. The position (carry it intact)

Three things this plan is NOT, so a later phase does not drift into them:

- **Not a new kernel module.** The floor is one field on
  `improve.CandidateEvidence` and one branch in `improve.classify`. The bit is
  computed by the **driver** (`dos.drivers.self_improve`) from
  `git diff --name-only`, exactly where the suite/truth/metric facts are
  already gathered. Kernel stays pure: `classify(evidence, policy)`, no git, no
  glob, no I/O ([[improve.py]] arbiter rule).
- **Not a content judge.** The floor does NOT try to decide whether a harness
  edit is *benign* or *malicious* — that is the unjudgeable distinction
  ([[234]]'s non-distillability). It asks only the env-authored question
  `git` can answer with zero loop-authored bytes: *did the candidate's diff
  touch a declared witness path, yes or no?* The disposition of a yes is
  policy (§4), but the bit itself is a fact, not an opinion.
- **Not the `SELF_MODIFY` predicate relocated.** `SELF_MODIFY` guards *this
  repo's kernel source* at the *lease* boundary; it knows nothing of a host's
  witness paths and never sees a diff. This floor guards *a host-declared
  witness tree* at the *keep* boundary. They are siblings, not the same code
  (§6 forbids importing one into the other).

## 1. The gap, precisely

Walk the four facts `improve.classify` reads ([[improve.py]] `CandidateEvidence`):

| fact | authored by | can a harness-weakening candidate move it on its own tree? |
|---|---|---|
| `suite_passed` | the test runner | **yes** — delete/skip the failing test class, the runner exits 0 |
| `truth_clean` | git ancestry + the oracle | partly — relax the stamp grammar the oracle reads |
| `work` / `baseline_work` | the host's metric script | **yes** — edit the script to count more, or lower the threshold |

The conjunctive floor (`admissible_under_floor`, [[113]]) is honored — KEEP
requires all three — but the floor compares the candidate's tree against
*itself*. `baseline_work` was measured on the green baseline tree at cycle
start ([[self_improve.py]] `CycleContext.baseline_work`), so a metric-script
edit that inflates the count produces `work > baseline_work` and reads as a
real gain. The worktree isolation ([[280]]) defends against *cross-candidate*
contamination (cached outputs, persistent state — Recursive's first two
observed reward hacks); it does **nothing** against a candidate that legitimately
owns its worktree and edits the harness *within* it (their third: "exploited
timing-harness details"). That is the hole.

## 2. The env-authored fact

One new field on `improve.CandidateEvidence`, gathered by the driver:

```
candidate_touches_witness_tree: bool = False
```

- **Authored by git, not the loop.** The driver computes it from
  `git diff --name-only <baseline>..<candidate>` intersected against the
  host-declared witness tree, using the **same prefix algebra** the rest of
  the kernel trusts for collision detection (`_tree.prefixes_collide`, the
  algebra `SELF_MODIFY` already reuses — §6). The loop authors zero bytes of
  it; the [[138]] invariant holds (the bytes that decide are bytes the
  claimant did not write).
- **Default `False`.** A host that declares no witness tree (the generic
  default) gets the exact old behavior — the floor is dead-by-default, armed
  only by data, the `[breaker]`/`[improve]` config-as-data idiom. This keeps
  the change additive: every existing `CandidateEvidence(...)` call and test
  is byte-compatible.
- **Computed once, frozen.** Like every other evidence field, the driver
  measures it at the I/O boundary and freezes it; `classify` only reads it.

The witness tree itself is host policy, declared in `dos.toml [improve]`:

```toml
[improve]
# The paths that AUTHOR this loop's witnesses. A candidate diff touching any of
# them is refused by the keep-gate's witness-tamper floor (docs/329). Globs in
# the lane-tree dialect; widening this list is mechanical, narrowing is signed.
witness_tree = [
    "tests/**",            # the suite that authors suite_passed
    "pyproject.toml",      # pytest config / addopts
    "scripts/metric_*.py", # the host's metric-measurement script(s)
    "dos.toml",            # the [improve] table — its own thresholds
]
```

`ImprovePolicy` grows a `witness_tree: tuple[str, ...] = ()` field (empty =
disabled), loaded from `[improve].witness_tree` by the existing config reader.
The kernel does not glob it — the **driver** intersects the diff against it and
hands `classify` the resulting boolean. (Mechanism/policy split: the kernel
reads a bit; which paths are witnesses is the host's `dos.toml`.)

## 3. The floor — refuse-first, same tier as a red suite

In `improve.classify`, the witness-tamper check is the **first** floor, ahead
of the regression floor — a candidate that edited the instrument is refused
*before* its (now untrustworthy) suite/metric readings are even consulted:

```
# 0. REVERT (tampered) — the candidate's diff touched a witness path. Its
#    suite_passed / work readings are measured by an instrument the candidate
#    just edited, so they are not evidence. Refuse BEFORE reading them — the
#    non-negotiable floor, ahead of the red-suite floor, because a green suite
#    on a candidate-edited harness is exactly the thing we cannot trust.
if policy.witness_tree and evidence.candidate_touches_witness_tree:
    <bump the breaker like any other non-keep; return REVERT/ESCALATE
     with revert_cause = TAMPERED>
```

Properties this placement buys:

- **Ahead of the suite floor, deliberately.** If the tamper check ran *after*
  the suite floor, a candidate that broke the suite AND edited the harness
  would surface as `REGRESSED` — the operator would never learn the harness
  was touched. Tamper-first means the operator always sees `TAMPERED` when it
  applies, the more specific and more alarming fact.
- **Conjunctive, never overridable by a gain.** Same non-negotiable tier as a
  red suite ([[113]]): no `work > baseline_work` can buy back a tampered
  candidate, because the gain was measured by the edited instrument.
- **Feeds the breaker.** A tamper REVERT bumps `consecutive_reverts` like any
  other non-keep, so a loop that keeps proposing harness edits eventually
  ESCALATEs to a human ([[223]]) rather than spinning — the right terminal
  state for a loop that has run out of honest moves.

### The typed outcome question (a plan decision, now decided)

#35 asks whether tamper "folds into REVERT or earns a distinct typed
outcome/reason." **Decision: a distinct `RevertCause`, NOT a new `Candidate`
verdict and NOT a new arbiter reason-vocabulary token.**

- A new **`RevertCause.TAMPERED`** (alongside `REGRESSED` / `NO_IMPROVEMENT` /
  `WASTEFUL`). The operator routes a tamper differently from a regression — it
  is a *trust* event, not a *correctness* one — so the kernel names which one
  fired, exactly the `RevertCause` discipline already in `improve.py` (the
  REGRESSED-vs-no-op split). This is the minimal, in-grain change.
- **Not** a new `Candidate` verdict: the *action* is still REVERT (discard the
  worktree) or ESCALATE (breaker open) — the actuation space is unchanged, so
  inventing a fourth verdict would split the actuator for no new act.
- **Not** a `reasons.py` registry token. The `SELF_MODIFY`/`SCHEMA_UNREADABLE`
  tokens are **arbiter-emitted admission refuses** (category `MISROUTE`) — they
  travel the lease/`refuse(reason_class)` path. A keep-gate REVERT is not an
  admission refuse; it never flows through `arbitrate`. Minting a registry
  token for it would imply a `dos refuse --reason TAMPERED` surface that does
  not exist. The `RevertCause` enum is the keep-gate's own typed vocabulary and
  is the correct home. (If a later plan wires a `dos improve`-side structured
  refuse, it can promote `TAMPERED` then — under that plan, with the verb that
  emits it. Not pre-emptively here.)

## 4. The asymmetry — widen freely, narrow signed

The witness tree is a protection boundary, so it obeys the same
tighten-freely / loosen-signed rule as #31's floors and the [[296]]
operator-armed override:

- **Widening** the witness tree (adding a path — *more* of the tree is
  protected) is a **mechanical** edit. A candidate may freely propose
  `witness_tree += ["scripts/new_metric.py"]`; that is the loop hardening its
  own integrity, the safe direction.
- **Narrowing** the witness tree (removing a path — *less* protected) is a
  **HUMAN-rung** act. A candidate diff that shrinks `[improve].witness_tree` is
  itself a witness-tree edit (the `dos.toml` line is in the tree), so it trips
  the floor and REVERTs — **the floor guards its own definition**, the same
  self-protecting closure `SELF_MODIFY` has (it lists `reasons.py` and
  `config.py` among the files it guards). To genuinely narrow the tree the
  operator edits `dos.toml` *between* loop runs, on the record — the [[296]]
  arm-file shape (PDP says TAMPERED; the operator's out-of-band edit is the
  sanctioned escape, not an in-loop force).

This closure is the load-bearing property: **a candidate cannot disarm the
floor that guards it**, because disarming it is a witness-tree edit and the
floor refuses witness-tree edits. The bootstrap is the operator's, between runs
— never the loop's, mid-flight.

## 5. The edge the plan must take a position on (and does)

The honest hard case from #35: **a candidate whose legitimate job IS to improve
the harness.** If the chosen metric is "property-test count," every honest
candidate adds a test under `tests/**` — and trips the floor. A naive
refuse-all would freeze the most valuable class of self-improvement.

**Position: the default floor stays refuse; the host picks one of two declared
escapes, both data-driven, neither a content judge.**

1. **Append-only witness paths (the common case).** The host marks a witness
   path **append-guarded** rather than **frozen**: the driver computes a
   second env-authored bit — `git diff` shows only *added* lines under that
   path, zero deletions/modifications (`--numstat`, deletions == 0). An
   add-only edit to `tests/**` is *strengthening* the harness (the widen
   direction of §4), so it is admitted; any deletion or modification under the
   path is the narrow direction and REVERTs. This handles "every candidate adds
   a property test" exactly — adding tests is fine, weakening or removing them
   is not. The append-guard list is `[improve]` data:

   ```toml
   [improve]
   witness_tree = ["tests/**", "scripts/metric_*.py", "dos.toml"]
   witness_append_only = ["tests/**"]   # adds OK, edits/deletes REVERT
   ```

   This stays env-authored: "are there zero removed lines under this path" is a
   `git --numstat` fact, not a judgment of intent.

2. **A witness-lane loop (the structural escape, for the rare metric whose
   honest candidates must *modify* the harness).** The harness lives **outside
   the candidate's editable tree**: the loop's editable lane and its
   witness tree are disjoint by construction, and a separate human-supervised
   pass improves the harness. This is the cleanest separation but costs a
   second loop; the plan documents it as the escape hatch for the metric that
   genuinely needs harness *rewrites*, not just additions. No new mechanism —
   it is two existing loops on disjoint lanes.

The default, with neither escape declared, is **refuse** — the safe direction
for a safety floor.

## 6. Kernel-litmus compliance (the additive proof)

The plan must not violate a CLAUDE.md litmus. It does not:

- **Kernel imports no host.** The new field lives on `CandidateEvidence`; the
  glob/`git diff` I/O lives in `dos.drivers.self_improve`. `improve.py` reads a
  `bool` and a `tuple[str,...]` policy field — no git, no host name, no path.
- **A driver is the only place policy lives.** *Which* paths are witnesses, the
  append-only list, the witness-lane choice — all `dos.toml [improve]` data
  read by the driver. No `config.py` edit beyond the existing `[improve]`
  reader growing two keys (that reader already exists for
  `max_consecutive_reverts`).
- **`verify` needs no plan.** Untouched — this is the `improve` rung.
- **Tighten-freely / loosen-signed.** §4 — widening is mechanical, narrowing is
  HUMAN-rung, and the floor guards its own definition.
- **No new SELF_MODIFY coupling.** `improve.py` does NOT import
  `self_modify.py`; the shared prefix algebra is `_tree.prefixes_collide`,
  already a kernel sibling both depend on. A test pins that `improve` imports
  no `self_modify` (keeps the two guards independent — one is admission, one is
  keep).

## 7. Phasing

| Phase | What | Layer | Status |
|---|---|---|---|
| **P1** | The floor, end to end, **frozen-tree only**: `CandidateEvidence.candidate_touches_witness_tree`; `ImprovePolicy.witness_tree`; the §3 refuse-first branch in `improve.classify`; `RevertCause.TAMPERED`; the `[improve].witness_tree` config reader; the driver's `git diff --name-only` gather + the `_tree.prefixes_collide` intersection; unit tests on a fake proposer (a candidate that edits a witness path REVERTs as TAMPERED; one that does not is judged normally; default-empty tree is byte-compatible with today). **Closes #35's implementation half.** | kernel + driver + config | unbuilt |
| **P2** | The **append-only escape** (§5.1): the second `--numstat` bit, `[improve].witness_append_only`, the add-only admit. The "every candidate adds a property test" case. | driver + config | unbuilt |
| **P3** | The **witness-lane** escape (§5.2): documented as a host pattern (two disjoint loops) + an example under `examples/kernel_keep_gate/`; no new mechanism, a recipe + a test that the two lanes' trees are disjoint. | docs + example | unbuilt |

P1 is the closer for #35: it is the whole floor for the common case (a frozen
witness tree), every dependency already ships (`_tree`, the `[improve]`
reader, the driver gather seam). P2/P3 layer the two escapes on as the metric
shapes that need them appear — neither blocks the floor.

## 8. What this is NOT

- **Not** a claim the loop is now tamper-proof against *all* harness games. A
  candidate could still, e.g., exploit a metric script the host forgot to
  declare in `witness_tree` — the floor is exactly as wide as the host's
  declared tree, no wider. The plan's honesty: it closes the *declared* surface
  and makes widening cheap; it cannot guess a witness the host did not name.
- **Not** an intent classifier. It never asks "was this edit malicious." It
  asks "did the diff touch a declared witness path" (P1) and "was the edit
  add-only" (P2) — both `git` facts.
- **Not** a kernel edit to `self_modify.py` or the arbiter. The keep-gate and
  the admission guard stay independent siblings (§6).

## 9. Lineage

- [[280]] — the keep-gate this floor extends; the three env-authored witnesses
  it protects.
- [[230]]/[[234]] — `reward.admit` / non-distillability: the keep-bit is
  unforgeable by narration, which is *why* the only remaining attack is editing
  the instrument, which is what this closes.
- [[138]] — the bytes that decide are bytes the claimant did not author; the
  tamper bit is a `git diff` fact, in-grain.
- `dos.self_modify` ([[73]]) — the admission-side sibling this generalizes from
  "the kernel's own code" to "this loop's witnesses."
- [[296]] — the operator-armed override shape the §4 narrow-is-signed rule
  reuses (PDP/PEP: the floor says TAMPERED; the operator's between-run edit is
  the sanctioned escape).
- [[318]] (keep-gate ablation) and #31 (the cross-run detent), #34 (the
  significance rung) — the sibling keep-gate extensions a research-scale
  recursive loop needs; harness integrity is the third leg.
