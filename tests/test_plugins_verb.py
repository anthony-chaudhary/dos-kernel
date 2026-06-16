"""`dos plugins` (the manifest) + `dos plugin new` (the scaffolder).

The manifest is a PURE FOLD over the static seam table + a uniform entry-point scan; the
scaffolder reuses the SAME table so a generated stub always matches a real seam. These
tests pin: every seam has the required descriptor fields; the fold lists the built-in
floor and discovered third-party occupants; the scaffolder emits a correct, installable
package per seam shape; an unknown seam fails loud; the singular alias resolves.
"""

from __future__ import annotations

import json

import pytest

from dos import plugins


def test_every_seam_has_required_fields():
    """A descriptor with an empty group/protocol/invariant would render a useless row."""
    assert plugins.SEAMS, "the seam table must not be empty"
    for s in plugins.SEAMS:
        assert s.group.startswith("dos."), s.group
        assert s.protocol and s.invariant and s.purpose, s.group


def test_drivers_seam_is_first():
    """The host-policy driver is the trunk of the extension surface — list it first."""
    assert plugins.SEAMS[0].group == "dos.drivers"


def test_groups_are_unique():
    groups = [s.group for s in plugins.SEAMS]
    assert len(groups) == len(set(groups))


def test_seam_for_accepts_full_bare_and_singular():
    assert plugins.seam_for("dos.judges").group == "dos.judges"
    assert plugins.seam_for("judges").group == "dos.judges"
    assert plugins.seam_for("judge").group == "dos.judges"        # singular alias
    assert plugins.seam_for("driver").group == "dos.drivers"      # singular alias
    assert plugins.seam_for("mcp").group == "dos.mcp_tools"       # shorthand
    assert plugins.seam_for("nonsense") is None


# --------------------------------------------------------------------------- #
# the fold
# --------------------------------------------------------------------------- #

def test_fold_covers_every_seam():
    reports = plugins.fold()
    assert {r.descriptor.group for r in reports} == {s.group for s in plugins.SEAMS}


def test_fold_lists_builtin_floor():
    reports = {r.descriptor.group: r for r in plugins.fold()}
    assert reports["dos.drivers"].builtins == ("workshop", "job")
    assert reports["dos.judges"].builtins == ("abstain",)


def test_fold_excludes_builtins_from_third_party(monkeypatch):
    """A discovered name matching a built-in is NOT counted as third-party (the built-in
    is what resolves — unshadowable)."""
    monkeypatch.setattr(
        plugins, "_discovered_names",
        lambda group, _stderr=None: (["abstain", "mine"] if group == "dos.judges" else []),
    )
    reports = {r.descriptor.group: r for r in plugins.fold()}
    assert "abstain" not in reports["dos.judges"].third_party
    assert "mine" in reports["dos.judges"].third_party


def test_render_text_and_json_run():
    reports = plugins.fold()
    text = plugins.render_text(reports)
    assert "DOS extension surface" in text
    assert "dos.drivers" in text
    payload = json.loads(plugins.render_json(reports))
    assert payload["seam_count"] == len(plugins.SEAMS)
    assert any(s["group"] == "dos.drivers" for s in payload["seams"])


# --------------------------------------------------------------------------- #
# the scaffolder (pure planning)
# --------------------------------------------------------------------------- #

def test_scaffold_unknown_seam_fails_loud():
    with pytest.raises(ValueError) as ei:
        plugins.scaffold("not_a_seam", "acme")
    assert "unknown seam" in str(ei.value)
    assert "dos plugins" in str(ei.value)


def test_scaffold_judge_emits_installable_package():
    plan = plugins.scaffold("judge", "acme")  # singular alias
    assert plan.seam.group == "dos.judges"
    assert plan.pkg == "dos_acme_plugin"
    # pyproject registers the occupant under the right group, pointing at the stub symbol
    py = plan.files["pyproject.toml"]
    assert '[project.entry-points."dos.judges"]' in py
    assert "acme = \"dos_acme_plugin:AcmePlugin\"" in py
    # the stub module defines that symbol
    mod = plan.files["dos_acme_plugin/__init__.py"]
    assert "class AcmePlugin:" in mod
    assert 'name = "acme"' in mod


def test_scaffold_driver_emits_factory():
    plan = plugins.scaffold("drivers", "acme")
    py = plan.files["pyproject.toml"]
    assert '[project.entry-points."dos.drivers"]' in py
    assert "acme = \"dos_acme_plugin:acme_config\"" in py
    mod = plan.files["dos_acme_plugin/__init__.py"]
    assert "def acme_config(workspace=None)" in mod
    assert "gather_workspace_facts" in mod  # the SELF_MODIFY-safe pack


def test_scaffold_mcp_tool_emits_register():
    plan = plugins.scaffold("mcp_tools", "acme")
    py = plan.files["pyproject.toml"]
    assert '[project.entry-points."dos.mcp_tools"]' in py
    assert "acme = \"dos_acme_plugin:register\"" in py
    mod = plan.files["dos_acme_plugin/__init__.py"]
    assert "def register(mcp)" in mod
    assert "@mcp.tool()" in mod


def test_scaffold_hyphenated_name_makes_valid_package():
    """A hyphenated occupant name maps to an underscore package + valid symbols."""
    plan = plugins.scaffold("judges", "acme-judge")
    assert plan.pkg == "dos_acme_judge_plugin"
    mod = plan.files["dos_acme_judge_plugin/__init__.py"]
    assert "class AcmeJudgePlugin:" in mod
