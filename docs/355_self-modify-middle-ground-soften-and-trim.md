# 355 — SELF_MODIFY middle ground: soften the no-loop case, trim the T1 set

> **One line:** The SELF_MODIFY guard is conceptually right but slows dev,
> because it hard-DENIES *every* interactive edit to a kernel file — even when
> no dispatch loop is live, the exact case the docs call safe. This plan keeps
> the hard deny exactly where the hazard is real (a live loop rewriting its own
> decision path) and removes it everywhere else, via two independent changes:
> **soften** the interactive-no-loop case to an advisory WARN, and **trim** the
> T1 set to the files a live admission actually reads each iteration.

## The friction, precisely

`SelfModifyPredicate` refuses any lease whose tree hits `_DISPATCH_RUNTIME_FILES`
(11 kernel files). The refusal is **request-absolute** (fires whether or not any
loop is live) and carries `reason_class == "SELF_MODIFY"`. The hook's
operator-session softening — which already downgrades a *collision* deny to an
advisory WARN for an interactive human (no `DOS_LOOP` / `CID_RUN_ID` /
`DISPATCH_LOOP_TS`) — is keyed on `reason_class == ""`, so it **deliberately
never touches SELF_MODIFY** (decide.go L147, `pretool_sensor.decide` L894).

The result: a human sitting at the keyboard, no loop running, editing
`reasons.py` between loop runs hits a hard wall. The only escape is the
heavyweight arm ritual (`dos override arm` needs a TTY + y/N, or hand-pasted
TOML). The hazard the guard exists for — a *live dispatch loop* rewriting
`arbiter.py` between two admission checks, mid-flight — is not present in that
case. The guard treats the common safe case and the rare dangerous case
identically. That is what "feels off."

This was hit in the field as the docs/296 / issue #11 type-specimen, and it
fired again building *this very plan* (a read-only grep that merely *named* the
T1 files was denied for the path-mention).

## What this is NOT

- Not a weakening of the live-loop guard. A dispatch loop
  (`OperatorSession=false`) editing a core kernel file is still hard-DENIED,
  byte-identical to today. The dangerous direction is untouched.
- Not a removal of the arm-file mechanism. The override window stays the
  sanctioned, audited, expiring path for the one case that still hard-denies:
  a kernel edit *while a loop is live*.
- Not a change to the pure verdict. The classifier keeps saying SELF_MODIFY —
  a T1 hit IS self-modification. The change is one rung up, at the PEP boundary
  (the docs/293 / docs/296 move: PDP decides, PEP disposes).

## Change 1 — soften the interactive-no-loop case to a WARN (the PEP boundary)

The pure `SelfModifyPredicate` is unchanged: it still returns
`reason_class="SELF_MODIFY"`. The disposition changes at the hook boundary,
beside the existing operator-session collision softening — the same `decide()`
function, the same `OperatorSession` signal, a new branch for the SELF_MODIFY
class.

When a SELF_MODIFY refusal meets an **interactive operator session** (no loop
env), `decide()` emits an advisory WARN instead of a deny:

```
DOS PRE-admission (advisory, operator session): <reason> You are editing the
live kernel, but NO dispatch loop is in flight (the mid-flight-rewrite hazard
needs a live loop) — you own the blast radius of your own deliberate edit, so
DOS warns instead of blocking. If a loop is actively running, it will carry the
loop env and this stays a hard deny; arm a window (dos override status) to edit
under a live loop.
```

The ordering inside `decide()` matters (both Python and Go):

1. The **override-arm perimeter** deny (a write to `.dos/override/`) stays
   FIRST and absolute — a window must never extend itself. Unchanged.
2. The new SELF_MODIFY **operator-session** branch: `reason_class == SELF_MODIFY`
   AND `OperatorSession` AND **no armed window needed** → WARN. This runs
   BEFORE the override-window `dispose` branch, because if we are softening for
   an interactive human with no loop, there is nothing for the arm file to
   convert — the deny is already gone.
3. The existing override-window `dispose` branch still handles the case that
   stays a hard deny: SELF_MODIFY + a **loop session** (`OperatorSession=false`)
   + an armed window → allow-with-note. This is now the arm file's *only*
   remaining job: authorize a kernel edit while a loop is live.
4. A SELF_MODIFY refusal in a **loop session with no armed window** → hard deny,
   exactly as today.

Why softening (not silent admit): the WARN is `additionalContext`-only — the
call proceeds, and the human gets the hazard named on the record. The
observation log carries a distinct `decision: "warn"` with
`reason_class: "SELF_MODIFY"` so `dos stats` / `dos top` can still surface "an
operator edited the kernel" — visibility is preserved, only the hard block is
removed.

The threat-model honesty is unchanged from docs/296: this raises the bar for
*cooperative* agents exactly as far as the loop-env signal is trustworthy. An
agent that scrubs its own `CID_RUN_ID` to look interactive is the same class of
adversary that can already allocate a PTY to script the arm `y/N` — the guard
was never proof against arbitrary exec, and this claims nothing stronger. What
it buys: the *common, safe, cooperative* case stops paying the arm-ritual tax.

### Files (Change 1)

- `src/dos/pretool_sensor.py` — a new SELF_MODIFY operator-session WARN branch
  in `decide()`, before the override-window `dispose` branch. Not a T1 file.
- `go/internal/hook/decide.go` — the byte-twin branch, gated on
  `av.reasonClass == selfModifyReason && in.OperatorSession`, before the
  `dispose` branch. (selfmodify.go's list is Change 2.)
- `go/internal/hook/parity/gen_corpus.py` + `corpus.jsonl` — regenerate so the
  new soften case is pinned in the Python↔Go byte-parity corpus.

### Litmus (Change 1)

1. **Interactive human, no loop, T1 edit → WARN, call proceeds.** New
   `test_override_window.py` case: kernel cfg, no loop env, Edit on `config.py`
   → `decision == "warn"`, `reason_class == "SELF_MODIFY"`, no
   `permissionDecision`.
2. **Loop session, T1 edit, no arm → DENY (unchanged).** With `CID_RUN_ID` set,
   the same edit → `permissionDecision: deny`, byte-identical to today.
3. **Loop session, T1 edit, armed window → override-admit (unchanged).** The arm
   file still converts a loop-session SELF_MODIFY deny.
4. **Go/Python parity.** The regenerated corpus passes
   `parity_dialect_test.go` for all four cases.

## Change 2 — trim the T1 set to the mid-flight-critical core

The docs/73 criterion for T1 membership is "modules that sit in a *running
orchestrator's own execution path*" — the logic a live admission/loop reads on
every iteration, so that rewriting it mid-flight changes the next packet's
verdict silently. Applied honestly, only six files meet that bar:

```
KEEP (read on every admission decision — the live collision-adjudication path):
  src/dos/arbiter.py        the admission kernel itself
  src/dos/admission.py      the predicate conjunction runner + this guard's host
  src/dos/self_modify.py    this guard
  src/dos/lane_overlap.py   the soft-overlap algebra the arbiter delegates to
  src/dos/_tree.py          the prefix algebra under that
  src/dos/config.py         the lane taxonomy / paths a live arbiter reads

DROP (edited far more often than they are mid-flight-critical):
  src/dos/reasons.py        closed-set DATA, not live-decision logic (the #11/#145
                            type-specimen friction — a reason rename is the
                            single most common blocked kernel edit)
  src/dos/tokens.py         token accounting; a loop reads it per-iteration but a
                            mid-edit reshape degrades a count, it does not corrupt
                            an admission verdict
  src/dos/wedge_reason.py   reason prose/lookup, downstream of the verdict
  src/dos/gate_classify.py  loop-internal go/no-go; not on the ARBITER path
  src/dos/loop_decide.py    loop-internal continue/stop; not on the ARBITER path
```

The honest concession: `tokens.py` / `gate_classify.py` / `loop_decide.py` ARE
read by a live *loop*, so dropping them slightly narrows what the always-on
guard protects from a live loop's own self-edit. But (a) the soften in Change 1
already means the only remaining hard-deny is the loop-session case, and a loop
editing its own `loop_decide.py` mid-run is a far rarer event than a human
editing `reasons.py`; (b) the apply-gate (docs/126, opt-in `DOS_APPLY_GATE`)
already generalizes containment to *any* held lease's tree, so a loop that
leased `src/` is still gated on these files via the apply-gate when the operator
opts in; (c) the arm window remains for the deliberate loop-time kernel edit.
The trim trades a thin slice of always-on loop-self-edit coverage for removing
the dominant source of false interactive walls — the right trade given Change 1.

### Files (Change 2)

- `src/dos/self_modify.py` — shrink `_DISPATCH_RUNTIME_FILES` to the six-file
  core; update the per-entry rationale comments and the module docstring.
  **This is itself a T1 edit** (`self_modify.py` stays in the kept set), so it
  rides the operator's between-loop window — fittingly, the seam's own first
  customer, exactly as docs/296 designed.
- `go/internal/hook/selfmodify.go` — mirror `dispatchRuntimeFiles` to the same
  six, same order (the GHF3 parity gate fails loudly on drift).
- `go/internal/hook/parity/corpus.jsonl` — regenerate (a `**/*` lease now names
  fewer hits; the refusal text's first-≤3 names are unchanged since the kept
  files lead the list).
- `tests/test_admission.py`, `tests/test_selfmodify_hook_probe.py`,
  `tests/test_workspace_config.py` — update any test that asserts a DROPPED file
  is T1; add a test that a dropped file (e.g. `reasons.py`) is NOT refused for a
  loop session, and a kept file (`arbiter.py`) still is.

### Litmus (Change 2)

1. **A kept file still fires.** A lease/edit tree of `src/dos/arbiter.py` →
   SELF_MODIFY refusal (loop session). Unchanged.
2. **A dropped file no longer fires.** A lease/edit tree of `src/dos/reasons.py`
   → admitted (no SELF_MODIFY), for a loop session too. New.
3. **Go/Python list parity.** `dispatchRuntimeFiles` == `_DISPATCH_RUNTIME_FILES`
   (the existing GHF3 gate).

## Combined behavior table

| Session | File | Before | After |
|---|---|---|---|
| Interactive (no loop) | `arbiter.py` (kept) | DENY | **WARN** (proceeds) |
| Interactive (no loop) | `reasons.py` (dropped) | DENY | **pass** (not T1) |
| Live loop | `arbiter.py` (kept) | DENY | DENY (unchanged) |
| Live loop | `reasons.py` (dropped) | DENY | **pass** (not T1) |
| Live loop + armed | `arbiter.py` | override-admit | override-admit (unchanged) |
| Any | write to `.dos/override/` | DENY | DENY (unchanged) |

Most interactive dev edits stop hitting any wall; the hard deny survives exactly
where the mid-flight-rewrite hazard is real.

## Out of scope

- A passphrase rung on `dos override arm` (deferred in docs/328, still deferred).
- Touching the apply-gate (docs/126) verdicts — this plan only changes the
  always-on SELF_MODIFY disposition; the opt-in apply-gate is orthogonal.
- The native-binary swap question: the trim ships in both `self_modify.py` and
  `selfmodify.go`, so a rebuilt bundle picks it up; the soften ships in both
  `decide()` paths likewise. No new operator-gated binary step beyond the normal
  Go-parity rebuild the GHF gate already requires.

See also: docs/296 (the arm window — now the loop-time-only override), docs/328
(arm ergonomics), docs/126 (the apply-gate), docs/73 (the predicate seam + the
original T1 criterion this trim re-applies).
