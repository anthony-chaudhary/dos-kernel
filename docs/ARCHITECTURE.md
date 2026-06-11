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
path to KEEP is to actually move the metric. A true leaf: it imports neither
`efficiency` nor `breaker` — their verdicts arrive as DATA in the evidence, and
the worktree-isolated propose→gather→classify→actuate engine is a driver
(`dos.drivers.self_improve`).

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
