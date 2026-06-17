"""Pins for check_published_site — the verify-the-EFFECT gate over a deploy.

The scoreboard build is unit-tested, yet the live site still shipped 404'ing
charts because nothing checked the DEPLOYED tree. This gate is that missing
check; these pins fix its contract: a dead local ref is CAUGHT, an external URL
/ anchor / directory link is NOT a false positive, and a tree whose every link
resolves passes clean. Stdlib-only (no `markdown`), so it runs on bare CI.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_published_site", REPO / "scripts" / "check_published_site.py")
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)


def test_local_refs_drops_external_and_anchors():
    html = """
      <img src="assets/ok.svg">
      <a href="page.html">x</a>
      <a href="https://example.com/y.png">ext</a>
      <a href="//cdn.example.com/z.js">proto-rel</a>
      <a href="mailto:a@b.c">mail</a>
      <a href="#section">anchor</a>
      <link href="style.css?v=2#top">
    """
    refs = chk.local_refs(html)
    assert refs == ["assets/ok.svg", "page.html", "style.css"]


def test_dangling_is_caught_but_present_resolves():
    pages = {
        "scoreboard/index.html":
            '<img src="assets/here.svg"><img src="assets/gone.svg">'
            '<a href="kenn-io/roborev.html">repo</a>'
            '<a href="https://x/y.svg">ext</a>',
        "scoreboard/assets/here.svg": "",
        "scoreboard/kenn-io/roborev.html": "",
    }
    bad = chk.dangling_local_refs(pages)
    # exactly the one missing asset, named with its resolved tree path.
    assert bad == [("scoreboard/index.html", "assets/gone.svg",
                    "scoreboard/assets/gone.svg")]


def test_directory_and_current_dir_links_are_satisfied():
    # `./` (the page's own folder) and a link to a directory served as its
    # index must NOT be flagged — a static host resolves them.
    pages = {
        "index.html": '<a href="./">root</a><a href="scoreboard/">board</a>',
        "scoreboard/index.html": "",
    }
    assert chk.dangling_local_refs(pages) == []


def test_clean_tree_passes_via_main(tmp_path, capsys):
    site = tmp_path / "site"
    (site / "scoreboard" / "assets").mkdir(parents=True)
    (site / "scoreboard" / "assets" / "c.svg").write_text("<svg/>", encoding="utf-8")
    (site / "scoreboard" / "index.html").write_text(
        '<img src="assets/c.svg">', encoding="utf-8")
    assert chk.main(["--dir", str(site)]) == 0


def test_dead_resource_fails_via_main(tmp_path, capsys):
    site = tmp_path / "site"
    (site / "scoreboard").mkdir(parents=True)
    (site / "scoreboard" / "index.html").write_text(
        '<img src="assets/missing.svg">', encoding="utf-8")
    assert chk.main(["--dir", str(site)]) == 1
    err = capsys.readouterr().err
    assert "missing.svg" in err and "DEAD RESOURCES" in err


def test_escaping_ref_is_not_flagged():
    # A ref that climbs past the tree root can't be witnessed here; it is left
    # alone rather than reported as a false dead-resource.
    pages = {"a/b/page.html": '<a href="../../../outside.html">x</a>'}
    assert chk.dangling_local_refs(pages) == []
