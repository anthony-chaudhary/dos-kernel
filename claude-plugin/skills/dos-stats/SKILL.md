---
name: dos-stats
description: Show what the bundled DOS hook binary has been doing — fold its per-call observation log into an at-a-glance report (how many tool calls it adjudicated, how many it DENIED / WARNED / passed through, which reason classes fired, how often verify-on-stop blocked a false "done", the wait-marker budget, and per-verb latency). Use when you want to see the trust substrate's OWN activity on this project, confirm the native fast-path is actually serving calls (not silently delegating to Python), or check how fast the hooks run. Read-only: it folds a log the hooks already wrote; it takes no lease, launches nothing, and changes nothing.
---

# dos-stats — read the kernel's own activity log

> **The DOS hooks judge your agents from non-forgeable evidence. This skill turns
> that lens on the kernel itself.** Every time a hook fires (before a tool call, after
> a read, on a stop), the bundled native `dos-hook` binary writes ONE line to a
> durable log describing what it decided. This skill folds that log into a report you
> read. It is the binary's own dogfood: the kernel emits evidence about its own
> adjudication, the same way it demands evidence from the agents it judges (docs/276).

## What writes the log (so the numbers mean something)

The plugin's hooks call the bundled `dos-hook` binary on every tool call. On each
call the binary appends one record to `.dos/metrics/observations.jsonl` under this
workspace — the verb it ran, the decision it reached, the reason class, the dialect,
the exit code, how long it took. The log is **append-only and local**; nothing reads
it back until you run this. It is on by default and opt-OUT with `DOS_HOOK_METRICS=0`.

## Step 1 — Fold the log into a report

The binary lives in the plugin bundle, so call it by its bundle path (the same
launcher the hooks use picks the right binary for your machine):

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace .
```

A populated run looks like this:

```
dos-hook stats — /your/project
  observations   4
  tool calls     3 adjudicated — 2 passed untouched (66.7%), 1 intervened (33.3%)
  by verb       posttool=1  pretool=3
  by outcome    deny=1  passthrough=3
  by exit code  0=3  2=1
  pretool rung  admission=1  none=1  provenance=1
  dialect       claude-code=3
  stream state  ADVANCING=1
  delegates     0  (native declined → Python)
  stop blocks   0  (false-done refusals)
  latency (ms)  verb            n     mean    p50    p95    max
                posttool          1    2.29   2.29   2.29   2.29
                pretool           3    5.32   5.32   5.42   5.42
```

**If it says `(no observations yet …)`** that is normal and correct — the log is
empty until the hooks have fired at least once. Make any tool call in this project
(read a file, run a command) and re-run; the count climbs.

## Step 2 — Read the lines that matter

- **observations / by verb** — how many calls the kernel adjudicated, split by hook
  (`pretool` = before a tool runs, `posttool` = after a read, `stop` = on a stop,
  `marker` = the keep-alive budget).
- **tool calls** — the intervention RATE: of every tool call adjudicated (one
  `pretool` record each), what share passed untouched vs was warned or denied. This
  is the "is the substrate a light touch or a nanny?" number — numerator and
  denominator come from the same log, so the percent cannot be argued with. A
  delegated call is counted in neither (Python decided it; see **delegates**).
- **by outcome** — `passthrough` (let it run), `deny` (refused a structurally-unsafe
  call, e.g. a self-modify of the kernel's own path), `warn` (advisory re-surface),
  `block` (a stop refused on an unverified claim).
- **delegates** — how often the fast native path **declined and fell back to Python**.
  The native binary exists to erase that cold-start, so a high delegate count is the
  one number you want LOW; `0` means the fast path is owning every call.
- **stop blocks** — how many times verify-on-stop **refused to let the loop close on a
  "done" the git history did not back**. This is the headline safety number.
- **reason class** — which refusals fired (`SELF_MODIFY`, a lane collision, …), when
  the kernel denied or warned.
- **latency (ms)** — per-verb wall time. The native path's whole point is sub-30 ms
  (the docs/270 claim) — here it is, measured live and continuously on your machine.
- **⚠ panics** — only appears if the fail-safe fired (a Go crash that recovered to a
  no-op so it never broke your turn). Non-zero here is worth a bug report.

## Step 3 — Narrow the window or get the machine form (optional)

```bash
# only the last hour (or 30m, 15m, …) — the clock lives at this read-only boundary:
"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace . --since 1h

# the same aggregate as a JSON object, to pipe into jq or a dashboard:
"${CLAUDE_PLUGIN_ROOT}/bin/dos-hook" stats --workspace . --json
```

`--since` with an unreadable value (or omitted) folds everything; it never errors —
a read-only surface degrades to "show what we have."

## What this skill deliberately does NOT do

- **It changes nothing.** It is a pure fold over a log the hooks already wrote — no
  lease, no launch, no mutation. It does not even add to the log it reads (the `stats`
  verb is the one verb that is NOT itself recorded, so it cannot count its own reads).
- **It does not decide ground truth.** It reports counts; the kernel made the
  decisions those counts summarize, at the moment each hook fired.
- **It is not a server.** A hook is a one-shot process, so there is nothing to scrape;
  the durable per-call log IS the cross-process surface, and this verb folds it on
  demand. To watch it live, re-run it (or wrap it in `/loop`).
