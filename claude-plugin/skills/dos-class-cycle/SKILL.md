---
name: dos-class-cycle
description: "Run one DOS plan-class lifecycle tick from the workspace `[lifecycle]` table: evaluate declared transitions, have a judge approve/defer them, write gated plan-meta edits, and log the cycle. Use when gardening plan lifecycle classes automatically."
---

# dos-class-cycle — the judge-gated plan-lifecycle tick

> **Garden the portfolio, but never alone.** A plan's class (active / done /
> parked / …) should track reality — a done plan should not sit "active," a
> long-idle one should park. `/dos-class-cycle` runs ONE tick of that gardening:
> it evaluates the declared triggers, asks a **JUDGE-rung adjudicator** (advisory,
> fail-to-abstain) to approve each candidate transition, applies only the gated
> ones, and logs the cycle. The class set, the legal transitions, and the
> failsafes are **declared data** (`[lifecycle]`) — a 2-class repo and a richly-
> classed one run the identical mechanism.

The shape: **read the declared lifecycle → evaluate triggers → build candidates
(deterministic order) → judge each → enact gated transitions → log.** The cycle
is domain-free mechanism; the taxonomy is `[lifecycle]` policy; the judge content
is a host `dos.judges` driver.

## Inputs

- `--dry-run` (optional) — evaluate + judge but enact nothing (a preview cycle).

## Step 0 — Read the declared lifecycle + the layout

```bash
dos doctor --workspace . --json
```

Read `lifecycle` — the declared `classes`, `transitions` (each `{from, to,
trigger, auto}`), `veto_class`, `max_transitions_per_cycle`, and
`per_plan_cooldown_hours` — and `paths` (the plan + run dirs). **Use these; never
hardcode a class name, a trigger, or a cap.** A repo that declared only
`active`/`done` cycles with those two; a repo with a richer taxonomy declares more.

## Step 1 — Evaluate each declared trigger → candidate transitions

For each declared `transition`, evaluate its `trigger` against the portfolio (the
plan-meta classes, the run-archive history, git). A trigger that fires on a plan
proposes that plan for that `from→to` transition. Build the candidate list in a
DETERMINISTIC order (plan id ascending) so a replay is byte-stable.

Skip a plan that is:
- in the `veto_class` (never auto-transitioned — a human moves it by hand), or
- transitioned within `per_plan_cooldown_hours` (the per-plan cooldown), or
- already past `max_transitions_per_cycle` candidates this tick (the cap).

The trigger evaluator is the host's (an opaque trigger token like
`all_phases_shipped` / `idle_30d` — the kernel never interprets it). A natural
ground-truth trigger: a plan whose `dos enumerate` residual is empty AND every
unit `dos verify`s SHIPPED is a real `all_phases_shipped` → done candidate.

## Step 2 — Judge each candidate (the JUDGE rung, advisory)

A class transition is a judgment call, so it goes to the **JUDGE rung** — a
non-deterministic adjudicator that rules on the residue the deterministic checks
left. Resolve the active judge by name and ask it to approve / defer each
candidate:

```bash
dos judge-eval --judge <name>   # the judge seam; built-in `abstain`, shipped `llm`, or a dos.judges plugin
```

The judge is hedged by the four disciplines (deterministic-first, advisory-only,
**fail-to-abstain** — a raise/bad-return becomes ABSTAIN, never APPROVE —
abstention-first). The judge *content* (the prompt) is a host `dos.judges` driver;
this skill spawns it via the seam and reads its verdict. An ABSTAIN defers the
transition to a human (the safe direction — the kernel never auto-applies on an
abstention).

## Step 3 — Enact the gated transitions (auto only where declared)

For each candidate the judge APPROVED **and** whose declared transition has
`auto = true`, enact it: rewrite the plan-meta `classification:` to the `to`
class, ONE commit per cycle. Read the trunk + ship grammar from `dos doctor
--json`'s `stamp`; **do not hardcode a commit prefix.** A transition with
`auto = false`, or one the judge deferred/abstained on, is surfaced for a human —
never enacted.

## Step 4 — Log the cycle (even a 0-transition tick)

Write a cycle record under `paths.runs`: the candidates, the judge verdicts, the
applied transitions, and the failsafe state (cap/cooldown/veto). A heartbeat row
every cycle — even one that applied nothing — so the gardener's history is
auditable.

## What this skill deliberately does NOT do (no silent gap)

- **No host class taxonomy.** Classes/transitions/failsafes are `[lifecycle]`
  data; this skill carries none. A transition naming an unknown class is a config
  error the kernel raises at load, not a silent skip.
- **No judge content.** The adjudicator prompt is a host `dos.judges` driver; the
  skill resolves + spawns it, fail-to-abstain. Forcing the prompt generic would
  re-couple the kernel.
- **No transition past a failsafe.** The per-cycle cap, the per-plan cooldown, and
  the veto class are hard gates; the cycle never exceeds them.

## Anti-patterns

- ❌ Applying a transition the judge ABSTAINED on — abstention defers to a human;
  the kernel never auto-applies on an abstention (the fail-to-abstain floor).
- ❌ Hardcoding `ACTIVE`/`PARK`/`TOMB` or a trigger like `idle_14d` — read the
  declared set from `dos doctor --json`'s `lifecycle`.
- ❌ Exceeding `max_transitions_per_cycle` in one tick — a runaway judge must not
  churn the whole portfolio; the cap is the backstop.
