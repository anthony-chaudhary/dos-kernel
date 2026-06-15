# The `dos` CLI — every verb, grouped

This is the **full user-facing command reference**: one `dos` entrypoint over the
syscalls, with every verb and a one-line gloss. The [README's CLI
section](../README.md#cli) shows only the dozen core verbs and links here for the
rest; [QUICKSTART.md](QUICKSTART.md) is the runnable tour of the core ones.

> Not to be confused with **[docs/CLI.md](CLI.md)**, which is the *design prose*
> moved out of `cli.py` (the *why* behind each command and its `docs/NN` lineage).
> This file is the *what*: the verb listing an operator scans.

Conventions: most verbs take `--workspace .` (or honor `$DISPATCH_WORKSPACE` /
cwd) and `--json` for machine-readable output. For verdict-bearing commands
(`verify` / `liveness` / `gate`) **the exit code is the verdict** — every verb
follows the universal Unix convention (`0` = ok, non-zero = a verdict), so any
shell or CI step can branch on it without parsing a word (`dos exit-codes [VERB]`
prints the table). A pluggable `--output <name>` renderer (the `dos.renderers`
entry-point group) is covered in [HACKING.md](HACKING.md).

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
dos arbitrate --lane L --kind K --leases '[…]'   # admission: may a lane start without collision? (decision only — journals nothing; hold via lease-lane)
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

Back to the [README](../README.md#cli) · the runnable tour in
[QUICKSTART.md](QUICKSTART.md) · extend the CLI (new renderers, judges, reasons)
via [HACKING.md](HACKING.md).
