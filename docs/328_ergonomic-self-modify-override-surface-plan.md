# 328 — ergonomic SELF_MODIFY-override arming, without an agent-callable arm path

> **Status:** ✅ **Phase 1 SHIPPED** (2026-06-13) — closed by the oracle, not
> this sentence: `dos verify --workspace .
> docs/328_ergonomic-self-modify-override-surface-plan "Phase 1"`. ✅ **Phase 2
> SHIPPED** (2026-06-14) — the TTY-only arm verb + the `dos top` approve key,
> behind the headless-unreachability gate §"Option 3 — SHIPPED" sets out
> (operator decisions: **no passphrase**, the `isatty`+confirm gate is the whole
> floor; shipped **incrementally** — Phase 2a CLI, then Phase 2c TUI). Closes
> issue #145.

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

## Option 3 — SHIPPED (Phase 2)

Option (3) from issue #145 is a TTY-only one-keystroke arm — the most ergonomic and
the most delicate. Phase 1's (a)+(b) already removed the friction the issue names
without opening any arm path; Phase 2 adds the one-step arm itself, **two surfaces**,
behind a gate a non-interactive shell cannot satisfy:

- **`dos override arm --reason "…" [--minutes N] [PATH …]`** (Phase 2a, the
  load-bearing unit). The ONE arm verb. It WRITES the arm file, but only after
  `_require_interactive_operator()` (both `sys.stdin` AND `sys.stdout` are ttys —
  exactly what an agent shell is NOT) AND an explicit `y/N` from the tty via
  `input()`. Deliberately **no `--yes`, no `--force`, no env bypass** — any of those
  would be the agent-reachable arm path docs/296 forbids. Refuses headless with exit
  2 (`refused: not-a-tty`), writing nothing.
- **`dos top` press-`a`-to-approve** (Phase 2c). The live dashboard's cadence wait is
  also a non-blocking keypress window (`_poll_keypress`): `q` quits, `a` arms the
  currently-surfaced block. Triple-gated to interactive-only: `run_top` only enters
  the live loop when `stdout.isatty()`; `_poll_keypress` yields no key where raw
  input is unavailable (headless); and `_tui_arm` re-asserts the gate + takes a y/N.
  It arms scoped to the blocked target (least privilege) via the SAME `cli._arm_write`
  the verb uses, so the two surfaces never diverge.

**Operator decisions** (2026-06-14): **no passphrase** — the `isatty`+confirm gate is
the whole floor (passwordless `sudo` on a trusted box); a passphrase rung can be added
later if ever wanted. Shipped **incrementally** (2a → 2c).

**The #148 win.** Both surfaces write the arm file as UTF-8 bytes from the command's
own process, never via a shell redirect — so the PowerShell-`>`-writes-UTF-16+BOM trap
that `suggest` must warn about cannot occur on this path at all.

### Threat model, stated honestly (the gate is a floor, not a proof)

Is `os.isatty()` sufficient? No, and the design does not claim it is. An agent with
arbitrary exec can allocate a PTY (`pexpect`/`script`) and script a `y`. docs/296
already concedes this: "an agent with arbitrary exec can write any file through a
subprocess; the guard was never proof against that." The arm gate sets the SAME bar as
the SELF_MODIFY guard itself — it stops **heedless, cooperative** self-arming (the
agent that would shell `dos override arm --yes` in a one-liner), not a deliberate
PTY-allocating adversary. The relaxation of docs/296's "no arm verb" is bounded: an arm
verb exists, but it is INERT from any non-interactive context — exactly the agent's
shell. Preserved byte-for-byte: the perimeter deny on agent TOOL writes to
`.dos/override/` (a window cannot extend itself, a DIFFERENT guard than the isatty
gate); the disarm asymmetry; unarmed behavior (no file → identical deny).

## Files touched

Phase 1:

- `src/dos/override_facts.py` — PURE `render_arm_toml` (+ `_render_until`,
  `_toml_str`). Not a T1 runtime file.
- `src/dos/cli.py` — the `suggest` branch in `cmd_override` + the subparser.
- `src/dos/dispatch_top.py` — `SelfModifyBlock`, `latest_self_modify_block`,
  `render_self_modify_banner`, the sixth `snapshot` read, the `Frame` field.
- `src/dos/dispatch_top_tui.py` — the banner panel over the same `Frame` field.

Phase 2 (all Layer-3 CLI/TUI boundary — I/O at the edge; the pure
`render_arm_toml` is reused unchanged):

- `src/dos/cli.py` — `_require_interactive_operator`, `_confirm_interactive`,
  `_arm_write` helpers; the `arm` branch in `cmd_override`; the `arm` subparser.
- `src/dos/dispatch_top_tui.py` — `_poll_keypress` (stdlib `msvcrt`/`termios`),
  `_tui_arm`, the `q`/`a` keypress dispatch in `run_top`, the arm-hint footer.
- `tests/test_override_window.py`, `tests/test_dispatch_top.py` — the Phase 2
  litmus (headless arm refuses + writes nothing) + behavior tests.

Out of scope: `reasons.py` (T1 — the `dos man wedge SELF_MODIFY` TYPICAL-FIX third
path was designated by docs/296 to ride the first armed window, not this change);
the hook deny perimeter (unchanged); the Go binary; a passphrase rung (deferred per
the operator decision above).

See also: docs/296 (the override plan), docs/92 (the live trigger), `dos override
status|disarm|suggest`, `dos top`.
