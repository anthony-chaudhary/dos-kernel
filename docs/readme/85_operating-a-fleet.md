## Operating a fleet

The listing above is the reference; this is the day-2 shape of running on it —
what an operator actually does each morning, and where to look first when
something wedges.

**Morning triage is three reads, in order.** `dos top` answers *what's running
right now*: each lane, the lease holding it, and a status chip that **is** the
liveness verdict — green `ADVANCING`, yellow `SPINNING`, red `STALLED` — so
"which one is wedged" is one glance, not a log dig. `dos decisions` answers
*what's waiting on me*: the refusals and open gates that need a decision, each
tagged by who can resolve it, so you spend attention only on the rows marked
HUMAN. And `dos plan` answers *is anyone over-claiming*: every declared phase,
the plan's self-reported status beside the oracle's verdict — run from outside
the agent loop, so an over-claimer is caught by ground truth, not by
re-reading its own narration. All three are read-only projections (no lease
taken, nothing launched, nothing mutated), so leave them open or script them —
each has `--once` and `--json`.

**Then route the signal to where you already look.** You don't have to keep a
terminal open: `dos notify` pushes what-needs-a-human (or what's-running) to
Slack or a webhook on whatever cadence you drive it with, and `dos export`
drains the verdict journal — every adjudication the kernel computed — to your
observability stack (file / statsd / OTLP → Datadog, Honeycomb, Grafana). "How
often did the fleet over-claim this week, and on which lanes?" becomes a
dashboard panel, not a log grep.

**When something wedges, start with the verdicts, not the logs.** The
symptom → one-command table — a run that swears it's progressing but isn't, a
lane nobody can take, a "done" that won't verify — is
**[Debug a stuck fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md)**,
which drives all three screens end-to-end on a worked example. Link it from
your on-call doc; it is the playbook.

Running smoothly and want the referee to fit your org — your own lanes, your
own block reasons, a model-backed judge? Step up a level:
[Hacking it](#hacking-it).
