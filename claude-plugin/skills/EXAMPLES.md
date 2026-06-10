# DOS in skills — a worked cookbook

A skill is a **screenplay**, not a program: it *shells `dos` verbs* and reads the
verdict, never deciding ground truth itself. The kernel decides; the skill
narrates. That split is the whole point — the skill is the judged agent, the
kernel is the part that doesn't believe it. Every host-specific value a recipe
needs (which lanes exist, where plans live, what a gate exit code means) is
**data** you read once from `dos doctor --workspace . --json` or from `dos.toml`
— never a literal you hardcode. The recipes below are worked end-to-end: each is
*problem → the `dos` verbs → a real transcript → the rule it teaches*. Captured
transcripts use this repo's own generic workspace values; anything not captured
live is marked ILLUSTRATIVE.

---

## Recipe 0 — Discover the workspace once, never re-read (the WCR on-ramp)

**Problem.** A skill that re-reads the same state files on every step burns the
budget and reads stale bytes — the classic read-loop pathology, where one
session re-opens a handful of files dozens of times. The fix is the
**workspace-config-read (WCR) on-ramp**: cache the workspace facts *once* at
Step 0, then read paths/lanes/exit-codes from the parsed object for the rest of
the run.

**Verbs.** `dos doctor --workspace . --json`

**Transcript** (trimmed to the fields a skill actually consumes):

```json
{
  "dos_version": "0.23.4",
  "git": true,
  "workspace_facts": { "is_kernel_repo": true, "kernel_runtime_files_present": 11 },
  "lanes": {
    "concurrent": ["benchmark", "docs", "examples", "scripts", "spikes", "src", "tests"],
    "exclusive":  ["global"],
    "autopick":   ["benchmark", "docs", "examples", "scripts", "spikes", "src", "tests"],
    "trees": { "docs": ["docs/**"], "src": ["src/**"], "global": ["**/*"] }
  },
  "paths": {
    "plans_glob":   "docs/**/*-plan.md",
    "next_packets": ".dos/verdicts",
    "runs":         ".dos/runs",
    "style":        "dos"
  },
  "stamp": { "style": "grep" },
  "overlap_policy": { "active": "prefix", "available": ["prefix", "semantic-groups"], "ratio_max": 0.3333333333333333 },
  "exit_codes": {
    "verify":    { "shipped": 0, "not_shipped": 1, "contract_error": 2 },
    "gate":      { "LIVE": 0, "DRAIN": 3, "STALE-STAMP": 4, "BLOCKED": 5, "RACE": 6, "contract_error": 2, "unknown": 7 },
    "liveness":  { "ADVANCING": 0, "SPINNING": 3, "STALLED": 4, "contract_error": 2, "unknown": 5 },
    "status":    { "ADVANCING": 0, "SPINNING": 3, "STALLED": 4, "contract_error": 2, "unknown": 5 },
    "arbitrate": { "acquire": 0, "refuse": 1, "contract_error": 2 }
  }
}
```

A skill reads this once and stashes it. The lane list, the plans glob, the
`.dos/verdicts` packet dir, the exit-code tables — all of it is *config data*, so
the same screenplay runs unchanged against a host with entirely different lane
names and whose plans live elsewhere. Note `stamp.style == "grep"`
and the *generic* stamp grammar: this workspace ships the domain-free default, so
nothing host-specific leaks into the skill.

**The rule.** Read the workspace once; treat its facts as data for the rest of
the run. Re-reading state files N times is the pathology DOS exists to make
unnecessary.

---

## Recipe 1 — Ask the truth syscall instead of grepping commit subjects (verify)

**Problem.** "Is this phase done?" is the question a loop most wants to answer
from its own narration ("I committed it, so it's shipped"). Don't. Ask the truth
syscall, which answers from git ancestry + ship-stamp grammar — and then **read
the rung**, because the rungs are not equally trustworthy.

**Verbs.** `dos verify --workspace . PLAN PHASE --json`

**Transcript — SHIPPED, grep rung:**

```json
$ dos verify --workspace . docs/82_liveness-oracle-plan liveness --json
{"phase":"liveness","plan":"docs/82_liveness-oracle-plan","rung":"direct","sha":"80d4f30",
 "shipped":true,"source":"grep-subject",
 "summary":"80d4f30 liveness: exclude the BIRTH acquire from the ADVANCING event count"}
```

**Transcript — NOT_SHIPPED, none rung:**

```json
$ dos verify --workspace . docs/99_runtime-validation-and-the-actuation-boundary halt --json
{"phase":"halt","plan":"docs/99_runtime-validation-and-the-actuation-boundary",
 "shipped":false,"source":"none"}
```

**The rule.** `source` is the rung that answered, and it is one of exactly three
values: `registry`, `grep-subject`, or `none`. `grep-subject` means a commit
*subject* contained the phase token — which flips the verdict to SHIPPED even if
little was actually built. So a skill must **read the rung, not the bare
boolean**: a `grep-subject` SHIPPED is a weaker fact than a `registry` SHIPPED,
and `none` means git ancestry never stamped it. Branch on `exit_codes.verify`
(`shipped:0`, `not_shipped:1`, `contract_error:2`), never on parsing the prose.

---

## Recipe 1b — Gate "keep working until the goal is met" on the verdict, not self-report (hook stop)

**Problem.** A harness completion condition ("don't stop until X is done") is
normally evaluated by the model re-reading the session — so a fluent narration of
an X the world does not corroborate ends the work early. That is consistency, not
grounding: the part deciding done-ness *is* the part being judged. Replace the
self-judgment with a Stop hook that refuses to let the agent stop until git backs
every phase it claimed shipped. (The screenplay is `/dos-goal-gate`; this recipe
is the captured transcript.)

**Verbs.** `dos init --with-hooks` (wire it once) · `dos hook stop --json` (the
gate verdict; the runtime consumes the default non-`--json` bytes on a real stop).

**Transcript — a confidently-claimed phase git does NOT back → the Stop is blocked:**

```json
$ echo '{}' | dos hook stop --workspace . \
    --plan docs/99_runtime-validation-and-the-actuation-boundary --phase halt --json
{"ok": false,
 "reason": "DOS verify: you claimed docs/99_runtime-validation-and-the-actuation-boundary halt (via none) shipped, but git has no commit backing it. Land the commit (with the ship-stamp grammar) or correct the claim before stopping.",
 "results": [{"confident": true, "phase": "halt", "shipped": false, "source": "none", "rung": "frontmatter",
              "plan": "docs/99_runtime-validation-and-the-actuation-boundary"}]}
```

**Transcript — nothing confidently claimed (or every claim corroborated) → stop allowed:**

```json
$ printf '%s' '{"transcript_path":"/nonexistent.jsonl","cwd":"."}' | dos hook stop --workspace . --json
{"checked": 0, "ok": true, "results": []}
```

On a real stop (no `--json`), the first case prints `{"decision":"block","reason":…}`
— the exact bytes Claude Code honors to decline the stop and feed the reason back
as the next instruction — and the second prints nothing (CC's "allow stop").

**The rule.** "The goal is met" is a claim with a byte-author. The gate lets the
agent stop only when `dos verify` finds git backs every phase it claimed — a
`source:"none"` keeps it working, and the agent cannot overrule that by asserting
completion again. Decompose the goal into checkable effects and witness each;
never let "I'm done" be its own proof. (Do not confuse this with `dos hook
marker`, docs/259 — that bounds keep-alive *polling*; this refuses a *false done*.
Opposite triggers, both Stop hooks.)

---

## Recipe 2 — Take a lane before you write (arbitrate); honor the redirect

**Problem.** Two agents editing overlapping file-regions race. A lane is a
leased range-lock over a glob-region; you ask the arbiter for one *before* you
write, and you honor whatever it hands back.

**Verbs.** `dos arbitrate --workspace . --lane L [--kind cluster] [--leases <json>]`

**Transcript** — asked for `src` (on the kernel's own repo, where `src/**` IS
the running kernel), the admission conjunction refused the hint, the arbiter
auto-picked a *different* free lane:

```json
$ dos arbitrate --workspace . --lane src
{"auto_picked":true,"free_clusters":[],"lane":"benchmark","lane_kind":"cluster",
 "outcome":"acquire","pick_count":null,
 "reason":"auto-picked free cluster lane benchmark (requested src was refused:
           lane src would edit the orchestrator's own running code … (SELF_MODIFY) …).",
 "tree":["benchmark/**"]}
```

You asked for `src`; you got `benchmark`. That redirect **is the admission
kernel working** — the conjunction refused the hinted region and the arbiter
handed back a disjoint free lane, naming the REAL refusal in the parenthetical
(here SELF_MODIFY; a region contended by a live lease redirects the same way).
A free, admissible lane you name is simply granted (`auto_picked:false`). The
outcome is still `acquire` (exit 0), so the skill proceeds — *on the lane it was
given*, writing under `benchmark/**`, not the `src/**` it asked for.

**The rule.** Branch on `exit_codes.arbitrate` (`acquire:0`, `refuse:1`,
`contract_error:2`) and write only under the `tree` you were granted. **Never
`--force` past a refuse in automation** — a refuse is the kernel preventing a
collision, and forcing past it re-introduces the race the lane exists to stop.

---

## Recipe 3 — Gate the empty case by EXIT CODE, not prose (gate)

**Problem.** A skill that decides "is there work to do / is it safe to proceed?"
by string-matching a human-readable status line is brittle. `dos gate` folds a
dispositions file into a single **typed exit code** designed for exactly this.

**Verbs.** `dos gate <dispositions.json>`

**Exit-code table** (from `exit_codes.gate`):

| Code | Disposition | Meaning |
|---|---|---|
| `0` | `LIVE` | proceed — there is live work |
| `3` | `DRAIN` | drain only; do not start new work |
| `4` | `STALE-STAMP` | a ship-stamp is stale; re-verify before trusting |
| `5` | `BLOCKED` | refused upstream; do not proceed |
| `6` | `RACE` | a concurrency race was detected |
| `2` | `contract_error` | malformed input — fail loud |
| `7` | `unknown` | unrecognized disposition — fail loud |

**Branch on `$?`, never on the printed line:**

```bash
dos gate "$DISPOSITIONS_JSON"
case $? in
  0) echo "LIVE — proceed" ;;
  3) echo "DRAIN — finish in-flight, start nothing new"; exit 0 ;;
  4) echo "STALE-STAMP — re-run dos verify before trusting the stamp"; exit 0 ;;
  5) echo "BLOCKED — refused upstream, stop"; exit 0 ;;
  6) echo "RACE — back off and retry"; exit 0 ;;
  2|7) echo "contract/unknown — failing loud" >&2; exit 1 ;;
esac
```

**The rule.** The exit code is the contract; the prose is for humans. A skill
that greps the message will drift the moment the wording changes — branch on
`$?` against the `exit_codes.gate` table you read in Recipe 0.

---

## Recipe 4 — Fold a run into ONE digest instead of re-reading state files (status)

**Problem.** To answer "how is run R doing?" by hand you would re-read its
liveness evidence, its intent ledger, its lease, and its resume state — four
files, every time you ask. `dos status` folds all four into **one digest**.

**Verbs.** `dos status RUN_ID --json`

**Transcript** (ILLUSTRATIVE shape — fields are real, the values are
placeholders; do not quote these as captured):

```json
$ dos status RUN_ID --json
{
  "run_id":   "RUN_ID",
  "liveness": "ADVANCING",
  "ledger":   { "declared": 5, "verified": 4 },
  "lease":    { "lane": "docs", "held": true },
  "resume":   "RESUMABLE"
}
```

Crucially, **there is no `claimed` field** anywhere in this digest. Every cell is
adjudicated ground truth — the liveness verdict from the git/journal delta, the
*verified* (not self-reported) ledger count, the live lease from the WAL. The
folded fact is the direct antidote to the read-loop: one `dos status` call
replaces re-reading liveness + ledger + lease + resume by hand on every step.
Branch on `exit_codes.status` (`ADVANCING:0`, `SPINNING:3`, `STALLED:4`).

**The rule.** When you need the state of a run, fold it once with `dos status` —
and trust it precisely because it carries *no* self-reported field. (For the
full cross-surface walk joined by `run_id`, `dos trace RUN_ID` follows the spine
+ ledger + WAL + git together.)

---

## Recipe 5 — Catch a doomed tool-loop in-flight (tool_stream, Python API)

**Problem.** An agent re-issues the *same* tool call against unchanged inputs and
gets back identical bytes each time — a Read re-opening a file that has not
changed, dozens of times in a row (the read-loop pathology). It is making no
progress, but nothing crashes, so a naive supervisor sees a busy agent. DOS can
see the stall.

**There is NO `dos tool-stream` CLI verb.** The stall verdict is consumed via the
**Python API** (or wired into a host's tool-result hook). The only CLI surface
here is `dos tool-stream-eval`, the per-axis evaluation harness — not the
classifier. Authors must call the Python API:

```python
from dos.tool_stream import ToolStream, StreamStep, StreamPolicy, classify_stream

# A Read tool re-issued 22 times against a file whose bytes never changed:
steps = tuple(
    StreamStep("Read", "digest(<unchanged-file>)", "sha-of-file-bytes")
    for _ in range(22)
)
v = classify_stream(ToolStream(steps))
# v.state         -> "STALLED"
# v.repeat_run    -> 22
# v.repeated_step -> the recurring (tool, args, result_digest) triple
```

**Real captured output** for that 22× repeat:

```text
state:      STALLED
repeat_run: 22
reason:     "the same (tool, args, result) triple repeated 22 consecutive times
             (>= stall 5) — the loop is near-certainly doomed; the env returned
             identical bytes each time (no new information)"
```

**`DEFAULT_POLICY`** is `repeat_n=3` (fire REPEATING at the 3rd identical call)
and `stall_n=5` (fire STALLED at the 5th). So DOS would have surfaced REPEATING
at the 3rd identical read and STALLED at the 5th — re-surfacing the file bytes to
the agent and saving roughly 17 of those 22 reads.

**Why this is byte-clean** (the load-bearing argument). The `result_digest` is
**env-authored**: the file (or gym, or tool) produced the result bytes, not the
agent. The agent did *not* author the *identity* of its own repeated results. So
REPEATING is **provenance-of-repeated-output**, never an "is the agent
succeeding?" satisfaction predicate — DOS is reporting a fact about who emitted
the bytes, not grading the work. And because eventual-consistency polling (re-GET
until a value flips) is a *legitimate* repeat, the consumer attaches a
turn-preserving WARN that **re-surfaces the value**, never a cut. The verdict
informs; it does not kill the loop.

**The rule.** Distrust *repetition*, not correctness. A recurring env-authored
result is a clean signal you may re-surface; it is never license to author a
"you're failing" judgment.

---

## Recipe 6 — Keep the WAL beat alive so a supervisor can see you (lease-lane heartbeat + liveness + journal)

**Problem.** A long-running agent that holds a lane but emits no beat is
indistinguishable, to a supervisor, from a dead one — and a liveness check that
sees no journal events can only report SPINNING. The fix is to keep the
write-ahead log's heartbeat alive while you hold the lease.

**Verbs.** `dos lease-lane {acquire,heartbeat,release,live}` ·
`dos liveness --run-id R --start-sha SHA` · `dos journal --workspace . tail N`

A generic acquire → heartbeat → release cycle, then read it back:

```bash
dos lease-lane acquire   --workspace . --lane docs --run-id "$RUN"
dos lease-lane heartbeat --workspace . --lane docs --run-id "$RUN"   # repeat on an interval
dos liveness --workspace . --run-id "$RUN" --start-sha "$START_SHA"  # -> ADVANCING (exit 0)
dos lease-lane release   --workspace . --lane docs --run-id "$RUN"
```

**`dos journal --workspace . tail` of a generic sequence** (ILLUSTRATIVE shape —
op names and ordering are real, run-ids/timestamps are placeholders):

```text
ACQUIRE    lane=docs   run=<RUN>  loop_ts=<TS>   tree=["docs/**"]
HEARTBEAT  lane=docs   run=<RUN>  loop_ts=<TS>                       # the beat that makes SPINNING reachable
REFUSE     lane=docs   run=<RUN>  reason="lane docs is already held by a live loop — pick a different --scope or wait."
RELEASE    lane=docs   run=<RUN>  loop_ts=<TS>
```

The HEARTBEAT op is what makes a true SPINNING verdict *reachable from real
evidence*: a heartbeat is a *beat*, not an *event*, so a run that beats but never
advances is correctly seen as spinning rather than dead. (`dos journal replay`
gives the full fold; `dos liveness` reads the commits-since-start rung when no
journal is present.)

**The rule.** The heartbeat is the join key. It is what lets `dos status`, a
fleet watcher, or a trajectory audit **join an agent's narration to ground
truth** — without a beat, the kernel cannot tell "working" from "wedged."

---

## Recipe 7 — Do not trust a recalled memory; re-verify at read (dos memory / dos_recall)

A memory store is the DOS problem turned inward: a frozen self-report, written
once and recalled later as if it were fact. Treat a recalled memory exactly like
any other unverified agent — **re-adjudicate it at read time**, not at write
time. `dos memory` / the `dos_recall` driver re-verify a recalled record against
current ground truth and return one of `FRESH` / `STALE` / `UNVERIFIABLE`. A
skill that consumes memory should branch on that verdict and never act on a
`STALE` or `UNVERIFIABLE` recall as though it were current.

**The rule.** Recall is not a fact; it is a claim with a timestamp. Verify on
read.

---

## Recipe 8 — Wrap a headless agent so it CAN call the referee (guard)

**Problem.** A headless agent run can't consult the kernel if the kernel isn't
reachable from inside the run. `dos guard` wraps the agent process and **mounts
the `dos-mcp` server**, so the agent gains the syscalls as MCP tools — it *can*
call `dos_verify`, `dos_arbitrate`, and the rest over JSON/stdio.

**Verbs.** `dos guard [--verify-on-stop] -- claude -p ...`

```bash
dos guard --verify-on-stop -- claude -p "land the docs/NN phase, then verify it shipped"
```

The win this delivers is the **MCP mount**: inside the wrapped run, the agent can
call the referee instead of self-certifying. Frame it that way.

**An honest caveat.** `dos guard --verify-on-stop` (and `dos hook stop`) emit a
stop-hook *dialect* that the live agent runtime **may not honor** — so do not
present this as a guaranteed BLOCK that halts an over-claiming agent at stop
time. What you can rely on is the mount (the agent *can* call `dos_verify`); what
you cannot yet rely on is the runtime enforcing a verify-on-stop refusal. Keep
the recipe about the capability you actually get.

**The rule.** Make the referee *reachable* (the MCP mount) and let the agent
consult it; don't claim an enforcement gate the runtime doesn't honor.

---

## Recipe 9 — Partition a fan-out's results on a DEATH, not on null — and where the witness can actually run (verify-result)

**Problem.** A multi-agent fan-out folds each worker's *return value* as a finding.
But a worker that died abnormally — a rate-limit / quota / auth / server error the
**harness** synthesized — still returns a non-null error STRING, so it survives a
`results.filter(Boolean)` and is banked as a finding. Worse, code that computes
`failed = N - survivors.length` counts a dead worker indistinguishably from a real
negative. Over the real workflow corpus this is **~32% of subagents** (measured): a
third of the array a naive fold banks is harness-authored death.

**Verbs.** `dos verify-result --transcript PATH` (or a hook event with
`transcript_path` on stdin).

**Exit-code table** (from `exit_codes.verify-result`):

| Code | State | Meaning |
|---|---|---|
| `0` | `HEALTHY` / `UNREADABLE` | a real-model result — fold it. (UNREADABLE is the fail-safe floor: a read fault never fabricates a death that drops a real result.) |
| `3` | `DEAD` (SYNTHETIC / EMPTY) | a harness-authored death or an empty deliverable — route to a DEAD bucket, count in the denominator, do NOT fold |
| `2` | `contract_error` | no transcript given — fail loud |

The catch reads a **different byte-author** than the judged worker:
`message.model == "<synthetic>"` is the harness's own authorship stamp on the
terminal record — the worker's model did not write it, so the `role:"assistant"`
slot is the conversation position, not authorship. That is grounding, not
consistency: it is a pure byte question about bytes the worker could not forge.

### Where the witness can run — and where it CANNOT (read this first)

The fold lives inside an orchestrator's plain-JavaScript stage, and that stage is
**sandboxed: no filesystem, no child-process API, clock/RNG built-ins blocked.** Two
consequences decide the binding, and pretending otherwise ships a recipe that does
not run:

1. **An in-script shell-out to `dos verify-result` is IMPOSSIBLE.** The stage cannot
   `require('child_process')` or read a transcript file. Any recipe that shells the
   verb from inside the orchestrator JS is wishful — it would throw in the sandbox.
2. **The stage cannot get a transcript path on its own.** `agent()` returns TEXT, not
   a path; the runtime exposes no workflow id and no per-child agent id, and the
   transcripts are written out of band. The stage simply does not hold the two
   identifiers needed to name the file.

So the witness runs **one rung out of the sandbox**, in a stop-of-child hook the host
invokes with the event JSON `{transcript_path, …}` on stdin — exactly the input
`dos verify-result` already reads. The hook is a real OS process (shell-out works)
and is *handed* the transcript path (no glob, no agent id needed). The orchestrator
stage never classifies a transcript itself.

### Stage 1 — the stop-of-child hook (the witness; runs OUTSIDE the JS sandbox)

Register `dos verify-result` as a stop-of-child hook. The host pipes the event to it;
exit `3` marks the child DEAD. Append the `--json` verdict — **keyed by the child's
own id** — to a per-run sidecar a non-sandbox consumer will read (one JSON object per
child):

```bash
# stop-of-child hook command (the host pipes the event JSON on stdin; dos
# verify-result reads transcript_path from it). DOS_FANOUT_SIDECAR must be set by the
# host to ONE path per run (else concurrent runs cross-contaminate — see preconditions).
dos verify-result --json >> "$DOS_FANOUT_SIDECAR"
```

Each appended line is the `--json` verdict (`{"state","dead","class","api_status",
"reason","envelope"}`); `dead:true` is the signal and `envelope.reason_class` is a
greppable `RESULT_DEAD_<CLASS>` / `RESULT_EMPTY`. Capture the child's id alongside it
(from the event) so the partition keys on identity, not on array position.

### Stage 2 — the partition is the CONDUCTOR's, keyed by child id (NOT an in-script index join)

The sandboxed orchestrator stage **cannot** read the sidecar and **cannot** reliably
align a `deadVerdicts[i]` against its `results[i]`: stop-of-child hooks fire in
completion order, not dispatch order, so a positional join is unsound. The honest
consumer of the sidecar is the **conductor** (the non-sandbox process that launched
the workflow) or a dedicated synthesis subagent the conductor *tells* to read it.
That consumer partitions by the child id the hook recorded:

```text
read DOS_FANOUT_SIDECAR  →  for each {child_id, dead}:
    dead == true   → DEAD bucket   (count in the denominator; re-dispatch child_id's OWN unit)
    dead == false  → LIVE bucket   (eligible to fold)
```

The partition lives where the data lives (outside the sandbox), keyed by identity —
never an in-script `filter((_, i) => …)` against a file the stage cannot see.

### Stage 3 — the safe action on a DEAD child (do NOT re-prompt the synthesizer)

A DEAD verdict's safe action is to **re-dispatch the dead child's OWN unit** — re-run
that one subagent's task — never to re-prompt the synthesizer mid-plan, which is the
DEFER-shaped derail that measured net −9 pp. `dos verify-result` is a PDP, not a PEP:
it reports the death; the conductor re-dispatches the unit.

### The degraded fallback (no hook available) — be honest about what you lose

If you cannot register a stop-of-child hook, the orchestrator stage's ONLY
script-visible death signal is the `null` count after the barrier (a dropped thunk).
That **undercounts** the keystone, because a synthetic-terminal death returns a
non-null error string, not `null` — exactly the case `.filter(Boolean)` cannot see.
The fallback catches dropped-thunk deaths but MISSES the 32% the witness exists for;
say so in the prompt — coverage computed without the hook is a floor, not the truth.

**The rule.** A non-null return is not a result — a harness-synthesized death is also
non-null. Partition the fan-out on the *terminal-state verdict*, not on `Boolean`, and
because the orchestrator JS is sandboxed, do it **outside the sandbox** (a
stop-of-child hook shells `dos verify-result`; the conductor folds the sidecar keyed
by child id), so a silent death becomes a counted, refusable, re-dispatchable event
instead of a laundered finding.

---

## Recipe 10 — Hand REAL coverage to the synthesizer, not a laundered 7/7 (coverage)

**Problem.** Recipe 9 partitions a fan-out into LIVE and DEAD; this recipe makes the
*size of the gap* legible to whatever consumes the fold. A synthesizer told "7 workers
returned" when only 4 were witnessed will confidently write up a sub-quorum as an
exhaustive survey — the laundering Recipe 9's `failed = N − survivors.length` could
not even see. `dos coverage` folds the partition against the **declared** fan-out
width and emits a sentence the synthesizer reads.

**Verbs.** `dos coverage --declared N {--transcript PATH … | --transcripts-glob GLOB | --states S1,S2,…}`

**Exit-code table** (from `exit_codes.coverage`):

| Code | State | Meaning |
|---|---|---|
| `0` | `FULL` / `EMPTY` | every declared worker returned a real result (or nothing was fanned out) — fold all, no caveat |
| `3` | `UNDERFILLED` / `STARVED` / `OVERFILLED` | the fold is degraded — inject the caveat, count the gap; STARVED = nothing real to synthesize; OVERFILLED = more results than declared (a dispatch bug) |
| `2` | `contract_error` | no `--declared`, an un-coercible state token, or nothing to fold |

**Two input modes — and the provenance they carry:**

```bash
# HARNESS-GROUNDED: coverage runs verify-result itself per transcript, so the
# healthy/dead counts cannot be forged by the caller (--json stamps grounded:true).
dos coverage --declared 7 --transcripts-glob "<run-dir>/agent-*.jsonl" --json

# CALLER-ASSERTED: the workflow already ran verify-result per child and passes the
# states. PROVENANCE-DEGRADED — the tokens have no <synthetic> provenance; coverage
# cannot re-ground them (--json stamps grounded:false).
dos coverage --declared 7 --states HEALTHY,HEALTHY,HEALTHY,HEALTHY,SYNTHETIC,SYNTHETIC --json
```

**`--declared` is the laundering fix.** It is REQUIRED and is **never inferred from the
input length** — that independence is the whole point: a short survivor list (a
workflow that already dropped the dead ones) reports `UNDERFILLED 4/7`, never `FULL`.

The `--json` carries a **`prompt_line`** — the deterministic sentence a workflow feeds
verbatim into its synthesis prompt:

```text
COVERAGE CAVEAT: only 4 of 7 fan-out workers returned a real result (2 died on a
harness-authored terminal (rate-limit); 1 did not return a transcript). Treat the
findings below as a SUB-QUORUM SAMPLE (4/7), not an exhaustive survey; do not state
or imply full coverage, and flag the gap above.
```

The reason text is built from the **real `(dead, unreadable, unaccounted)`
partition**, so it never asserts a death that was not witnessed: an unreadable slot
reads "could not be read", a missing slot "did not return a transcript", and only a
witnessed death (with its class) says "died".

**`dos coverage` is an HONEST AGGREGATOR.** It folds the per-worker `verify-result`
verdicts that were *already* adjudicated; it mints **zero** new ground-truth labels
(the same posture as `dos status`-folding tools — see the fold-mints-data law). Its
value is not a new label, it is two things: a denominator independent of the survivor
list (laundering becomes structurally impossible), and the `unaccounted` count the
naive fold discarded. It is **advisory**: it reports coverage; it never re-runs a dead
worker and never judges whether a HEALTHY return is *correct* — that is the
witness-routing rung (`/dos-witness-claim`).

**The rule.** Fold the partition against the *declared* width, not the survivor count,
and put the resulting `prompt_line` INTO the synthesis prompt — so a sub-quorum
fan-out cannot be laundered as a full one.

---

## The one rule under all recipes

Every recipe is the same move: **the skill never self-certifies — it shells a
`dos` verb and reads the verdict.** The kernel answers from git ancestry, the
WAL, the journal, the env-authored result bytes — evidence whose *byte-author is
not the judged agent*. That inequality is the invariant the whole cookbook
protects: the part that decides ground truth is never the part being judged.
