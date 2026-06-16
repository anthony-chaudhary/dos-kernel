"""The host-policy DRIVER seam — Axis 1 of hackability made third-party-pluggable.

Why this exists
===============

A **host-policy driver** is the dull-but-load-bearing kind of driver: the *policy* a
particular host repo supplies on top of the kernel *mechanism* — which lanes exist, how
they admit concurrency, where its plans and ship-state live. ``dos.drivers.job`` (the
reference userland app) and ``dos.drivers.workshop`` (the copy-me template) are the
in-tree ones. A driver is two pieces: a ``LaneTaxonomy`` constant and a
``<name>_config(workspace) -> SubstrateConfig`` factory (see
``dos.drivers.workshop``).

For every OTHER seam (judges, exporters, notifiers, predicates, evidence sources, hook
dialects, …) a third party can ship an occupant in their own pip package and DOS
discovers it by name through a ``dos.<seam>`` entry-point group — built-ins first
(unshadowable), then the entry-point group, fail-loud on an unknown name. The
host-policy driver was the ONE seam that lacked this: it was loaded ONLY by
``importlib.import_module(f"dos.drivers.{name}")``, hard-bolted to the kernel's own
package, so the documented "new host = a new driver" onramp meant *fork the kernel*.

This module closes that gap. It is the resolver the CLI delegates ``--driver <name>``
(and ``dos init --example <name>``) to, with the SAME shape ``dos.judges`` already has:

  1. **In-tree first, unshadowable.** ``dos.drivers.<name>`` with a ``<name>_config``
     factory resolves FIRST and returns immediately — a third-party plugin named
     ``workshop`` can never displace the reference one.
  2. **Then the ``dos.drivers`` entry-point group.** A third party registers
     ``acme = "acme_pkg:acme_config"`` in their ``[project.entry-points."dos.drivers"]``;
     the entry point points DIRECTLY at the factory (no re-derived ``<name>_config``
     attribute, unlike the in-tree convention — packaging already carries the
     ``module:attr`` target).
  3. **Fail-loud on a miss.** Driver selection is a SELECTOR — the operator typed
     ``--driver acme`` and meant it — so an unknown name raises ``ValueError`` with the
     known list, never a silent degrade. (Contrast the witness/oracle drivers, which are
     fail-SAFE adjudicators resolved elsewhere; those stay in ``cli`` untouched.)

Layering: this is a kernel helper (layer 1/3). It imports stdlib + ``dos.config`` and
discovers drivers BY NAME at the call boundary — it never ``import``s ``dos.drivers``
statically, so the one-way arrow (``test_no_kernel_module_imports_a_driver``) stays
green, exactly as ``dos.judges`` does.

The SELF_MODIFY guarantee stays KERNEL-OWNED
============================================

A host-policy factory MUST gather workspace facts onto its config so the SELF_MODIFY
guard is workspace-scoped (``dos.drivers.workshop.workshop_config`` explains why: absent
facts force the guard to the conservative full static set and wrongly refuse a
whole-repo exclusive lane in a foreign repo). The in-tree factories do this. A
third-party factory might forget. So the resolver does NOT trust the plugin: if the
resolved config came back with ``workspace is None``, the resolver attaches
``gather_workspace_facts`` itself before returning. The guard's blast-radius scope is the
kernel's to set, never a plugin author's.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from dos.config import (
    LaneTaxonomy,
    SubstrateConfig,
    gather_workspace_facts,
    resolve_workspace_root,
)

# The entry-point group a third-party host-policy driver registers under. The same
# `dos.<seam>` shape as `dos.judges` / `dos.exporters` / `dos.notifiers`.
DRIVER_ENTRY_POINT_GROUP = "dos.drivers"


def _reject_non_single_token(name: str) -> None:
    """Raise the canonical 'single module token' error for a dotted / path-y name.

    A driver name is a single module token (no ``.``, ``/`` or ``\\``) — both a safety
    surface against path traversal and what keeps the in-tree ``import_module`` branch
    honest. This is applied to the IN-TREE branch only; an entry-point name is matched by
    ``ep.name ==`` and may carry a hyphen (``acme-driver``), so it is NOT validated here.
    The message wording is load-bearing (``tests/test_drivers_workshop.py`` asserts the
    'single module token' substring)."""
    if "." in name or "/" in name or "\\" in name:
        raise ValueError(
            f"unknown driver {name!r}: a driver name is a single module token "
            f"(no '.', '/' or '\\'); drivers live in src/dos/drivers/, see "
            f"dos.drivers.workshop for a template, or register a third-party driver "
            f"under the '{DRIVER_ENTRY_POINT_GROUP}' entry-point group")


def _load_in_tree(name: str):
    """Resolve the in-tree ``dos.drivers.<name>`` module, or ``None`` if genuinely absent.

    Returns the imported module on success. Returns ``None`` ONLY when the module does
    not exist (so the caller can fall through to the entry-point group). A driver's OWN
    broken internal import is re-raised (never masked as 'no such driver') — a genuine
    bug in a driver fails loud, exactly as the prior ``_resolve_driver_config`` did."""
    import importlib

    try:
        return importlib.import_module(f"dos.drivers.{name}")
    except ModuleNotFoundError as e:
        # Only "the driver module itself is missing" falls through; a driver importing a
        # missing dependency of its own re-raises (its bug, not a missing driver).
        if (e.name or "") in (f"dos.drivers.{name}", "dos.drivers"):
            return None
        raise


def _discover_entry_point_drivers(*, _stderr=None) -> dict[str, object]:
    """Every ``dos.drivers`` plugin as ``{name: factory_object}``.

    A plugin registers ``name = "pkg.module:factory"`` under
    ``[project.entry-points."dos.drivers"]``; ``ep.load()`` yields the factory object
    (a callable ``(workspace) -> SubstrateConfig``, or a class). A plugin that fails to
    load is SKIPPED with a one-line stderr note rather than crashing the call — the same
    posture ``judges._discover_entry_point_judges`` / exporter / notifier discovery take
    (a broken third-party plugin is the operator's to fix, not a kernel fault). Does
    entry-point I/O, so it is a call-boundary helper, never called inside a verdict."""
    stderr = _stderr if _stderr is not None else sys.stderr
    out: dict[str, object] = {}
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    try:
        eps = entry_points(group=DRIVER_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(DRIVER_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash a call
        return out
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            out[ep.name] = ep.load()
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(
                f"warning: driver plugin {ep.name!r} failed to load ({e}); skipping",
                file=stderr,
            )
            continue
    return out


def _factory_takes_arg(factory: object) -> bool:
    """Does ``factory`` accept a positional ``workspace`` argument?

    Decided by SIGNATURE INTROSPECTION, not by calling-and-catching ``TypeError`` — a
    catch-retry would mask a genuine ``TypeError`` raised INSIDE the factory body (e.g. a
    plugin building a malformed ``SubstrateConfig``) by re-invoking it zero-arg and
    surfacing a confusing second error. We look at the parameters: a factory with at
    least one positional-or-keyword/positional-only/var-positional parameter takes the
    workspace; a strictly zero-arg callable does not. On any introspection failure we
    assume it takes the arg (the in-tree convention), which is the common case."""
    import inspect

    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return True
    for p in params.values():
        if p.kind in (
            p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL,
        ):
            return True
    return False


def _call_factory(factory: object, workspace: Path | str | None):
    """Invoke a resolved driver factory with ``workspace`` → a ``SubstrateConfig``.

    A host-policy factory is a CALLABLE taking the workspace (the in-tree convention is a
    ``<name>_config(workspace)`` function; a third party may register a function or a
    class). We pass ``workspace`` iff the signature accepts a positional argument; a rare
    zero-arg occupant (does its own workspace resolution) is called bare. A ``TypeError``
    raised inside the factory body propagates UNMASKED — it is the plugin's bug, fail
    loud."""
    if not callable(factory):
        raise ValueError(
            f"driver factory {factory!r} is not callable; register "
            f"'name = \"pkg.module:factory\"' where factory is a "
            f"(workspace) -> SubstrateConfig callable")
    if _factory_takes_arg(factory):
        return factory(workspace)
    return factory()


def _ensure_facts(cfg: SubstrateConfig, workspace: Path | str | None) -> SubstrateConfig:
    """Attach workspace facts if the factory left them unset — KERNEL-OWNED guard scope.

    A third-party factory that forgot ``gather_workspace_facts`` would leave
    ``cfg.workspace is None``, which forces the SELF_MODIFY guard to the conservative
    full static set (``dos.drivers.workshop`` documents this hazard). The resolver — not
    the plugin — owns the guard's scope, so we fill it in here rather than trust the
    author. A factory that already gathered facts is returned untouched."""
    if getattr(cfg, "workspace", None) is not None:
        return cfg
    root = resolve_workspace_root(workspace)
    return dataclasses.replace(cfg, workspace=gather_workspace_facts(root))


def resolve_driver_config(
    name: str, workspace: Path | str | None = None, *, _stderr=None
) -> SubstrateConfig:
    """Resolve a host-policy driver by name → a ``SubstrateConfig``.

    Built-ins (``dos.drivers.<name>`` with a ``<name>_config`` factory) resolve FIRST and
    cannot be shadowed by a plugin of the same name — the trusted-fallback guarantee,
    identical to ``resolve_judge``. Then the ``dos.drivers`` entry-point group is
    consulted by name. An unknown name fails LOUD with the known list (a typo'd
    ``--driver`` is an operator error, never a silent degrade). The returned config is
    guaranteed to carry workspace facts (the kernel-owned SELF_MODIFY guard scope)."""
    # 1. In-tree first (unshadowable). The single-token validator guards this branch.
    _reject_non_single_token(name)
    mod = _load_in_tree(name)
    if mod is not None:
        factory = getattr(mod, f"{name}_config", None)
        if factory is None:
            raise ValueError(
                f"driver {name!r} (dos.drivers.{name}) exposes no "
                f"{name}_config(workspace) factory")
        return _ensure_facts(_call_factory(factory, workspace), workspace)

    # 2. The dos.drivers entry-point group (third-party packages).
    discovered = _discover_entry_point_drivers(_stderr=_stderr)
    if name in discovered:
        return _ensure_facts(_call_factory(discovered[name], workspace), workspace)

    # 3. Miss — fail loud with the known list (built-in template + any discovered).
    known = sorted({"workshop", "job", *discovered})
    raise ValueError(
        f"unknown driver {name!r} (no module dos.drivers.{name}); drivers live in "
        f"src/dos/drivers/, see dos.drivers.workshop for a template, or register a "
        f"third-party driver under the '{DRIVER_ENTRY_POINT_GROUP}' entry-point group. "
        f"known: {', '.join(known)}")


def resolve_driver_taxonomy(name: str, *, _stderr=None) -> LaneTaxonomy:
    """The ``LaneTaxonomy`` of a named driver pack — built-in first, then ``dos.drivers``.

    The same two-stage resolution as ``resolve_driver_config``, returning the resolved
    config's ``.lanes`` — what ``dos init --example <name>`` serializes into a
    scaffolded ``dos.toml``. Built-ins are unshadowable; an unknown name fails loud."""
    return resolve_driver_config(name, None, _stderr=_stderr).lanes


def _in_tree_host_pack_names() -> list[str]:
    """The in-tree ``dos/drivers/*.py`` stems that ARE host-policy packs, sorted.

    A host-policy pack is DEFINED by exposing a ``<stem>_config`` factory; a driver that
    plugs into a DIFFERENT seam (an adjudicator, a transport, a witness source) has no
    such factory, so it is excluded automatically — no hand-kept exclusion list to rot,
    and (deliberately) NO name of another seam's driver is written into this kernel
    module, keeping the by-substring layering litmi
    (`test_kernel_does_not_import_a_log_driver` et al.) green.

    The check is a cheap SOURCE scan for the factory symbol ``<stem>_config`` — it
    matches both a module that ``def``s the factory and one that re-exports it from
    ``dos.config`` (the ``job`` pack's shape). It does NOT import the driver module (a
    kernel helper importing every driver would be the coupling the one-way arrow forbids);
    the factory is imported only later, by ``resolve_driver_config``, and only for the ONE
    name an operator asked for."""
    import re

    names: list[str] = []
    drivers_dir = Path(__file__).parent / "drivers"
    if not drivers_dir.is_dir():
        return names
    for py in sorted(drivers_dir.glob("*.py")):
        stem = py.stem
        if stem.startswith("_"):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable file, skip
            continue
        # The factory symbol `<stem>_config` as a whole word — matches `def job_config`,
        # `from dos.config import job_config`, and a `__all__` listing it alike.
        if re.search(rf"\b{re.escape(stem)}_config\b", text):
            names.append(stem)
    return names


def active_driver_names(*, _stderr=None) -> list[str]:
    """Every resolvable host-policy driver name — in-tree packs THEN discovered plugins.

    In-tree names are the ``dos/drivers/*.py`` stems that define a ``<stem>_config``
    module-level factory (which excludes the adjudicator/transport/witness drivers that
    plug into their OWN seams). Powers ``dos plugins`` and ``dos doctor``'s host-policy
    roster. Does directory + entry-point I/O, so it is a call-boundary helper, never
    called inside a verdict."""
    names = _in_tree_host_pack_names()
    discovered = sorted(_discover_entry_point_drivers(_stderr=_stderr))
    # In-tree first (unshadowable), then any third-party names not already in-tree.
    for n in discovered:
        if n not in names:
            names.append(n)
    return names


__all__ = [
    "DRIVER_ENTRY_POINT_GROUP",
    "resolve_driver_config",
    "resolve_driver_taxonomy",
    "active_driver_names",
]
