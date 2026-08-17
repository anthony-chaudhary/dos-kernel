# 386 — The agent-pluggable account switcher (and the seat-tracking + rotate-on-wall arc)

> **Status: landed (2026-06-17).** The seam, the per-agent specs, the per-account
> ledger, the seat stamp, and the rate-limit auto-restart wiring shipped first; the
> two follow-ups this doc once queued are now landed too (the git ancestry of the
> commits is the `dos verify` horizon, not this sentence): (1) the rotate-on-wall
> **apply** — DOS's side of the StopFailure-to-SessionStart env-override contract,
> a two-phase rotation handoff (§4); and (2) the **Go port + FLIP** of the pure
> ranking core (§6, the docs/385 ratchet). The one residual is genuinely the
> harness's, named in §4: StopFailure cannot relaunch or pass env; Claude Code's
> retry loop (`CLAUDE_CODE_MAX_RETRIES`, with `CLAUDE_CODE_RETRY_WATCHDOG=1` for
> sustained 429/529 capacity errors) is the actual resumption mechanism, and DOS
> applies the recorded env only at the next real SessionStart boundary
> (`CLAUDE_ENV_FILE`).

The operator's ask: a **10× better account switcher** — (1) support arbitrary
agents (Codex, Claude, Gemini, …), (2) land the in-progress Claude items (safe
refresh-on-login, the default account, the canonical/vendored drift), (3) land the
auto-restart hooks for rate limits, (4) better independent session tracking. This
doc is the architecture that ties the four together and is honest about which parts
are landed, which are queued behind an external contract, and why a brand-new pure
core here is **Python today and Go tomorrow** under docs/385.

## 1. The one idea: the pick is agent-blind; only the AUTH names a vendor

The switcher already had the right shape. `drivers/account_switcher.pick_account` /
`serving_pool` / `allocate_seats` reason over `Account` / `AccountState` /
`ProbeLike` and **name no vendor** — a roster of seats ranks identically whether the
worker is Claude Code, Codex CLI, or Gemini CLI. What was Claude-specific was only
the **auth glue**: the isolated-config-dir env var (`CLAUDE_CONFIG_DIR`), the
static-token env (`CLAUDE_CODE_OAUTH_TOKEN`), the token/creds file names
(`.oauth-token` / `.credentials.json`), the token prefix (`sk-ant-oat`), the enroll
flow (`setup-token` / `login`).

So "support arbitrary agents" is **not** a rewrite of the picker — it is lifting
that glue into one resolvable spec, the same pure-protocol + by-name-resolver move
DOS already makes for the JUDGE rung (`dos.judges`), the hook envelope
(`dos.hook_dialects`), and the VCS read (`dos.vcs`).

## 2. The seam — `dos.account_auth` (landed)

`src/dos/account_auth.py` (kernel) defines `AccountAuthSpec` — the typed per-agent
data (`config_dir_env`, `token_env`, `token_file_name`, `creds_file_name`,
`token_prefix`, `enroll_methods`, `enroll_hint`) plus pure methods
(`validate_token`, `env_overrides`, `supports_config_dir_isolation`) — and the
by-name resolver `resolve_account_auth(name)`: in-tree (`dos.drivers.agent_auth.<name>`)
first, then the `dos.account_auth` entry-point group, **fail-LOUD** on an unknown
kind (the `resolve_dialect` discipline — launching a worker under the wrong agent's
env is the wrong-identity hazard the seam exists to prevent, so it never silently
falls back to the default).

The kernel module **names no vendor as code** (the
`test_vendor_agnostic_kernel.py` litmus): `DEFAULT_AGENT_KIND = "claude"` is a string
constant (the `vcs_backend="git"` analogue), not an identifier the kernel branches
on. The vendor literals live one layer down.

### The three shipped specs (the vendor tier)

`src/dos/drivers/agent_auth/{claude,codex,gemini}.py` — each a `SPEC =
AccountAuthSpec(...)`, registered under the `dos.account_auth` entry-point group:

| agent | config-dir env | creds file | token env | isolation? |
|---|---|---|---|---|
| **claude** | `CLAUDE_CONFIG_DIR` | `.credentials.json` (+ `.oauth-token`) | `CLAUDE_CODE_OAUTH_TOKEN` | yes |
| **codex** | `CODEX_HOME` | `auth.json` | `OPENAI_API_KEY` | yes |
| **gemini** | *(none)* | `oauth_creds.json` | `GEMINI_API_KEY` | **no — named, not hidden** |

Claude is the **verified baseline**: its literals mirror the vendored switcher
exactly, pinned by a parity test (`test_account_auth.py`) that ties the spec's
env-var names + token-prefix to `account_switcher.env_for` / `write_account_token`'s
real behavior — so the seam and the switcher cannot silently disagree about Claude's
identity. Codex is verified against the Codex config reference (`CODEX_HOME` /
`auth.json`). Gemini is **honest about a gap**: the Gemini CLI exposes no
config-dir-override env var, so per-account env-isolation cannot be expressed for it
— `config_dir_env=""` and `supports_config_dir_isolation=False` surface that rather
than emitting a no-op that pretends to isolate.

### The config selector (landed)

`SubstrateConfig.agent_kind` (default `"claude"`) + a `dos.toml [agent] kind` loader,
mirroring `[vcs] backend`: an override resolves-or-warns-and-keeps-base, so a typo'd
agent kind never silently launches a fleet under the wrong agent. A per-account
`agent_kind` roster field is the per-seat override (a roster can mix agents).

## 3. Independent session tracking — the seat axis (landed)

DOS keyed state two ways: by **session** (`.dos/stop-failures/<sid>.json`, the
breaker) and by **run** (`.dos/runs/<run_id>/`). Neither answered the **seat**
question a multi-account fleet has. Two additive pieces close it:

- `run_id.RunId.account_name` — an optional, **caller-supplied** seat label (the
  kernel never derives an account name), carried across a child boundary as
  `CID_ACCOUNT`, emitted in `run.json` only when set (byte-identical legacy shape
  otherwise).
- `account_ledger` — an append-only, fail-soft per-seat ledger under
  `.dos/accounts/<name>/{runs,failures,tokens}.jsonl` (the `lane_journal` discipline:
  flush+fsync, torn-tail tolerant, name sanitized to a safe stem). `summary()` /
  `known_accounts()` fold it. This is the substrate rotate-on-wall reads.

## 4. Rate-limit retry handoff (landed wiring; rotation gated)

The breaker + StopFailure machinery (`cmd_hook_stop_failure` / `dos.breaker` /
`stop_failure_sensor`) shipped long ago but was **never bound to an event** — a
rate-limited session just died without attribution or a durable rotation record. Now
wired in both `claude-plugin/hooks/hooks.json` and this repo's `.claude/settings.json`:

- **StopFailure → `dos hook stop-failure`** for overload / server-error / rate-limit
  failures: attribute the wall, back off, maybe write a rotation handoff, and keep the
  hook honest. A StopFailure hook's output and exit code are not a relaunch channel, so
  `asyncRewake` is not the resumption mechanism here. The next attempt comes from
  Claude Code's own retry loop (`CLAUDE_CODE_MAX_RETRIES`; `CLAUDE_CODE_RETRY_WATCHDOG=1`
  keeps retrying 429/529 capacity errors). The breaker opens after N consecutive
  failures (escalate, stop asking for retry) so a non-retriable failure cannot loop.
- **Stop → `dos hook stop-failure --success`**: heal the breaker after a clean
  session.

### The rotate-on-wall apply — a two-phase env-override handoff (landed)

The "10×" step is: on a wall, **choose the serving account the next real session
should use**, not just back off the same one. The kernel side is cheap (`serving_pool`
already answers "a different serving seat"), and the per-account failure attribution
is landed (the ledger). The one thing the harness does **not** expose is the obvious
channel: a StopFailure hook cannot relaunch the session or hand the next attempt a
different `CLAUDE_CONFIG_DIR` / token. Its output and exit code are ignored for that
purpose, so a StopFailure `asyncRewake` does not resume the run. The only env channel
DOS can write is `CLAUDE_ENV_FILE`, and only on a real SessionStart-class event.

So the apply is a **two-phase handoff**, and DOS's side of the env-override contract is
now landed (`src/dos/rotation_handoff.py`, the `stop_failure_sensor` / `account_ledger`
fail-soft discipline):

- **WRITE half — the StopFailure hook (on a wall).** It attributes the wall to the
  current seat (`account_ledger.record_failure`), picks a serving seat, builds that
  seat's env-override through the resolved `AccountAuthSpec` (`spec.launch_env` — the
  vendor-blind capability path, byte-identical to what the launcher emits), and
  **persists a typed `RotationHandoff`** keyed by session (`_write_rotation_handoff` in
  `cli.py`). This is all the StopFailure boundary can reach: it cannot launch or pass
  env, so it records the decision durably.
- **APPLY half — the SessionStart applier (at the next real SessionStart).** `dos hook
  session-start` reads the pending handoff and, when `CLAUDE_ENV_FILE` is present,
  appends the seat's env there and clears the handoff (apply-once). It surfaces the
  rotation in the orientation digest. The applier consumes the handoff **only** on a
  successful env-file write, so with no channel present it stays pending for the next
  env-capable start rather than being silently dropped.
- **The residual is the harness's, named not waved off.** StopFailure `asyncRewake` is
  not a relaunch path here. Claude Code's retry loop / `CLAUDE_CODE_RETRY_WATCHDOG` is
  the actual resumption mechanism. When that retry (or any later genuine start) produces
  a real SessionStart that exposes `CLAUDE_ENV_FILE`, the rotation applies; until then,
  the handoff remains pending. Either way the rotation is **computed + recorded the
  instant the wall is seen, and applied at the first env-capable boundary** — never lost.
  A direct env channel on the retry/start boundary would collapse the two phases into
  one; that is the only piece still outside this repo.

## 5. The in-progress Claude items (status)

- **Safe refresh-on-login propagation** — SHIPPED (docs/380; `_has_fresh_login_creds`
  gates the static-token splice so a fresh `.credentials.json` is never shadowed).
- **`dos accounts sync`** (issue #219) — SHIPPED: `merge_account_settings` deep-merges
  roster `defaults.settings` into every account's `settings.json` without clobbering
  account-specific keys; idempotent; `--dry-run`.
- **The default account** — the small remaining piece: a roster `default` marker the
  picker and the launcher prefer; tracked in §7.
- **Canonical/vendored drift** — the `seed_account_settings` / `merge_account_settings`
  block is public-led, reconciled in one deferred re-vendor pass (docs/380 note); under
  docs/385 the switcher's pure core is Go-bound anyway, so the Python vendoring seam is
  a shrinking concern.

## 6. Why this is Python today and Go tomorrow (the docs/385 reconciliation)

docs/385 set the direction: new decision-bearing logic is born in **Go**; Python
retreats to named seams. The account switcher is a **driver** — the vendor /
interconnect layer (CLAUDE.md layer 4), not one of the §4 kernel cores
(arbiter/liveness/loop/oracle) queued for the port. The operator's explicit call for
this work was **"Python now, queue the Go port"**: ship the four threads as small
green Python increments in the driver tier, and queue the port of the switcher's
**pure ranking core** (`pick_account` / `allocate_seats` / `serving_pool` — the
Hamilton apportionment + headroom weighting + the serving/walled classification) to
Go behind the parity harness, on the same PORT → SOAK → FLIP ratchet. The auth glue
(env/token/enroll), the ledger I/O, and the hook wiring are genuine interconnects and
stay Python (docs/385 §5 seam #4). This doc is the recorded reason the seam list
(docs/385 §8) asks for.

**Landed (2026-06-17): the pure ranking core is now Go.** `go/internal/account` ports
`account_state` (the classification), `pick_account`, `serving_pool`, `allocate_seats`,
and `pick_account_spread` — pure, stdlib-only, every fact injected. The port is pinned
by a value-exact differential corpus (`go/internal/account/parity`) generated from the
**real** Python switcher (temp config dirs + an injected probe, so the vendored copy is
untouched — no re-vendor): the Go `TestParityCorpus` asserts `go == corpus`, the Python
`tests/test_go_account_parity.py` asserts `python == corpus`. Because the core is PURE
and fully corpus-covered (no live-shadow drift a soak window guards against), the FLIP
followed the PORT directly: `native_canonical` now records `account_pick` /
`serving_pool` / `allocate_seats` as Go-canonical (phase `386`), surfaced on `dos doctor`.
Python survives as the runtime + corpus-pinned shadow; moving the runtime to call Go is
the later I/O-orchestration step (docs/385 §4.3), not this flip.

## 7. The threads (status — one still queued)

All but the last are now landed (the git ancestry is the `dos verify` horizon):

1. **Default account** — LANDED: a roster `default: true` marker, surfaced in `dos
   accounts list` and resolvable via `dos accounts default` (does not change the
   serving-pool ranking).
2. **`dos accounts env --agent-kind` / per-account `agent_kind`** — LANDED: the resolved
   spec is threaded through `dos accounts env` so it emits the right per-agent env.
3. **Rotate-on-wall apply** — LANDED (§4): the two-phase env-override handoff
   (`rotation_handoff` WRITE on StopFailure, the SessionStart applier at the next real
   SessionStart). The only residual is the harness's — retry/resumption is Claude Code's
   own loop / `CLAUDE_CODE_RETRY_WATCHDOG`, not StopFailure `asyncRewake` (§4).
4. **Go port of the pure ranking core** — LANDED + FLIPPED (§6): `go/internal/account`
   + the value-exact parity corpus; `native_canonical` records it Go-canonical.
5. **The one batched re-vendor** of the public-led settings block into the canonical
   copy (docs/380 note) — still queued (the only remaining thread).

## References

- [`380`](380_account-credential-propagation-to-live-sessions.md) — the refresh-on-login propagation fix + the canonical/vendored drift note this doc extends.
- [`385`](385_the-strongly-typed-mandate-and-the-python-retreat-plan.md) — the Go-first mandate; §6 here is the recorded reason this seam stays Python today.
- [`217`](217_the-cross-vendor-hook-dialect-seam.md) / [`221`](221_the-cross-vendor-hook-installer.md) / [`379`](379_vcs-backend-seam-git-stops-being-assumed.md) — the pure-protocol + by-name-resolver pattern `dos.account_auth` mirrors.
- [`223`](223_the-circuit-breaker-primitive-failure-counting-as-mechanism.md) / [`189`](189_ideas-from-the-claude-code-source-extensible-to-dos.md) — the breaker the rate-limit auto-restart rides on.
