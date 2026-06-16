<a id="the-verbs-by-the-question-they-answer"></a>

# The syscall ABI & CLI reference

> ← Part of the [DOS README](https://github.com/anthony-chaudhary/dos-kernel/blob/master/README.md). The full lookup surface: every syscall by the question it answers, the `dos` CLI verbs, the three live read-only screens, and the verdict journal.

## The syscall ABI

Every syscall answers a question you'd otherwise have to *take the agent's word
for*. "Reach for this when…" is the plain-English trigger; the rest is the
contract — and the module names are auditable.

| Syscall | Reach for this when… | What it is | Module |
|---|---|---|---|
| `verify()` | an agent says a unit of work is **done** and you don't want to take its word | the **truth syscall** — "did (plan, phase) actually ship?" registry-first, ancestry-checked, from git history if there's no plan at all | `dos.oracle`, `dos.phase_shipped` |
| `liveness()` | a long run says it's **"making progress"** and you want to know if it actually is | the **temporal verdict** — "is the run ADVANCING, or just SPINNING / STALLED?" from the git/journal delta and the clock | `dos.liveness` |
| `verify-result()` | a **subagent hands a result back** to an orchestrator that folds it as a finding — but the result string may be a harness-synthesized error the worker never authored | the **fold-site result-state witness** ([docs/197](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/197_how-dos-is-directly-useful-to-ultracode.md)) — classifies a subagent transcript's terminal record, gating on `message.model == "<synthetic>"` (the unforgeable harness-authorship marker), never the agent's self-report; **exit 3 = DEAD** (a harness 429 / quota / auth / server error), `0` = HEALTHY / UNREADABLE | `dos.result_state` |
| `resume()` | a run **died or paused mid-flight** and you need to continue without re-doing work or double-applying it | the **third ARIES phase** — "how far did the *fossils* say it got, and what's the residual?" over a run-id-keyed intent ledger; re-enters from a git-VERIFIED SHA, never the dead run's self-report (RESUMABLE / COMPLETE / DIVERGED / UNRESUMABLE) | `dos.resume`, `dos.intent_ledger` |
| `complete()` | you need to know if the **whole declared job** is verifiably done, not just one phase | the **completion verdict** — `residual = declared − verified`, asked forward; read-only, never self-certifies | `dos.completion` |
| `rewind()` | a run thrashed and you want to **excise the dead-end turns** without the kernel authoring a correction | the **conversation-rewind verdict** — replays the ledger for a minted checkpoint and PROPOSES the excision (never truncates; the host owns the transcript) | `dos.rewind` |
| `productivity()` | a long run is burning turns and you want to know if it's **still doing work, or fading** | the **loop-economics verdict** ([docs/218](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/218_the-productivity-verdict-diminishing-returns-as-a-syscall.md)) — `classify(work-deltas) -> PRODUCTIVE / DIMINISHING / STALLED` over a trend of per-step work; pure, no I/O | `dos.productivity` |
| `efficiency()` | you want to know if the **tokens a run spent actually bought work** (a run can be productive yet burn 10× its work's worth) | the **token-effectiveness verdict** ([docs/263](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/263_token-effectiveness-verdict-plan.md)) — `work / tokens -> EFFICIENT / COSTLY / WASTEFUL`; both counts are env-authored, so a run can't narrate its way to EFFICIENT | `dos.efficiency` |
| `improve()` | a **self-improving loop** proposes a change to its own code and you must decide **keep or revert** — without trusting the loop's own claim that it helped | the **keep-gate** ([docs/280](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md)) — `KEEP / REVERT / ESCALATE` from witnesses the candidate's author didn't write: the suite green on the candidate-only tree, the truth syscall clean, and a strictly-measured metric gain; a regression always REVERTs, a run of non-keeps ESCALATEs to a human | `dos.improve` |
| `reward()` | a fine-tune is about to **train on an agent's trajectory** and the "it worked" label came from the agent itself | the **reward-set admission verdict** ([docs/230](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md)) — `ACCEPT / REJECT_POISON / ABSTAIN` off a witness the agent authored zero bytes of, so no answer text can flip a reject to an accept (the non-distillable label) | `dos.reward` |
| `breaker()` | a **failure class keeps tripping** and you want to stop retrying and escalate | the **circuit-breaker primitive** ([docs/223](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/223_the-circuit-breaker-primitive-failure-counting-as-mechanism.md)) — a pure two-counter state machine, `CLOSED / OPEN`, tripping on consecutive *or* total failures; an OPEN verdict names the escalation rung (none / judge / human) | `dos.breaker` |
| `hook_exit()` / `exec_capability()` | you wire a **plain shell hook** into a runtime, or need to know if a command **grants arbitrary code execution** | two classifier leaves the cheapest integrations consult — `hook_exit` maps an exit code to an intervention ([docs/226](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/226_the-hook-exit-classifier-a-shell-scripts-exit-code-as-a-verdict.md): 0 pass / 2 BLOCK / other WARN), `exec_capability` classifies the *invoked program token* — never a substring — as `GRANTS_ARBITRARY_EXEC / BOUNDED` ([docs/224](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/224_the-exec-capability-classifier-a-shape-not-a-word.md)) | `dos.hook_exit`, `dos.exec_capability` |
| `refuse(reason)` | you need to say **why** a pick was blocked in a way a machine can act on | **structured refusal** — a closed, declared reason vocabulary (`dos.reasons`, extensible per-workspace), every reason emittable, verifiable, and refusable | `dos.wedge_reason`, `dos.picker_oracle` |
| `lease()` / `arbitrate()` | two agents might **touch the same files** and you need to admit one without a collision | the **pure admission kernel** — `arbitrate(request, live_leases, config) -> decision`, state-in / decision-out, no I/O | `dos.arbiter` |
| `spawn()` / `reap()` | you need every run to carry a **traceable identity** and its effects to be replayable | the **correlation spine** (sortable, lineage-carrying run-ids) + the lease **write-ahead log** | `dos.run_id`, `dos.lane_journal` |
| `enumerate()` / `pickable()` / `cooldown()` / `reconcile()` | an unattended loop must know **is there anything pickable, why-not, have I tried it, and did the claim hold?** — without re-storming a known drain or believing a "done" the git can't confirm | the **picker substrate** ([docs/207](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/207_dispatch-workflow-extraction-and-the-pickable-substrate-completion.md)) — `enumerate` is the phase-list producer (the `declared` set, never a silent empty); `pickable` the pre-dispatch gate (OFFERABLE / HELD(reason)); `cooldown` the anti-churn fold over pick-attempts (CLEAR / RECENTLY_ATTEMPTED); `reconcile` the quiet-completion join (VERIFIED / QUIET_INCOMPLETE / HONEST_OPEN, fail-closed on the claim) | `dos.enumerate`, `dos.pickable`, `dos.cooldown`, `dos.reconcile` |

> Three terms the table assumes: a **plan** (e.g. `AUTH`) groups **phases** —
> a phase is a named unit of work (e.g. `AUTH1`); a **lane** is a leased region of
> the file tree an agent works in. All are defined in the
> [quickstart](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md).

> **The newest catch — a result that *died*.** When a subagent hands a result
> back to an orchestrator that folds it as a finding (an ultracode `Workflow`, an
> Agent-SDK fan-out), the result string itself may be a **harness-synthesized
> error** the worker never authored — and ~32% of real subagents return exactly
> that (a 429 / quota / auth string) where the fold expects a finding ([docs/197](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/197_how-dos-is-directly-useful-to-ultracode.md)).
> `verify-result` reads the transcript's terminal record and refuses to believe a
> harness-authored death:
>
> ```bash
> dos verify-result --transcript dead.jsonl
> #   DEAD SYNTHETIC class=OTHER — harness-authored terminal
> #   (model=<synthetic> + stop_reason=stop_sequence) — not a finding; route to DEAD, do not fold
> echo $?   # → 3   (count it in the denominator; never bank it as a result)
>
> dos verify-result --transcript real.jsonl
> #   HEALTHY — terminal assistant record is real-model authored with content
> echo $?   # → 0
> ```
>
> It gates on `message.model == "<synthetic>"` — the marker the agent's own model
> *cannot forge* (the runtime harness authored those bytes, not the worker) — which
> is broader than rate-limits alone: quota, auth, and server deaths are caught too.

Around these sit ~30 supporting kernel modules — the file-tree disjointness
algebra, the timeline reader, the gate/loop classifiers, the typed-verdict
contract, the JUDGE-rung seam. The full map is in **[CLAUDE.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/CLAUDE.md)**.

<a id="cli"></a>

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

<a id="three-live-projections-read-only-tuis"></a>

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

---

*Next: [operating a fleet day-to-day](./operating-a-fleet.md) · [extend it without forking](./extending.md) · back to [why a referee](./why-a-referee.md).*
