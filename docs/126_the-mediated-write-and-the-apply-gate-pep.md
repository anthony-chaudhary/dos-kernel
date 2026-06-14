# 126 — The mediated write, and the apply-gate PEP

> **Status:** Phase 1 SHIPPED — DOS now has its first **enforcement point**: the
> `dos apply` diff turnstile (`cli.cmd_apply` + the pure `apply_gate.decide`,
> `tests/test_apply_{gate,cli}.py`, the `dos-apply` pre-commit hook). A staged write
> that escapes the held lane — or collides with a live sibling lease — is now
> *refused before it lands* (exit 1), with `--force` the operator's sole audited
> override. Phases 2–5 (the lease fence, the launched exec, the spawn-time manifest,
> the docs/125 loop) remain design plan; the PreToolUse binding is the Phase-1.5
> follow-on (§4). This doc is the argument for it, the narrow shape that keeps it from
> turning DOS into a sandbox, and the phasing.
>
> **One line:** DOS today is, in its own audit's words (docs/114), **a sound PDP
> with no PEP** — it *decides, observes, and re-adjudicates* but never **mediates the
> write moment**, so every guarantee is *detected, not prevented*. This plan adds a
> single, narrow, effect-typed **Policy Enforcement Point**: a `dos`-mediated write
> (the commit, the lease grant, the spawn) that runs the verdicts the kernel already
> computes *at the moment the effect would land*, and **refuses the effect itself**
> when the verdict refuses. Same verdicts; now binding.
>
> **Lineage.** The enforcement half of the security program in
> `dos-private/dispatch-os-security-10x-100x.md` (§2 names the gap five ways, §5 is
> this build). docs/125 (trifecta color) ships a *detector* verdict that this gate
> makes *bind* (its Phase 4). docs/119 (the claim) already wants a `dos`-mediated
> apply for a *different* reason (region-without-a-name); this doc is the
> *security/soundness* reason for the same chokepoint, and the two share the seam.
> docs/114 §A1/A2 deferred the disjointness-floor and the lease-fence *into* "a real
> mediated write" — this is that doc. docs/95 (OS-evidence) and docs/117
> (`log_source` actor-witness split) supply the unforgeable acceptance signal the
> launcher half produces.

---

## 0. The gap, stated once

A **PDP** (Policy Decision Point) answers *"should this be allowed?"* A **PEP**
(Policy Enforcement Point) sits *in the path of the effect* and makes the answer
binding — the effect physically cannot happen if the PDP says no. DOS is all PDP:

| Verdict | What it does today | The PDP-with-no-PEP tell |
|---|---|---|
| `arbitrate()` | refuses a *declared* region collision | the agent declares its tree; nothing stops a write *outside* it; `scope.py` checks conformance **post-hoc** (docs/89) |
| `verify()` | grades a ship from git ancestry | reads the fossil **after** the commit lands — a receipt, not a gate |
| `liveness()` / watchdog | records `OP_HALT`, *proposes* a stop | never signals a process (docs/99, docs/101); a rogue loop runs until something outside DOS acts |
| ⅓ soft-overlap | tolerates ≤⅓ tree overlap | an **unsound** fractional lock at the unmediated write — two lanes can still collide *under any ratio* (docs/114) |
| (missing) | — | no **fence/generation** (Kleppmann): a stale paused holder whose lease expired can still corrupt a re-granted region |

That is **one architectural fact restated five times**: DOS chose, correctly for its
first life as a *forensic/trust* substrate, to be **out-of-band** — pure verdicts,
no I/O in the decision, the operator wires the consequence. That purity is *why* the
kernel is small, testable, and vendor-neutral. But it caps the security pitch: *"DOS
tells you, with a receipt, that the agent did the thing it shouldn't have — just
slightly too late to stop it."* For a trust ledger that is a feature; for security,
"I detected the breach" is a weaker product than "I prevented it."

This plan closes the gap **without** discarding the property that makes the kernel
good. The discipline (§3) is the whole design.

---

## 1. The three mediated chokepoints

The PEP is not "DOS confines a process." It is **three specific writes** — the ones
that already have a pure PDP function behind them — routed through `dos` so the
verdict binds. In increasing ambition:

### 1.1 The apply-gate (the keystone — write-mediation for the commit)

Today: agent commits → `verify()`/`scope.py` grade the diff *after* it is in history.
Inverted: the agent's write goes **through** `dos apply` (or a pre-commit / PR gate
the agent cannot bypass), which, *at the moment of the write*, runs:
- the **artifact rung over the diff** — refuse a diff that escapes the lease's
  declared tree (closes the declared-vs-actual hole: the lying/injected agent that
  declares `playbooks/**` and writes `src/**` is refused *before* the commit, not
  caught after);
- a **real disjointness floor** against the live leases at the mediated point — not
  the advisory ⅓ ratio but a true region lock (docs/114 §A1: the honest fix is a
  floor at the mediated write, plus the shared/exclusive lock modes already built in
  `lock_modes.py`);
- the **trifecta color conjunction** (docs/125) with teeth — refuse the commit that
  would land a third-color effect.

Now a refusal is *binding*: the bad commit never enters history. This is the single
change that converts every existing diff-grading verdict from forensic to preventive,
because they all already grade the diff — they just grade it too late.

### 1.2 The launcher with a fence (write-mediation for the effect-bearing exec)

The `OS_RECORDED` accountability rung (docs/117, docs/95) is *only* reachable when
**the kernel launched the process and read the exit code** — i.e. the agent did not
author the bytes. Make `dos` the launcher for the effect-bearing step and you get:
- an **unforgeable acceptance signal** (the OS exit status + captured stream the
  agent cannot fake — the docs/117 §5 "acceptance verb" prize); and
- the place to put a **lease fence / generation number** (Kleppmann): the launched
  process carries its lease generation, and the mediated write **rejects a write from
  a superseded generation**. This closes the stale-paused-holder corruption (docs/114
  §A2): `liveness()` stops being purely advisory *at this one chokepoint* — the
  launcher can refuse to let a superseded run commit.

### 1.3 The spawn-time manifest check (write-mediation for capability acquisition)

The content-addressed tool manifest (security essay §4.3): the child declares its
allowed tools, descriptions are **hashed and frozen at `spawn()`**, and a
description whose hash drifts mid-run blocks the child's capability grant at the
mediated spawn (answers ASI04 / MCPTox — poisoned descriptions loaded at runtime).
Same content-addressing discipline already in the state plane and `durable_schema`,
applied at a gate.

---

## 2. Why this is the same verdicts, made binding — not new policy

The load-bearing economy of this plan: **every gate reuses a pure PDP function that
already exists.** The apply-gate calls `scope.classify` / the overlap floor / the
trifecta predicate. The launcher's fence is a generation check over the lease the WAL
already records. The manifest check is a hash compare. **No new decision logic is
written** — the PEP is *plumbing the existing decisions into the one place the effect
passes through.* That is what makes a category-changing capability a mostly-
mechanical build, and it is the test for whether a proposed gate belongs here: *if
there is no existing pure verdict behind it, it is not in scope.*

The safe-direction guarantees come along for free. The predicate conjunction is
conjunctive-only (`admission.py`): a PEP built on it can only ever *refuse more* at
the gate, never admit something the pure PDP would have refused. The fence is a
strict generation comparison (a superseded write is refused, never silently
reordered). The manifest check fails closed (a drifted/unreadable manifest blocks,
the `durable_schema` refuse-don't-guess posture). **Enforcement built the DOS way
inherits the DOS failure direction** — which is exactly what makes adding a PEP
*safe to add*.

---

## 3. The discipline — what stays OUT of band (the part that keeps DOS the kernel)

This is the most important section. The failure mode of "add enforcement" is
scope-creep from "mediate the commit" to "mediate everything," which is how a clean
kernel becomes a bloated guard that has to *know what a process is* — the thing DOS's
domain-freedom (docs/99: advisory-only's real basis) exists to refuse. The boundary,
stated as hard rules:

1. **DOS is a referee with a turnstile, not a sandbox.** The PEP mediates *the
   specific shared-state writes the kernel already adjudicates* — commit, lease
   grant, spawn — **not arbitrary process behavior.** It does not confine a malicious
   process at the OS-syscall level, does not enforce memory/network isolation, does
   not replace containers/VMs/least-privilege. SECURITY.md stays true: *pair DOS with
   real isolation.* The PEP adds *one binding turnstile in front of the writes that
   have a verdict*, not a jail.

2. **No gate without a pre-existing pure verdict.** Every chokepoint must call a PDP
   function that already exists and is already replay-tested. If a proposed
   enforcement has no verdict behind it, it does not belong in the kernel — it is a
   driver/host concern. This is the litmus that keeps §1 from growing a §1.4, §1.5,…

3. **The out-of-band PDP path survives unchanged.** `dos verify` / `dos arbitrate` /
   `dos liveness` as *pure advisory verdicts* keep working exactly as today, with no
   I/O in the decision (`tests/test_verify_no_plan.py` et al. stay green). The PEP is
   an **additional, opt-in** surface (`dos apply`, the launcher, the gated spawn) —
   not a mutation of the existing verdicts. A host that wants the forensic substrate
   and wires its own enforcement keeps doing so; the PEP is for the host that wants
   DOS to *be* the turnstile.

4. **`--force` remains the sole human override, audited.** A gate refusal is
   overridable only by the operator's explicit `--force`, which writes a typed,
   ancestry-visible reason (the docs/`sudo` "audited break-glass" shape, the
   `dispatch-os-sudo-privilege-model` authority plane). The agent can never force its
   own gate.

If a future change cannot be expressed within these four rules, it is not this plan —
it is a different product (a sandbox), and DOS should decline it.

---

## 4. Phase plan

**Phase 1 — `dos apply`, the diff turnstile (the keystone, smallest useful core). SHIPPED.**
- A `dos apply` verb (`cli.cmd_apply`) takes a staged diff (`--staged` →
  `git diff --cached`) or an explicit footprint (`--file`), resolves the run's OWN
  held lease **at the boundary** (`_resolve_self_lease`: match `run-id` / `loop-ts` /
  `pid` against the WAL leases — the self-lane cannot be derived inside a pure
  predicate, so the caller resolves it and freezes it in, the `SelfModifyPredicate`
  pattern), and runs the **declared-tree-escape** check + the **sibling-collision
  floor** as one pure verdict (`apply_gate.decide` = `scope.gate` +
  `lock_modes.region_conflict`, the sound `ratio_max = 0` floor — NO new decision
  logic, §2). Refuse ⇒ exit 1, the commit does not proceed; the refusal carries the
  typed `SCOPE_ESCAPE` reason (declared as `dos.toml [reasons]` data — host policy,
  opt-in, never a built-in predicate).
- The pre-commit integration shape (a hook the agent cannot bypass): the `dos-apply`
  hook in `.pre-commit-hooks.yaml` fires `dos apply --staged` at the **pre-commit
  stage** (where the staged footprint exists — unlike `commit-audit`, off-by-one
  there). `--force` is the operator's sole, audited override (an `APPLY_FORCE` HUMAN
  row via `home.append_decision`); the agent cannot force its own gate (§3 rule 4).
- Tests (`tests/test_apply_gate.py`, `tests/test_apply_cli.py`): an escaping diff is
  refused; an in-tree diff passes (exit 0); a colliding diff is refused at the floor;
  an unresolvable lane fails CLOSED; `--force` overrides AND writes the audited
  HUMAN row; the existing advisory verdicts (`scope-gate`, `arbitrate`) are untouched.
- **Discipline kept:** the out-of-band PDP path is unchanged (this is an *additional*
  opt-in surface, §3 rule 3); the gate is refuse-MORE only; the kernel leaf
  (`apply_gate.py`) names no host. The PreToolUse binding (generalize the SELF_MODIFY
  surface from T1-files to any-lease-tree) is the deliberate Phase-1.5 follow-on —
  it must first resolve the docs/143 softening trap (a `reason_class` refuse is never
  softened at that surface, so an interactive operator needs the operator-session
  soften OR a `dos override` arm); the CLI gate, which HAS `--force`, is the safe home
  for the typed reason and ships first.

**Phase 2 — the lease fence (generation numbers).**
- A generation/epoch on each lease grant (the WAL already records grants); the
  mediated write rejects a write whose generation is superseded (the stale-paused-
  holder fix, docs/114 §A2). This is where `liveness` first *binds* (a SPINNING/
  superseded run cannot commit through the gate).
- Tests: a superseded-generation write is refused; the current holder passes; the
  fence is monotonic.

**Phase 3 — the `dos`-launched exec + OS acceptance.**
- `dos` launches the effect-bearing step, captures exit code + stream → an
  `OS_RECORDED` `LogEvidence` (docs/117) the agent cannot forge; the acceptance verb
  grounds an oracle verdict on the real exit status.
- Tests: a non-zero exit is recorded as failure regardless of the agent's narration;
  the captured stream is the `OS_RECORDED` rung.

**Phase 4 — the content-addressed tool manifest at spawn.**
- Hash + freeze the child's declared tool descriptions at `spawn()`; a drifted hash
  blocks the capability grant at the mediated spawn (ASI04/MCPTox). Lives on the
  spine (`run_id`/run-dir), correlated-by-construction.

**Phase 5 — close the docs/125 loop.** Route the exfiltration capability through the
Phase-1 grant so the trifecta `TRIFECTA_THIRD_COLOR` refuse *withholds the
capability* instead of printing — the detector becomes enforcement.

---

## 5. Why this is the 10× and worth the larger lift

It changes the **category** of the product, not its tuning. "A trust ledger that
detects breaches with a receipt" and "a reference monitor that prevents the breach at
the write" are different procurement lines, different budget owners (forensic/audit
vs. security/platform), and different competitive positions — it is precisely the
half of Limen's posture (in-band write mediation,
`dispatch-os-vs-limen-and-right-to-history`) that DOS amputated, recoverable here
*without* amputating the epistemic plane (verify + liveness + typed refusal +
accountability spectrum) that Limen amputated. The result combines *both* the binding
PEP *and* the epistemic plane — an uncommon pairing among the substrates surveyed
here, where tools tend to ship one or the other.

And it is mostly plumbing — every verdict it binds already exists as a pure function.
The honest cost is the §3 discipline: the boundary between "turnstile in front of the
adjudicated writes" and "sandbox that knows what a process is" is a judgment call made
repeatedly, and the four rules are there to make it answerable each time. The day the
answer requires breaking a rule, the feature has left this plan — and that is the
signal to stop, not to bend the kernel.
