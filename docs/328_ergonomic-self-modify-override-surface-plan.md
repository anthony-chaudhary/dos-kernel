# 328 — ergonomic SELF_MODIFY-override arming, without an agent-callable arm path

> **Status:** ✅ **Phase 1 SHIPPED** (2026-06-13) — closed by the oracle, not
> this sentence: `dos verify --workspace .
> docs/328_ergonomic-self-modify-override-surface-plan "Phase 1"`. Phase 2
> (option 3, the TTY-only one-keystroke arm) is **deliberately DEFERRED** — it
> may ship only behind the headless-unreachability argument §"Option 3" sets out,
> or not at all. Closes issue #145.

> **One line:** Arming the SELF_MODIFY override (docs/296) is correct but
> high-friction for the *human*. This plan kills the friction WITHOUT adding any
> agent-callable arm path — by SURFACING a blocked edit on `dos top` and PRINTING
> (never writing) a correctly-timestamped arm file via `dos override suggest`.

## The gap this closes

docs/296 made the operator override real: a human hand-writes an expiring arm
file (`.dos/override/self-modify.toml`), the hook reads it, and a SELF_MODIFY
deny is converted to an admit while the window is open. The security model is an
**asymmetry**: anyone (incl. an agent) may `dos override disarm`; only a human may
arm, *because there is no agent-callable arm verb* — any verb an agent's shell can
call is an arming path an agent can take.

That asymmetry is right, but it makes the human's arm action clumsy: the operator
must recall the TOML schema, hand-compute a UTC `until`, and write the file
out-of-band. It was hit live — an agent finished a fully-validated kernel function
(docs/92 Phase 1, `globs_can_overlap`) and the state of the art for unblocking it
was handing the operator a raw `printf '… .toml'` line (issue #145, #144).

## The constraint that makes this non-trivial

The fix may **not** be `dos override arm`. The affordance has to live where only a
human acts, or be inert unless a human completes it out-of-band. So the two shipped
surfaces are read-only/print-only: a banner that READS the observation log, and a
verb that EMITS text. Neither writes the guarded file; an agent invoking either
produces information, never an armed window.

## What ships (Phase 1) — surface, don't arm

### (a) `dos top` SELF_MODIFY banner

`dos top` is the live operator dashboard. The hook already records a blocked kernel
edit as a `pretool` deny with `reason_class == "SELF_MODIFY"` in the per-call
observation log (docs/297). The banner turns that otherwise-invisible deny into a
visible, actionable item:

- **Not armed:** `⛔ kernel edit blocked (SELF_MODIFY) <age> ago[: <target>].
  Override disarmed.` + the one ergonomic step — `dos override suggest <target>
  --reason "…"` (pre-scoped to the blocked path when the record named one).
- **Armed:** `✓ self-mod override ARMED until T — a supervised kernel edit is
  admitted …` + how to close it (`dos override disarm`).

A fresh deny ages off the screen on its own after a 2h TTL when unarmed (a blocked
edit is a momentary event, not a permanent nag); an open window shows regardless of
age. When there is no fresh block the banner draws **nothing** — the frame is
byte-identical to the pre-#145 render.

Mechanism: a PURE fold `dispatch_top.latest_self_modify_block(records, *, now,
armed, armed_until, ttl_ms)` + a PURE renderer `render_self_modify_banner(block)`;
`snapshot()` gains a sixth fail-soft read (the observation log + the parsed arm
file), stored on `Frame.self_modify_block`. The plain-text floor and the rich TUI
both render the same field.

### (b) `dos override suggest` — a PURE arm-line emitter, never a writer

```
dos override suggest [PATH …] --reason "issue #145 — …" [--minutes 30] [--json]
```

Captures `now` at the CLI boundary, computes `until = now + minutes` (default 30,
docs/296's recommended ≤30-min window), and **prints the exact arm-file TOML to
stdout** for the operator to paste (clean for `> "<arm-file>"`); the human how-to
goes to stderr so a redirect captures only the file content. `--json` wraps it as
`{path, until, toml, …}`. It writes **nothing**.

Mechanism: a PURE `override_facts.render_arm_toml(reason, *, until, scope)` — text
in, text out, no disk, no clock — that produces a file `read_override` round-trips.
The clock is the CLI boundary's; the renderer stays pure (the kernel's
`classify(evidence, policy)` discipline).

## The three litmus tests (pinned as regression tests)

1. **No new agent-callable arm path.** A headless invocation of `suggest` leaves
   `dos override status` → disarmed; `suggest` touches no file; the banner only
   reads. (`tests/test_override_window.py::
   test_cli_suggest_leaves_status_disarmed_LITMUS_no_agent_arm_path`.)
2. **`.dos/override/` stays in the hook's deny set** — unchanged; this plan edits
   nothing in `override_facts.touches_arm_path` / `pretool_sensor`.
3. **Unarmed behavior byte-identical.** No fresh SELF_MODIFY deny → the `dos top`
   frame and its render are exactly the pre-#145 output. (`tests/test_dispatch_top
   .py::TestSelfModifyBanner::test_snapshot_no_deny_renders_byte_identical_frame`.)

## Option 3 — DEFERRED (the design gate)

Option (3) from issue #145 is a TTY-only one-keystroke arm in `dos top` — the most
ergonomic and the most delicate. It would have to be **provably unreachable
headless**: gated on `stdin.isatty()` AND an explicit interactive y/N, with no
pipe, no `--yes`, no env bypass, no way for a non-interactive agent shell to reach
it. (a)+(b) already remove the friction the issue names without opening **any** arm
path, so (3) is not built here. Shipping it later requires that explicit
unreachability argument as its own phase — the asymmetry in docs/296 §"What this
plan does NOT do" must hold byte-for-byte.

## Files touched

- `src/dos/override_facts.py` — PURE `render_arm_toml` (+ `_render_until`,
  `_toml_str`). Not a T1 runtime file.
- `src/dos/cli.py` — the `suggest` branch in `cmd_override` + the subparser.
- `src/dos/dispatch_top.py` — `SelfModifyBlock`, `latest_self_modify_block`,
  `render_self_modify_banner`, the sixth `snapshot` read, the `Frame` field.
- `src/dos/dispatch_top_tui.py` — the banner panel over the same `Frame` field.
- `tests/test_override_window.py`, `tests/test_dispatch_top.py` — the litmus +
  behavior tests.

Out of scope: `reasons.py` (T1 — the `dos man wedge SELF_MODIFY` TYPICAL-FIX third
path was designated by docs/296 to ride the first armed window, not this change);
the hook deny perimeter; the Go binary.

See also: docs/296 (the override plan), docs/92 (the live trigger), `dos override
status|disarm|suggest`, `dos top`.
