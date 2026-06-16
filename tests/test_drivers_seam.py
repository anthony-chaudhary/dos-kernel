"""The host-policy DRIVER seam (`dos.drivers` entry-point group) — third-party-pluggable.

DOS had ONE seam a third party could not reach without forking the kernel: the
host-policy driver (`--driver <name>`), loaded by a hardcoded
`importlib.import_module(f"dos.drivers.{name}")`. `dos.drivers_seam` gives it the same
shape every OTHER seam already has — in-tree first (unshadowable), then the `dos.drivers`
entry-point group, fail-loud on a miss — so `acme-dos-driver` (a separate pip package)
can register `acme = "acme_pkg:acme_config"` and `dos --driver acme` resolves with no
kernel edit.

These tests pin: (1) in-tree packs still resolve and still carry workspace facts;
(2) a registered plugin resolves to its factory; (3) an in-tree name SHADOWS a same-named
plugin; (4) an unknown name fails loud KEEPING the legacy error substrings; (5) the
kernel attaches facts when a forgetful third-party factory leaves them unset;
(6) `dos.drivers` is a regular (non-namespace) package, the precondition the
unshadowability guarantee rests on.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from dos import drivers_seam
from dos.config import LaneTaxonomy, PathLayout, SubstrateConfig
from dos.drivers.workshop import WORKSHOP_LANE_TAXONOMY


def _acme_config(workspace=None):
    """A stand-in third-party host-policy factory: a real SubstrateConfig with a
    distinctive single-lane taxonomy. DELIBERATELY leaves workspace=None (facts unset) so
    we can assert the kernel fills them in. Provides `paths` (required) via PathLayout."""
    root = Path(workspace) if workspace is not None else Path("C:/tmp/acme-ws")
    return SubstrateConfig(
        lanes=LaneTaxonomy(
            concurrent=("acme",), exclusive=("global",), autopick=("acme",),
            trees={"acme": ("acme/**/*",), "global": ("**/*",)},
        ),
        paths=PathLayout.for_root(root),
        workspace=None,
    )


# --------------------------------------------------------------------------- #
# 1. in-tree packs resolve, unchanged, and carry workspace facts
# --------------------------------------------------------------------------- #

def test_in_tree_workshop_resolves():
    cfg = drivers_seam.resolve_driver_config("workshop", "C:/tmp/foreign-ws")
    assert isinstance(cfg, SubstrateConfig)
    assert cfg.lanes.tree_for("frontend") == WORKSHOP_LANE_TAXONOMY.tree_for("frontend")


def test_in_tree_job_resolves():
    cfg = drivers_seam.resolve_driver_config("job", "C:/tmp/foreign-ws")
    assert isinstance(cfg, SubstrateConfig)


def test_in_tree_config_carries_workspace_facts():
    """The SELF_MODIFY guard needs the config to carry workspace facts; the in-tree
    factory gathers them and the seam never strips them."""
    cfg = drivers_seam.resolve_driver_config("workshop", "C:/tmp/foreign-ws")
    assert cfg.workspace is not None


def test_taxonomy_resolves_in_tree():
    tax = drivers_seam.resolve_driver_taxonomy("workshop")
    assert isinstance(tax, LaneTaxonomy)
    assert "frontend" in tax.trees


# --------------------------------------------------------------------------- #
# 2. a registered dos.drivers plugin resolves to its factory
# --------------------------------------------------------------------------- #

def test_plugin_name_resolves_to_factory(monkeypatch):
    monkeypatch.setattr(
        drivers_seam, "_discover_entry_point_drivers",
        lambda *, _stderr=None: {"acme": _acme_config},
    )
    cfg = drivers_seam.resolve_driver_config("acme", "C:/tmp/foreign-ws")
    assert isinstance(cfg, SubstrateConfig)
    assert "acme" in cfg.lanes.trees


def test_plugin_taxonomy_resolves(monkeypatch):
    monkeypatch.setattr(
        drivers_seam, "_discover_entry_point_drivers",
        lambda *, _stderr=None: {"acme": _acme_config},
    )
    tax = drivers_seam.resolve_driver_taxonomy("acme")
    assert list(tax.concurrent) == ["acme"]


def test_zero_arg_plugin_factory_resolves(monkeypatch):
    """An entry point may point at a zero-arg callable that does its own workspace
    resolution; the seam calls it bare (signature introspection, not catch-retry)."""
    monkeypatch.setattr(
        drivers_seam, "_discover_entry_point_drivers",
        lambda *, _stderr=None: {"acme": lambda: _acme_config(None)},
    )
    cfg = drivers_seam.resolve_driver_config("acme", "C:/tmp/foreign-ws")
    assert "acme" in cfg.lanes.trees


def test_factory_internal_typeerror_propagates_unmasked(monkeypatch):
    """A TypeError raised INSIDE a one-arg factory body must propagate, not be masked by
    a zero-arg retry — the regression the signature-introspection design prevents."""
    def broken(workspace=None):
        raise TypeError("plugin built a malformed config")

    monkeypatch.setattr(
        drivers_seam, "_discover_entry_point_drivers",
        lambda *, _stderr=None: {"acme": broken},
    )
    with pytest.raises(TypeError, match="malformed config"):
        drivers_seam.resolve_driver_config("acme", "C:/tmp/foreign-ws")


# --------------------------------------------------------------------------- #
# 3. an in-tree name is UNSHADOWABLE by a same-named plugin
# --------------------------------------------------------------------------- #

def test_in_tree_name_shadows_plugin(monkeypatch):
    """A third-party plugin registered as `workshop` must NOT displace the reference
    in-tree pack — built-ins resolve first (the trusted-fallback guarantee, identical to
    judges/renderers)."""
    def evil(workspace=None):
        return SubstrateConfig(
            lanes=LaneTaxonomy(
                concurrent=("evil",), exclusive=("global",), autopick=("evil",),
                trees={"evil": ("evil/**/*",), "global": ("**/*",)},
            ),
            workspace=None,
        )

    monkeypatch.setattr(
        drivers_seam, "_discover_entry_point_drivers",
        lambda *, _stderr=None: {"workshop": evil},
    )
    cfg = drivers_seam.resolve_driver_config("workshop", "C:/tmp/foreign-ws")
    # the REAL workshop wins — frontend/backend present, no `evil` lane.
    assert "frontend" in cfg.lanes.trees
    assert "evil" not in cfg.lanes.trees


# --------------------------------------------------------------------------- #
# 4. unknown name fails loud, keeping the legacy error substrings
# --------------------------------------------------------------------------- #

def test_unknown_driver_raises_valueerror():
    with pytest.raises(ValueError) as ei:
        drivers_seam.resolve_driver_config("nope_not_a_driver", ".")
    msg = str(ei.value)
    assert "nope_not_a_driver" in msg
    # The legacy substring `tests/test_drivers_workshop.py` asserts is preserved.
    assert "dos.drivers.nope_not_a_driver" in msg
    # And it now points the way to the third-party path.
    assert "dos.drivers" in msg and "entry-point" in msg


def test_dotted_or_pathy_name_rejected():
    for bad in ("foo.bar", "../evil", r"a\b"):
        with pytest.raises(ValueError) as ei:
            drivers_seam.resolve_driver_config(bad, ".")
        assert "single module token" in str(ei.value)


def test_factory_less_in_tree_module_raises():
    """A real module under dos.drivers with no `<name>_config` factory (e.g. llm_judge)
    is a clear error, not silently treated as missing."""
    with pytest.raises(ValueError) as ei:
        drivers_seam.resolve_driver_config("llm_judge", ".")
    assert "llm_judge_config" in str(ei.value)


# --------------------------------------------------------------------------- #
# 5. the kernel attaches workspace facts when a third-party factory forgets
# --------------------------------------------------------------------------- #

def test_plugin_config_gets_facts_attached(monkeypatch):
    """A forgetful third-party factory returns workspace=None; the resolver — not the
    plugin — attaches facts so the SELF_MODIFY guard scope is kernel-owned."""
    monkeypatch.setattr(
        drivers_seam, "_discover_entry_point_drivers",
        lambda *, _stderr=None: {"acme": _acme_config},
    )
    cfg = drivers_seam.resolve_driver_config("acme", "C:/tmp/foreign-ws")
    assert cfg.workspace is not None  # filled in by the kernel, not the plugin


# --------------------------------------------------------------------------- #
# 6. a broken plugin is skipped, never crashes the call
# --------------------------------------------------------------------------- #

def test_broken_plugin_skipped(monkeypatch, capsys):
    """A plugin that raises on load is skipped with a stderr note — a built-in still
    resolves (the same posture as judges/exporters discovery)."""
    def boom(*, _stderr=None):
        raise RuntimeError("importlib exploded")

    monkeypatch.setattr(drivers_seam, "_discover_entry_point_drivers", boom)
    # in-tree resolution does not consult discovery, so workshop still resolves
    cfg = drivers_seam.resolve_driver_config("workshop", "C:/tmp/foreign-ws")
    assert "frontend" in cfg.lanes.trees


# --------------------------------------------------------------------------- #
# 7. active_driver_names lists in-tree packs then discovered plugins
# --------------------------------------------------------------------------- #

def test_active_driver_names_lists_in_tree(monkeypatch):
    monkeypatch.setattr(
        drivers_seam, "_discover_entry_point_drivers",
        lambda *, _stderr=None: {"acme": _acme_config},
    )
    names = drivers_seam.active_driver_names()
    assert "workshop" in names
    assert "job" in names
    assert "acme" in names  # discovered third-party appended after in-tree
    # adjudicator/transport modules are NOT host packs
    assert "llm_judge" not in names
    assert "notify_slack" not in names


# --------------------------------------------------------------------------- #
# 8. dos.drivers is a REGULAR package (precondition for unshadowability)
# --------------------------------------------------------------------------- #

def test_dos_drivers_is_a_regular_package():
    """The unshadowability guarantee rests on `dos.drivers` being a regular package
    (an `__init__.py`), so a third party cannot drop a file into the `dos.drivers`
    namespace and shadow `workshop`. A future packaging change that turned it into a
    namespace package would silently break that — pin it here."""
    import dos.drivers

    init = Path(dos.drivers.__file__)
    assert init.name == "__init__.py"
    assert init.is_file()
