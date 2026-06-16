"""Pins for build_scoreboard_pages — the HTML render of the scoreboard front door.

The observatory front door embeds three SVG charts by relative path
(`<img src="assets/…svg">`). The HTML builder must copy those assets into the
rendered site, or the images 404 on the Pages site — a valid render that ships
a dead resource (the published-effect-vs-source-diff gap). This test renders
into a tmp dir and pins: the assets are copied, every `<img>` src resolves to a
real file, and every per-repo link resolves to a built page.

Guarded by `importorskip("markdown")` — the renderer needs the dev-only
`markdown` package, which is absent on a bare CI install; skipping (not failing)
there is the documented contract for these render tests.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytest.importorskip("markdown")  # dev-only dep; skip where absent (CI bare)

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "build_scoreboard_pages", REPO / "scripts" / "build_scoreboard_pages.py")
bsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsp)


def _render(tmp_path: Path) -> Path:
    # Render into a unique per-call subdir: on Windows a shared tmp dir can hit
    # a cleanup race when sibling tests reuse it. Each call gets its own tree.
    out = tmp_path / "site"
    bsp.render(REPO, out, date="2026-06-16")
    return out


def test_svg_assets_are_copied_into_the_site(tmp_path):
    out = _render(tmp_path)
    assets = out / "scoreboard" / "assets"
    # every committed front-door SVG lands in the rendered site.
    committed = sorted(
        (REPO / "docs" / "scoreboard" / "assets").glob("*.svg"))
    assert committed, "expected committed SVG charts under docs/scoreboard/assets"
    for svg in committed:
        assert (assets / svg.name).exists(), f"{svg.name} not copied to the site"


def test_every_index_image_resolves(tmp_path):
    out = _render(tmp_path)
    index = out / "scoreboard" / "index.html"
    html = index.read_text(encoding="utf-8")
    srcs = re.findall(r'<img[^>]*src="([^"]+)"', html)
    assert srcs, "the front door embeds no images — the charts vanished"
    for src in srcs:
        # relative to the index page's directory (scoreboard/)
        target = out / "scoreboard" / src
        assert target.exists(), f"<img src={src!r}> 404s — dead resource shipped"


def test_per_repo_links_point_at_built_pages(tmp_path):
    out = _render(tmp_path)
    html = (out / "scoreboard" / "index.html").read_text(encoding="utf-8")
    # per-repo links are rewritten to absolute SITE_BASE/scoreboard/…html URLs.
    site_links = {h for h in re.findall(r'href="([^"]+)"', html)
                  if "/scoreboard/" in h and h.endswith(".html")}
    assert site_links, "no per-repo HTML links on the index"
    for h in site_links:
        rel = h.split("/dos-kernel/", 1)[1]  # scoreboard/<org>/<repo>.html
        assert (out / rel).exists(), f"index links a missing page: {h}"
