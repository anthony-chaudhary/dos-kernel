"""account_auth — the vendor-neutral seam that makes the account switcher AGENT-pluggable.

> **The pick is agent-blind; only the AUTH names a vendor (docs/386).** The seat
> picker (`drivers/account_switcher.pick_account` / `serving_pool` / `allocate_seats`)
> already reasons over `Account` / `AccountState` and names no vendor — a roster of
> seats ranks the same whether the worker is Claude Code, Codex CLI, or Gemini CLI.
> What differs per agent is only the AUTH glue: which env var names the isolated
> config dir, which env var a static token splices into, what a stored token looks
> like, where the auto-refreshing creds live, how you enroll. This module lifts that
> glue into one typed spec so a new agent is a `dos.account_auth` plugin, never a
> switcher edit — the same pure-protocol + by-name-resolver pattern as `dos.judges`
> (the JUDGE rung), `dos.hook_dialects` (the output envelope), and `dos.vcs` (the
> VCS read).

Why this is a seam, not a switch
================================

The switcher bakes Claude's literals as code identifiers (``CLAUDE_CONFIG_DIR``,
``CLAUDE_CODE_OAUTH_TOKEN``, ``.oauth-token``, ``sk-ant-oat``, ``.credentials.json``).
That is correct for a Claude driver but blocks a Codex/Gemini fleet from reusing the
same seat-spread machinery. An ``AccountAuthSpec`` carries those literals as DATA,
resolved by ``agent_kind``, so the host names its agent once (``dos.toml [agent]
kind`` or a per-account roster field) and the same ``dos accounts`` verbs emit the
RIGHT env for whatever it runs.

The kernel names no vendor (the litmus): this module defines the typed spec + the
by-name RESOLVER only. The per-agent specs — each of which inherently names its
vendor's env vars — live in ``dos.drivers.agent_auth.*`` (the driver tier IS the
home for vendor names) and register through the ``dos.account_auth`` entry-point
group. ``DEFAULT_AGENT_KIND = "claude"`` is the in-tree default NAME (the analogue
of ``vcs_backend="git"`` / ``DEFAULT_DIALECT="claude-code"``), resolved to the
shipped ``dos.drivers.agent_auth.claude`` spec, not a literal env var here.

Fail-LOUD on an unknown kind (the ``resolve_dialect`` discipline, not the
fail-to-abstain of ``judges``): a host that asked for ``gemini`` and silently got
``claude`` would launch a worker under the WRONG identity's env — the exact
wrong-seat hazard this seam exists to prevent. So an unknown ``agent_kind`` raises
with the known set, never a silent default.

PURE — the spec is frozen data + pure methods (no I/O, no clock). Resolution does
boundary I/O (an importlib lookup) exactly like the other by-name seams.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

#: The default agent NAME (resolved to the in-tree ``dos.drivers.agent_auth.claude``
#: spec). A string default, the ``vcs_backend="git"`` / ``DEFAULT_DIALECT`` analogue —
#: not a vendor literal the kernel acts on, just the name the resolver looks up.
DEFAULT_AGENT_KIND = "claude"


@dataclass(frozen=True)
class AccountAuthSpec:
    """The per-agent auth glue, as typed data — everything the switcher's vendor
    literals encoded, lifted to one resolvable spec.

      ``agent_kind``       — the name a roster / config selects this spec by.
      ``config_dir_env``   — the env var that points the agent at an ISOLATED config
                             dir (Claude ``CLAUDE_CONFIG_DIR``, Codex ``CODEX_HOME``).
                             ``""`` when the agent has no config-dir-override env var
                             (Gemini today) → per-account env isolation is unsupported
                             for it, surfaced honestly via ``supports_config_dir_isolation``.
      ``token_env``        — the env var a STATIC token splices into for a headless
                             child (Claude ``CLAUDE_CODE_OAUTH_TOKEN``, Codex/Gemini
                             the provider API-key var). ``""`` = no static-token splice.
      ``token_file_name``  — a sibling file storing a durable token inside the config
                             dir (Claude ``.oauth-token``). ``""`` = no sibling store.
      ``creds_file_name``  — the agent's OWN auto-refreshing credential file (Claude
                             ``.credentials.json``, Codex ``auth.json``). ``""`` = none.
      ``token_prefix``     — the required prefix of a pasted token (Claude
                             ``sk-ant-oat``). ``""`` = accept any non-empty token (do
                             not reject a shape this module can't know).
      ``enroll_methods``   — the enroll flow labels this agent supports.
      ``enroll_hint``      — a one-line copy-pasteable how-to-enroll for the operator.

    The methods are PURE: token validation and env-building from the data, no disk.
    The agent's richer, disk-reading behaviors (Claude's docs/380 fresh-creds
    deferral) stay in the agent's own driver, which this spec can carry a hook to.
    """

    agent_kind: str
    config_dir_env: str
    token_env: str = ""
    token_file_name: str = ""
    creds_file_name: str = ""
    token_prefix: str = ""
    enroll_methods: tuple[str, ...] = ()
    enroll_hint: str = ""
    #: An OPTIONAL agent-specific, disk-aware launch-env builder ``(account) -> dict``
    #: that SUPERSEDES the generic ``env_overrides`` when present — the seam by which
    #: an agent whose launch identity needs more than the data fields (Claude's
    #: docs/380 deferral to a fresh ``.credentials.json``, so a static token never
    #: shadows a live session) plugs its own builder WITHOUT the consumer branching on
    #: the agent's NAME (the vendor-blindness litmus: a host reads this CAPABILITY, not
    #: ``if kind == "…"``). Set by the agent's driver (it may reference the switcher);
    #: None for an agent that uses the pure generic builder. Carries no vendor name.
    launch_env_fn: Optional[Callable[[object], dict]] = None

    @property
    def supports_config_dir_isolation(self) -> bool:
        """True when this agent can be pinned to an isolated config dir via env.

        False (e.g. Gemini, which has no config-dir-override env var) means a host
        cannot give each account its own dir purely through ``env_overrides`` — it
        must isolate another way (a wrapper, a separate ``HOME``). Surfaced so the
        gap is VISIBLE, never silently emitting an env that does not isolate."""
        return bool(self.config_dir_env)

    def validate_token(self, token: Optional[str]) -> bool:
        """True iff ``token`` is a plausibly-valid token for this agent. PURE.

        A non-empty string passing the ``token_prefix`` check (or any non-empty
        string when no prefix is declared — refusing to reject a shape this seam
        cannot know). The agent's own enroll path still owns the real validation;
        this is the cheap pre-check that stops an obvious bad paste."""
        token = (token or "").strip()
        if not token:
            return False
        return token.startswith(self.token_prefix) if self.token_prefix else True

    def env_overrides(
        self, config_dir: str, *, token: Optional[str] = None
    ) -> dict[str, str]:
        """The child-process env to launch as one account of this agent. PURE.

        Emits ``config_dir_env -> config_dir`` (when the agent supports it) and, when
        a ``token`` is supplied AND the agent has a ``token_env``, splices it so a
        headless child authenticates. This is the GENERIC builder; an agent whose
        launch identity needs disk-aware logic (Claude deferring to a fresh creds
        file, docs/380) keeps that in its own driver and the CLI routes to it — this
        method is the floor every agent shares."""
        out: dict[str, str] = {}
        if self.config_dir_env:
            out[self.config_dir_env] = config_dir
        if token and self.token_env:
            out[self.token_env] = token
        return out


# --------------------------------------------------------------------------- #
# By-name resolution — in-tree driver first, then the dos.account_auth group.
# Fail-LOUD on an unknown kind (the resolve_dialect discipline). The kernel
# imports no agent spec statically; it resolves one by name at the boundary,
# exactly like cli._load_account_switcher and hook_dialect._load_plugin_dialect.
# --------------------------------------------------------------------------- #
_INTREE_PKG = "dos.drivers.agent_auth"
_ENTRY_GROUP = "dos.account_auth"


def _intree_spec(name: str) -> Optional[AccountAuthSpec]:
    """Load the in-tree spec ``dos.drivers.agent_auth.<name>:SPEC``, or None."""
    import importlib

    try:
        mod = importlib.import_module(f"{_INTREE_PKG}.{name}")
    except ModuleNotFoundError:
        return None
    spec = getattr(mod, "SPEC", None)
    return spec if isinstance(spec, AccountAuthSpec) else None


def _iter_entry_points():
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - very old Python
        return []
    try:
        eps = entry_points()
        if hasattr(eps, "select"):
            return list(eps.select(group=_ENTRY_GROUP))
        return list(eps.get(_ENTRY_GROUP, []))  # type: ignore[attr-defined]
    except Exception:
        return []


def _plugin_spec(name: str) -> Optional[AccountAuthSpec]:
    for ep in _iter_entry_points():
        if ep.name != name:
            continue
        try:
            obj = ep.load()
            spec = obj() if callable(obj) and not isinstance(obj, AccountAuthSpec) else obj
        except Exception:
            return None
        return spec if isinstance(spec, AccountAuthSpec) else None
    return None


def _intree_names() -> list[str]:
    """The agent names shipped in-tree (``dos.drivers.agent_auth.*`` with a SPEC)."""
    import importlib
    import pkgutil

    try:
        pkg = importlib.import_module(_INTREE_PKG)
    except Exception:
        return []
    names: list[str] = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        if _intree_spec(info.name) is not None:
            names.append(info.name)
    return names


def available_account_auths() -> list[str]:
    """Every resolvable ``agent_kind`` — in-tree drivers + ``dos.account_auth`` plugins."""
    names = set(_intree_names())
    try:
        names.update(ep.name for ep in _iter_entry_points())
    except Exception:
        pass
    return sorted(names)


def resolve_account_auth(name: Optional[str]) -> AccountAuthSpec:
    """Resolve an ``agent_kind`` to its :class:`AccountAuthSpec`. RAISES on unknown.

    ``None``/empty → the default (``claude``). A name → the in-tree
    ``dos.drivers.agent_auth.<name>`` spec, else a ``dos.account_auth`` plugin. An
    UNKNOWN name → ``ValueError`` listing the known set — NEVER a silent fallback to
    the default, because launching a worker under the wrong agent's env (a Gemini
    seat resolved as Claude) is the wrong-identity hazard this seam prevents (the
    ``resolve_dialect`` fail-LOUD discipline, not ``judges``' fail-to-abstain)."""
    name = (name or DEFAULT_AGENT_KIND).strip().lower()
    spec = _intree_spec(name) or _plugin_spec(name)
    if spec is not None:
        return spec
    known = ", ".join(available_account_auths()) or DEFAULT_AGENT_KIND
    raise ValueError(
        f"unknown agent_kind {name!r} — known: {known} (or a registered "
        f"{_ENTRY_GROUP} plugin). Refusing to fall back to {DEFAULT_AGENT_KIND!r}: "
        f"a wrong agent's auth env launches a worker under the wrong identity."
    )


__all__ = [
    "AccountAuthSpec",
    "DEFAULT_AGENT_KIND",
    "available_account_auths",
    "resolve_account_auth",
]
