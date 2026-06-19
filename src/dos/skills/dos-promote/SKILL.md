---
name: dos-promote
description: "Surface units held out of the pickable set with their typed HoldReason and derived unblock action, auto-applying only safe reclassifies. Use when work is stuck unpickable and a human needs the exact unblock move."
---

# dos-promote — surface every held unit + its unblock action

> **Make the invisible pickable.** The picker silently drops a unit it cannot
> offer; `/dos-promote` does the inverse — it runs the **pre-dispatch gate**
> (`dos pickable`) over every declared unit, and for each one that is HELD it
> surfaces the unit, its *typed* hold reason, and the derived unblock action. The
> hold reason → action routing is data, not a guess: a `DRAFT_CLASS` hold wants a
> promotion, a `SOAK_OPEN` hold wants the clock, an `OPERATOR_GATED` hold wants a
> decision. This is the operator-facing half of the shipped `pickable` primitive.

The shape: **enumerate the units → gate each → route each hold to its action →
auto-enact only the safe mechanical reclassify → surface the rest.** The gate and
the enumeration are kernel verbs (`dos pickable`, `dos enumerate`); the
reason→action routing is derivable from the `HoldReason` itself.

## Inputs

- `--scope <lane>` (optional) — limit to one lane from the active `[lanes]`.
  Omitted = every declared unit the workspace can see.

## Step 0 — Discover the layout

```bash
dos doctor --workspace . --json
```

Read `paths.plans_glob` (where the plan docs live), `lanes` (the taxonomy), and
`lifecycle.classes` (the declared class set — which class is the workspace's
"draft" / "active"). **Use these; never hardcode a plan path or a class name.**

## Step 1 — Enumerate the declared units

For each plan doc under `paths.plans_glob`, enumerate the units it declares:

```bash
dos enumerate <plan-doc> --json
```

Read `units` (the universe), `remaining` (the not-yet-shipped), and `drift` — a
`drift[kind=unparseable]` is itself a held-by-UNPARSEABLE signal (the
picker-invisibility cure: a typed refusal, never a silent drop). Collect the
remaining units across all plans as the candidate set.

## Step 2 — Gate each candidate (the pre-dispatch verdict)

For each remaining unit, run the pre-dispatch gate over the unit's host-gathered
state (its plan class, soak index, live claims):

```bash
dos pickable <UNIT> --state '<json>' --json
```

Branch on the exit code (the verdict IS the code — distinct per hold):

- `0` **OFFERABLE** → a worker could pick it up now; NOT surfaced (it is not stuck).
- `10` **DRAFT_CLASS** → the plan is draft-class; the unblock is a **promotion**.
- `11` **OPERATOR_GATED** → blocked on a decision; raise it.
- `12` **SOAK_OPEN** → a soak deadline; **wait** (never promote — time un-gates it).
- `13` **DEPENDENCY_UNMET** → ship the prerequisite first.
- `24` **UNPARSEABLE** → inspect/fix the deriver or the doc; the unit is invisible.
- `20`–`23` **IN_FLIGHT / SOFT_CLAIMED / STALE_CLAIM / COOLDOWN** → a live/transient
  hold that clears on its own; surface as info, no action.

## Step 3 — Route each hold to its unblock action (data, not a guess)

The action is derived from the typed `HoldReason`, not re-discovered per unit:

| Hold | Unblock action |
|---|---|
| `DRAFT_CLASS` | promote the plan draft→active (the workspace's `lifecycle` default→next class) |
| `OPERATOR_GATED` | raise an operator decision (`dos decisions add`) |
| `SOAK_OPEN` | wait for the soak to close — surface the deadline, do NOT promote |
| `DEPENDENCY_UNMET` | ship the named prerequisite first |
| `UNPARSEABLE` | inspect the deriver / backfill the doc's phase grammar |

A host may declare a richer reason→action map in `dos.toml`; absent one, this
default routing (documented on the `HoldReason` enum) is used.

## Step 4 — Auto-enact ONLY the safe mechanical reclassify

The single auto-applied action is the **DRAFT→active promotion** of a plan whose
draft phases are demonstrably wanted — a mechanical plan-meta `classification:`
edit + ONE commit, gated. Read the trunk + ship grammar from `dos doctor --json`'s
`stamp`; **do not hardcode a commit prefix.** Everything else — a decision, a
soak wait, a dependency, an unparseable doc — is surfaced for a human, never
auto-applied (those are real judgment calls).

## Step 5 — Surface the rest

```bash
dos decisions add  # one row per held unit that needs a human
```

## What this skill deliberately does NOT do (no silent gap)

- **No auto-decision / auto-dependency-ship.** Only the mechanical reclassify is
  auto; an OPERATOR_GATED / DEPENDENCY_UNMET hold is a human's call.
- **No soak fast-forward.** A SOAK_OPEN hold is surfaced with its deadline; the
  loop NEVER promotes past a soak (time is the only thing that un-gates it).
- **No host class taxonomy.** The draft/active classes come from
  `lifecycle.classes`; a 2-class repo and a job-shaped repo both run this skill.

## Anti-patterns

- ❌ Promoting a SOAK_OPEN unit — a soak is un-gated by the clock, not a promotion;
  promoting it re-introduces the very drain-trap the typed hold prevents.
- ❌ Auto-resolving an OPERATOR_GATED hold — that decision is the operator's.
- ❌ Hardcoding "DRAFT"/"ACTIVE" — read the class set from `dos doctor --json`'s
  `lifecycle.classes`.
