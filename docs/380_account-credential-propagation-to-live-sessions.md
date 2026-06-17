# 380 — Account credential refresh: why a static env token broke live-session propagation

> **Status:** shipped (the launcher-contract fix). The `dos verify` horizon for
> this phase is the git ancestry of the commit that lands it, not this sentence.

## The symptom the operator hit

Running a fleet of Claude Code sessions, each pinned to a different account.
When you refresh a login (or switch which account a session uses) in one place,
the change does **not** reach the other live sessions. Plain Claude *seems* to
do this for free — re-login in one window and the others pick it up. "Our custom
thing" looked like it was overriding that.

It was. Here is the exact mechanism.

## Why plain Claude propagates a refresh for free

All sessions that share one `CLAUDE_CONFIG_DIR` read the **same**
`.credentials.json` on disk. The CLI treats that file as the live source of
truth: when the short-lived access token nears expiry, the CLI refreshes it and
rewrites the file **in place**. Every other session re-reads the file, so a
refresh (or a fresh `/login`) propagates to all of them. The file is the channel.

## Why the account-switcher broke it

The switcher (`src/dos/drivers/account_switcher.py`, a vendored driver) does two
things to run a fleet across many accounts:

1. **Isolated config dirs** — each account gets its own `CLAUDE_CONFIG_DIR`.
   A `/login` in one dir is invisible to a session on a *different* dir. This is
   correct and intended: different accounts are different identities.

2. **A static `CLAUDE_CODE_OAUTH_TOKEN`** — for a `claude setup-token` account
   (a ~1-year token, no `.credentials.json`) the launcher splices the token into
   the child's environment, because a headless `claude -p` child with no creds
   file would otherwise authenticate as `apiKeySource=none` ("Not logged in").

Bug #2 is the one the operator felt. The old code spliced the static token
whenever a `.oauth-token` file existed — **even when the dir also held a fresh,
auto-refreshing `.credentials.json`.** Two consequences, both bad for a live
session:

- **An env var shadows the file.** `CLAUDE_CODE_OAUTH_TOKEN` takes precedence
  over `.credentials.json`, so the session ignores the channel that propagation
  flows through.
- **An env var is frozen for the life of the process.** You cannot change a
  running process's environment from outside. So even a re-mint or re-login that
  rewrites the disk can never reach a session that was launched with the token
  baked into its env.

Net: the static token turned a self-updating, file-watched session into one
pinned to whatever creds existed at launch. That is the "overflowing."

Notably the driver's own comment already said the token was for *"token-only
config dirs … no `.credentials.json`"* — the code just never enforced the
"token-only" part.

## The hard constraint

A live process's environment cannot be mutated from outside it. So "refresh the
token for all existing sessions" has a firm ceiling:

- Sessions reading `.credentials.json` (nothing shadowing it) → a disk refresh
  **propagates automatically**. This is the Claude default, and the goal.
- Sessions launched with a frozen `CLAUDE_CODE_OAUTH_TOKEN` → **no propagation
  is possible** without relaunching the process. There is no safe live patch.

The fix therefore is not a new "refresh" verb — it is to stop freezing sessions
that don't need it, so the file-based channel keeps working.

## The fix

`account_env_overrides` and `env_for` now splice the static token **only when
there is no present-and-unexpired `.credentials.json` to defer to** — gated by a
new `_has_fresh_login_creds(account)` helper:

| config dir holds | launch env | refresh propagates to a live session? |
| --- | --- | --- |
| login creds only (fresh) | config-dir only | **yes** — reads the auto-refreshing file |
| setup-token only (no creds) | config-dir + token | no — but a ~1yr token rarely needs it |
| **both, creds fresh** | **config-dir only** (was: + token) | **yes** — the fix |
| both, creds expired | config-dir + token | no — stale creds can't serve; token required |

Enrollment classification is unchanged: `account_state` still treats a stored
setup-token as the durable "this account is enrolled" signal (so a pool member
never flaps to `needs_enroll` between refreshes). That is orthogonal to **launch
identity** — classify via the durable token, but launch via the auto-refreshing
creds file whenever it can serve. The headless-pool guarantee survives because a
token-only dir (the actual headless case) still gets the token; a short-lived
`-p` child with a *fresh* creds file is equally well served by the file.

Tests: `tests/test_account_switcher.py`
(`test_env_for_prefers_refreshing_creds_over_static_token`,
`test_env_for_injects_token_when_creds_expired`, the two
`account_env_overrides` cases, and `test_has_fresh_login_creds_*`).

## Operator guidance — getting propagation across live sessions

- **Sessions on the same account should share one `CLAUDE_CONFIG_DIR`.** Then a
  `/login` or auto-refresh in any one of them propagates to all — the Claude
  default, now unshadowed.
- **Don't bake `CLAUDE_CODE_OAUTH_TOKEN` into long-lived interactive sessions.**
  Reserve it for headless `-p` children on token-only dirs. With this fix the
  `dos accounts env` launcher already does the right thing automatically.
- **A frozen session can only be refreshed by relaunch.** If a session was
  started with the env token, restart it to pick up new creds; there is no
  live patch (OS constraint above).

## Out-of-scope note: canonical/vendored drift

The canonical core is `dos-private/tools/claude_account_switcher.py`; this repo
ships a byte-faithful vendored copy. The env/token logic in this change was
applied to **both** so the two stay in sync. Separately, the earlier
`seed_account_settings` / `RosterDefaults` block (commit `b73fb47`) was added to
the public copy only and never back-ported — a pre-existing forward drift worth a
re-vendor pass, tracked apart from this fix.

**Update (2026-06-17).** `merge_account_settings` / `_deep_merge_settings` (the
`dos accounts sync` primitive, issue #219) join that same public-led settings
block, pending the one batched re-vendor pass that reconciles the whole block back
into the canonical copy. Treating it as one deferred re-vendor (rather than an
inline cross-repo edit per change) is the established handling — and under the
strongly-typed mandate ([docs/385](385_the-strongly-typed-mandate-and-the-python-retreat-plan.md))
the account switcher's pure ranking core is itself queued for a Go port, so the
Python canonical/vendored relationship is a shrinking concern, not one to deepen.
