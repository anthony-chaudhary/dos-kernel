## CLI

One `dos` entrypoint over the syscalls (see [QUICKSTART.md](docs/QUICKSTART.md) for
a runnable tour of the core ones):

```bash
# --- the syscalls ---
dos verify PLAN PHASE                  # truth: did (plan,phase) ship? (works with no plan)
dos commit-audit [REF] [--sweep]       # truth: does a commit's SUBJECT match its own diff? (--sweep = drift rate over a range)
dos verify-result --transcript T       # fold-site witness: did a subagent's terminal record DIE (harness 429/quota)? (exit 3 = DEAD)
dos coverage --declared N              # fan-out coverage: how many of N declared workers REALLY returned a result vs died?
dos liveness --run-id R --start-sha S  # temporal: ADVANCING / SPINNING / STALLED?
dos resume --run-id R                  # the resume verdict: replay a run's intent ledger, re-verify against git, PROPOSE the continuation
dos complete --run-id R [--diverged]   # completion verdict: is the WHOLE declared job done? (residual = declared − verified)
dos rewind --run-id R [--fire SIGNAL]  # conversation-rewind verdict: PROPOSE excising dead-end turns (never truncates)
dos status --run-id R                  # the folded fact: one fail-closed digest of a run (liveness + verified progress + lease)
dos arg-provenance --tool T --args J [--new-key K]  # did the model MINT this id/FK, or RESOLVE it from env bytes? (exit 0 believe / 3 UNSUPPORTED)
dos arbitrate --lane L --kind K --leases '[…]'   # admission: may a lane start without collision?
dos scope-gate --lane L [--staged]     # binding pre-effect scope gate: may this PROPOSED write land in its lane? (ALLOW/REFUSE)
dos lease {acquire,release,status} OWNER         # the cross-process archive lock
dos lease-lane {acquire,release,heartbeat,live}  # durable lane lease over the pure arbiter (write-back to the WAL)
dos run-id mint PROCESS                # mint a correlation run-id
dos id-alloc {allocate,peek} SCOPE     # atomically allocate a never-reused, monotonic id for a scope
dos journal {tail,replay,seq,compact}  # the lane write-ahead log
dos halt --handle H                    # the reap verb: emit the stop-plan for a live run/lease
dos pickable / enumerate / cooldown / reconcile  # picker substrate: anything pickable? why-not? tried recently? did the claim hold?

# --- workspace & inspection ---
dos init [DIR]                         # scaffold a dos.toml workspace config
dos doctor [--json] [--check]          # report the active workspace + taxonomy + predicates
dos lint [--strict] [--json]           # dead policy in this workspace's own dos.toml? (unreachable lanes, dangling refs)
dos man {wedge,lane} [ID]              # the self-describing manual over the registries
dos exit-codes [VERB]                  # print the verdict-IS-the-exit-code table (all verbs or one)
dos gate PACKET                        # typed empty-packet verdict (LIVE/DRAIN/STALE-STAMP/…)
dos judge wedge RUN_TS                 # adjudicate a no-pick verdict (deterministic)
dos judge-eval --judge N --cases C     # score a JUDGE-rung adjudicator against labelled claims
dos overlap-eval --policy P --cases C  # score an overlap scorer by false-admit rate (the disjointness backtest)
dos intervention-eval --cases C        # score an intervention policy by NET task delta (not verdict accuracy)
dos tool-stream-eval --cases C         # score a stall-reader policy by NET recovery (not detection accuracy)
dos precursor-gate-eval --cases C      # score a precursor grammar by recall vs false-refute waste
dos memory {recall,verify}             # re-verify recalled agent-memory at read time (RECALL_FRESH/STALE/UNVERIFIABLE)
dos health --lane L                    # pre-dispatch lane-health gate (overlap + recurring-blocker → route)
dos scout                              # pre-dispatch chooser: pick the next activity before leasing a lane
dos trace RUN_ID                       # walk one run across spine + intent ledger + WAL + git, joined by run_id

# --- agent-host binding (Claude Code / MCP) ---
dos guard [--verify-on-stop] -- CMD…   # wrap a headless agent launch: inject the DOS MCP server (+ optional verify-on-stop Stop hook)
dos hook {pretool,posttool,stop}       # the live agent-host hook surface (PreToolUse deny / PostToolUse sensor / Stop verify)

# --- live projections (read-only TUIs) ---
dos top [--once] [--json]              # live fleet watchdog: lanes, leases, verdicts, commits
dos decisions [N]                      # the operator-decision queue (list + drill-in TUI)
dos plan [--once] [--json]             # work-terrain board: every phase, the plan's claim vs the oracle's verdict
dos watch --track R [--budget-ms M]    # the watchdog driver: poll liveness for tracked runs + propose halts on spin/hang
dos loop --target N [--watch] [--json] # supervisor (init/PID-1): keep N dispatch-loops alive — emits a spawn/reap/flag plan

# --- loop-economics & reliability verdicts (pure; exit code is the verdict) ---
dos productivity --deltas 5,3,1,0      # is the run still doing work? PRODUCTIVE / DIMINISHING / STALLED
dos efficiency --work W --tokens N     # did the tokens buy work? EFFICIENT / COSTLY / WASTEFUL
dos breaker --consecutive N --max-consecutive M  # has this failure class tripped? CLOSED / OPEN (+ escalation rung)
dos hook-exit --code N                 # map a shell hook's exit code → PASS / BLOCK / WARN
dos exec-capability --command "…"      # does this command grant arbitrary exec? BOUNDED / GRANTS_ARBITRARY_EXEC
dos improve --suite-passed --truth-clean --work W --baseline-work B  # self-improving loop: KEEP / REVERT / ESCALATE
dos reward --claim --witness {confirm,refute,none}   # may a fine-tune TRAIN on this trajectory? ACCEPT / REJECT_POISON

# --- observability: the verdict journal → your dashboards ---
dos observe [--run R] [--json]         # project the verdict journal: every kernel adjudication, folded by run/syscall/verdict
dos helped [--since TS] [--json]       # the operator rollup: how many things DOS productively caught for you
dos export [--to file|statsd|otlp] [--since SEQ]  # drain the journal outward (Datadog / Honeycomb / Grafana); null = report only
dos notify {decisions,top} [--notifier slack|webhook --channel NAME]  # push what-needs-a-human / what's-running to where the operator is; null = render only

# --- portable proof (third-party verifiable, no loop access) ---
dos attest --claim KEY {--accept-cmd CMD | --before P --after P}  # mint an HMAC-signed receipt over an effect-witness verdict
dos verify-receipt --receipt R         # the skeptic's side: check the signature with the shared key alone (fails LOUD on tamper)

# --- cross-project (machine-local index) ---
dos projects                           # the projects DOS has served
dos learn AXIS                         # aggregates over resolved decisions
dos reindex                            # rebuild the central store from the .dos/ dirs
```

Most verbs take `--workspace .` (or honor `$DISPATCH_WORKSPACE` / cwd) and
`--json` for machine-readable output. For verdict-bearing commands (`verify` /
`liveness` / `gate`) **the exit code is the verdict.** A pluggable `--output
<name>` renderer (the `dos.renderers` entry-point group) is covered in
[HACKING.md](docs/HACKING.md).

### Three live projections (read-only TUIs)

A fleet leaves its state scattered across git history, a write-ahead log, and a
pile of verdict envelopes. DOS folds that into three read-only screens, each
answering a different operator question. They are *projections*, not stores:
every one reads kernel state, mutates nothing, takes no lease, launches nothing
— delete any of them and you lose the screen, not the data. Pick by the
question you're asking:

| Screen | Answers | Reads |
|---|---|---|
| `dos top` | *What's **running** right now?* — the lanes, the leases holding them, recent verdicts, live git activity. The screen you leave open in a side terminal during a run. | leases (WAL) + per-lane `liveness` + verdict envelopes + git |
| `dos decisions` | *What's waiting on **me** right now?* — the no-picks (refusals, wedges, open gates) that need a decision, each tagged by *who can resolve it*. | the four refusal sources, joined |
| `dos plan` | *Does the plan's **claim** match the **ground truth**?* — every declared phase, the plan's self-reported status beside the oracle's verdict, so an over-claim is its own cell. | the plan source × `verify()` per phase |

In `dos top` a held lane's status chip **is** its `liveness` verdict — green
`ADVANCING` / yellow `SPINNING` / red `STALLED` — so "which one is wedged" is one
glance, not a log dig. `dos decisions` tags each row by resolver — a deterministic
**ORACLE** (may auto-clear), an **LLM JUDGE** (could rule before you spend
attention), or a **HUMAN** (a genuine operator call) — and on a keypress prints
the exact shell command and exits; *you* run it, the screen never mutates
substrate. `dos plan` is a `verify()` fan-out, **not** a plan reader: a human runs
it from *outside* the agent loop, so an over-claiming loop is caught by ground
truth, not by re-reading its own narration.

All three have a plain-text floor that needs no dependencies — the live `rich`
redraw is the optional `[tui]` extra, but `--once` (one frame) and `--json`
work on a bare core install (no extras). Here is `dos top --once` on a fresh
checkout (no leases yet, so every lane is `FREE` and the git strip carries the
content):

```text
┌─ dos top · /path/to/repo · 2026-06-07T17:14:32+00:00 ──────────────────────
LANES
   benchmark     ⚪ FREE
   docs          ⚪ FREE
   …                                  (one concurrent lane per source dir)
  *global        ⚪ FREE              (* = the exclusive whole-repo lane)
  8 lanes · 0 advancing · 0 spinning · 0 stalled · 8 free
RECENT VERDICTS        [trust = ship-oracle cross-check]
  (no verdicts yet)
RECENT COMMITS        [ground truth — git history]
  0857bd4    docs/206 Appendix A: the whole program in plain words
  …                                  (last 10 commits — the content even a
                                      zero-lease repo always has)
──────────────────────────────────────────────────────────────────────────────
read-only · q quit · this screen mutates nothing
```

The stuck-fleet walkthrough that drives all three end-to-end is
**[Debug a stuck fleet](examples/playbooks/06_debug-a-stuck-fleet.md)**.

<p align="center">
  <img src="docs/assets/decisions-tui.svg" alt="The dos decisions queue: four pending arbiter refusals on the left, each routed to who can resolve it — a deterministic ORACLE (may auto-clear), an LLM JUDGE (could rule), or a HUMAN (your call) — and on the right the selected SELF_MODIFY decision expanded with its meaning, typical fix, and the exact commands to run." width="100%">
</p>

### Observability: the verdict journal, drained to your dashboards

Those three screens read a fleet's *running* state. Underneath, every verdict
the kernel computes — each `verify` / `liveness` / `efficiency` / `breaker` /
`reward` / hook decision — also lands in a **verdict journal**: a
`run_id`-correlated write-ahead log of the kernel's *own* adjudications
([docs/262](docs/262_the-verdict-journal-observability-as-a-first-class-surface.md)).
Two verbs make it useful. `dos observe` is the read-only projection — fold the
journal by run, syscall, or verdict, or replay one run's verdict history. `dos
export` is the delivery seam: it drains the journal outward to an observability
backend through the `dos.exporters` entry-point group, with three shipped
transports — `file` (JSONL), `statsd` (DogStatsD counters), and `otlp`
(OpenTelemetry log records → Datadog / Honeycomb / Grafana), the `null` default
reporting only ([docs/266](docs/266_the-verdict-exporter-shipping-the-journal-to-where-dashboards-live.md)).
So "how often did the fleet over-claim this week, and on which lanes?" becomes a
dashboard panel, not a log grep — and adding a transport is a driver, never a
kernel edit (the same kernel/driver split as judges and notifiers).
