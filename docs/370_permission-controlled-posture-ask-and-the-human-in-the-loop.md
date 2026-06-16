# 370 — The permission-controlled posture: ASK and the human in the loop

> **Status:** Phase 1 + Phase C + Phase E SHIPPED. Phase 1 — the `ASK` action +
> the `Posture` seam + Claude-Code-family native rendering. Phase C — posture-aware
> observation: the PRE hook records the SURFACE the posture produced and `dos doctor`
> splits the kernel's refusals into escalated-to-human vs hard-blocked vs observed.
> Phase E — `dos init --posture <observe|block|gate>` wires the posture at install.
> The remaining roadmap phases (§6) are numbered, each a litmus-checkable unit.
> Implementation: `hook_dialect.HookAction.ASK`, `hook_dialect.Posture` /
> `parse_posture` / `under_posture`, `cli._hook_posture` / `_effective_posture`,
> `hook_observation.posture_split` (+ the `posture`/`surface` record fields),
> `cli._upsert_enforcement_posture`; `tests/test_hook_ask_posture.py`,
> `tests/test_posture_observation.py`, `tests/test_init_posture.py`.

## The wound, stated once

DOS grew up in one deployment: the **headless, bypass-permissions fleet**. An
autonomous loop runs with the host's permission prompts turned off
(`--dangerously-skip-permissions`, YOLO mode, full-auto). There is no human at
the keyboard. In that world DOS is the *only* gate, so the only useful thing a
hook can do is **block** a bad call or **stay silent**. The whole hook protocol
reflects that: the dialect-neutral verdict had exactly three actions —

```
DENY   — withhold the call
WARN   — add context, do not block
PASS   — emit nothing
```

But most of the industry does **not** run headless bypass. The default Claude
Code session asks the human before each edit. Cursor, Codex, and Gemini all
ship an approval mode. Enterprises run least-privilege allowlists with a human
approver on anything outside them. For those users — the ones who want *more*
control, not less — DOS had a relevance gap:

- **The protocol could not ROUTE a call to the human.** Claude Code's own
  permission verb set is `z.enum(['allow','deny','ask'])`. DOS only ever emitted
  `deny`. It could block, but it could not say "I'm not certain — *you* decide,"
  which is the single most common thing a human-in-the-loop operator wants from
  a referee. DOS could *replace* the human's judgment (block) or *abstain*
  (stay silent); it could not *inform* it.
- **The default posture was written for the loop.** docs/364 made BLOCK the
  default for loop sessions — correct for headless, where a block is the only
  safety net. But the control-oriented operator does not want a hard block from
  a referee; they want the referee's finding handed to *them* at the prompt they
  already answer.
- **The UX was operator-PULL.** `dos doctor`, `dos decisions`, the enforce
  journal — all are surfaces the operator runs a command to read. Nothing PUSHED
  an adjudicated fact to the human *at the moment they were deciding*.

The fix is not a new subsystem. It is one new action and one new seam, both
small, both pure, that let DOS **participate in** a host's permission flow
instead of replacing it.

## 1. ASK — the missing action

`HookAction.ASK`: escalate a refusal to the human's permission prompt. DOS does
not decide for the human; it hands them a reason **the agent could not have
authored** (a git fact, a lane collision, a minted-id finding) and lets them
answer with the host's existing allow/deny UI.

ASK is the byte-author axiom (docs/138, ZERO_TRUST.md) applied to the human
seam: the agent narrates "this is fine"; DOS does not believe it and does not
overrule the human either — it surfaces the adjudicated fact and routes the
decision to the one party with the authority to make it. The ORACLE → JUDGE →
HUMAN escalation ladder (the breaker's stalemate rungs, ZERO_TRUST.md pillar 5)
finally has a *mechanism* at the hook surface: ASK **is** the HUMAN rung,
delivered inline.

Where ASK renders natively (verified against the host's gate):

| Host | ASK grammar | Verified |
|---|---|---|
| Claude Code | `permissionDecision: "ask"` | CC source `permissionBehaviorSchema = z.enum(['allow','deny','ask'])` |
| Codex CLI | CC-identical envelope → `ask` | delegates to the CC renderer (docs/217) |
| Claude Cowork | runs the CC harness → `ask` | delegates to the CC renderer (docs/298) |

Where it does **not** yet render natively (Gemini / Cursor / Antigravity /
Hermes), ASK degrades to a **turn-preserving WARN** — a non-blocking surface
that names the finding but does not pause. This is the honest-coverage
discipline the seam already holds for every host gap (Hermes' WARN→`{}`,
docs/278; Trae's deliberate absence, docs/294): DOS never fabricates a host
grammar it has not verified, and an ASK never silently hard-blocks (that would
contradict the operator's choice to keep the human in the loop). Native
per-host ASK is §6 Phase A.

The fail-open dual is pinned: an ASK must carry **no** hard-deny signal on any
host (`tests/test_hook_ask_posture.py::test_non_cc_hosts_degrade_ask_to_a_non_blocking_surface`),
the ASK analogue of the docs/268 deny-must-block floor.

## 2. The posture seam — observe / block / gate

The operator declares ONE choice — how a refusal is **surfaced** — and the same
adjudicated verdict renders three ways:

| Posture | A refusal becomes | For whom |
|---|---|---|
| `observe` | a turn-preserving WARN (records, never blocks, never prompts) | the cautious first-adopter; the pre-docs/364 behavior, now an explicit choice |
| `block` | a hard DENY | the **headless / bypass-permissions** fleet — DOS is the gate. **The default.** Byte-for-byte today's behavior. |
| `gate` | an **ASK** at the human's permission prompt | the **control-oriented / human-in-the-loop** operator |

The posture is **monotone-softening**: `under_posture` only ever re-shapes a
DENY into a *softer* surface (ASK or WARN). It never hardens a PASS/WARN into a
block, and never turns an admit into a refusal. This is the refuse-equal-or-
softer dual of the overlap policy's refuse-MORE-only floor (CLAUDE.md litmus):
a posture is a rendering choice the operator owns, structurally incapable of
manufacturing a refusal the verdict did not already contain.

Selection (boundary read, `cli._hook_posture`, precedence):

1. `DOS_HOOK_POSTURE` env — a fleet-wide override, read per-call like
   `DOS_APPLY_GATE` (the docs/364 cut-the-gate precedent: no restart, the env is
   read on every hook event).
2. `dos.toml [enforcement] posture` — the per-workspace declaration.
3. the `block` default — so an operator who sets nothing sees no change.

An unknown posture name degrades to `block` (the safe, behavior-preserving
direction) — unlike a dialect typo, which fails LOUD. The asymmetry is
deliberate: a wrong dialect is a silent no-op against a real host (must surface);
a wrong posture can only ever make DOS *stricter*, never drop a gate, so the
safe default is correct and `dos doctor` surfaces the effective posture.

## 3. How GATE composes with the existing softening

`decide()` already softens many denies to WARN for an **interactive operator**
(no loop env — docs/355 for SELF_MODIFY, docs/126/143 for a lane collision). The
posture composes cleanly with that, because the two answer different questions:

- The operator-session softening asks *"is a human already in command of this
  session?"* If yes, their own deliberate edit is theirs to make → WARN.
- The posture asks *"how should a refusal that survives be surfaced?"*

So a **loop** session under `gate` posture turns its hard denies into ASKs (the
human gets the gate the loop cannot answer for itself), while an **interactive**
operator keeps their advisory WARN (they *are* the human). The two are not in
tension — GATE is most load-bearing exactly where the softening does *not* fire:
the automated session that needs a human's call.

## 4. Why this stays kernel-clean

- `HookAction.ASK`, `Posture`, `parse_posture`, `under_posture` are PURE and
  name no host (an env name and a closed enum, like `DOS_APPLY_GATE`). The
  vendor-blind litmus (`test_vendor_agnostic_kernel.py`) is untouched.
- Native ASK *rendering* lives where every renderer lives: the unshadowable
  `ClaudeCodeDialect` default in the kernel seam, and the per-vendor renderers in
  the `drivers/hook_dialects` driver (docs/217). A host's ask grammar is OUTPUT,
  downstream of an already-decided verdict — driver territory, not a decision.
- The transform is applied at the CLI rendering boundary
  (`parse_cc → under_posture → render`), so `decide()` and its 67 green hook
  tests are untouched, and `--dialect claude-code` + default posture is
  byte-for-byte today's bytes (the docs/217 Phase-1 parity floor, re-pinned
  here for the posture: `test_real_deny_under_block_stays_a_deny`).

## 5. The litmus tests this seam must satisfy

1. **ASK never hard-blocks.** On every host, an ASK carries no deny signal —
   it escalates, it does not block. (The fail-open dual.)
2. **Posture is monotone-softening.** `under_posture` re-shapes only a DENY,
   only ever softer; a PASS/WARN/ASK is returned unchanged under every posture.
3. **The default is parity.** Unset posture == `block` == today's bytes.
4. **GATE is PRE-only.** "Ask the human" is meaningful only before a tool runs;
   a Stop/Post refusal under GATE stays a DENY (no permission-prompt seam to
   escalate into — never a fabricated ask).
5. **The kernel names no host in the posture seam.**

## 6. The roadmap — what 98% coverage of the control-oriented world still needs

Each is a numbered, litmus-checkable follow-up; none is a kernel-architecture
change.

- **Phase A — native per-host ASK.** Verify each non-CC host's "ask the human"
  grammar (Cursor's `permission: "ask"`, Gemini's confirm path, Antigravity's
  decision vocabulary) against the real runtime, then render natively instead of
  degrading to WARN. Same web-grounded discipline as docs/268/278. → a GitHub
  issue per host, gated on a verified probe.
- **Phase B — `HookAction.ALLOW` + the `assist` posture (the verified
  allowlist).** In a permission-on deployment the human is prompted constantly.
  DOS holds facts they do not (this read is in-lane, this write is contained,
  this is a no-footprint tool). `assist` = GATE + auto-`allow` of the calls DOS
  has *positively verified* safe, so the human is prompted only for the genuinely
  uncertain ones — a verified allowlist, not a static one. ALLOW must be strictly
  opt-in and refuse-narrow (auto-allow only what is provably safe; everything
  else still asks).
- **Phase C — posture-aware observation. ✅ SHIPPED.** The PRE hook now records,
  off the `block` default, the POSTURE in effect and the SURFACE action it
  produced (a deny is `surface=ask` under `gate`, `surface=warn` under `observe`)
  as additive `hook_observation` fields — the `outcome` still records the kernel
  verdict, so the refused/intervened denominator stays honest. `posture_split`
  folds the refused records into `escalated` / `blocked` / `observed` (a total
  partition; a posture-blind record counts as blocked), and `dos doctor` (text +
  `--json`) shows the effective posture, its source, and the split — so a `gate`
  operator sees how often DOS handed them a call. The default-`block` record is
  byte-identical to the pre-posture one (the parity floor). `posture`/`surface`
  carried into the Go decider's record is Phase F. Tests:
  `tests/test_posture_observation.py`.
- **Phase D — the decision-inbox round-trip.** An ASK the human DEFERS becomes a
  `decisions` row (the HUMAN resolver kind already exists, `decisions.py`), so a
  deferred escalation is not lost — it lands in the `dos decisions` queue.
- **Phase E — `dos init --posture gate`. ✅ SHIPPED.** `dos init --posture
  <observe|block|gate>` writes `[enforcement] posture` into `dos.toml`. The choices
  come from the `Posture` enum (the flag never drifts from the seam); the value is
  MERGED into an existing config — a surgical, comment-preserving text upsert
  (`_upsert_enforcement_posture`: append a documented table when absent, replace a
  value in place when present, idempotent on a no-op) — so an operator turns `gate`
  on in a repo that already has a `dos.toml` without `--force` clobbering the rest.
  The `gate` confirmation documents the interplay with the host's OWN permission
  settings (the allowlist handles the routine; DOS escalates the adjudicated
  exceptions). A bare `dos init` is byte-for-byte the pre-Phase-E scaffold (no
  `[enforcement]` table unless asked). Tests: `tests/test_init_posture.py`.
- **Phase F — Go fast-path parity.** The native `dos-hook` binary serves the
  default BLOCK path; the posture transform is Python-side today. Carry the
  posture into the Go decider so a gated fleet keeps the fast path. (Default
  BLOCK already has Go parity — only `gate`/`observe` need the Python path now.)

## 7. The one-line frame

DOS was the referee for the room with no human in it. The posture seam makes it
the referee for the room *with* one — not by deciding for the human, but by
handing them the one fact the agent could not forge, at the moment they decide.

## Fixes (none yet — opens the Phase A–F issues above)
