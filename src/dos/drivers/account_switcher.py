"""Claude account-switcher core — pick the next serving Claude Code login.

KIND: harness-launch-config (generic core) · VENDORED COPY · driver tier.

This is the public DOS kernel's vendored copy of the host-free account-switcher
decision core. It is the seat-pool seam ``dos accounts`` (and the goal-fleet
skill's Step 0/Step 3) read: given a roster of accounts (each a name -> an
isolated ``CLAUDE_CONFIG_DIR``), it folds two ground-truth signals per account —
enrolled? (a stored setup-token / un-expired creds, read from disk) and serving
right now? (an INJECTED live probe; absent here -> fail-open "assume serving") —
and answers the serving pool a fan-out spreads its workers across
(``serving_pool`` / ``allocate_seats`` / ``pick_account_spread``).

WHY IT LIVES IN ``drivers/`` (and not the kernel proper)
-------------------------------------------------------
Account rotation names a vendor: ``CLAUDE_CONFIG_DIR``, ``CLAUDE_CODE_OAUTH_TOKEN``,
the ``claude setup-token`` enroll path. The kernel's "names no vendor" litmus
forbids those from ``src/dos/*.py`` outside ``drivers/`` — and the driver tier is
explicitly "the only home for provider/network/non-determinism/vendor names." So a
Claude-account module HERE *satisfies* the litmus rather than breaking it. The core
itself stays pure: stdlib + optional ``yaml``, every network/host call injected
(``probe_fn`` / ``wait_for_walled``); with neither it runs fully fail-open.

CANONICAL SOURCE + SYNC RULE
----------------------------
Canonical copy: ``dos-private/tools/claude_account_switcher.py`` (with its 51
standalone tests). This file is a BYTE-FAITHFUL vendored copy of that core's code
body (only this provenance docstring differs from the canonical's own docstring) so
the public kernel is self-contained — it never imports the private sibling at
runtime (a path-add to ``../dos-private`` would break any machine/CI without it).
**Edit the switcher logic in the canonical copy, then re-vendor here** (re-copy the
code body; verify the body matches with a SHA256 compare). Never fork the two. The
same "byte-thin vendored core" pattern Job's ``_account_switcher_core.py`` uses.

Fail-open is the load-bearing posture throughout: a probe returning ``None`` (no
token / network error / no signal) is treated as "assume serving", and a
missing/unreadable/malformed roster yields an empty roster — never an exception
into the caller. A switcher that crashes would strand the host on a walled account,
which is worse than not switching. (``account_env_overrides`` / ``env_for`` are the
deliberate fail-LOUD exception: launching a child under the WRONG identity is the
danger they exist to prevent, so a missing config dir raises ``OriginError``.)

The roster file defaults to ``~/.claude/accounts.yaml`` (override with
``CLAUDE_ACCOUNTS_FILE``) and is never part of any tracked tree — real account
config-dir paths live only on the host's disk.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Protocol, Sequence

# The roster file default: under the user home, NEVER a repo/tracked tree, so real
# account config-dir paths never enter version control. Override per host with
# CLAUDE_ACCOUNTS_FILE. A host that roots its roster elsewhere just sets that env.
DEFAULT_ACCOUNTS_FILE = Path("~/.claude/accounts.yaml").expanduser()
ACCOUNTS_FILE_ENV = "CLAUDE_ACCOUNTS_FILE"

# Default "near cap" utilization — an account at/over this is avoided when a
# lower-utilization serving account exists (but is still usable if it's the only
# one). Mirrors the roster's `rotation.near_cap_util`.
_DEFAULT_NEAR_CAP_UTIL = 0.9

# When every account is walled and none carries a parseable reset (and no host
# wait_for_walled is injected), sleep this long before re-probing (a typical Max
# window). The core owns no backoff policy of its own beyond this floor.
_NO_HINT_WAIT_SECONDS = 1800


# --------------------------------------------------------------------------- #
# Typed probe seam — structural so a host injects a plain object / its own state.
# --------------------------------------------------------------------------- #
class ProbeLike(Protocol):
    """The subset of a live rate-limit probe state the core reads.

    A host's probe object (e.g. a parsed rate-limit-headers response) satisfies
    this structurally — no base class to import. ``allowed`` is the binary "would a
    real request be SERVED right now" verdict; ``utilization`` ∈ [0,1] is the
    binding window's consumed fraction; ``reset_at_epoch`` is when that window next
    rolls (or None).
    """

    status: str
    utilization: float
    reset_at_epoch: Optional[int]

    @property
    def allowed(self) -> bool:  # pragma: no cover - structural
        ...


# probe_fn signature: (creds_path: str, now_epoch: float) -> ProbeLike | None
ProbeFn = Callable[[str, float], Optional[ProbeLike]]

# wait_for_walled signature: (reset_epoch: int | None, now_epoch: float) -> int
WaitFn = Callable[[Optional[int], float], int]


@dataclass(frozen=True)
class Account:
    """One enrollable Claude account: a name -> isolated config dir."""

    name: str
    config_dir: str
    chrome_profile: str = ""
    email: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class RotationPolicy:
    order: str = "by_reset"
    near_cap_util: float = _DEFAULT_NEAR_CAP_UTIL


# AccountState.kind values — the per-account fold the picker ranks over.
ACCT_NEEDS_ENROLL = "needs_enroll"  # no creds file, or token expired
ACCT_DISABLED = "disabled"  # enabled: false in the roster
ACCT_SERVING = "serving"  # creds present + live probe allowed (or fail-open)
ACCT_NEAR_CAP = "near_cap"  # serving but utilization >= near_cap_util
ACCT_WALLED = "walled"  # creds present + live probe rejected


@dataclass(frozen=True)
class AccountState:
    """The folded ground-truth state of one account at decision time."""

    account: Account
    kind: str
    creds_present: bool
    token_expired: bool
    # Live-probe fields (None when not probed / no signal — fail-open).
    probe_status: Optional[str] = None
    utilization: Optional[float] = None
    reset_at_epoch: Optional[int] = None
    detail: str = ""

    @property
    def pickable(self) -> bool:
        """An account the picker may route work to right now."""
        return self.kind in (ACCT_SERVING, ACCT_NEAR_CAP)


@dataclass(frozen=True)
class Pick:
    """The switcher's decision."""

    account: Optional[Account]
    reason: str
    states: Sequence[AccountState] = field(default_factory=tuple)
    # Set only when every enrolled account is walled: how long to wait before
    # the soonest-resetting account is expected to serve again.
    wait_seconds: Optional[int] = None
    soonest_reset_epoch: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.account is not None and self.wait_seconds is None


# --------------------------------------------------------------------------- #
# Env overrides for a chosen account — the launcher contract (host-free).
# --------------------------------------------------------------------------- #
class OriginError(ValueError):
    """A request identity was asked for but cannot be fully resolved.

    Deliberately loud: launching a child under the WRONG identity (or a
    logged-out config dir) is the failure ``env_for`` exists to prevent, so a
    missing config dir raises rather than silently falling back to the default
    account. (Contrast ``load_roster``, which is fail-OPEN.)
    """


_LOGIN_RECIPE = (
    'create it and log in once:  $env:CLAUDE_CONFIG_DIR = "<dir>"; claude '
    "(then /login with that account)"
)

# A setup-token account's config dir carries NO .credentials.json — its token
# lives in this sibling file (see the setup-token store section below).
_TOKEN_FILENAME = ".oauth-token"


def account_env_overrides(
    account: Account,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the child-process env overrides to run Claude Code as ``account``.

    Emits ``CLAUDE_CONFIG_DIR`` (the launcher contract) and, for a *token-only*
    dir, splices its ``CLAUDE_CODE_OAUTH_TOKEN`` so the child authenticates as this
    account directly. A config dir that does not exist on disk raises
    ``OriginError`` (a fresh dir would be a logged-out identity — fail now with the
    login recipe, not at request time). This is the host-free inline of the env
    shape the ``--origin`` launcher seam uses, so the emitted env is byte-identical.

    The static token is spliced ONLY when no present+unexpired ``.credentials.json``
    is there to defer to (see ``_has_fresh_login_creds``): that file is Claude's own
    auto-refreshing source, so a refresh on disk propagates to every live session
    reading it, whereas a static env token would shadow + freeze it.

    ``environ`` (default ``os.environ``) is consulted only to let an explicit
    ``CLAUDE_CODE_OAUTH_TOKEN`` already in the env win over the on-disk token.
    """
    environ = os.environ if environ is None else environ
    cfg = Path(account.config_dir).expanduser()
    if not cfg.is_dir():
        raise OriginError(
            f"account {account.name!r}: config_dir does not exist: {cfg} — "
            + _LOGIN_RECIPE.replace("<dir>", str(cfg))
        )
    overrides: dict[str, str] = {"CLAUDE_CONFIG_DIR": str(cfg)}
    # Token-only config dirs (a stored `claude setup-token`, no usable
    # `.credentials.json`) must carry their OAuth token in the env or the child
    # authenticates as apiKeySource=none ("Not logged in" under `-p`). Skip when:
    # the env already carries a token (an explicit caller wins); there is no token
    # file; OR a present+unexpired `.credentials.json` is already there. That last
    # case is the propagation fix — Claude auto-refreshes the creds file in place,
    # so a static env token would only SHADOW it and freeze the session at its
    # launch-time creds (a running process's env cannot be updated from outside),
    # defeating the refresh-propagates-to-live-sessions behavior the operator
    # expects from Claude by default. Defer to the file when it can serve.
    if (
        not (environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        and not _has_fresh_login_creds(account)
    ):
        try:
            tok = (cfg / _TOKEN_FILENAME).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            tok = ""
        if tok:
            overrides["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    return overrides


# --------------------------------------------------------------------------- #
# Roster loading (the only file read; fail-open to empty).
# --------------------------------------------------------------------------- #
def accounts_file_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """The active roster file: explicit arg > CLAUDE_ACCOUNTS_FILE > home default."""
    if explicit:
        return Path(explicit)
    env = os.environ.get(ACCOUNTS_FILE_ENV, "").strip()
    return Path(env) if env else DEFAULT_ACCOUNTS_FILE


def load_roster(
    path: str | os.PathLike[str] | None = None,
) -> tuple[list[Account], RotationPolicy]:
    """Parse the roster YAML into (accounts, policy).

    Fail-OPEN: a missing/unreadable/malformed file (or no PyYAML installed) yields
    ([], default policy) rather than raising — a switcher with no roster simply has
    nothing to pick, it never crashes the caller. (Contrast ``account_env_overrides``,
    which is fail-LOUD because launching under the WRONG identity is the danger
    there; here the danger is the opposite — a crash that strands the host.)
    """
    p = accounts_file_path(path)
    if not p.is_file():
        return [], RotationPolicy()
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — fail-open on any parse fault / missing yaml
        return [], RotationPolicy()
    if not isinstance(data, dict):
        return [], RotationPolicy()
    raw_accounts = data.get("accounts") or []
    out: list[Account] = []
    if isinstance(raw_accounts, list):
        for spec in raw_accounts:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            config_dir = str(spec.get("config_dir") or "").strip()
            if not name or not config_dir:
                continue
            out.append(
                Account(
                    name=name,
                    config_dir=config_dir,
                    chrome_profile=str(spec.get("chrome_profile") or ""),
                    email=str(spec.get("email") or ""),
                    enabled=bool(spec.get("enabled", True)),
                )
            )
    raw_rot = data.get("rotation") or {}
    policy = RotationPolicy()
    if isinstance(raw_rot, dict):
        try:
            near = float(raw_rot.get("near_cap_util", _DEFAULT_NEAR_CAP_UTIL))
        except (TypeError, ValueError):
            near = _DEFAULT_NEAR_CAP_UTIL
        policy = RotationPolicy(
            order=str(raw_rot.get("order") or "by_reset"),
            near_cap_util=near,
        )
    return out, policy


# --------------------------------------------------------------------------- #
# Per-account disk reads (no network).
# --------------------------------------------------------------------------- #
def account_creds_path(account: Account) -> Path:
    """The isolated credentials file for an account's login."""
    return Path(account.config_dir).expanduser() / ".credentials.json"


# --------------------------------------------------------------------------- #
# Setup-token store — the headless-pool enrollment path.
# --------------------------------------------------------------------------- #
# `claude setup-token` mints a ~1-year OAuth token but PRINTS it (it writes NO
# `.credentials.json`), so the switcher persists it itself: a `.oauth-token`
# sibling of the creds file, inside the account's isolated config dir (which
# lives under ``~``, never a tracked tree, so it is never committed).
# ``account_env_overrides`` emits it as ``CLAUDE_CODE_OAUTH_TOKEN`` and a host's
# live probe reads the same sibling, so a setup-token account is durable (no
# short-lived access-token flap) AND probeable. The ``login`` path's
# ``.credentials.json`` keeps working unchanged.
_OAUTH_TOKEN_PREFIX = "sk-ant-oat"  # setup-token: sk-ant-oat01-… (version-tolerant)
_SETTINGS_FILENAME = "settings.json"  # Claude Code user-settings file seeded per account


class TokenError(ValueError):
    """A string that is not a usable Claude OAuth token was handed to save."""


def account_token_path(account: Account) -> Path:
    """The isolated setup-token store for an account (sibling of the creds file)."""
    return Path(account.config_dir).expanduser() / _TOKEN_FILENAME


def read_account_token(account: Account) -> Optional[str]:
    """The stored setup-token for an account, or None — never raises."""
    try:
        tok = account_token_path(account).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return tok or None


def write_account_token(account: Account, token: str) -> Path:
    """Persist a setup-token for an account; returns the file path.

    Validates the token shape (a bad paste must fail loud, not silently enroll a
    garbage token), creates the config dir if needed, and best-effort-restricts
    the file to owner-only. Raises ``TokenError`` on a non-token string.
    """
    token = (token or "").strip()
    if not token.startswith(_OAUTH_TOKEN_PREFIX):
        raise TokenError(
            f"not a Claude OAuth token (expected {_OAUTH_TOKEN_PREFIX!r}..., "
            f"from `claude setup-token`): got {token[:12]!r}"
        )
    path = account_token_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:  # owner-only where the OS honors it (POSIX; best-effort on Windows)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# --------------------------------------------------------------------------- #
# Account settings — seed a new account's config dir with global defaults.
# --------------------------------------------------------------------------- #
def account_settings_path(account: Account) -> Path:
    """The settings.json path inside an account's isolated config dir."""
    return Path(account.config_dir).expanduser() / _SETTINGS_FILENAME


def seed_account_settings(
    account: Account,
    settings: dict,
    *,
    overwrite: bool = False,
) -> Optional[Path]:
    """Write settings.json into the account's config dir if not already present.

    This is the fix for new accounts not inheriting global defaults (model,
    effortLevel, permissions.defaultMode, etc.): call this once after
    ``write_account_token`` with the settings dict from ``load_roster_defaults``
    and the new account's config dir will carry those defaults on the next launch.

    Returns the written path, or None when the file already existed and
    ``overwrite=False`` (safe to call on re-enrollment — idempotent).
    Returns None silently when ``settings`` is empty or falsy.
    Creates the config dir if needed (matching ``write_account_token``).
    """
    if not settings:
        return None
    path = account_settings_path(account)
    if path.is_file() and not overwrite:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _deep_merge_settings(base: dict, overlay: dict) -> dict:
    """Deep-merge ``overlay`` onto ``base``; return a NEW dict. PURE.

    Overlay keys win. A key present in BOTH whose values are BOTH dicts is merged
    recursively, so a nested block (e.g. ``permissions``) gains the overlay's keys
    WITHOUT dropping the base's own ones. Any other overlapping key is REPLACED by
    the overlay value (a list / scalar default is authoritative, not merged
    element-wise). Keys only in ``base`` are preserved untouched — the whole point
    of ``sync``: push the roster defaults without clobbering an account's settings.
    """
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_settings(out[k], v)
        else:
            out[k] = v
    return out


def merge_account_settings(
    account: Account,
    settings: dict,
    *,
    dry_run: bool = False,
) -> tuple[Optional[Path], bool]:
    """Deep-merge ``settings`` into an account's EXISTING ``settings.json``.

    The ``dos accounts sync`` primitive (issue #219). Where ``seed_account_settings``
    only writes when the file is ABSENT (or replaces it WHOLESALE under
    ``overwrite=True``), this MERGES the roster defaults into whatever the account
    already has, so a global default (model, effortLevel, ``permissions.defaultMode``)
    reaches EVERY account without dropping that account's own keys (theme, a
    per-account model, …). Reads the current file (a missing / unreadable / non-dict
    file is treated as ``{}``), deep-merges via ``_deep_merge_settings`` (defaults
    win, nested dicts merged, other keys preserved), and writes the result.

    Idempotent: when the merge changes nothing (the defaults are already a subset of
    the file) it writes nothing and reports ``changed=False`` — so a re-run is a
    no-op (issue #219's done-condition). ``dry_run=True`` computes the verdict but
    writes nothing (the ``--dry-run`` preview).

    Returns ``(path, changed)``: ``path`` is the settings.json path (``None`` only
    when ``settings`` is empty/falsy — nothing to merge); ``changed`` is whether the
    file content would change. Creates the config dir on a real write (matching
    ``seed_account_settings``). The change test compares PARSED dicts, not bytes, so
    a file already carrying the defaults in a different key order is correctly
    ``unchanged``. Never raises on a malformed existing file — it is treated as empty
    and replaced with the defaults (the operator asked to sync).
    """
    if not settings:
        return None, False
    path = account_settings_path(account)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, ValueError):
        existing = {}
    merged = _deep_merge_settings(existing, settings)
    if merged == existing:
        return path, False
    if dry_run:
        return path, True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, True


@dataclass(frozen=True)
class RosterDefaults:
    """Global defaults from the roster ``defaults`` section.

    Seeded into each new account's ``settings.json`` at enrollment time so that
    model, effortLevel, permissions, and any other global preferences carry over
    to every account in the pool — not just the primary one. Read by
    ``load_roster_defaults``; applied by ``seed_account_settings``.
    """

    settings: dict = field(default_factory=dict)


def load_roster_defaults(
    path: "str | os.PathLike[str] | None" = None,
) -> RosterDefaults:
    """Parse the roster's ``defaults`` section into a ``RosterDefaults``.

    Reads the same file as ``load_roster`` and extracts the optional
    ``defaults.settings`` dict — the Claude Code ``settings.json`` content to
    seed into each new account config dir at enrollment time.

    Fail-OPEN: a missing file, missing ``defaults`` key, or any parse error
    returns ``RosterDefaults(settings={})`` — a roster with no defaults section
    is valid; new accounts just get no auto-seeding.

    Example roster section::

        defaults:
          settings:
            model: opus
            effortLevel: xhigh
            permissions:
              defaultMode: bypassPermissions
              skipDangerousModePermissionPrompt: true
    """
    p = accounts_file_path(path)
    if not p.is_file():
        return RosterDefaults()
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — fail-open on any parse fault / missing yaml
        return RosterDefaults()
    if not isinstance(data, dict):
        return RosterDefaults()
    raw = data.get("defaults") or {}
    if not isinstance(raw, dict):
        return RosterDefaults()
    settings = raw.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    return RosterDefaults(settings=settings)


def _token_expired(creds_path: Path, now_epoch: float) -> tuple[bool, bool]:
    """(creds_present, token_expired) read from disk — never raises.

    Reads ``claudeAiOauth.expiresAt`` (epoch-MS). A missing/unreadable file is
    (False, False) — "not present"; an unparseable/absent expiry on a present
    file is (True, False) — present but unknown-expiry (treat as usable, the CLI
    auto-refreshes). Only a parsed expiry in the past is (True, True).
    """
    if not creds_path.is_file():
        return False, False
    try:
        d = json.loads(creds_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, False
    oauth = d.get("claudeAiOauth") if isinstance(d, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        # File exists but carries no usable token — treat as not enrolled.
        return False, False
    exp = oauth.get("expiresAt")
    if isinstance(exp, (int, float)) and exp > 0:
        return True, (exp / 1000.0 <= now_epoch)
    return True, False  # present, unknown expiry → usable (auto-refresh)


def _has_fresh_login_creds(
    account: Account, *, now_epoch: float | None = None
) -> bool:
    """True when the config dir carries a present, UNEXPIRED ``.credentials.json``
    — Claude Code's own auto-refreshing credential source.

    Such a file is the live source of truth: the CLI re-reads it and rolls the
    short-lived access token in place, so a login/token refresh on disk PROPAGATES
    to every session reading it. The launcher contract (``account_env_overrides`` /
    ``env_for``) uses this to decide NOT to splice a static ``CLAUDE_CODE_OAUTH_TOKEN``
    when the file can serve — a static env token would shadow the file and freeze a
    live session at its launch-time creds (a running process's env cannot be updated
    from outside), defeating the refresh-propagates-to-live-sessions behavior the
    operator expects. A token-only dir (no creds, or expired) returns False, so the
    durable setup-token is still spliced where it is genuinely required.

    Never raises (delegates to ``_token_expired``, which is itself fail-safe).
    """
    now = time.time() if now_epoch is None else now_epoch
    present, expired = _token_expired(account_creds_path(account), now)
    return present and not expired


def account_state(
    account: Account,
    *,
    probe_fn: ProbeFn | None = None,
    now_epoch: float | None = None,
    near_cap_util: float = _DEFAULT_NEAR_CAP_UTIL,
) -> AccountState:
    """Fold one account's disk + (optional) live-probe state into a verdict.

    ``probe_fn(creds_path, now_epoch) -> ProbeLike | None`` is injected so this
    is testable with no network. When ``probe_fn`` is None, or returns None, the
    account is treated as serving (fail-open) provided it is enrolled.
    """
    now = time.time() if now_epoch is None else now_epoch
    if not account.enabled:
        return AccountState(
            account=account, kind=ACCT_DISABLED, creds_present=False,
            token_expired=False, detail="disabled in roster",
        )
    creds = account_creds_path(account)
    present, expired = _token_expired(creds, now)
    # A stored setup-token (~1yr, opaque expiry → usable) enrolls the account on
    # its own — it is the headless-pool path and takes PRECEDENCE over the login
    # creds' short-lived on-disk access-token, so a pool member never flaps to
    # needs_enroll between refreshes. `via` distinguishes the two for the operator.
    has_token = read_account_token(account) is not None
    if has_token:
        via = "setup-token"
    elif present and not expired:
        via = "login"
    elif present and expired:
        return AccountState(
            account=account, kind=ACCT_NEEDS_ENROLL, creds_present=True,
            token_expired=True, detail="oauth token expired — re-enroll/refresh",
        )
    else:
        return AccountState(
            account=account, kind=ACCT_NEEDS_ENROLL, creds_present=False,
            token_expired=False, detail="no token / .credentials.json — run enroll",
        )
    # Enrolled — consult the live probe (injected). A host's probe reads the
    # setup-token sibling when present, else the creds file.
    probe = probe_fn(str(creds), now) if probe_fn is not None else None
    if probe is None:
        # Fail-open: no live signal → assume serving (never manufacture a wall).
        return AccountState(
            account=account, kind=ACCT_SERVING, creds_present=True,
            token_expired=False, detail=f"enrolled via {via} (no live probe — fail-open)",
        )
    util = float(getattr(probe, "utilization", 0.0) or 0.0)
    reset = getattr(probe, "reset_at_epoch", None)
    status = str(getattr(probe, "status", "") or "")
    if not probe.allowed:
        return AccountState(
            account=account, kind=ACCT_WALLED, creds_present=True,
            token_expired=False, probe_status=status, utilization=util,
            reset_at_epoch=reset, detail=f"live-probe rejected (util {util:.0%})",
        )
    kind = ACCT_NEAR_CAP if util >= near_cap_util else ACCT_SERVING
    return AccountState(
        account=account, kind=kind, creds_present=True, token_expired=False,
        probe_status=status, utilization=util, reset_at_epoch=reset,
        detail=f"serving (util {util:.0%})",
    )


# --------------------------------------------------------------------------- #
# The switcher decision.
# --------------------------------------------------------------------------- #
def pick_account(
    accounts: Sequence[Account],
    *,
    probe_fn: ProbeFn | None = None,
    now_epoch: float | None = None,
    policy: RotationPolicy | None = None,
    wait_for_walled: WaitFn | None = None,
) -> Pick:
    """Choose the next account to route Claude Code work to.

    Order of preference:
      1. a SERVING account (probe allowed, utilization < near_cap), roster order;
      2. else a NEAR_CAP account (serving but near the ceiling), lowest util;
      3. else (every enrolled account WALLED) the soonest-resetting walled
         account, with a clamped ``wait_seconds`` so the caller waits rather than
         spins; the Pick is then NOT ``ok``.
      4. else (nothing enrolled at all) an empty Pick naming the enroll gap.

    Fail-open throughout: an account whose probe returns None is SERVING.
    ``wait_for_walled(reset_epoch, now) -> int`` is injected (a host bridges its
    own backoff helper); with none, a pure constant fallback is used so the core
    needs no backoff module.
    """
    now = time.time() if now_epoch is None else now_epoch
    pol = policy or RotationPolicy()
    states = [
        account_state(
            a, probe_fn=probe_fn, now_epoch=now, near_cap_util=pol.near_cap_util
        )
        for a in accounts
    ]

    serving = [s for s in states if s.kind == ACCT_SERVING]
    if serving:
        # Roster order is the operator's preference; keep the first serving one.
        s = serving[0]
        return Pick(account=s.account, reason=f"serving: {s.detail}", states=states)

    near = [s for s in states if s.kind == ACCT_NEAR_CAP]
    if near:
        # All serving accounts are near the cap — take the lowest-utilization one.
        s = min(near, key=lambda x: (x.utilization if x.utilization is not None else 1.0))
        return Pick(
            account=s.account,
            reason=f"near-cap fallback (no account below {pol.near_cap_util:.0%}): {s.detail}",
            states=states,
        )

    walled = [s for s in states if s.kind == ACCT_WALLED]
    if walled:
        # Every enrolled account is walled — pick the soonest to reset and tell
        # the caller how long to wait. NOT ok (caller must wait, not launch).
        if wait_for_walled is None:
            wait_for_walled = _default_wait_for_walled
        with_reset = [s for s in walled if s.reset_at_epoch]
        if with_reset:
            s = min(with_reset, key=lambda x: x.reset_at_epoch or 0)
            reset = s.reset_at_epoch
        else:
            s = walled[0]
            reset = None
        wait = wait_for_walled(reset, now)
        return Pick(
            account=s.account,
            reason=f"all enrolled accounts walled — soonest reset is {s.account.name}",
            states=states,
            wait_seconds=wait,
            soonest_reset_epoch=reset,
        )

    # Nothing enrolled (all needs_enroll / disabled).
    n_enroll = sum(1 for s in states if s.kind == ACCT_NEEDS_ENROLL)
    return Pick(
        account=None,
        reason=(
            f"no enrolled account available ({n_enroll} need enrollment) — "
            "enroll an account once (`claude setup-token`, then store the token)"
            if n_enroll
            else "no accounts in roster"
        ),
        states=states,
    )


def pick_account_spread(
    accounts: Sequence[Account],
    *,
    seat_index: int,
    probe_fn: ProbeFn | None = None,
    now_epoch: float | None = None,
    policy: RotationPolicy | None = None,
    wait_for_walled: WaitFn | None = None,
) -> Pick:
    """Like ``pick_account``, but SPREAD across the serving pool by ``seat_index``.

    ``pick_account`` always returns ``serving[0]`` — correct as a *wall-rotation*
    primitive (rotate OFF a walled account), but it PINS a concurrent burst: every
    launcher resolving the rotation sentinel in its own process lands on the SAME
    first-serving account until it walls, leaving the other windows idle. This is
    the SAME spread the in-batch fleet allocator does, for the per-call rotation
    seam, where each launch is a SEPARATE process with no shared memory — so the
    caller threads a persistent, monotonically-advancing ``seat_index`` (an on-disk
    cursor) and this maps it onto the headroom-ordered serving pool.

    The mapping orders the serving windows by descending headroom (ties broken by
    roster order for determinism), then indexes by ``seat_index % pool_width``. So a
    run of ``seat_index = 0, 1, 2, …`` round-robins across DISTINCT serving windows
    (highest-headroom first).

    Falls through to ``pick_account``'s EXACT behaviour when there is no clean
    spread pool — every account near-cap / walled / unenrolled — so the all-walled
    fail-LOUD wait and the empty-roster fail-open are byte-identical to the
    non-spread path. A single-account pool (width 1) also returns that one account
    for every ``seat_index``, so the spread seam is a no-op until ≥2 accounts serve.
    """
    now = time.time() if now_epoch is None else now_epoch
    pol = policy or RotationPolicy()
    states = _serving_pool_states(
        accounts, probe_fn=probe_fn, now_epoch=now, policy=pol
    )
    # Only spread across CLEANLY-serving windows; a near-cap account is a
    # last-resort tail for the seat count, not a target the per-call rotator should
    # round-robin onto. If nothing serves cleanly, defer entirely to pick_account
    # so the walled/near-cap/empty verdicts stay identical.
    serving = [s for s in states if s.kind == ACCT_SERVING]
    if len(serving) <= 1:
        return pick_account(
            accounts,
            probe_fn=probe_fn,
            now_epoch=now,
            policy=pol,
            wait_for_walled=wait_for_walled,
        )
    # Order the serving windows by descending headroom (the same key the seat
    # allocator interleaves on), ties broken by roster order for determinism.
    def _headroom(s: AccountState) -> float:
        util = s.utilization if s.utilization is not None else 0.0
        return max(_MIN_HEADROOM, 1.0 - float(util))

    ordered = sorted(
        range(len(serving)),
        key=lambda i: (_headroom(serving[i]), -i),
        reverse=True,
    )
    chosen = serving[ordered[seat_index % len(ordered)]]
    return Pick(
        account=chosen.account,
        reason=f"spread seat {seat_index} of {len(ordered)} serving: {chosen.detail}",
        states=states,
    )


def _serving_pool_states(
    accounts: Sequence[Account],
    *,
    probe_fn: ProbeFn | None = None,
    now_epoch: float | None = None,
    policy: RotationPolicy | None = None,
) -> list[AccountState]:
    """The pickable account STATES to spread a fleet across, in spread order
    (clean-serving head in roster order, near-cap tail lowest-util first).

    The shared fold behind ``serving_pool`` (which drops to ``.account``) and
    ``allocate_seats`` (which needs each state's ``utilization`` for the headroom
    weighting). Computing the probe ONCE here keeps the two callers from
    double-probing the network.
    """
    now = time.time() if now_epoch is None else now_epoch
    pol = policy or RotationPolicy()
    states = [
        account_state(
            a, probe_fn=probe_fn, now_epoch=now, near_cap_util=pol.near_cap_util
        )
        for a in accounts
    ]
    serving = [s for s in states if s.kind == ACCT_SERVING]
    near = sorted(
        (s for s in states if s.kind == ACCT_NEAR_CAP),
        key=lambda x: (x.utilization if x.utilization is not None else 1.0),
    )
    return serving + near


def serving_pool(
    accounts: Sequence[Account],
    *,
    probe_fn: ProbeFn | None = None,
    now_epoch: float | None = None,
    policy: RotationPolicy | None = None,
) -> list[Account]:
    """The serving accounts, in roster order — the pool to SPREAD a worker fleet
    across so no single usage window saturates.

    ``pick_account`` answers "which ONE account should the next request use"
    (rotate off a wall). ``serving_pool`` answers the population question a
    supervisor has: "across how many independent windows can I spread N concurrent
    workers". A ramp-to-N pinned to ONE account walls at far fewer than N, yet the
    SAME N spread across the serving pool sit well under each window's ceiling.

    Returns SERVING accounts first, then NEAR_CAP (lowest-util first) as a tail so
    a caller that needs more seats than there are clean-serving windows still
    spreads onto the least-loaded near-cap ones rather than re-piling on the
    serving head. Empty when nothing serves (every account walled / unenrolled) —
    the caller then keeps its current single-account behaviour and lets the
    quota gate handle the wall. Fail-open: an un-probeable account is SERVING.

    For an even round-robin this order is all the caller needs; to fill each
    window in proportion to its REMAINING headroom (so a near-empty account seats
    more workers than a near-cap one), use ``allocate_seats`` instead.
    """
    states = _serving_pool_states(
        accounts, probe_fn=probe_fn, now_epoch=now_epoch, policy=policy
    )
    return [s.account for s in states]


# Minimum headroom credited to any serving account when weighting seats. An
# account probed at/over its window cap still gets a SLIVER of weight so the
# allocator never hands it literally zero seats while it is the only thing
# serving — but far fewer than a near-empty window. Fail-open accounts (no live
# probe) are credited FULL headroom (1.0), exactly as ``pick_account`` treats
# them as cleanly serving.
_MIN_HEADROOM = 0.05


def allocate_seats(
    accounts: Sequence[Account],
    n_workers: int,
    *,
    probe_fn: ProbeFn | None = None,
    now_epoch: float | None = None,
    policy: RotationPolicy | None = None,
) -> list[Account]:
    """Assign ``n_workers`` seats across the serving pool weighted by each
    window's REMAINING HEADROOM — the window-FILL optimisation.

    ``serving_pool`` + a flat ``pool[i % len]`` round-robin gives every window an
    EQUAL number of seats regardless of how full it already is, so an account at
    5% utilisation seats no more workers than one at 85% — the near-cap window
    walls first and the near-empty window stays under-used. This allocator instead
    distributes the ``n_workers`` seats in PROPORTION to each account's headroom
    (``headroom = 1 - utilization``, floored at ``_MIN_HEADROOM``; a fail-open
    no-probe account is credited full headroom 1.0), so a near-empty window seats
    more workers and a near-cap window fewer — maximising total concurrent
    throughput before ANY single window walls.

    Returns a list of length ``min(n_workers, …)`` of ``Account`` objects, ONE per
    worker seat, that the caller indexes by worker position ``i`` (replacing
    ``pool[i % len(pool)]``). The result is ORDERED so consecutive seats round-robin
    across DISTINCT windows first (interleaved by descending headroom) — a fleet
    smaller than the pool still touches the highest-headroom windows, and the cold
    -start stagger still sees each successive worker land on a different account.

    Determinism: integer seats are apportioned by the largest-remainder (Hamilton)
    method, ties broken by pool order — no floating drift, no RNG (``now_epoch`` is
    the only clock and it is injected). Empty pool (nothing serves) or
    ``n_workers <= 0`` → ``[]``, so the caller keeps its single-account default.
    """
    if n_workers <= 0:
        return []
    states = _serving_pool_states(
        accounts, probe_fn=probe_fn, now_epoch=now_epoch, policy=policy
    )
    if not states:
        return []
    # Headroom weight per serving account (fail-open None util → full headroom).
    weights: list[float] = []
    for s in states:
        util = s.utilization if s.utilization is not None else 0.0
        weights.append(max(_MIN_HEADROOM, 1.0 - float(util)))
    total_w = sum(weights)
    if total_w <= 0:  # pragma: no cover — _MIN_HEADROOM keeps this positive
        total_w = float(len(states))
        weights = [1.0] * len(states)
    # Largest-remainder apportionment of n_workers across the weights.
    exact = [n_workers * w / total_w for w in weights]
    floors = [int(x) for x in exact]
    assigned = sum(floors)
    remainder = n_workers - assigned
    # Hand the leftover seats to the largest fractional parts (ties: pool order).
    order = sorted(
        range(len(states)),
        key=lambda i: (exact[i] - floors[i], weights[i]),
        reverse=True,
    )
    seats = list(floors)
    for k in range(remainder):
        seats[order[k % len(order)]] += 1
    # Build the per-seat account list, interleaved across windows by descending
    # headroom so a fleet smaller than its seat budget still spreads to the
    # highest-headroom windows and consecutive workers land on distinct accounts.
    by_headroom = sorted(
        range(len(states)), key=lambda i: weights[i], reverse=True
    )
    remaining = {i: seats[i] for i in range(len(states))}
    out: list[Account] = []
    while len(out) < n_workers and any(remaining.values()):
        for i in by_headroom:
            if remaining[i] > 0:
                out.append(states[i].account)
                remaining[i] -= 1
                if len(out) >= n_workers:
                    break
    return out


def _default_wait_for_walled(reset_epoch: Optional[int], now_epoch: float) -> int:
    """Pure fallback wait when no host ``wait_for_walled`` is injected.

    Waits until the parsed reset (clamped to a sane floor), or a typical Max
    window when there is no reset hint. A host with a real backoff helper passes
    its own ``wait_for_walled`` to ``pick_account`` instead of relying on this.
    """
    if not reset_epoch:
        return _NO_HINT_WAIT_SECONDS
    delta = int(reset_epoch - now_epoch)
    if delta <= 0:
        return 0  # already reset — re-probe immediately
    return delta


# --------------------------------------------------------------------------- #
# Env overrides for the chosen account — the launcher contract.
# --------------------------------------------------------------------------- #
def env_for(
    account: Account,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The child-process env overrides to run Claude Code as this account.

    Emits ``CLAUDE_CONFIG_DIR`` (byte-identical to the launcher contract). The
    OAuth token is spliced as ``CLAUDE_CODE_OAUTH_TOKEN`` ONLY for a token-only dir
    — one whose ``.credentials.json`` is absent or expired — so a ``-p`` child there
    does not auth as ``apiKeySource=none`` ("Not logged in"). When a present+unexpired
    ``.credentials.json`` IS there, this emits config-dir only and defers to that
    file: it is Claude's own auto-refreshing source, so a token or login refresh
    PROPAGATES to every live session reading it — whereas a static env token would
    shadow the file and freeze the session at its launch-time creds (a running
    process's env cannot be updated). A ``login``-enrolled account thus emits
    config-dir only. Raises ``OriginError`` if the config dir is missing (the
    account isn't enrolled) — a loud failure is correct here because the caller
    asked to launch AS this account.
    """
    env = account_env_overrides(account, environ=environ)
    # Defer to an auto-refreshing creds file when one can serve (see docstring);
    # splice the durable static token only for a token-only / expired-creds dir.
    if "CLAUDE_CODE_OAUTH_TOKEN" not in env and not _has_fresh_login_creds(account):
        token = read_account_token(account)
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


# --------------------------------------------------------------------------- #
# Enrollment recipe — the one-time login bootstrap for an account.
# --------------------------------------------------------------------------- #
# The two supported login methods. `setup-token` mints a ~1-year token via a
# single browser approval — the right default for the switcher's purpose (a
# NON-INTERACTIVE reuse pool a headless `claude -p` child rotates through);
# `login` runs an interactive `claude` session the operator drives with /login.
ENROLL_METHOD_SETUP_TOKEN = "setup-token"
ENROLL_METHOD_LOGIN = "login"
ENROLL_METHODS = (ENROLL_METHOD_SETUP_TOKEN, ENROLL_METHOD_LOGIN)


def enroll_recipe(
    account: Account,
    *,
    method: str = ENROLL_METHOD_SETUP_TOKEN,
    save_token_cmd: str = "<store the printed token for {name}>",
) -> list[str]:
    """The exact PowerShell lines to log this account into Claude Code once.

    Returns a list of lines (comments + the two commands) the operator runs in
    their own terminal — an OAuth login needs a browser + an interactive TTY, so
    it cannot be driven from inside a tool/pipe; the honest move is to hand the
    operator the precise recipe.

    Enrollment makes ``account_state`` flip the account from ``needs_enroll`` to
    ``serving``. The two methods persist DIFFERENT artifacts (this is the whole
    reason a setup-token account needs its own store — ``setup-token`` writes no
    creds file):
      * ``setup-token`` (default) → ``claude setup-token`` mints a ~1-yr token and
        PRINTS it (writes no ``.credentials.json``); the recipe then stores it to
        ``<config_dir>/.oauth-token`` — the durable headless-pool path the switcher
        reads. The host supplies the exact store command via ``save_token_cmd``
        (``{name}`` is substituted), since that verb is host-specific;
      * ``login`` → ``claude`` (the operator runs ``/login`` then exits), which
        writes ``<config_dir>/.credentials.json`` (short-lived, auto-refreshed).
    Raises ValueError on an unknown method (a typo must not silently emit the
    wrong command). The config dir is ``~``-expanded so the line is copy-pasteable.
    """
    if method not in ENROLL_METHODS:
        raise ValueError(
            f"unknown enroll method {method!r}; choose from {ENROLL_METHODS}"
        )
    cfg = str(Path(account.config_dir).expanduser())
    prov = f" (source claude.ai login: Chrome '{account.chrome_profile}')" if account.chrome_profile else ""
    lines = [
        f"# enroll {account.name}{prov}",
        f'$env:CLAUDE_CONFIG_DIR = "{cfg}"',
    ]
    if method == ENROLL_METHOD_SETUP_TOKEN:
        lines.append("claude setup-token   # approve in the browser; mints + prints a ~1-yr token")
        lines.append(
            save_token_cmd.format(name=account.name)
            + "   # store the printed token (read it from stdin, never argv/history)"
        )
    else:
        lines.append("claude   # then run /login with this account, and exit")
    return lines


__all__ = [
    "Account",
    "AccountState",
    "Pick",
    "RosterDefaults",
    "RotationPolicy",
    "ProbeFn",
    "ProbeLike",
    "WaitFn",
    "OriginError",
    "ACCOUNTS_FILE_ENV",
    "DEFAULT_ACCOUNTS_FILE",
    "ACCT_DISABLED",
    "ACCT_NEAR_CAP",
    "ACCT_NEEDS_ENROLL",
    "ACCT_SERVING",
    "ACCT_WALLED",
    "ENROLL_METHOD_LOGIN",
    "ENROLL_METHOD_SETUP_TOKEN",
    "ENROLL_METHODS",
    "TokenError",
    "accounts_file_path",
    "account_creds_path",
    "account_env_overrides",
    "account_settings_path",
    "account_state",
    "account_token_path",
    "allocate_seats",
    "enroll_recipe",
    "env_for",
    "load_roster",
    "load_roster_defaults",
    "merge_account_settings",
    "pick_account",
    "pick_account_spread",
    "seed_account_settings",
    "serving_pool",
    "read_account_token",
    "write_account_token",
]
