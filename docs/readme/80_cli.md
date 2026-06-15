## CLI

One `dos` entrypoint over the syscalls. Here are the dozen verbs you reach for
first (see [QUICKSTART.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md) for
a runnable tour) — the **full reference, every verb grouped, is
[docs/CLI-REFERENCE.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI-REFERENCE.md)**:

```bash
# --- the core verdicts ---
dos verify PLAN PHASE                  # truth: did (plan,phase) ship? (works with no plan)
dos commit-audit [REF] [--sweep]       # truth: does a commit's SUBJECT match its own diff? (--sweep = drift rate over a range)
dos liveness --run-id R --start-sha S  # temporal: ADVANCING / SPINNING / STALLED?
dos status --run-id R                  # one fail-closed digest of a run (liveness + verified progress + lease)
dos resume --run-id R                  # replay a run's intent ledger, re-verify against git, PROPOSE the continuation

# --- admission (let a lane in, or refuse the collision) ---
dos arbitrate --lane L --kind K --leases '[…]'   # may a lane start without collision? (decision only)
dos scope-gate --lane L [--staged]     # binding pre-effect scope gate: may this PROPOSED write land? (ALLOW/REFUSE)

# --- workspace & live projections ---
dos init [DIR]                         # scaffold a dos.toml workspace config
dos doctor [--json] [--check]          # report the active workspace + taxonomy + predicates
dos top [--once] [--json]              # live fleet watchdog: lanes, leases, verdicts, commits
dos decisions [N]                      # the operator-decision queue (what's waiting on you)
dos plan [--once] [--json]             # every phase: the plan's claim vs the oracle's verdict
```

Most verbs take `--workspace .` (or honor `$DISPATCH_WORKSPACE` / cwd) and
`--json` for machine-readable output. For verdict-bearing commands (`verify` /
`liveness` / `gate`) **the exit code is the verdict** (`dos exit-codes` prints
the table). The other ~55 verbs — the picker substrate, loop-economics
verdicts, observability exporters, portable-proof attestation, the eval
harnesses — and the pluggable `--output <name>` renderer
([HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md)) are all in
**[docs/CLI-REFERENCE.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI-REFERENCE.md)**.

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
**[Debug a stuck fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md)**.

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/docs/assets/decisions-tui.svg" alt="The dos decisions queue: four pending arbiter refusals on the left, each routed to who can resolve it — a deterministic ORACLE (may auto-clear), an LLM JUDGE (could rule), or a HUMAN (your call) — and on the right the selected SELF_MODIFY decision expanded with its meaning, typical fix, and the exact commands to run." width="100%">
</p>

### Observability: the verdict journal, drained to your dashboards

Those three screens read a fleet's *running* state. Underneath, every verdict
the kernel computes — each `verify` / `liveness` / `efficiency` / `breaker` /
`reward` / hook decision — also lands in a **verdict journal**: a
`run_id`-correlated write-ahead log of the kernel's *own* adjudications
([docs/262](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/262_the-verdict-journal-observability-as-a-first-class-surface.md)).
Two verbs make it useful. `dos observe` is the read-only projection — fold the
journal by run, syscall, or verdict, or replay one run's verdict history. `dos
export` is the delivery seam: it drains the journal outward to an observability
backend through the `dos.exporters` entry-point group, with three shipped
transports — `file` (JSONL), `statsd` (DogStatsD counters), and `otlp`
(OpenTelemetry log records → Datadog / Honeycomb / Grafana), the `null` default
reporting only ([docs/266](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/266_the-verdict-exporter-shipping-the-journal-to-where-dashboards-live.md)).
So "how often did the fleet over-claim this week, and on which lanes?" becomes a
dashboard panel, not a log grep — and adding a transport is a driver, never a
kernel edit (the same kernel/driver split as judges and notifiers).
