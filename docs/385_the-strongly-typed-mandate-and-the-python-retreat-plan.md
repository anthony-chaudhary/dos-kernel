# 385 — The strongly-typed mandate and the retreat of Python to the seams

> **Operator direction (2026-06-17).** Going forward DOS is written in a
> **strongly-typed language** — Go is the chosen one — and Python is allowed
> **only at rare seams and interconnects.** This reverses the standing default.
> The whole Go arc to date ([`100`](100_native-spine-port-plan.md) →
> [`122`](122_the-core-go-runtime-and-the-on-device-kernel.md) →
> [`124`](124_the-go-core-build-plan-and-the-parity-contract.md) →
> [`125`](125_go-hook-fastpath-build-plan.md) →
> [`270`](270_go-hook-fastpath-benchmarks.md) →
> [`271`](271_the-pure-go-frontier.md)) treated Go as an *optional accelerator*
> and Python as the *permanent spec and substrate* —
> [`271`](271_the-pure-go-frontier.md) §5 says it plainly: *"pure Go is not a
> goal in itself. The kernel is Python and stays Python."* The operator now
> sets the opposite default: **Go is the substrate; Python retreats to a short,
> named list of seams.** This doc states that direction as a rule, names the
> seams it keeps, and lays out the order to move as much out of Python as the
> evidence says is safe — without pretending the move is free.

Status: PLAN (2026-06-17), greenfield on top of a half-built Go module. It does
**not** re-decide the boundary, the parity-contract split, or the fallback
discipline — those are settled in [`100`](100_native-spine-port-plan.md) /
[`124`](124_the-go-core-build-plan-and-the-parity-contract.md) and ADOPTED here
verbatim. What it changes is the *default and the direction of the ratchet*:
[`271`](271_the-pure-go-frontier.md) §5's conclusion ("the kernel stays Python")
and [`100`](100_native-spine-port-plan.md)'s framing ("Python is the executable
spec") are reversed. §1 states the reversal exactly. §2 is the argument (why a
trust kernel especially wants compile-time types). §3 is the load-bearing design
move — the **ratchet inversion** that lets the spec leave Python. §4 is the map
(what moves, in what order, with honest difficulty). §5 names the four genuine
seams Python keeps. §6 is the cost ledger (what this is NOT). §7 is the phase
order. §8 is the acceptance/litmus set. §9 reconciles with the existing arc.

The positioning half — *why a typed trust substrate is a market* — stays in
[`dos-private`](../../dos-private) (CLAUDE.md split). This is the mechanism half.

---

## 1. The reversal, stated exactly

Two defaults, opposite arrows:

| | The standing default (docs/100–271) | The new default (this doc) |
|---|---|---|
| **Source of truth** | Python is the executable spec; Go must match it byte-for-byte or it does not ship. | **Go is the spec.** Python (where any remains) must match Go, or is generated from it, or is deleted. |
| **Go's role** | an *optional accelerator*, swapped in behind a flag, never the only path. | the *default substrate* — new decision-bearing logic is born in Go. |
| **Python's role** | the whole kernel; "stays Python" (271 §5). | a **rare seam / interconnect** language — a short, named list (§5), not an open default. |
| **"Pure Go"** | "a non-goal" (271 §2). | the **target**, bounded only by the named seams. |
| **The parity ratchet** | freezes Go *against* Python. | freezes Python (while it lasts) *against* Go; then the truth-pointer flips and the ratchet guards Go alone. |

This is a direction, not a flag-day. The mechanics that made the accelerator
safe — the pure `classify(evidence, policy)` boundary, the JSON envelope ABI,
the byte-exact-decision / prose-excluded parity split, the structural fallback —
are exactly the mechanics that make the *retreat* safe. We are not throwing them
away; we are pointing them the other way (§3).

**Why now, not earlier.** The arc earned the reversal. The cores were *built
port-ready* (100); the parity claim was *split precisely* so the gate is strict
where trust lives and absent where it is theatre (124); ground was *broken* and
the hook path *measured* — Go ~6–15 ms vs Python ~230–262 ms, byte-identical on
every real spawn (125/270). The hazards are **few and named**: one float-format
function (124 §1.1), three lookbehind regexes (124 §1.2 / 271 §2.1), a handful
of already-sorted sets. The thing that was theoretical when 100 was written —
"can a second language reproduce every verdict?" — is now *demonstrated* on the
hottest, most-exercised surface. The accelerator proved the substrate is
reachable. The only question 271 left open was *whether to walk the rest of the
way*; the operator's answer is **yes, by default, henceforth.**

## 2. Why a trust kernel especially wants compile-time types

The general case for static typing is well-worn. The case *specific to DOS* is
sharper, and it rhymes with the kernel's own thesis.

- **The kernel's whole pitch is "don't believe the narration; read the
  effect."** A dynamically-typed core extends the kernel a courtesy it extends
  to no agent: it *believes its own shapes* until runtime proves otherwise. A
  verdict struct that is wrong-shaped is a verdict the fleet trusts and the
  kernel cannot refute until it ships. Compile-time types are the *evidence
  rung* for the kernel's own code — the in-language analogue of `dos verify`:
  a claim ("this returns a `Verdict`") checked against ground truth (the type)
  before anyone relies on it, not after.
- **The verdicts are the trust surface, and they should go still.**
  [`79`](79_primitives-not-features.md) argues the deciders must approach
  steady-state. A type system is friction applied exactly where stillness is the
  goal: changing a `Verdict`'s shape is a loud, total, compiler-checked event,
  not a silently-added optional dict key. (100's "boundary as a change-budget on
  the core" — but enforced by the compiler on every edit, not only at the
  parity gate.)
- **One substrate beats two.** The accelerator era pays a real dual-language
  tax: every ported decider lives in two places, and the differential corpus is
  the only thing keeping them honest. That tax is *worth it as a ratchet* (100
  §quality) but it is a *tax*, and it caps how much can be ported before
  maintenance dominates. Making Go the single source of truth (§3) removes the
  duplication for everything past the soak line — the corpus stays, but it
  guards one implementation against regression, not two implementations against
  each other.
- **The edge needs it regardless.** [`122`](122_the-core-go-runtime-and-the-on-device-kernel.md)
  showed the on-device kernel is *physically un-deployable* in Python — you
  cannot `pip install` onto a phone, an MCU, or a browser sandbox. A static
  typed binary is the only deployable unit there. Every step of this retreat is
  a step the device runtime would have to take anyway; doing it as the *default*
  means the edge target comes "free later" exactly as 122/271 §4 promised.

The honest counter-pressure, kept in view: Python's reach into the agent
ecosystem (every framework consumer `import dos`) and the maturity of the
`rich`/`mcp` surfaces are real, and they are *why* the seams in §5 exist. The
direction is "as much out of Python as is safe," not "Python is forbidden."

## 3. The ratchet inversion — the one load-bearing design move

[`100`](100_native-spine-port-plan.md) built a one-way ratchet: *Python is the
spec; Go is graded against it.* You cannot move the kernel *out* of Python while
Python remains the spec — that is a contradiction. So the central act of this
plan is to **flip the truth-pointer of the differential ratchet, per decider, on
a soak gate.** The corpus mechanism is unchanged; only which side is canonical
changes.

The per-decider transition protocol (it runs the same machine 100/124 already
built, in three steps):

1. **PORT.** Implement the decider in Go behind the existing parity gate
   (124 §2): byte-exact on the decision-bearing projection, prose excluded.
   Python is still canonical. This is exactly what 125 already did for the hook
   deciders.
2. **SOAK.** The cross-engine replay (unit corpus + live shadow, 100 §harness)
   stays green for a declared window. Nothing flips while a divergence is open.
3. **FLIP.** Once soaked byte-green, **Go becomes the spec for that decider.**
   Concretely: new behavior is authored Go-first; the Python decider becomes
   either (a) a thin re-export / generated shim that *calls* the Go core, or
   (b) deleted, with its tests rewritten as `go test` + the residual Python
   tests retargeted at the binding. The differential corpus is *kept* — but it
   now pins Go against regression and pins any surviving Python seam against Go,
   not the reverse.

This directly answers [`271`](271_the-pure-go-frontier.md) §5's invariant
("anything whose verdict is the *only* copy must stay Python, because the corpus
has nothing to pin it against"). The invariant is *correct and preserved* — there
must always be exactly one source of truth with a corpus around it. This plan
just says **that one copy should be the Go one, once soaked**, and the corpus is
what makes flipping the pointer safe rather than a leap. The "one source of
truth, two mouths" discipline becomes "one source of truth *in Go*, with a
Python mouth only where §5 says a mouth is owed."

The fallback discipline (100) is unchanged during the transition and retired
*per decider* only at FLIP: while Python is still the spec, a missing/erroring
binary falls back to Python; after FLIP for a decider whose Python copy was
deleted, the Go binary is the path and its absence is a build error, not a silent
degrade — surfaced by `dos doctor` (122 §5.2 #4 / 271 §3), never hidden.

## 4. The map — what moves, in what order, with honest difficulty

Read against the [`100`](100_native-spine-port-plan.md) audit (the pure-decider
set, the four clusters) and the live `go/` tree.

### 4.0 Already in Go (shipped — the proof the rest is reachable)
The hook deciders: `pretool` / `posttool` / `marker` / `stop` (direct rung),
plus the ports they already needed — `admission` (DISJOINTNESS + SELF_MODIFY),
`overlap`, `tree`, `claim_extract`, the dialect transcoder (271 §1), proc
liveness. These are byte-exact and benchmarked (125/270). **TP1 (§7) flips these
to Go-canonical** — they have soaked the longest and carry the least risk.

### 4.1 Tier 1 — the RE2-clean pure set (clean cut, parity-ready)
The docs/100 pure clusters *minus* the oracle, confirmed RE2-clean and
float-clean by the 124 §1 audit:

| Cluster | Modules | Why it moves cleanly |
|---|---|---|
| **Arbiter** | `arbiter` + `lane_overlap` + `_tree` + `admission` | crown jewel; A+ tested; the one float hazard is prose-only and handled by 124-A (render Python-side, or in the §5 binding) |
| **Liveness** | `liveness` + `journal_delta` (+ the `git_delta` seam) | the *cleanest* decider — ints + enums, zero of the three hazard classes (124 §1.4) |
| **Loop** | `loop_decide` + `gate_classify`(core) + `tokens` | enum/table-shaped; RE2-clean |

These are the bulk of the kernel's adjudication by line count and the bulk of its
trust surface. They move under the §3 protocol with the lowest correctness risk.

### 4.2 Tier 2 — the oracle / RE2 frontier (the hard, correctness-critical one)
`oracle` + `phase_shipped` + `stamp` + `picker_oracle` — the **truth syscall
recognizer**, and the single part of the kernel that is *not* RE2-clean. The
blocker is exactly three lookbehinds (`phase_shipped.py:201`, `stamp.py:158`,
`stamp.py:465` — 124 §1.2 / 271 §2.1), each expressible in RE2 by
capture-and-check but each a careful rewrite, not a translation. The deliverable
is **the full stamp-grammar parity corpus, not the regex** (271 §2.2): every
`[stamp]` convention, the host-strict and generic defaults, gated byte-for-byte
on this repo's *real* git history before the rung is trusted. A subtly-wrong
recognizer is a *correctness* bug in the truth syscall — the worst place to have
one — so this tier is **corpus-first, soak-long, flip-last** among the cores.
271 §2.2 recommended *deferring* this for latency; under this plan it is no
longer optional (Go must own `verify` to leave Python), but its *risk* posture
is unchanged: it ships only behind a corpus over real history.

### 4.3 Tier 3 — the I/O orchestration (follows its decider into Go)
The Python "helpers" layer (CLAUDE.md layer 3) and the I/O shells 100 ruled a
non-goal *for the accelerator*: `config`/`dos.toml` loading, the `lane_journal`
WAL, `archive_lock`'s file-lock CAS, the `git` shell, `timeline`, the supervisor
loop. 100's reason for leaving these in Python — "reimplementing file-format/git
semantics in Go reintroduces I/O semantics and voids the differential guarantee"
— was a *dual-maintenance* argument: two implementations of the same parser
drift. **The §3 spec-flip removes that argument**: once a decider's spec is Go,
its I/O shell in Go has no Python twin to drift from. So the honest path is:
**a module's I/O orchestration moves to Go only after its decider has FLIPped**,
so seam and decider land in one language together, and only the *truly external*
interconnect (the actual call to git / the filesystem / the clock) stays a thin
shim. The `go/internal/hook` tree already does this — it reads the WAL and
resolves the workspace in Go (`wal.go`, `workspace.go`), gated by the same
corpus (125 §4 S2). Tier 3 generalizes that pattern.

### 4.4 Tier 4 — the operator surface (large, low-urgency, last)
The `rich`-based CLI and the TUIs (`decisions`, `top`). A typed CLI (a Go
`cobra`-class surface) and a typed TUI (`bubbletea`-class) are the long-horizon
target. This is a *big* rewrite with a *low* payoff per the parity-contract logic
(124 §2: its value is the prose and the I/O, which the contract declines to
freeze byte-for-byte). So it is sequenced **last and explicitly accepted as a
long-lived Python seam** until then — see §6. "As much out of Python as possible"
deliberately does not mean "rewrite the rich TUI this quarter."

## 5. The four genuine seams Python keeps (the named exception)

"Rare seams and interconnects" is not an open escape hatch. It is **this list**,
and adding to it is an operator decision recorded here, not a default an edit may
reach for:

1. **The Python *binding* (the PyPI / `import dos` interconnect).** DOS's reach
   into the agent ecosystem is that consumers `import dos` (LangGraph, CrewAI,
   the Agents SDK — `examples/playbooks/`). That interconnect stays. But the
   target shape is that the Python package becomes a **thin binding over the Go
   core** (a `cffi`/`ctypes` shim on the `c-archive` build 122 §5.2 already
   plans), not the implementation. The Python *name* survives as an adoption
   surface; the Python *brain* does not. This is the seam that justifies keeping
   the package — it is an interconnect to a Python-native world, exactly the
   "rare interconnect" the direction allows.
2. **The MCP server (`dos_mcp/`).** MCP is a server framework and a
   vendor-neutral, JSON-over-stdio *interconnect* — the layering already holds it
   as a separate top-level package the kernel never imports (CLAUDE.md;
   271 §5). It stays a seam. If it is ever re-implemented in a typed language it
   stays a separate package and a pure interconnect; it is **not** urgent and
   buys little (271 §5: "a second implementation doubles maintenance and buys
   nothing"). Listed as a seam, not a port target.
3. **The differential-corpus generation + the consumer test harness.** Python is
   the spec *generator* today; after each FLIP, `go test` + the Go corpus become
   primary for that decider, and `pytest` survives as the *consumer-side* and
   *binding* test seam (the thing that proves the §5.1 binding still speaks for
   the Go core). The harness is an interconnect between the two languages during
   the multi-year transition; it shrinks as FLIPs accumulate.
4. **The genuine OS interconnects.** The actual syscalls — git, the filesystem,
   the clock, the process table. These are thin shims in whatever language hosts
   the call. On the device tiers (122 §4) the host shim is already Kotlin / Swift
   / JS — *already* typed and *already* not Python, so the edge case is the
   purest form of this direction, not an exception to it.

Anything not on this list is **not** a seam; it is a port target, and a new pure
decision-bearing module born Python-only (with no Go port queued and no recorded
reason) is a step *backward* under this direction (§8).

## 6. The cost ledger — what this is NOT

Honesty is the kernel's product; the same applies to its own roadmap.

- **It is not a flag-day rewrite.** It is a per-decider ratchet flip (§3) over a
  long horizon. Tier 4 (the CLI/TUI) may stay Python for *years*; that is by
  design, not failure. "As much as possible" has a denominator, and §5 names it.
- **The RE2 rewrite of the truth recognizer (Tier 2) is a real correctness
  risk.** It is the worst place in the kernel to introduce a subtle divergence.
  Mitigation is the same as everywhere — a corpus over real history, soak before
  flip — but the risk is named, not waved off.
- **Some Go ports add a dependency where the seam removes one.** A Go YAML reader
  is a third-party dep where Python's was stdlib-adjacent; `dos.toml` is the
  primary config (TOML, well-served in Go) but any YAML rung (`execution-state`)
  must be weighed. The "near-stdlib, zero-third-party-import" discipline (100)
  applies to the Go core too: prefer the standard library, vendor deliberately.
- **The dual-maintenance tax exists *during* each transition** — that is the
  point of the soak window, and it ends at FLIP. The plan is structured so the
  tax is bounded per decider, never carried across the whole kernel at once.
- **The accelerator's `|| python` fallback is retired *per decider*, not
  globally.** Until a decider's Python copy is deleted at FLIP, the fallback
  stands. There is no window where a missing binary silently breaks a verdict
  that still has a Python spec.

## 7. The phase order (extends docs/271's P1–P4)

271's P1 (dialect transcoder) and P2 (cross-compile CI + ratio guard) stand
unchanged — they are on or supporting the hook hot path and are cheap. This plan
adds the **TP (typed-retreat) phases** that carry the new default. Each
transition phase is gated by the §3 protocol (port → soak → flip).

| Phase | Work | Risk | Gate |
|---|---|---|---|
| **TP1** | Flip the already-shipped hook deciders + their ports (§4.0) to Go-canonical; delete or thin their Python twins. | low | longest soak already banked; corpus green; `dos doctor` reports the flip |
| **TP2** | Port + flip the RE2-clean pure set — arbiter, liveness, loop (§4.1). | low–med | the docs/100 cluster corpora byte-green across engines, then flip per cluster |
| **TP3** | Port + flip the oracle / `verify` recognizer (§4.2): the 3 lookbehind RE2 rewrites + the full stamp-grammar corpus over real history. | **high** | corpus-first; soak longest; flip last among cores |
| **TP4** | Move each FLIPped module's I/O orchestration into Go (§4.3); leave only the external interconnect as a thin shim. | med | the WAL/config/git-reader corpora (the 125 §4 S2 pattern) green |
| **TP5** | Make the Python package a thin binding over the Go `c-archive` core (§5.1); `import dos` calls Go. | med | the binding round-trips the corpus; consumer playbooks (`examples/`) stay green |
| **TP6** | (deferred) Typed CLI + TUI (§4.4). | high effort, low payoff | explicitly last; Python seam accepted until then |

Through-line: **TP1 is alive on day one** (the deciders are already Go and
soaked — the flip is a pointer move, not new code), and each later phase thickens
the same substrate rather than waiting for a big-bang at the end. That is the
phased-plan ceremony's own rule (no feature stranded at the last phase), applied
to the retreat itself.

### TP1 — landed (2026-06-17): the truth-pointer flip

The first TP1 slice ships the *pointer move* the phase is named for. The hook
deciders (`pretool`/`posttool`/`marker`/`stop`) and the ports they already needed
(`admission`, `overlap`, `tree`, `claim_extract`, the dialect transcoder, proc
liveness) are now declared **Go-canonical** in a closed, kernel-read registry
(`src/dos/native_canonical.py`), and `dos doctor` reports the flip — so the §8
litmus "*the truth-pointer is single and known per decider*" is an OBSERVED fact
(a `go-canonical` text line + a `truth_pointer` map in `--json`), not a doc
sentence. The parity corpus stays green and is reframed as the Go-canonical pin:
the Go `TestParityCorpus` asserts the committed corpus IS the Go decider's output
(`go == corpus`, the canonical pin), and the Python `tests/test_go_hook_parity.py`
proves the Python *shadow* still reproduces it. A divergence is now reconciled
toward Go (§3), not Python.

What this slice deliberately does NOT do (the §6 cost ledger, honestly): it does
not delete the Python twins. The Python deciders survive as the docs/100 fallback
a pure-Python install relies on (§6: the fallback stands until the Python copy is
deleted). Thinning them onto a binding follows once the `c-archive` binding (TP5,
§5.1) exists; until then §6's per-decider fallback discipline keeps a binary-less
install deciding. The truth-pointer has flipped; the deletion is a later slice.

**Files:** `src/dos/native_canonical.py`, `src/dos/cli.py`, `tests/test_native_canonical.py`, `tests/test_go_hook_parity.py`, `go/internal/hook/parity_test.go`

## 8. Acceptance / litmus for the direction

Not all of these are test-pinned today (the direction is forward-looking); each
is *checkable*, which is the bar the kernel holds itself to.

- **New pure decision-bearing logic is authored in Go**, or ships with a Go
  parity port queued before it is relied on. A new Python-only verdict core with
  no Go port and no recorded reason is a regression against this direction.
- **The truth-pointer is single and known per decider.** For every ported
  decider, exactly one of {Python, Go} is canonical, the other is a corpus-pinned
  shadow or absent. `dos doctor` reports which, per the §3 flip state.
- **After TP5, the dependency arrow points Python → Go**, never Go → Python: the
  Python package depends on the Go core; nothing in `go/` depends on `src/dos/`
  for behavior (it consumes the *corpus*, not the code — 271's framing, preserved).
- **The seam list (§5) is closed and named.** A change that keeps logic in Python
  must place it under one of the four §5 seams or record why a fifth is owed —
  the same discipline as adding a refusal reason or a lane: a named, reviewable
  act, not a default.
- **The parity corpus survives every flip.** No decider's behavior loses its
  differential pin in the transition; the corpus's *canonical side* flips, the
  corpus itself never disappears.

## 9. Relationship to the existing arc

This doc **supersedes the stance, adopts the mechanics.**

- **Adopted verbatim:** the pure `classify(evidence, policy)` boundary
  ([`86`](86_the-typed-verdict-surface.md)); the JSON envelope ABI
  ([`100`](100_native-spine-port-plan.md) §architecture); the byte-exact-decision
  / prose-excluded parity-contract split
  ([`124`](124_the-go-core-build-plan-and-the-parity-contract.md) §2); the
  structural fallback and `dos doctor` honesty surface (100/122); the dialect
  transcoder and cross-compile/on-device targets (271 §1/§4, 122).
- **Reversed:** [`100`](100_native-spine-port-plan.md)'s "Python is the
  executable spec" (the spec migrates to Go per §3) and
  [`271`](271_the-pure-go-frontier.md) §2/§5's conclusion that "pure Go is a
  non-goal; the kernel stays Python." 271's *recommendation to defer the oracle
  RE2 rewrite for latency* is correct on its own terms (verify is not hot) and is
  re-scoped here: the rewrite is now required to leave Python, but its risk
  posture (corpus-first, soak-long) is exactly 271's. 271 §4 already named the
  one context that flips its recommendation — the on-device runtime where no
  Python is present; this plan generalizes that flip from "the edge" to "the
  default everywhere."

The binding statement of the direction lives in the architecture contract
([CLAUDE.md](../CLAUDE.md), the "Strongly typed by default" directive); this doc
is its reasoning and its phased order.

---

## References

- [`100_native-spine-port-plan.md`](100_native-spine-port-plan.md) — the pure-decider boundary, the JSON envelope ABI, the parity ratchet, the fallback. This plan adopts the mechanism and reverses the "Python is the spec" framing.
- [`124_the-go-core-build-plan-and-the-parity-contract.md`](124_the-go-core-build-plan-and-the-parity-contract.md) — the byte-exact-decision / prose-excluded split (§3's port step); the three lookbehind regexes (§4.2 / TP3).
- [`125_go-hook-fastpath-build-plan.md`](125_go-hook-fastpath-build-plan.md) — the shipped Go hook deciders (§4.0 / TP1); the WAL/workspace-in-Go pattern (§4.3 / TP4).
- [`270_go-hook-fastpath-benchmarks.md`](270_go-hook-fastpath-benchmarks.md) — the measured speed claim that proved the substrate is reachable (§1).
- [`271_the-pure-go-frontier.md`](271_the-pure-go-frontier.md) — the stance this doc reverses (§9); its §5 invariant ("one source of truth, corpus-pinned") is preserved with the truth-pointer flipped (§3); its §2.2 oracle deferral re-scoped (§4.2).
- [`122_the-core-go-runtime-and-the-on-device-kernel.md`](122_the-core-go-runtime-and-the-on-device-kernel.md) — the edge target every retreat step also serves (§2); the `c-archive` binding shape (§5.1 / TP5).
- [`86_the-typed-verdict-surface.md`](86_the-typed-verdict-surface.md) / [`79_primitives-not-features.md`](79_primitives-not-features.md) — the typed-verdict contract and the stay-still thesis the compiler now enforces edit-by-edit (§2).
