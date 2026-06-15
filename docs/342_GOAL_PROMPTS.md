# docs/342 — Goal prompts: ship the five properties that make DOS equal-caliber to TCP

> **What this is.** Copy-paste `/goal` prompts, one per milestone in
> [docs/342](342_the-equal-caliber-goal-what-dos-must-ship-to-match-tcp.md), each
> **self-contained** so a fresh agent can pick it up cold and ship it. They are
> ordered by the docs/342 §3 milestone ladder — **mandatoriness first**, because
> nothing else matters without a gate the actor cannot skip. Every prompt bakes in
> the repo's non-negotiable discipline so the agent cannot drift into an
> over-claim: **the new capability is a refusal a witness confirms, never a
> narration; each property's DONE is a test a self-report cannot pass; the kernel
> stays pure (mechanism here, policy in a driver); `src/dos/` is the kernel's own
> running code, so edit it in a DETACHED WORKTREE — SELF_MODIFY refuses an in-place
> edit of the arbiter trio.**
>
> **How to use.** Paste one block after `/goal`. Run them **in order** (M1 → M5),
> one at a time — M2 checks its fence *at the gate M1 builds*, M3 binds its rung
> *through that gate*, so the order is a real dependency, not a preference. M5
> (P-SPOKEN) is the exception: it runs in **parallel** to 1–4 (a standardization
> curve that does not wait on internals). Each block names its own DONE condition (a
> green targeted suite + a `dos commit-audit`-clean commit + the property's witness
> test) so the Stop hook holds until the mechanism actually refuses a bad effect —
> not until the agent says it does.

**Shared context every prompt assumes (don't re-explain it — it's in the repo):**
docs/342 decomposes "equal-caliber to TCP" into five properties, each with a
witness a self-report can't pass: **P-GATE** (mandatory gate), **P-FENCE**
(stale-writer rejection), **P-CHECK** (un-authored binding rung), **P-EXACTLY-ONCE**
(no double-fired effect), **P-SPOKEN** (shared verb standard). The diagnosis is
docs/335 (the TCP analogy: DOS matches the *shape* but not the *enforcement verb*);
the mechanism plan is docs/126 (the apply-gate PEP); the positioning is docs/340.
The keystone already shipped: **docs/126 P1 `dos apply`** — `apply_gate.decide`
(`src/dos/apply_gate.py`) composes two pure verdicts, `scope.gate` (declared-tree
escape) + `lock_modes.region_conflict` (the sound `ratio_max=0` collision floor),
refuses a staged write that escapes the lane or collides with a sibling lease, and
exits 1 before the commit lands. It is opt-in (CLI + pre-commit hook) today. The
existing *binding* PEP to generalize is `pretool_sensor.decide`
(`src/dos/pretool_sensor.py`) — it already physically blocks via
`permissionDecision: deny` for SELF_MODIFY (T1 files). The progress metric for the
whole goal is docs/335 §5's **acceptance-rate against a non-cooperating consumer**:
the fraction of bad effects a *non-gating* actor still lands; the goal is achieved
when each milestone drives its slice of that number to zero.

**The discipline that makes `src/dos/` edits possible (read before M1–M4):** the
arbiter/scope/apply trio is T1 — an in-place edit trips SELF_MODIFY
([[project-self-modify-hook-guards-t1-files]]). Build kernel changes in a **detached
worktree at `origin/master`** ([[project-concurrent-loop-sweeps-index.md]] /
[[project-isolated-worktree-release-on-hot-tree]]), run the targeted suite there,
and commit your own files by explicit pathspec — the tree is multi-session hot.
New host policy is a **driver or `dos.toml` data**, never a `config.py`/`reasons.py`
edit (the litmus). A new refusal token is `dos.toml [reasons]` data.

---

## M1 — Make the gate mandatory (P-GATE complete): wire `apply_gate` onto the binding PreToolUse block

```
Ship docs/342 Milestone 1 (P-GATE): make the docs/126 apply-gate MANDATORY by wiring
apply_gate.decide into the pretool_sensor PreToolUse surface that physically blocks —
generalizing the already-binding SELF_MODIFY deny from T1-files to ANY-lease-tree, so a
NON-cooperating agent's out-of-lane or colliding write is refused AT THE TOOL CALL, not
just by an opt-in CLI it can skip. This is docs/126 Phase 1.5, the single change that
converts DOS from "checker the host may consult" to "gate the write must pass" — the
distance docs/335 named between TCP's false verb and true verb.

CONTEXT: docs/126 P1 shipped `dos apply` (src/dos/apply_gate.py): scope.gate +
lock_modes.region_conflict as one pure verdict, exit 1 on refuse, typed SCOPE_ESCAPE
reason (dos.toml [reasons] data). But it is CLI/pre-commit-stage only — `git commit`
without the hook bypasses it. The working binding PEP is pretool_sensor.decide
(src/dos/pretool_sensor.py): it returns `permissionDecision: deny` and the host enforces
it, today for SELF_MODIFY on T1 files. The job is to run apply_gate's verdict on the
Write/Edit tool's target footprint at that surface, so an escaping/colliding write is
denied before it happens.

THE TRAP TO RESOLVE FIRST (docs/126 P1.5 names it): a reason_class-bearing refuse is NEVER
softened at pretool_sensor (~line 648: `provable and operator_session and not reason_class`),
so an interactive operator would get a DEAD-END deny with no escape — the docs/143 −9pp
spurious-disruption mistake. Pick ONE: (a) enroll SCOPE_ESCAPE in the operator-session
soften set (WARN for an interactive human, hard-deny only under a loop), OR (b) give
SCOPE_ESCAPE a `dos override` armed-window arm like SELF_MODIFY (pretool_sensor ~:667-699).
Do NOT ship a dead-end deny. The CLI gate keeps `--force` as the operator's audited
override; this surface needs its own escape or it regresses docs/143.

BUILD: in a DETACHED WORKTREE at origin/master (src/dos is T1 — an in-place edit trips
SELF_MODIFY). Resolve the run's own lease at the boundary (cli._resolve_self_lease — match
run-id/loop-ts/pid against the WAL leases; empty self-tree → fail CLOSED, refuse). Map the
PreToolUse Write/Edit args to the target footprint, run apply_gate.decide(request, live
leases, config), and translate a refuse into the deny the host already honors. Keep it
HOST POLICY / opt-in (a sensor wiring, not a new built-in predicate — scope.py:54-58
pre-declares apply-gate opt-in). Touch no decision logic: apply_gate already decides.

MEASURE (the P-GATE witness — this is the acceptance test, not a claim): a NON-cooperating
agent process — one that issues a raw Write to a path OUTSIDE its lease through the host's
normal tool path — is BLOCKED at the tool call with a typed SCOPE_ESCAPE refusal, and the
file is UNCHANGED on disk afterward. The witness is the filesystem state + the host deny
record, never the agent's report. This is docs/335 §5's production-vs-acceptance test run
against a non-cooperating consumer: equal-caliber means that consumer's acceptance rate of
bad effects is STILL driven to zero — the exact test TCP passes and DOS today fails.

DISCIPLINE: refuse-MORE only (the gate can only add refusals, never loosen one). Kernel
leaf names no host. The out-of-band advisory PDP path (scope-gate/arbitrate verdicts) is
UNCHANGED — this is an additional binding surface, not a replacement. Do not soften a
loop-context refuse; do not ship a dead-end interactive deny.

DONE = a committed wiring (the pretool_sensor change + the chosen soften/arm escape) with:
a test proving an out-of-lane Write is denied at PreToolUse AND the file is unchanged on
disk (the P-GATE witness); a test proving an in-lane Write passes; a test proving the
interactive-operator escape works (no dead-end); the targeted suite green
(`python -m pytest -q tests/test_pretool_sensor.py tests/test_apply_gate.py tests/test_apply_cli.py`);
`dos verify --workspace . docs/126 P1.5` consulted; and `dos commit-audit --workspace . HEAD`
clean. Explicit-pathspec commit from the worktree. If the docs/143 soften trap can't be
cleanly resolved this session, ship the arm-window escape (option b) — never the dead-end.
```

---

## M2 — Fence the re-granted region (P-FENCE): generation numbers checked at the gate

```
Ship docs/342 Milestone 2 (P-FENCE): add a monotonic generation/epoch number to each lease
grant and CHECK IT AT THE NOW-MANDATORY GATE FROM M1, so a stale-paused holder that wakes
after a SCAVENGE cannot write over a region re-granted to another agent. This is docs/126
Phase 2 and the docs/114 §A2 fix (Kleppmann's fencing-token result) — the place `liveness`
first BINDS instead of advising.

PREREQ: M1 must be shipped — a fence is only meaningful at a mandatory write path. If M1's
PreToolUse binding is not in, STOP and ship M1 first; a generation checked only by an
opt-in CLI is a lock on a door with no wall around it (docs/342 §3).

CONTEXT: the lease WAL already records grants (the lane_journal/lane_lease write path) but
hands the holder NO token it must re-present — docs/114 §A2 verified: a grep for
fencing|generation|epoch|sequencer finds only seq_watermark (a cosmetic compaction counter,
never handed out). The hazard is routine for LLM agents whose pauses exceed a lease TTL:
A acquires → A stalls >TTL without crashing → scavenger frees the lane → B acquires and
edits → A wakes ignorant and writes the same region. Nothing rejects A's write today.

BUILD (detached worktree, T1): stamp a monotonically increasing generation on each lease
grant in the WAL (the grant record already exists — add the counter, fold by append-order
the way replay already reconstructs state). At the M1 gate, the writing run presents the
generation it holds; the gate REFUSES a write whose generation is superseded by a later
grant on an overlapping region. Keep it a pure verdict (generation-in, verdict-out) folded
from the WAL at the boundary — no clock inside the predicate. New refusal token (e.g.
STALE_GENERATION) is dos.toml [reasons] data, category MISROUTE/STALE_CLAIM — never a
reasons.py edit.

MEASURE (the P-FENCE witness): a scripted interleave — holder A is scavenged, the region is
re-granted to B (B's generation > A's), A wakes and attempts a write through the gate. A's
write is REFUSED on a superseded generation; B's write PASSES. The witness is the WAL
generation record + the gate's refusal, reproducible in a test — not the holder's belief
about whether it still holds the lease (which is exactly the forgeable self-report the
fence exists to overrule).

DISCIPLINE: the generation is checked at the gate, not trusted from the holder's claim
(the heartbeat is itself a self-report — docs/114 §A2 sharpest point). Monotonic by
construction. Fail CLOSED on an unresolvable/absent generation. refuse-MORE only.

DONE = a committed fence (the WAL generation field + the gate check) with: a test proving a
superseded-generation write is refused AND the current holder passes (the P-FENCE witness);
a monotonicity test; the targeted suite green (`python -m pytest -q tests/test_apply_gate.py
tests/test_lane_lease.py tests/test_lane_journal.py` plus any new test file); a clean
`dos commit-audit`. Explicit-pathspec commit from the worktree.
```

---

## M3 — Make the binding rung un-authored (P-CHECK): bind completion on OS exit, not a forgeable stamp

```
Ship docs/342 Milestone 3 (P-CHECK): make the gate's COMPLETION decision bind on an
UN-AUTHORED rung (OS_RECORDED exit code / artifact footprint), not the forgeable
grep-subject, wherever the host supplies an execution command. This is docs/126 Phase 3 +
the docs/85 rung-hardening — closing the last forgery: an actor that clears a weak rung at
a strong gate. TCP has no rung an attacker clears by writing a plausible label; this makes
DOS's binding rung the same.

PREREQ: M1 (mandatory gate) shipped. The un-authored check matters once the gate is
mandatory and fenced — before that, the forgery is upstream of the gate.

CONTEXT: the evidence ladder (docs/138) already HAS checksum-grade rungs — `grep-artifact`
(file-path/diff, src/dos/oracle.py _NONFORGEABLE_GREP_RUNGS), `registry`, and `OS_RECORDED`
(drivers/os_acceptance.py: the kernel launches the process via subprocess and reads the OS
exit code the agent cannot forge). The hole is that the DEFAULT binding rung in many flows
is still `grep-subject` — an `--allow-empty` right-SHA commit clears it (docs/335 §3.2,
docs/138 "Where truth is still forgeable"). The fix is to make the strong rung the BINDING
one where the host can supply an exec command.

BUILD (detached worktree if touching src/dos): wire the docs/126 Phase-3 path — `dos`
launches the effect-bearing step, captures exit code + stream into an OS_RECORDED
LogEvidence (the drivers/os_acceptance.py + docs/117 acceptance verb), and the gate/oracle
binds completion on that exit status when the host declares a test/exec command in dos.toml.
Where no exec command exists, prefer the file-path (grep-artifact) rung over grep-subject as
the binding default (docs/114 §A3 disposition — already the recommended order). The
execution rung is a host-supplied driver concern, NOT a kernel default that assumes a test
command exists — keep that boundary.

MEASURE (the P-CHECK witness): an `--allow-empty` right-SHA commit that CLEARS grep-subject
is graded NOT_SHIPPED (or refused at the gate) when the host has declared an execution rung,
because the OS exit code / artifact footprint DISAGREES with the forged stamp. The witness
is the OS exit code the kernel captured — which the agent did not author. Report the
forgeable-rung acceptance rate before/after: the docs/335 §5 forgeable-rung kill, closed.

DISCIPLINE: this verifies CONFORMANCE via an un-authored artifact, not CORRECTNESS (Rice's
theorem forecloses a mechanical correctness oracle — docs/183; green-on-wrong-tests is still
forgeable, docs/138/85). Do not claim the exec rung proves the work is RIGHT — only that the
completion signal is one the agent didn't author. The exec rung is opt-in host policy; the
kernel default where no command is given is the file-path rung, never a fabricated exec.

DONE = a committed change making the binding rung un-authored where an exec command exists,
with: a test proving an `--allow-empty`/forged-subject commit is NOT accepted when the exec
rung is declared (the P-CHECK witness); a test proving the file-path rung is preferred over
grep-subject as the no-exec default; the targeted suite green (`python -m pytest -q
tests/test_oracle.py tests/test_os_acceptance.py` plus touched files); clean
`dos commit-audit`. Explicit-pathspec commit.
```

---

## M4 — The exactly-once question (P-EXACTLY-ONCE): declare the envelope, then build the effect-ledger

```
Ship docs/342 Milestone 4 (P-EXACTLY-ONCE): make re-driving a residual SAFE for
non-idempotent external effects — OR, as the honest shippable floor, DECLARE the
reliability envelope precisely (git-resident effects only) so the host owns idempotency the
way the application owns it above TCP. This is the property docs/342 §4 flags as most likely
to BREAK rather than bend; do the honest floor first, the real fix second.

CONTEXT: TCP's retransmission is idempotent for free — it layers over a pure byte stream, so
re-sending segment 7 never acts twice. DOS's "stream" is agent EFFECTS, and re-driving a
residual that already fired a non-git effect (an external POST, a charged card, a sent
email) is at-least-once execution, and DOS has no Undo (docs/114 dropped the ARIES third
phase; the lineage is forward-recovery/saga over the event-sourced intent ledger, not
backward recovery). docs/342 §4 lays out three options: (1) bound the envelope to git
effects [shippable today, the conservative + honest floor — and equal-caliber, because TCP
too only guarantees the byte stream, the end-to-end argument]; (2) an effect-ledger with
idempotency keys at the gate [the real fix]; (3) compensators [judged over-heavy, skip].

BUILD — PHASE A (the floor, ship this first): write the envelope boundary as a DECLARED fact
— DOS guarantees exactly-once for git-resident effects; non-git side effects are outside the
substrate's reliability envelope and the host must make them idempotent (an idempotency key
on the POST) or accept at-least-once. This is a docs + a contract assertion (the resume/
re-drive path documents and, where it can, ASSERTS that it only re-drives git-idempotent
residuals). Drawing the line precisely IS an equal-caliber move (docs/342 §4: a substrate
that over-claims its envelope is lower caliber than one that draws the line tight).

BUILD — PHASE B (the real fix, if Phase A lands with budget): an effect-ledger on the
mediated-write spine (the M1 gate already owns the run-id/run-dir spine). Record an
idempotency key per external effect; a re-drive presenting a key already in the ledger is
REFUSED at the gate — the effect is not re-fired. This is the saga/event-sourcing direction
docs/114 pointed at, buildable on the intent ledger (src/dos/intent_ledger.py). Keep it a
driver/host-policy seam for the external-effect adapter; the kernel owns the key-dedup
verdict, not the POST.

MEASURE (the P-EXACTLY-ONCE witness): Phase A — the declared envelope is in the docs AND the
re-drive path refuses (or documents the bound on) a non-git residual. Phase B — a re-drive
presenting a ledger-known idempotency key is refused at the gate; the effect fires exactly
once across a re-drive. The witness is the effect-ledger key + the gate refusal, not the
agent's belief about whether it already ran.

DISCIPLINE: do NOT claim exactly-once for effects DOS cannot witness — Phase A's whole value
is honesty about the boundary. The kernel dedups KEYS; it does not execute or compensate the
external effect (that's the host's adapter). effect ≠ correctness still holds.

DONE = Phase A committed (the declared envelope + the re-drive contract assertion + tests
that the re-drive path only re-drives git-idempotent residuals) is the minimum DONE; Phase B
(the effect-ledger dedup verdict + a test proving exactly-once across a re-drive) is the
stretch DONE. Targeted suite green (`python -m pytest -q tests/test_resume.py
tests/test_intent_ledger.py` plus touched files); clean `dos commit-audit`; explicit-pathspec
commit. If P-EXACTLY-ONCE proves unclosable this session, the DONE is the HONEST envelope
declaration — that is itself the equal-caliber result, not a failure.
```

---

## M5 — The spoken standard (P-SPOKEN): one effect-vocabulary across two independent hosts (RUN IN PARALLEL)

```
Ship docs/342 Milestone 5 (P-SPOKEN): prove the guarantee is UNIVERSAL, not per-host — a
second, independent agent host coordinates a real concurrent run through DOS's verbs and is
refused a colliding write BY THE SAME GATE, with no host-specific check re-implemented. This
is docs/340 §3.1 (TCP won by being SPOKEN, not clever — own the effect-language so hosts
share one check instead of each re-deriving its own). RUN THIS IN PARALLEL to M1–M4: it is a
standardization/adoption curve that does not wait on the gate's internals, and the gate's
value compounds with every host that speaks the verbs.

CONTEXT: the wiring already ships — `dos init --hooks` covers the host matrix (claude-code/
cursor/codex/gemini/antigravity/claude-cowork), the MCP server (src/dos_mcp), and the
plugin (claude-plugin/). What's missing for P-SPOKEN is the PROOF that a second host
coordinates through the SAME gate mechanism — not a second copy of the logic. The moat
(docs/340 §4) is that this is a foundation-time, network-effect bet a vendor can't bolt on:
a vendor's verdict on its own agents is a self-report with a dashboard (docs/333 §5
co-resident self-grading); the neutral shared verb is the thing the trend makes most
valuable and a vendor structurally can't own.

BUILD/MEASURE (the P-SPOKEN witness): stand up TWO different agent hosts (e.g. the reference
claude-code wiring + one other from the `dos init --hooks` matrix, OR the MCP surface +
a hook surface) against one shared repo/lease journal. Drive a real concurrent run where
host-A and host-B race on an overlapping region. Show host-B's colliding write is refused by
the SAME gate (the M1 PreToolUse binding or the `dos apply` CLI), reading the SAME lease WAL,
with NO host-specific collision check re-implemented on the B side. The witness is: two
different hosts, ONE refusal mechanism, one WAL. Record it as a reproducible walkthrough
(an examples/playbooks/ entry is the natural home — the playbooks open-tasks-inline-copy
convention).

DISCIPLINE: P-SPOKEN is a LIMIT/adoption claim, not a today claim (docs/342 §1, docs/340 §5:
bet on the derivative; the standardization fight can be lost to a bigger-but-owned vendor).
Do not claim "the field has standardized on DOS" — claim only the measured fact: two
independent hosts, one gate, no re-derived check. The deliverable is the existence proof +
the versioned verb contract it exercises, not a market claim.

DONE = a committed examples/playbooks/ walkthrough (or benchmark entry) showing two
independent hosts refused by one gate over one WAL, reproducible end-to-end, with the verb
contract it relies on named; any kernel/MCP seam touched has its targeted suite green; clean
`dos commit-audit`; explicit-pathspec commit. The honest DONE is the two-host existence
proof — never a "the ecosystem adopted it" narration.
```

---

## The discipline checklist (true of all five — paste into any prompt if the agent drifts)

- **The property is a refusal a witness confirms, never a narration.** Each
  milestone's DONE is a test where the witness is filesystem state / OS exit / WAL
  generation / effect-ledger key — something the agent did not author (docs/138's
  one invariant). "The gate works" is not done; "an out-of-lane write is denied and
  the file is unchanged on disk" is done.
- **Mandatoriness first, then the order is a dependency.** M1 builds the gate M2
  fences and M3 binds through; ship them in order. M5 runs in parallel. Do not start
  M2 before M1's PreToolUse binding is in — a fence on an opt-in CLI is no fence
  (docs/342 §3).
- **`src/dos/` is the kernel's own running code — edit it in a detached worktree.**
  The arbiter/scope/apply trio is T1; an in-place edit trips SELF_MODIFY
  ([[project-self-modify-hook-guards-t1-files]]). Worktree at `origin/master`, run
  the targeted suite there, commit your own files by explicit pathspec (the tree is
  multi-session hot — [[project-concurrent-loop-sweeps-index]]).
- **Mechanism is the kernel; policy is a driver; a refusal token is data.** A new
  host policy is a `drivers/` module, never a `config.py` edit; a new refusal token
  is `dos.toml [reasons]` data, never a `reasons.py` edit (the litmus). The gate can
  only refuse-MORE.
- **Conformance, not correctness.** Every rung verifies that the effect conforms to
  its commitment, never that the work is *good* (Rice — docs/183). Do not claim the
  exec rung proves correctness; green-on-wrong-tests is still forgeable (docs/85).
- **Draw the envelope tight and say so.** Especially P-EXACTLY-ONCE: declaring the
  git-only boundary honestly is itself an equal-caliber move; over-claiming the
  envelope is *lower* caliber than a tight line (docs/342 §4).
- **The metric is acceptance-rate against a non-cooperating consumer → zero.** Every
  milestone closes a slice of docs/335 §5's one number. Report that slice
  before/after, over a stated denominator — not "it now enforces."
- **Prove "done" with the oracle, not narration.** DONE is the property's witness
  test green + a `dos commit-audit`-clean commit — the kernel witnesses the work, the
  way CLAUDE.md's "DOS on DOS" ritual prescribes.
