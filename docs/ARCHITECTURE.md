# DOS kernel — module reference (the cold tier)

> This is the **cold tier** of the architecture contract. `CLAUDE.md` is the
> always-read **hot tier**: it holds the layering law, the litmus tests, the
> syscall ABI, and a one-line-per-module roster. This file holds the *detail* a
> reader only needs when actually editing a given module — the `docs/NN` lineage,
> the byte-clean argument, the mechanism/policy split for each kernel leaf.
>
> The split mirrors the memory store's `MEMORY.md` (hot) / `MEMORY_archive.md`
> (cold) discipline: cheap-and-frequent stays loaded, deep-and-rare is recalled on
> demand. **Every module named in `CLAUDE.md`'s Layer-1 roster is expanded here**;
> the bijection (roster line ⇔ section) is the invariant.
>
> The load-bearing *laws* live in `CLAUDE.md`'s litmus tests — this file does not
> re-argue them. Each section states what the module IS and the one or two facts
> unique to it.

## The cohesion clusters (the kernel's own import graph)

The kernel is internally cohesive. The enforced line is **no host, no I/O policy**
— NOT "no sibling import." The sibling edges:

- `arbiter` → `lane_overlap` → `_tree`
- `loop_decide` → `gate_classify` → `tokens`
- `picker_oracle` → `wedge_reason`
- `timeline` → `git_delta`
- `journal_delta` → `lane_journal`
- `judge_eval` → `judges`
- `intent_ledger` → `durable_schema` / `run_id`
- `resume` → `intent_ledger`
- `reward` → `effect_witness` → `evidence` → `log_source`

## The temporal-verdict family (`liveness` and its siblings)

These verdicts all share one shape: a PURE `classify(evidence, policy) -> verdict`
where the I/O (git read, journal read, the clock) is gathered at the CLI boundary,
never inside the verdict. They differ only in *what stream* they distrust.

### `liveness`
The **temporal verdict** (docs/82): "is the run *moving* (ADVANCING) or just
spinning (SPINNING/STALLED)?", from the git/journal delta, never from the agent's
"making progress" self-report. `verify`'s in-flight sibling. A PURE
`classify(ProgressEvidence, policy) -> LivenessVerdict`; evidence gathered at the
caller boundary via `git_delta` (commits since start SHA) and `journal_delta` (the
PURE lane-journal fold → events-since-start + newest-beat-age, scoped to a run's
`(loop_ts, lane)` lease). Both readers shape `timeline` too. Works with no plan
present (commits-since-start alone suffices). Phase 1 = commit rung + CLI verb;
Phase 2 (shipped) = the journal/heartbeat rung (`journal_delta.fold_since` grounds
the beat + lease-event signal, scoped to a run's lease — identity required, a
`HEARTBEAT` is a *beat* not an *event*, so SPINNING is reachable). The
loop-self-stop + `[liveness]` policy (P3) are not yet built. Advisory.

### `tool_stream`
`liveness`'s **LATERAL sibling** (docs/145, the loop-economics axis): the same
temporal-distrust verdict (`classify_stream(ToolStream, StreamPolicy) ->
StreamVerdict`, ADVANCING/REPEATING/STALLED) re-aimed off the git/journal stream
onto the **in-process tool-result stream**. Where `liveness` asks "did GIT state
advance?", `tool_stream` asks "did the env's tool RESULTS advance, or did the same
`(tool, args, result_digest)` triple recur N times?" It lifts
`churn.decide_coalesce`'s consecutive-identical-run-length pattern off git history
onto the tool stream (the `journal_delta`-vs-`git_delta` "different input, separate
leaf" split). Byte-clean for the same reason `arg_provenance` is: the judged agent
did not author the **identity** of its own repeated env-results (the gym MCP server
authored the result bytes), so REPEATING is provenance-of-repeated-output, never a
"is the agent succeeding?" satisfaction predicate (the §5a line). Advisory: the
consumer attaches a turn-preserving re-surface WARN (never a cut — eventual-
consistency polling is a legitimate repeat), riding the shipped `intervention`
ladder.

### `tool_stream_eval`
`tool_stream`'s pure per-axis eval (recovered-task rate + false-resurface rate —
the `intervention_eval`/`overlap_eval` friendliness instrument).

### `productivity`
`liveness`'s **OTHER lateral sibling** (docs/218, idea H1 from the docs/189 Claude
Code audit): the same pure-verdict shape re-aimed from "did state move *at all*?"
onto "is the work-per-step RATE fading?" — a verdict over a **trend**
(`classify(WorkHistory, ProductivityPolicy) -> ProductivityVerdict`,
PRODUCTIVE/DIMINISHING/STALLED), not a single count. It lifts Claude Code's own
diminishing-returns gate (`tokenBudget.ts:checkTokenBudget`, the `isDiminishing =
steps>=N AND lastDelta<floor AND priorDelta<floor` 3-signal AND) as a kernel
primitive: a run can be `liveness`-ADVANCING (it committed) yet
`productivity`-DIMINISHING (each step lands less), the gap neither `liveness` (one
since-start count) nor `loop_decide` (hard count caps) can see. The cleanest
mechanism/policy split in the kernel — the host names the *work unit*
(tokens/commits/changed bytes) and the thresholds in `dos.toml [productivity]`; the
kernel only compares magnitudes (the `deltas` field is unit-agnostic, never
`tokens`). Byte-clean (a per-step delta is runtime/env-authored, the docs/138
invariant) and **timeless** — it reads a sequence, not ages, so `classify` makes no
I/O *at all* (it bans the clock `liveness` still reads): the strongest
no-plan/no-telemetry floor of any verdict. Advisory: the natural consumer is a
`loop_decide` DIMINISHING_RETURNS rung (stop-when-unproductive, not stop-after-N) or
a WARN-before-BLOCK nudge; the verdict reports the *rate*, never the *quality*.

### `efficiency`
`productivity`'s **lateral sibling** (docs/263): the same pure-verdict shape
re-aimed from a *trend* onto a **ratio** — `classify(EfficiencyEvidence,
EfficiencyPolicy) -> EfficiencyVerdict` (EFFICIENT/COSTLY/WASTEFUL) over
`work / tokens`. It answers the loop-economics question the other two can't:
`liveness` says whether state moved, `productivity` whether the per-step rate is
fading; neither relates the work to its **price**, and a run can pass both while
burning ten times the tokens its work was worth. Byte-clean by construction
(docs/138): both counts are env-authored — the work is what git or the test
runner witnessed, the tokens are what the provider billed — so a run cannot
narrate its way to EFFICIENT. WASTEFUL (zero work, meaningful spend) is
unit-independent and always armed; COSTLY sits behind a host-armed `floor`
(default 0.0 = disabled), so a unit mismatch never manufactures a false COSTLY.
Timeless like `productivity` — `classify` makes no I/O at all. Advisory.

### `noop_streak`
The **wait-marker budget, generalized** (docs/259 §Follow-up 1).
`loop_decide.wait_marker_budget` counts ONE flavor of no-op turn (the `claude -p`
keep-alive marker); this verdict counts the general case — *turns in a row that
paid a full context replay and moved zero ground truth* — so a wakeup-poll loop
that re-reads an output file in a tight tick is the SAME pathology under the same
count-vs-cap verdict. Sits in the temporal family: pure, count-in/verdict-out,
no I/O.

### `marker_sensor`
The wait-marker axis's **boundary I/O** (the temporal family's `posttool_sensor`
analogue): the pure budget verdict needs a count, but the Stop event a host hands
us carries none — so this leaf keeps the per-session tally
(`.dos/markers/<sid>.jsonl`) across the many short-lived hook invocations of one
session and feeds the number to the pure core. I/O at the boundary, data to the
verdict — never inside it.

## The recovery / durability family

### `resume`
`liveness`'s **FORWARD sibling** (docs/107): the third ARIES phase (analysis → redo
→ *continue*), a PURE `resume_plan(LedgerState, AncestryFacts, policy) ->
ResumePlan` (RESUMABLE/COMPLETE/DIVERGED/UNRESUMABLE) over a `run_id`-keyed
`intent_ledger` (`intent.jsonl` in the run-dir — the WAL's sibling, declared
*intent* + adjudicated *progress*). Evidence (which claimed SHAs are in git
ancestry, the `STEP_VERIFIED` mint on the **non-forgeable** rung — never the dead
run's `STEP_CLAIMED` self-report) gathered at the boundary by `resume_evidence`,
exactly as `liveness`'s git read is. It MINTS a belief (the re-entry SHA) and
PROPOSES an effect (`dos resume` prints the residual + re-dispatch command, never
executes — the docs/99 advisory floor). Pause is crash/scavenge made voluntary
(`SUSPEND` op + `dos halt --resumable` + the docs/106 reachability clause retaining
a SUSPENDED-RESUMABLE run-dir). Phases 1–5 shipped; the bench (Phase 6) is future.

### `intent_ledger`
The `run_id`-keyed intent ledger (`intent.jsonl` in the run-dir): the WAL's sibling
— declared *intent* + adjudicated *progress*, the third durable surface. Every
durable record carries a `durable_schema` `schema:` tag.

### `durable_schema`
The §6 floor every durable record now rides: a `schema:` tag + a refuse-don't-guess
`classify` so a record a newer kernel wrote is REFUSED, never misparsed.

### `resume_evidence`
The resume axis's boundary I/O reader — gathers the `AncestryFacts` (which claimed
SHAs are in git ancestry) for `resume.resume_plan`.

## The picker substrate (docs/168 + docs/207)

`loop_decide`'s pre-flight tier — the producers/gates that decide *is there
anything pickable, why-not, have I tried it, did the claim hold, and which fresh
unit to pick first?* All PURE; the file/journal/oracle reads happen at the CLI
boundary.

### `enumerate`
The phase-list PRODUCER (`enumerate_units(source_bytes, *, grammar) ->
Enumeration`; the `declared` set; grammar from `dos.toml [enumerate]`). Closes the
picker-invisibility gap with a typed `DriftNote`, never a silent empty. **Module
named `enumerate.py` so the verb reads `dos enumerate`, but its public fn is
`enumerate_units`** — NEVER the bare `from dos import enumerate`, which would shadow
the builtin.

### `pickable`
The pre-dispatch GATE (shipped `8357ac0`): `classify(unit_state, *, now_ms) ->
Pickability` (OFFERABLE / HELD(`HoldReason`)). Its `is_redispatch_invariant` drives
the `loop_decide.PICK_HELD_INVARIANT` honest-STOP rung.

### `cooldown`
The **anti-churn** fold over the lane-journal **`OP_ATTEMPT`** event (the FIRST
lane-journal record to carry a `durable_schema` tag — a forensic op NOT in
`_STATE_MUTATING_OPS`, so `replay` ignores it; the fold reads it via `read_all`).
`cooldown_verdict(unit, attempts, *, now_ms, policy) -> Cooldown` (CLEAR /
RECENTLY_ATTEMPTED) drives the `PICK_COOLDOWN` rung — the cross-run memory that
breaks the re-pick storm. `cooldown` inlines the lane-journal schema family/version
(a test pins them equal) to break a `config`→`cooldown`→`lane_journal`→`config`
import cycle.

### `reconcile`
The **quiet-completion** JOIN over the agent's claim × the `oracle` verdict:
`reconcile(unit, *, claimed_done, oracle_shipped) -> Reconciliation` (VERIFIED /
QUIET_INCOMPLETE / HONEST_OPEN). Fail-closed on the claim — only ground truth
removes work.

### `pick_priority`
The **anti-churn ORDERING** primitive (docs/254): where `cooldown` *gates* an
already-tried unit, this *orders* what is left so the picker prefers NEW work.
`classify(unit_id, AttemptSummary) -> PickPriority` (NEVER_ATTEMPTED / ATTEMPTED)
folds the SAME `OP_ATTEMPT` history `cooldown` reads into a `sort_key` a host
appends AFTER its own `(priority, status, …)` key. Two signals: never-attempted
first, then least-recently-tried (LRU) among attempted. The safety invariant: the
`sort_key` is a within-tier TIE-BREAKER — it can only reorder inside a
priority/status tier, never gate a unit in/out and never reorder across tiers (a P1
attempted unit still beats a P2 fresh one). Fail-open to NEVER_ATTEMPTED; parameter-
free (no config table — both signals come straight off the ledger). Motivated by the
job repo's 5.3%-ship churn (re-confirming known drains while fresh plans sat
un-picked).

## The seam-protocol family (pure protocol + by-name resolver)

Four kernel seams share one pattern: a pure `Protocol` + a typed verdict/result +
a by-name resolver over an entry-point group + one unshadowable built-in (the safe
default). Every *ruling* implementation with provider/vendor surface lives in a
**driver**, never the kernel. Discovery I/O happens at the call boundary, never
inside a verdict.

### `judges`
The **pure** JUDGE-rung seam: a `Judge` Protocol + a three-valued `JudgeVerdict`
(`agree`/`disagree`/`abstain`) + `run_judge` (fail-to-abstain) + a by-name resolver
over the `dos.judges` entry-point group + the built-in `AbstainJudge` (unshadowable
baseline). Holds the protocol/resolver only — every *ruling* judge with provider
surface lives in a driver (`drivers/llm_judge`). Discovery I/O happens at the call
boundary (`active_judges`), never inside a verdict — the `active_predicates` rule.

### `judge_eval`
The pure evaluation harness over `judges` (confusion grid + false-clear rate +
deterministic-first rung occupancy).

### `overlap_policy`
The **pure** disjointness-SCORER seam (Axis 7, docs/113): an `OverlapPolicy`
Protocol + the built-in `PrefixOverlapPolicy` (a wrap of
`lane_overlap.overlap_verdict`, the unshadowable deterministic floor) + a by-name
resolver over the `dos.overlap_policies` group + `admissible_under_floor`, which
AND-s any policy under the prefix floor (`admit ⟺ floor.admissible AND
policy.admissible`) so a swappable scorer can only refuse-MORE, never admit a
collision. `DisjointnessPredicate` delegates the both-known case to it (default
`prefix` = byte-for-byte the old inline rule); a model-backed scorer lives in a
driver.

### `overlap_eval`
The pure evaluation harness over `overlap_policy` (confusion grid + false-ADMIT
rate + safe-concurrency-forgone — `dos overlap-eval`, the friendliness instrument,
docs/90 §2's backtest).

### `notify`
The **notification-spine seam** (docs/225): the FOURTH instance of the
pure-protocol + by-name-resolver pattern, now on the DELIVERY side. It turns the two
read-only projections (`decisions`/`dispatch_top`) into ONE transport-agnostic
`Notification` (severity + title + summary + the TOP rows as fields + an
edit-in-place `key`) and hands it to a by-name `Notifier`. Holds the `Notifier`
Protocol + `NotifyResult` + the two PURE adapters (`notification_for_decisions` /
`_for_top`, duck-typed over the passed-in `Decision`/`Frame` so this stays a true
leaf importing no layer-3 module) + the resolver + the unshadowable `NullNotifier`
(the honest zero, the default — a bare `dos notify` renders + sends nothing). Names
NO transport. Failure direction = fail-**SOFT**: `send_safely` converts any `send`
raise into a non-delivered `NotifyResult` (a notification is advisory telemetry),
while a *resolve* of an unknown name still raises (config-time operator error).
Advisory floor (docs/99): it READS a projection → push; takes no lease, stops no run
— a LIVENESS-halt field CARRIES the paste-to-stop command but never enacts it.

### `hook_dialect`
The pure-protocol + by-name-resolver pattern on the **OUTPUT side** (docs/217):
DOS computes ONE dialect-neutral hook decision and renders it into the exact JSON
the host runtime parses. The seam holds only the neutral pieces — `HookVerdict` +
`parse_cc` + the `HookDialect` Protocol + `resolve_dialect` + the ONE unshadowable
built-in `ClaudeCodeDialect` (byte-for-byte what the sensors already emit, the
`AbstainJudge` analogue). Every OTHER renderer names its vendor as code, so it
lives in `dos.drivers.hook_dialects` and registers through the
`dos.hook_dialects` entry-point group. The load-bearing direction: a dialect is
OUTPUT chosen by `--dialect`, strictly downstream of an already-decided verdict —
no kernel adjudication can branch on which vendor is acting. Pinned by
`tests/test_vendor_agnostic_kernel.py` (AST-level: no non-driver kernel module
names a vendor) + `tests/test_hook_dialect.py`.

## The witness family (docs/121 → 181 → 230/234)

One thesis at three scales: **a belief bit may only be set by bytes the claimant
did not author.** `evidence` is the seam, `effect_witness` the runtime join,
`reward` the training-set gate — and two kin aim the same split elsewhere:
`commit_audit` at any git repo's history, `improve` at a self-improving loop's
keep decision.

### `evidence`
**Axis 8 of hackability** (docs/121 §5): the pluggable witness-population seam.
`verify()` ships the witness for exactly one class of effect — a commit, read
from git — and is blind to every other (an email sent, a payment made, a deploy
shipped). For those the accountable witness is the **counterparty that received
the effect**: the registry's JSON, the provider's sent-log, the OS exit code.
This leaf is the pure seam such a witness plugs into — the proven apparatus
(Protocol + frozen value types + unshadowable built-in + by-name resolver over an
entry-point group + fail-safe runner) with the `overlap_policy` floor discipline;
every witnessing source with real I/O surface is a driver. Imports
`log_source.Accountability` — who authored the bytes is part of the verdict.

### `effect_witness`
The **result-state witness** (docs/181) — *did the world actually change the way
the agent claimed?* Every in-trajectory detector reads a distress shape the
agent's own bytes co-author, and a competent model fails them silently (docs/177:
83.3% of frontier fails leave no in-trace signal). This leaf reads the other
thing: an **out-of-trajectory read-back of world state**, authored by a witness
the agent did not control, JOINed against the extracted claim. The verdict is a
join of two independently-authored facts — never a re-read of the claim against
itself (the mirror-verifier trap: consistency is not grounding). The field
shipped three shapes of this in early 2026 (Agent-Diff, VAGEN, Tool Receipts,
docs/180); this is the domain-free, deterministic, floor-disciplined version,
built on `evidence`'s apparatus.

### `reward`
`effect_witness`'s **lab-facing consumer** (docs/230/234): `admit(claim_present,
readbacks) -> ACCEPT / REJECT_POISON / ABSTAIN / NO_CLAIM` — may a fine-tune
TRAIN on this trajectory? A self-judged rejection sampler banks every "resolved"
claim as a positive label, which trains the policy to over-claim MORE; this
filter purges that poison (the REFUTED "resolved" is the dispreferred DPO
member). The property a lab pays for is the **non-distillable label**: the
accept bit is a pure function of witness bytes the agent authored zero of, so no
answer text can move it reject→accept — a forgeable read-back is structurally
ignored (`believe_under_floor`). PURE, no I/O; the claim extractor and the
witness are the host's.

### `commit_audit`
The byte-author≠claimant split aimed at a **single commit**: the subject is
authored by whoever wrote the message (forgeable); the files the commit touched
are authored by the commit machinery (not). So the verdict is **author-neutral**
— a human's `fix:` touching only a README fails it exactly as an agent's
`--allow-empty "phase shipped"` does. PURE
(`classify(CommitClaim, DiffFacts, policy) -> ClaimVerdict`; the git read happens
at the CLI boundary), and unlike `oracle.is_shipped` it needs **no plan, no
phase, no DOS vocabulary** — the universal zero-config form of the floor, a
`git log` audit any repo can run. It grades the relationship between the claim's
KIND and the diff's SHAPE, never correctness (the Wall-3 line): it fires
CLAIM_UNWITNESSED only where a concrete code/test claim and a contradicting diff
coexist, and ABSTAINs on the uncheckable (`wip`, `merge`, doc claims on doc
diffs). Advisory; `--sweep` reports a range's DRIFT RATE. This repo's own ritual
runs it as the out-of-loop honesty witness (CLAUDE.md step 6).

### `improve`
The **self-improving-loop keep-gate** (docs/280): `reward.admit` re-aimed from a
training-set admission to a commit-KEEP admission — the kernel leaf of the
propose→verify→measure→keep-or-revert cycle, closing its one fatal hole (the
loop grading its own homework). `classify(CandidateEvidence, policy) ->
KEEP / REVERT / ESCALATE` over four env-authored facts (suite exit on the
candidate-only tree, truth-syscall cleanliness, metric before/after) plus the
carried breaker count. KEEP iff suite green AND truth clean AND a STRICT
env-measured metric gain AND not WASTEFUL; a regression is the non-negotiable
conjunctive floor → REVERT; a safe no-op → REVERT; N non-keeps in a row →
ESCALATE to a human (the RSI human-judgment bottleneck as a kernel rule). The
`narrated` string is carried for the operator and parsed for NOTHING — the only
path to KEEP is to actually move the metric. It composes two sibling kernel
leaves directly: `dos.breaker` runs the escalation arithmetic and
`dos.efficiency` prices the gain (the WASTEFUL revert rung — dead until a host
arms a floor; with docs/300 the candidate's optional `SpendBreakdown` rides
into that rung so a keep/revert record can state its price facts). Only the
breaker's carried COUNT arrives as data in the evidence; the worktree-isolated
propose→gather→classify→actuate engine is a driver (`dos.drivers.self_improve`).

## The Claude-Code-audit family (docs/189 lifts)

Five kernel leaves lifted as primitives from the Claude Code v2.1.88 source audit
(docs/189), each a faithful lift of a CC mechanism re-grounded on DOS's
byte-author≠agent invariant.

### `breaker`
The **generic circuit-breaker facility** (docs/223, idea H2) extracted from
`loop_decide`'s six hand-coded breakers (`consecutive_unclear` /
`consecutive_overloaded` / `consecutive_dirty_zero` / `consecutive_stale_stamp` —
each the same ~15-line bump/trip/reset block, differing only in
counter/threshold/trip-action): a PURE two-counter state machine
(`record_failure` / `record_success` / `classify` over `BreakerCounts` →
`BreakerVerdict`, CLOSED/OPEN). Lifts Claude Code's `denialTracking.ts` two-counter
split faithfully — `consecutive` (a SUSTAINED outage, reset on success) AND `total`
(a FLAPPING failure a streak misses, never reset), tripping on EITHER (the
consecutive-only `loop_decide` shape is blind to flapping; this fixes that). The
malloc move stated cleanest: the kernel is handed COUNTS, never the failure's
*identity* (no UNCLEAR token reaches it), so it cannot smuggle a host assumption.
The DOS enrichment (idea H3 folded in): an OPEN verdict names an `Escalation` rung
(NONE/JUDGE/HUMAN) on the existing ORACLE→JUDGE→HUMAN trust ladder — "don't keep
refusing identically, escalate the rung" — so the trip is a routing decision, not
just a stop. Advisory.

### `exec_capability`
The **arbitrary-exec capability classifier** (docs/223b, idea B1): a PURE
`classify_command(cmd, policy) -> ExecCapabilityVerdict`
(GRANTS_ARBITRARY_EXEC/BOUNDED/EMPTY) lifting CC's `dangerousPatterns.ts` as the
docs/158 "a capability is a SHAPE, not a word" law applied to *command* auditing —
it matches the INVOKED PROGRAM token (first word, after stripping `env`/`sudo`
wrappers + `VAR=value`; basename, lower-cased) against a closed set
(`CROSS_PLATFORM_CODE_EXEC`: interpreters/shells/runners/ssh/sudo), NEVER a
substring (so `cat python.txt` is BOUNDED — it invokes `cat`). A **classifier leaf,
NOT an admission predicate** (the `self_modify` distinction: `self_modify` answers
"may this LANE/tree be leased?" over a tree and plugs into the arbiter conjunction;
`exec_capability` answers "does this COMMAND grant arbitrary exec?" over a command
string — DOS has no permission-rule allow-list surface, CC's home for this, so it is
a detector the `pretool_sensor` PEP CONSULTS, not an arbiter rung). Byte-clean-ish
(the command is the agent's *proposal*, so it is a PRE check on a proposed
capability). The SET is data (`dos.toml [exec_capability]`, `--extra`). ADVISORY by
default — it never denies on its own (the docs/143 −9 pp lesson); BOUNDED is "not in
the declared set," NOT a safety guarantee.

### `hook_exit`
The **shell-hook exit-code classifier** (docs/226, idea C3): a PURE
`classify_exit(code, policy) -> ExitVerdict` mapping a plain shell hook's exit code
onto the `intervention.Intervention` vocabulary (OBSERVE/WARN/BLOCK/DEFER), lifting
Claude Code's `hooks.ts` convention (0 = proceed, 2 = blocking error → BLOCK, any
other non-zero → WARN). The cheapest integration surface — a script too simple to
emit JSON still rides the intervention ladder; its exit code is authored by the
SCRIPT (a deterministic JUDGE), not the judged agent (the actor-witness split,
docs/117). The map is data (`dos.toml [hook_exit]` / `--map`). Fail-safe: an
unanticipated non-zero code falls to WARN (inform, never silently pass, never
spuriously block). Advisory — it RECOMMENDS an `Intervention` (which
`enforce.run_handler` consumes); it never acts.

### `config_lint`
The **config-integrity linter** (docs/227, G1): a PURE `lint(LaneTaxonomy,
ReasonRegistry) -> tuple[Finding, ...]` that finds **dead policy** in the
workspace's own declarations — the `shadowedRuleDetection.ts`/`detectUnreachableRules`
analogue aimed at DOS's registries instead of CC's permission rules. It lifts the
scattered in-CLI checks (`_treeless_lane_findings` inline in `cli.py`; the
overlap-pair logic mirrored in `supervise.overlapping_concurrent_lanes`) DOWN into
one tested leaf and adds the four with no prior home: a lane in BOTH
concurrent+exclusive (contradiction), a dangling autopick/alias target, a dead
reason `see_also` cross-ref, and — the real `detectUnreachableRules` case — a
concurrent lane whose region is a **strict subset** of another's
(`LANE_REGION_SHADOWED`: it can never be picked independently → dead). The
load-bearing distinction is SHADOW (strict subset → *remove the dead lane*) vs
OVERLAP (incidental intersection → *disjoin or mark exclusive*):
`_tree.lane_trees_disjoint` answers only "do they collide?", so the leaf adds a
DIRECTIONAL strict-subset test (`_region_within`, every A-prefix at-or-below some
B-prefix AND not the reverse) over the same case-folded `_tree.norm_tree_prefix` — a
pair is reported by EXACTLY one of the two. Byte-clean by construction (its only
input is operator-authored config), pure (no I/O — the taxonomy is gathered at the
CLI boundary), and names no host (it reads the generic taxonomy FIELDS, never a lane
NAME — the Law-1 litmus, pinned at the finding-text level). Typed `Finding` (closed
`LintKind` + `Severity` error/warn/info). Advisory: it REPORTS dead policy, never
rewrites `dos.toml`, never refuses a lease — `dos doctor --check` and the focused
`dos lint` verb gate on it (error/warn gate; info is cosmetic and never gates).

## The hook telemetry contract (docs/276 → 297)

### `hook_observation`
The **`hook-observation` record family** (docs/297, issue #24): the kernel-owned
per-call telemetry contract every hook runtime writes — one schema-tagged JSONL
line per hook invocation under `.dos/metrics/observations.jsonl` (verb, outcome,
exit, latency; verb-specific fields written only-when-set, the additive
contract). Born on the plugin's Go binary (docs/276 Part 2), lifted to a kernel
leaf so the kernel reads a contract IT defines, not a log "only the plugin
writes" — the Option-B ownership inversion that keeps the awareness arrow clean:
nothing here names a vendor or a binary; the Go binary and the Python hook verbs
(`dos hook pretool/posttool/stop/marker`) are both *conforming writers*. Owns
the PURE entry builder, the FAIL-SOFT fsync'd append (a telemetry fault can
never change an emitted dialect or exit code — docs/99; `DOS_HOOK_METRICS=0`
opts out), the tolerant `durable_schema`-gated reader, and the PURE
`intervention_rate` fold — the denominator `dos helped` lacked (issue #24):
`adjudicated` = pretool records minus `delegate` handoffs (a handoff's real
verdict is the deciding runtime's own record, so each call counts exactly once
in the binary-only / Python-only / mixed writer worlds), `intervened` =
adjudicated minus passthrough. Like-for-like by construction: the fold admits
observation records only — the lane journal (a different log, window, and
scope) has no path into either side of the ratio. Byte-clean (docs/138): every
counted field is env-authored, downstream of an already-decided verdict.

## The seam-data leaf

### `lifecycle`
The `[lifecycle]` plan-class-taxonomy seam data (class set + transitions +
failsafes) the `dos-class-cycle` operator skill reads — pure leaf, validated shape
(a transition naming an unknown class raises). The judge *content* stays a
`dos.judges` driver.

---

# Lifted from CLAUDE.md (2026-06-12)

The sections below carried the full detail in the architecture contract
([CLAUDE.md](../CLAUDE.md)) until the contract was slimmed to its hot-tier core.
They moved here verbatim — this file is the cold tier the contract points at.
The contract keeps the binding short forms; on any conflict, the contract wins.

## The layering — long form

| Layer | What it is | Where | May import |
|---|---|---|---|
| **1. Kernel** (mechanism) | The syscalls, pure: `verify()`, `refuse()`, `arbitrate()`, `liveness()`, `resume`, `spawn/reap`. Closed verdict vocabulary, ship oracle, structured-refusal enum, lease arbiter, liveness verdict, resume/intent-ledger, correlation spine. Every verdict is a PURE `classify(evidence, policy)`; I/O is gathered at the CLI boundary, never inside the verdict. **No host names, no plan schema, no I/O policy.** Per-module detail (the `docs/NN` lineage + the byte-clean argument for each leaf) lives in this file — read it before editing a leaf. | `src/dos/*.py` — every module not claimed by rows 2a/2b (`config.py`, `reasons.py`, `stamp.py`), row 3's shells, or `drivers/` (row 4). **The roster is the directory listing — deliberately NOT enumerated here: a hand-kept list rots** (two audits, 2026-06-08 and 2026-06-10, found this cell naming ~45 of ~150 shipped modules). Orientation by cohesion cluster (a sample, not a roster): the temporal-verdict family (`liveness`/`tool_stream`/`productivity`/`efficiency`/`noop_streak` + its `marker_sensor` boundary I/O); the recovery family (`resume`/`intent_ledger`/`durable_schema`); the picker substrate (`enumerate`/`pickable`/`cooldown`/`reconcile`); the seam-protocol families (`judges`/`overlap_policy`/`notify`/`hook_dialect`); the witness family (`evidence`/`effect_witness`/`reward`, docs/230/234); the docs/189 CC-audit lifts (`breaker`/`exec_capability`/`hook_exit`/`config_lint`). This file carries the per-module `docs/NN` lineage for the leaves it covers but LAGS the newest modules — a newer leaf's lineage is the `docs/NN_*.md` plan its own module docstring names. | stdlib, `dos.config`, the seam-data modules (2b), and **sibling kernel modules** — the kernel is internally cohesive (the import edges are listed above). The enforced line is the next column's litmus — **no host, no I/O policy** — NOT "no sibling import." |
| **2a. Seam** (config) | `SubstrateConfig` — the single injected boundary. Workspace root (`.root`) + the *policy hooks* (lane taxonomy, path layout, **refusal vocabulary, ship-stamp grammar**) the kernel reads instead of hardcoding `REPO_ROOT`, **plus the discovered `WorkspaceFacts` (`.workspace`)**: facts gathered ONCE via build-time I/O (chiefly *which of the kernel's own runtime files exist under this root* → `is_kernel_repo`), cached as data so a **pure** verdict (`arbitrate`) can be workspace-aware without re-probing the disk — the "I/O at the boundary, data to the pure core" rule (cf. `git_delta`/`journal_delta` → `liveness.classify`), lifted to the config seam so the *workspace is a first-class object with discovered properties*, not a bare path. Ships a **generic** `main`/`global` default. | `src/dos/config.py` | stdlib + the seam-data modules (2b) |
| **2b. Seam data** (closed-sets-as-data) | The hackability registries the seam carries *as values*: the block-reason vocabulary (`ReasonSpec`/`ReasonRegistry`/`BASE_REASONS`) and the ship-stamp convention (`StampConvention`/`JOB_`/`GENERIC_STAMP_CONVENTION`). Pure stdlib leaves; declared per-workspace in `dos.toml`. This is the "closed enum → declared data" pattern (see `docs/HACKING.md`). | `src/dos/{reasons,stamp}.py` | stdlib only |
| **3. Helpers** | Thin shells over the kernel that carry **no policy of their own**: the umbrella CLI, the file-tree disjointness algebra, timeline assembly, the operator-decision queue + its TUI (the *what-needs-me* projection), and the live fleet-watchdog `dos top` + its TUI (the *what's-running-now* projection, behind the `[tui]` extra). Both projections are read-only — they read kernel state only (`decisions` over the four refusal sources; `dispatch_top` over `lane_journal.replay` + `liveness.classify` + the verdict envelopes + `git_delta`), acquire no lease, launch nothing, mutate no substrate. The `dos notify {decisions,top}` verb pipes either projection through the `notify` seam to a by-name transport (default `null` = render only); host-cadence-free (a fleet drives it with `/loop`/cron — no daemon in the kernel). | `src/dos/{cli,_tree,timeline}.py` + the read-only projection pairs (`decisions`/`dispatch_top`/`plan_board` and their `*_tui` shells) — same rule as row 1: the disk listing is the roster, this is orientation | layers 1–2 |
| **4. Drivers** (policy + adjudicators) | The layer outside the kernel boundary, in **three kinds**: (a) a *host repo's policy pack* — which lanes exist, how they admit concurrency, where its plans/ship-state live (`job.py`); (b) *out-of-kernel adjudicators* — the **JUDGE rung** (ORACLE → JUDGE → HUMAN), a non-deterministic adjudicator ruling on the residue the oracle ABSTAINED on (`llm_judge.py`), hedged by four disciplines: deterministic-first, advisory-only, fail-to-abstain, abstention-first (`docs/86_*`, seam `dos.judges`); and (c) *out-of-kernel transports* — the notification spine's delivery side, where a `Notifier` names a vendor as code (`notify_slack.py`, behind the `[notify-slack]` extra, registered via `dos.notifiers`). Each has the surface the kernel forbids (provider/I/O/non-determinism/network). Adding a host or adjudicator or transport = a module here (or a `dos.judges`/`dos.predicates`/`dos.renderers`/`dos.notifiers` plugin), **never touching the kernel.** | `src/dos/drivers/<host>.py` (e.g. `job.py`; `llm_judge.py:LlmJudge` the `dos.judges` occupant; `notify_slack.py:SlackNotifier` the `dos.notifiers` occupant) | layers 1–2 |

**Four things live OUTSIDE the four layers — each operates *on* the package, never
*inside* it (the dependency arrow is one-way: they `import dos` / call the `dos`
CLI; nothing under `src/dos/` imports them).** Treat each as its own workstream — an
edit to one is never an edit to the substrate:

- **Release & dev tooling** — the release scripts (`scripts/release_*.py`) and the
  `/release` + `/stable-release` skills (`.claude/skills/`) cut/gate versions. Not
  shipped. (Litmus: "no `scripts/` in the kernel.")
- **The MCP server** (`src/dos_mcp/`, `docs/80_*`) — a `FastMCP` server exposing the
  syscalls as MCP tools (JSON over stdio, zero Python coupling), the lowest-friction
  **adoption surface**. *Is* shipped, but as a **separate top-level package**
  (deliberately `dos_mcp`, not `dos.mcp` — folding it under `dos` would force a
  server framework into the near-stdlib kernel); the `mcp` dependency lives only in
  the `[mcp]` extra, so the kernel's own dependency set stays PyYAML-only. (Litmus:
  "kernel never imports the MCP server.")
- **The generic skill pack** (SKP, `docs/74_*`) — the domain-free `SKILL.md`
  screenplays under `src/dos/skills/` (one per reference workflow; the directory
  listing is the roster), shipped as package-DATA (not code — nothing
  imports them; they shell only `dos` verbs). The Axis-5 hackability surface: the
  *shape* "snapshot → `verify` → render → `gate` → take a lane → archive" is
  mechanism, every host specific is config data. (Litmus: "a shipped generic skill
  names no host.")
- **Phased-plan concepts** — `execution-state.yaml`, soft-claims, plan-meta
  frontmatter, a host's tuned `next-up`/`dispatch`/`replan` skills are the **host's
  workflow** (they live in the reference userland app). The kernel treats the plan
  registry as an *optional* `source`: `verify()` in a repo with no plan answers from
  git history alone (`source="none"`). **Do not couple the kernel to the phased-plan
  layer.** (Litmus: "`verify` needs no plan.")

## The litmus tests — full arguments

- **Kernel imports no host.** No module under `src/dos/` (except `drivers/`) may
  name `job`, `apply`, `tailor`, or any host-specific lane. The generic default
  in `config.py` is `main`/`global`.
- **A driver is the only place policy lives.** `JOB_LANE_TAXONOMY` and
  `job_config` live in `dos.drivers.job`, re-exported from `dos.config` /
  `dos` only for backward compatibility. New host policy → a new `drivers/`
  module, not an edit to `config.py`.
- **`verify` needs no plan.** `tests/test_verify_no_plan.py` proves the truth
  syscall runs against a plain git repo with no `docs/*-plan.md` and no registry.
- **The package never assumes it lives in the repo it serves.** Every path
  resolves against `SubstrateConfig.root` (the workspace root: explicit arg ›
  `DISPATCH_WORKSPACE` › cwd, via `resolve_workspace_root`), never `__file__`.
  (`SubstrateConfig.workspace` is the distinct *facts* field — see the seam row.)
- **The kernel never imports its own tooling.** No module under `src/dos/` may
  import from `scripts/`. The release scripts and `.claude/skills/` consume the
  package (they `import dos` / call the `dos` CLI); the package is unaware they
  exist. Grep-checkable: `import .*scripts` / `from scripts` must not appear under
  `src/dos/`. (Release scripts *do* anchor on `git rev-parse --show-toplevel`, not
  `__file__` — they ship with the repo they release, so the git top-level is the
  honest root.)
- **The kernel never imports the MCP server.** No module under `src/dos/` may
  import `dos_mcp`. The server consumes the package (it `import dos`); the kernel
  is unaware it exists. Grep-checkable: `import dos_mcp` / `from dos_mcp` must not
  appear under `src/dos/`. `dos_mcp` resolves its served workspace via the same
  `SubstrateConfig` seam as everything else (explicit `workspace` arg ›
  `DISPATCH_WORKSPACE` › cwd), never `__file__`, and passes the built config
  EXPLICITLY into each syscall (`oracle.is_shipped(cfg=…)`,
  `arbiter.arbitrate(config=…)`) rather than mutating process-global active
  state — correct for a long-lived server fielding concurrent workspaces. Pinned
  by `tests/test_mcp_server.py`.
- **The kernel never imports a judge implementation.** The JUDGE rung is the one
  place a non-deterministic, provider-backed adjudicator is allowed — and it lives
  in a **driver**, never the kernel. The `dos.judges` seam (kernel) holds only the
  pure `Judge` protocol + `JudgeVerdict` + `run_judge` + resolver + the built-in
  `AbstainJudge`; every *ruling* judge (`drivers/llm_judge:LlmJudge`, any
  `dos.judges` plugin) is discovered by name at the call boundary, never imported by
  a kernel module. Grep-checkable: no module under `src/dos/` (except `drivers/`)
  may `import dos.drivers` / `from dos.drivers`. The discipline that makes an open
  adjudicator set safe is four-fold — deterministic-first, advisory-only,
  fail-to-abstain (`run_judge` converts any raise/bad-return to `ABSTAIN`, never
  `AGREE`), abstention-first — and is the judge analogue of the predicate
  conjunctive-only rule. Pinned by `tests/test_judges.py` (+ `docs/86_*`).
- **A swappable overlap scorer can only refuse-MORE, never admit a collision.**
  The disjointness scorer is now pluggable (Axis 7, `dos.overlap_policies`,
  docs/113) — and because a policy returns a verdict that *includes admit*
  (unlike a predicate, which can only refuse), the safe direction is guaranteed
  STRUCTURALLY by a deterministic floor: `overlap_policy.admissible_under_floor`
  AND-s any policy under the unforgeable prefix-disjointness verdict
  (`admit ⟺ floor.admissible AND policy.admissible`). So a buggy/hostile/raising
  policy cannot admit a path-colliding pair — it degrades to the prefix floor
  (today's behavior), never looser. The default `prefix` policy under the floor
  reproduces `lane_overlap.overlap_verdict` byte-for-byte (the whole existing
  arbiter/overlap suite stays green). A model-backed scorer lives in a **driver**
  (the `dos.drivers` litmus above covers it); the kernel seam imports no ruling
  policy. Pinned by `tests/test_overlap_policy.py` (incl. an arbiter-level proof
  that a lying-admit policy cannot double-book a held lane through `arbitrate`).
- **The kernel names no vendor in code; a host dialect is a driver.** The hook
  output a runtime parses is vendor-shaped (Claude Code's `hookSpecificOutput`,
  Gemini's top-level `decision`, Cursor's `permission`, docs/217). The kernel seam
  `dos.hook_dialect` holds only the dialect-NEUTRAL `HookVerdict` + `parse_cc` + the
  `HookDialect` Protocol + the by-name `resolve_dialect` + the ONE unshadowable
  built-in `ClaudeCodeDialect` (the default — byte-for-byte what the sensors already
  emit, the `AbstainJudge` analogue). Every OTHER renderer (`CodexDialect` /
  `GeminiDialect` / `CursorDialect`) names its vendor as code, so it lives in a
  **driver** (`dos.drivers.hook_dialects`) and registers through the
  `dos.hook_dialects` entry-point group — the same kernel/driver split as
  `judges`/`llm_judge` and `overlap_policy`. Grep-checkable + AST-pinned: no
  non-driver kernel module may name a vendor as a code identifier (the sole
  allowance is the `claude-code` default in `hook_dialect.py`), so no kernel
  *adjudication* can branch on which vendor is acting — a dialect is OUTPUT chosen by
  `--dialect`, strictly downstream of an already-decided verdict. Pinned by
  `tests/test_vendor_agnostic_kernel.py` + `tests/test_hook_dialect.py`.
- **A shipped generic skill names no host.** No `SKILL.md` under
  `src/dos/skills/` may name a host directory (`docs/_plans`, `output/next-up`), a
  host lane (`apply`/`tailor`/`discovery`), or a host commit prefix (`docs/dispatch:`)
  — every host specific comes from `dos doctor --json` / `dos.toml`. The skill
  analogue of "kernel imports no host," and like that rule it is grep-checkable +
  pinned (`tests/test_skill_pack_*.py`). The skills are package-DATA, not code:
  `src/dos/skills/` has no `__init__.py` and nothing under `src/dos/*.py` imports
  it.

## The syscall ABI in full

| Syscall | Module | What it is |
|---|---|---|
| `verify()` | `dos.oracle`, `dos.phase_shipped` (grammar: `dos.stamp`) | the **truth syscall** — "did (plan,phase) actually ship?", registry-first, ancestry-checked, never from self-report. Works with **no** plan present. The grep rung's ship-subject grammar is `SubstrateConfig.stamp` (a `dos.stamp.StampConvention`), declarable per-workspace in `dos.toml` `[stamp]` — strict host-grammar by default, generic by opt-in. |
| `liveness()` | `dos.liveness` (evidence readers: `dos.git_delta`, `dos.journal_delta`) | the **temporal verdict** (docs/82) — "is the run *moving* (ADVANCING) or just spinning (SPINNING/STALLED)?", from the git/journal delta, never the agent's "making progress" self-report. `verify`'s in-flight sibling; works with no plan present. Phase 2 (the journal/heartbeat rung) shipped; loop-self-stop (P3) is not. Advisory. |
| `productivity()` | `dos.productivity` | the **loop-economics verdict** (docs/218) — "is the run still *doing work*, or fading?" `liveness`'s lateral sibling re-aimed onto a **trend**: `classify(WorkHistory, policy) -> ProductivityVerdict` (PRODUCTIVE/DIMINISHING/STALLED) over per-step work deltas. The cleanest mechanism/policy split in the kernel (host names the work-unit + thresholds; kernel only compares magnitudes); timeless — makes **no I/O at all**. Advisory. CLI: `dos productivity --deltas …` (PRODUCTIVE 0 / DIMINISHING 3 / STALLED 4). |
| `efficiency()` | `dos.efficiency` | the **token-effectiveness verdict** (docs/263) — "did the tokens this run spent *buy work*?" `productivity`'s lateral sibling re-aimed from a trend onto a **ratio**: `classify(EfficiencyEvidence, policy) -> EfficiencyVerdict` (EFFICIENT/COSTLY/WASTEFUL) over `work / tokens`. Relates the work to its *price* — the question an operator means by "token effectiveness" (a run can be PRODUCTIVE yet burn 10× the tokens its work was worth). **Non-forgeable by construction**: both counts are env-authored (the work git/the test-runner witnessed; the tokens the provider billed), so a run cannot narrate its way to EFFICIENT (the docs/138 invariant). WASTEFUL (0 work, meaningful spend) is unit-independent and always-free; COSTLY is opt-in behind a host-armed `floor` (default 0.0 = disabled, so a unit mismatch never manufactures a false COSTLY). Timeless — makes **no I/O at all**. Advisory. CLI: `dos efficiency --work W --tokens N [--floor R]` (EFFICIENT 0 / COSTLY 3 / WASTEFUL 4). |
| `work_account()` | `dos.work_account` | the **work-kind account** (docs/310) — "what KINDS of work did this iteration land?" The **composition** sibling: `productivity` reads a trend, `efficiency` a ratio, this reads a typed account by kind. `classify_work(WorkAccount) -> SHIPPED/CAUGHT/ADVANCED/GROOMED/SURFACED/IDLE` over per-kind counts the witnesses authored (oracle-verified ships; oracle-REFUTED claims; git's lane commits; grooms/unblocks; raised decisions). The fix for the one-bit stats forcing ("did a pick ship?"): a 0-pick iteration that advanced, groomed, or caught a false claim stops reading as "drained" — `event_severity` grades it NOTICE off the account, and the headline composes every non-zero kind ("1 pick shipped · 4 commits advanced"). Claims alone classify IDLE: narration cannot climb the ladder (docs/138); the unadjudicated over-claim count is echoed, visible but powerless. Two axes, two words: IDLE is the iteration's work, DRAIN stays the backlog's. Timeless — makes **no I/O at all**. Advisory and observability-only (changes what stats SAY, never what the loop DOES). CLI: `dos work-account --verified-ships N --advance-commits N …` (healthy kinds 0 / CAUGHT 3 / IDLE 4). |
| `improve()` | `dos.improve` (composes `dos.efficiency` + `dos.breaker`; engine: `dos.drivers.self_improve`) | the **self-improving-loop keep-gate** (docs/280) — "may a self-improving work loop *keep* this candidate change?" The kernel leaf of the first recursive-self-improvement loop for DOS: `reward.admit` ([[234]]) re-aimed from a training-set admission to a commit-KEEP admission. `classify(CandidateEvidence, policy) -> KEEP / REVERT / ESCALATE` over four **env-authored** facts (the suite's exit on the candidate-only tree, the truth syscall's cleanliness, the metric before/after) + the carried breaker count. KEEP iff suite green AND truth clean AND a STRICT env-measured metric gain AND not WASTEFUL; a regression (red suite / dirty truth) is the non-negotiable conjunctive floor → REVERT; a safe no-op → REVERT; N non-keeps in a row → ESCALATE to a human (the RSI "human-judgment bottleneck" as a kernel rule). **Non-forgeable** (docs/138/234 at loop scale): the keep-bit reads zero loop-authored bytes — a `narrated` string is carried for the operator and parsed for nothing, so a loop *cannot write its way into the kept set*; the only path to KEEP is to actually move the metric. PURE, no I/O, names no host (the metric + the proposer are the host's, injected into the `self_improve` engine which does the worktree-isolated propose→gather→classify→actuate). Advisory. CLI: `dos improve --suite-passed --truth-clean --work W --baseline-work B [--max-reverts N]` (KEEP 0 / REVERT 3 / ESCALATE 4). |
| `breaker()` | `dos.breaker` | the **circuit-breaker primitive** (docs/223) — "this failure class keeps tripping; stop, and escalate the rung." A PURE two-counter state machine (`record_failure`/`record_success`/`classify -> CLOSED/OPEN`); trips on `consecutive` (sustained) OR `total` (flapping). An OPEN verdict names an `Escalation` rung (NONE/JUDGE/HUMAN). Advisory. CLI: `dos breaker --consecutive N --max-consecutive M` (CLOSED 0 / OPEN 3). |
| `exec_capability()` | `dos.exec_capability` | the **arbitrary-exec capability classifier** (docs/223b) — "does this command grant arbitrary code execution?" `classify_command -> GRANTS_ARBITRARY_EXEC/BOUNDED/EMPTY`, matching the INVOKED PROGRAM token against a closed set, NEVER a substring (`cat python.txt` is BOUNDED). A classifier leaf the `pretool_sensor` PEP consults, not an arbiter predicate. ADVISORY (BOUNDED ≠ a safety guarantee). CLI: `dos exec-capability --command "…"` (BOUNDED/EMPTY 0 / GRANTS_ARBITRARY_EXEC 3). |
| `hook_exit()` | `dos.hook_exit` | the **shell-hook exit-code classifier** (docs/226) — "a plain script exited N; which intervention is that?" `classify_exit -> OBSERVE/WARN/BLOCK/DEFER` (0 = proceed, 2 = BLOCK, other non-zero = WARN). The cheapest integration surface; fail-safe (unknown non-zero → WARN). Advisory. CLI: `dos hook-exit --code N` (PASS 0 / BLOCK 3 / WARN 4 / DEFER 5 / OBSERVE 6). |
| `resume` | `dos.resume`, `dos.intent_ledger` (durability: `dos.durable_schema`; evidence reader: `dos.resume_evidence`) | the **third ARIES phase** (docs/107) — "a run died/paused mid-flight; how far did the *fossils* say it got, and what is the residual?" `liveness`'s FORWARD sibling. `resume_plan -> RESUMABLE/COMPLETE/DIVERGED/UNRESUMABLE` over a `run_id`-keyed intent ledger; MINTS the re-entry SHA off the non-forgeable rung, PROPOSES (never executes) the re-dispatch. Phases 1–5 shipped; bench (P6) future. |
| `reward()` | `dos.reward` (join: `dos.effect_witness` → `dos.evidence`) | the **reward-set admission verdict** (docs/230/234) — "may a fine-tune *train* on this trajectory?" The on-ramp that puts the deterministic floor *inside a training loop*: `admit(claim_present, readbacks) -> ACCEPT / REJECT_POISON / ABSTAIN / NO_CLAIM`. `effect_witness`'s lab-facing consumer — a self-judged sampler banks every "resolved" claim as a positive (training the policy to over-claim more); this purges the poison a non-forgeable witness REFUTES (the dispreferred DPO member). The **non-distillable label**: the accept bit is a pure function of the witness the agent authors zero bytes of, so no answer text can move it reject→accept (inherits `believe_under_floor`; a forgeable read-back is structurally ignored). PURE, no I/O, names no host (the claim extractor + witness are the host's). Advisory. CLI: `dos reward --claim --witness {confirm,refute,none} [--forgeable]` (ACCEPT 0 / REJECT_POISON 3 / ABSTAIN 4 / NO_CLAIM 5). |
| `refuse(reason_class)` | `dos.wedge_reason`, `dos.picker_oracle` (vocabulary: `dos.reasons`) | **structured refusal** — a closed reason vocabulary, simultaneously emittable, verifiable, refusable. The reason set is `SubstrateConfig.reasons` (a `dos.reasons.ReasonRegistry`, base `BASE_REASONS`), declarable per-workspace in `dos.toml` `[reasons]`. |
| `lease()` / `arbitrate()` | `dos.arbiter` | the **pure admission kernel** — `arbitrate(request, live_leases, config) -> decision`, state-in / decision-out, no I/O. |
| `spawn()` / `reap()` | `dos.run_id`, `dos.lane_journal` | the **correlation spine** (sortable, lineage-carrying run-ids) + the lease **write-ahead log**. |
| `pickable()` / `enumerate()` / `cooldown()` / `reconcile()` | `dos.pickable`, `dos.enumerate`, `dos.cooldown`, `dos.reconcile` (grammar: `dos.toml` `[enumerate]`/`[cooldown]`/`[lifecycle]`) | the **picker substrate** (docs/168 + docs/207) — the producers/gates that decide *is there anything pickable, why-not, have I tried it, and did the claim hold?* `enumerate` produces the `declared` phase set; `pickable` is the pre-dispatch gate (OFFERABLE / HELD); `cooldown` is the anti-churn fold over `OP_ATTEMPT` (CLEAR / RECENTLY_ATTEMPTED — the cross-run memory breaking the re-pick storm); `reconcile` is the quiet-completion join (VERIFIED / QUIET_INCOMPLETE / HONEST_OPEN, fail-closed on the claim). All PURE; reads at the CLI boundary. CLI: `dos pickable`/`enumerate`/`cooldown`/`reconcile`. |
| `notify()` | `dos.notify` (transport: a `dos.notifiers` driver, e.g. `dos.drivers.notify_slack`) | the **notification spine** (docs/225) — "push *what needs a human* / *what's running* to where the operator is (Slack first)." NOT a verdict: the FOURTH pure-protocol + by-name-resolver seam, on the DELIVERY side. Two PURE adapters turn the `decisions`/`dispatch_top` projections into one transport-agnostic `Notification`; `send_safely` delivers FAIL-SOFT (a transport raise → a non-delivered result, never a crashed producer); the built-in `null` sink is the safe default. Advisory (docs/99): reads a projection → push; takes no lease, stops no run. CLI: `dos notify {decisions,top} [--notifier slack --channel NAME] [--dry-run] [--json]`. |
| `lint()` | `dos.config_lint` (algebra: `dos._tree`) | the **config-integrity linter** (docs/227, G1 from docs/189) — "is there *dead policy* in this workspace's own declarations?" A PURE `lint(LaneTaxonomy, ReasonRegistry) -> tuple[Finding, ...]`, the `detectUnreachableRules` analogue. Finds a treeless lane, a concurrent∩exclusive contradiction, a dangling autopick/alias/`see_also` target, an order-sensitive roster, and the real unreachable-rule case — a concurrent lane whose region is a strict subset of another's (`LANE_REGION_SHADOWED`). SHADOW (subset → remove) vs OVERLAP (intersection → disjoin) is the load-bearing split. Typed `Finding` (closed `LintKind` + `Severity`). Advisory. CLI: `dos lint [--strict] [--json]` (0 clean / 1 error-or-warn), folded into `dos doctor --check`. |

## Consumers, releasing, and the docs/97 drift note

The reference userland app's `scripts/{ship_oracle,wedge_reason,picker_oracle,dispatch_tokens,
dispatch_loop_decide,gate_classify,dispatch_timeline,fanout_preflight_context,
fanout_archive_lock,check_phase_shipped,run_id,lane_journal}.py` are **byte-thin
re-export shims** over `dos.*` (`from dos.X import *`). Its `pyproject.toml`
pins `dos-kernel` (the distribution name; `dos-kernel>=X.Y` or `==X.Y.Z`); dev
install is `pip install -e` against this repository. **Edit substrate LOGIC in
this repo, not in the host shims** — the shims carry none. The host picks up
changes via the editable install. This is one pinned dependency, NOT a mono-repo
fold (the host's own "Independent Repository" rule is honored on both sides).
`install.py` is safe by construction (it does `pip install -e .` and asserts the
resolved path is inside this repo); docs that write `pip install dos-kernel[mcp]`
mean "the `[mcp]` extra vs. the core install."

**Releasing** (dev tooling, outside the kernel): `/release`
(`.claude/skills/release/`) cuts a rolling `vX.Y.Z` — bumps the two version
markers (`pyproject.toml` + the `src/dos/__init__.py` fallback literal, kept in
lockstep by `scripts/release_bump.py`), drafts `docs/releases/vX.Y.Z.md`,
commits, tags, pushes to `master`, creates a GitHub release, verifies with
`pytest -q` + `dos doctor`; backed by `scripts/release_context.py`. No Go binary
/ zip / screenshots / versioned-install snapshot (host-only ceremonies DOS
doesn't have). `/stable-release` (`.claude/skills/stable-release/`) promotes an
already-shipped `vX.Y.Z` to `stable/<codename>` on a gate of green kernel suite
+ clean truth syscall + soak window; writes an evidence file + a second
annotated tag; mints no new version; backed by
`scripts/stable_release_context.py`.

**What is NOT yet ported (the heavy tier):** `fanout_state.py` lease core,
`next_up_*` renderers, and the plan-meta schema stay in the reference userland
app — host workflow + heavy I/O, not kernel mechanism. The `dos.arbiter` is the
*extracted pure* admission kernel for new consumers; the host still owns its own
`arbitrate_lane`.

**Drifting downward (docs/97 Phase 1 has landed at the API).** The
concurrency-class *claim-budget* — "at most N of kind K may hold a lease at
once" — is no longer purely host-side: `arbiter.arbitrate(...,
class_budgets={"priority": 3})` takes the budgets (`arbiter.py:159`), counts
live leases per kind on the auto-pick walk and skips budget-exhausted candidates
(`arbiter.py:329-348`), and returns the named `CLASS_BUDGET_EXHAUSTED` refuse
(`arbiter.py:637-655`), pinned by `tests/test_arbiter.py`. So the kernel already
owns the *admission logic*; the host supplies only the *value*. What is NOT yet
reachable from the operator surface is the rest of docs/97: no `dos arbitrate
--class-budget K=N` flag and no `[[concurrency_class]]` table in `dos.toml` (the
budgets are a Python parameter only). Wiring that CLI/config seam — and folding
the host soft-claim / `STALE_CLAIM` adjudication into the class registry — is
the remaining lift, planned in `docs/97_concurrency-class-model-plan.md`. (Note
the word *claim* is overloaded here: a lane lease is a region-CLAIM the arbiter
adjudicates (docs/89); the intent ledger's `STEP_CLAIMED` is the agent's
distrusted *self-report* of progress vs the git-`STEP_VERIFIED` fact (docs/107);
the host `soft_claim`/`STALE_CLAIM` is a packet read against
`execution-state.yaml`. Three different things — keep them apart.)

## Glossary — the kernel-internal vocabulary

(Industry / competitive-landscape acronyms are glossed in the strategy repo's
`README.md`, not here.)

**Architecture**

- **DOS** — Dispatch Operating System (this package).
- **ABI** — Application Binary Interface. "The syscall ABI" is the stable surface of
  kernel calls (`verify`/`liveness`/`resume`/`refuse`/`arbitrate`/`spawn`/`reap`) a
  consumer codes against — borrowed from the OS sense: the contract that doesn't
  change under you.
- **CLI / TUI** — Command-Line Interface / Terminal User Interface (the `dos` verbs;
  the `rich.live` screens behind the `[tui]` extra: `dos top`, `dos decisions`).
- **MCP** — Model Context Protocol (the `dos_mcp` server exposes the syscalls as MCP
  tools — JSON over stdio, no `import dos`; the lowest-friction adoption surface).
- **PDP / PEP** — Policy **Decision** Point / Policy **Enforcement** Point. DOS's
  kernel is a PDP (it *decides* a verdict) with no PEP (it does not *enforce* — it
  reports and proposes, never acts). The opt-in host PEP is `dos apply` (docs/126).

**Durability & recovery**

- **WAL** — Write-Ahead Log. The lease journal (`lane_journal`): an effect is logged
  *before* it is believed, so the record outlives the process that wrote it.
- **ARIES** — Algorithms for Recovery and Isolation Exploiting Semantics (the classic
  database crash-recovery algorithm: analysis → redo → undo). DOS's `resume` is "the
  ARIES third phase" — *continue* a run from its durable fossils rather than undo it.
- **CAS** — Compare-And-Swap (the value-keyed atomic steal in the archive-lock).
- **TOCTOU** — Time-Of-Check to Time-Of-Use (the archive-lock race that was closed).

**Trust & evidence**

- **TCB** — Trusted Computing Base (the minimal-TCB / reference-monitor doctrine the
  kernel descends from: keep the trusted part small and separated).
- **ORACLE → JUDGE → HUMAN** — the trust ladder: a deterministic verdict first
  (`dos.oracle`), a non-deterministic *advisory* adjudicator only on the residue
  (`dos.judges`, fail-to-abstain), a human only at the irreducible seed.
- **SKP** — Skill Pack (docs/74: the domain-free generic `SKILL.md` screenplays
  shipped as package-data under `src/dos/skills/`).

**Plan-codes** (internal shorthand for the job→dos extraction plans; see the
"Substrate roadmap" memory): **ISV** in-substrate-verify · **AOS** arbiter/oracle
seam · **DSM** durable-schema · **DLA** durable-lane · **CID** correlation-id ·
**LJ** lane-journal · **DSP** dispatch-spine (the Python→Go port, docs/100/124) ·
**DOM** the self-describing `man` surface. These are private to the planning notes,
not part of the shipped API.
