---
name: dos-unstick
description: "Analyze BLOCKED/DRAIN run history, cluster recurring causes, and propose one structural fix per wedge via `dos decisions`. Use when a fleet keeps stalling and you want a systemic unblock rather than another manual nudge."
---

# dos-unstick — the recurring-blocker remediation sweep

> **Stop the cause, not the instance.** A one-off unblock fixes today's stall;
> `/dos-unstick` asks the different question — *what keeps blocking progress
> across runs, and what one change would unblock it?* It mines the run archive,
> normalizes every blocker to a canonical **cause key**, clusters the recurring
> ones, ranks by recurrence × measured stall-cost, and proposes a **structural**
> fix per cluster. Read-only; it writes no code and surfaces findings, never acts.

The shape: **mine the trail → key each blocker → cluster the recurring ones →
rank → propose a structural fix → surface.** The recurrence fold is the kernel's
(`recurring_wedge`); the cause TAXONOMY is `[reasons]` data — a host adds a cause
by declaring a reason, never by editing this skill.

## Inputs

- `--runs <N>` (optional) — how many most-recent runs to sweep (default: a recent
  window). `--since <Nd|Nh>` overrides with a time window.
- `--min-recurrence <N>` (optional) — a cause is "recurring" at N+ distinct runs
  (default 2 — the `recurring_wedge` `DEFAULT_MIN_RECURRENCE`).

## Step 0 — Discover the layout + the cause taxonomy

```bash
dos doctor --workspace . --json
```

Read `paths.runs` (the run-archive dir to sweep) and run `dos man wedge` to read
the **closed reason vocabulary** — the canonical cause keys this workspace knows.
**Use these; never hardcode a run path or a cause string.** A cause the workspace
cares about is a declared `[reasons]` entry, surfaced by `dos man wedge`.

```bash
dos man wedge
```

## Step 1 — Mine the run-archive trail

Read the run records under `paths.runs` (read each once, sequentially — no
tailing/polling, the very anti-pattern this sweep exists to surface). For each
run that STOPPED on a BLOCKED / DRAIN / STALLED outcome, capture: the run id, the
iteration, the blocker text, and the measured stall cost (`$`/wall) if recorded.

> **No host evidence reader is wired by default.** A host that curates a
> postmortem stream or a hand-ranked next-hits file can expose it as a
> `dos.evidence_sources` driver hook; the generic sweep reads only the run-archive
> verdicts. **`log` a one-line note when a host evidence source is not consulted**
> — no silent gap (the `/dos-dispatch-loop` discipline).

## Step 2 — Key each blocker to a canonical cause

Normalize each blocker text to ONE cause key from the declared `[reasons]`
vocabulary (the host's cue table maps an Outcome-cell string to a key — the same
kernel-catalog ↔ host-cue split `dos man wedge` documents). A blocker that maps to
no declared cause is keyed `UNCATEGORIZED` (surfaced as a gap, never dropped).

## Step 3 — Cluster the recurring causes (the kernel fold)

Feed the keyed blockers to the recurrence fold. It clusters by cause key, ranks by
recurrence (dominant) × stall-cost (the tie-break), and tells you whether the
cause the CURRENT sweep hit spans `>= min-recurrence` distinct runs:

The fold is `recurring_wedge.classify_recurring_wedge` — pure, frozen-data-in,
verdict-out. A cluster spanning ≥ `min-recurrence` runs is **recurring** (worth a
structural fix); a one-off is noise the sweep cannot help.

## Step 4 — Propose ONE structural fix per recurring cluster

For each recurring cluster, propose a **structural** change — a contract edit, an
oracle rung, a preflight check — that would stop the cause RECURRING, not a
one-off unblock of today's instance. Rank the proposals by the cluster's
stall-score (recurrence × cost). The cause→fix mapping is the host's `[reasons]`
fix-sketch (the reason's documented remediation), not a literal in this skill.

## Step 5 — Surface, don't act

Route each proposal to the operator-decision queue:

```bash
dos decisions add  # the routing surface; the proposal is a finding, not an edit
```

This skill is **read-only**: it writes no code, edits no plan, takes no lease. A
recurring cause is a finding for a human (or a follow-up plan), not an
auto-applied change — the structural fix is a real engineering decision.

## What this skill deliberately does NOT do (no silent gap)

- **No auto-unblock.** It proposes the structural fix; a human applies it. A loop
  that auto-unblocks would paper over the recurring cause, the exact failure mode.
- **No curated postmortem / next-hits ingestion** unless a host wires a
  `dos.evidence_sources` hook. `log` when it is not consulting one.
- **No trajectory mining** by default (the heavier read) — the run-archive
  verdicts are the generic floor.

## Anti-patterns

- ❌ Proposing a one-off unblock for a 3-run recurring cause — that is the bug
  this skill exists to replace. Recurring ⇒ structural.
- ❌ Hardcoding a cause string or a run path — read the vocabulary from
  `dos man wedge` and the path from `dos doctor --json`.
- ❌ Tailing/polling the run dir — read each record once; polling is the waste
  this sweep surfaces in others.
