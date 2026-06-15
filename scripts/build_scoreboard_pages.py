#!/usr/bin/env python3
"""Render docs/scoreboard/**/*.md into styled HTML for the GitHub Pages site.

The per-repo scoreboard pages (docs/311 / #98) are the SEO/AEO discovery surface
of the drift scoreboard: one page per audited repo, each a receipts-backed CLEAN
verdict an agent (or its human) finds when searching that repo's name + "agent
honesty" / "commit drift". The Markdown under ``docs/scoreboard/`` is the single
source of truth; this script renders it to HTML on the same dark theme as the
rest of the Pages site, reusing the incident-page builder's template + link
machinery (docs/311 P2's pages, made web-discoverable).

It renders, deterministically:
  * every ``docs/scoreboard/<org>/<name>.md`` (the per-repo pages) +
    ``docs/scoreboard/README.md`` (the index root) → HTML under ``--out``,
  * rewriting relative ``*.md`` links to their ``.html`` sibling or the repo's
    absolute GitHub blob URL (the same rule build_incident_pages.py uses),
  * a regenerated sitemap fragment for the new pages,
  * never touching the working tree (writes only into ``--out``), so it is safe
    on a shared tree. The output is published by copying onto ``gh-pages``.

Usage:
    python scripts/build_scoreboard_pages.py            # → site-scoreboard/
    python scripts/build_scoreboard_pages.py --out _pub

Dependency: the ``markdown`` package (dev-only), like build_incident_pages.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE_BASE = "https://anthony-chaudhary.github.io/dos-kernel"

# Reuse the incident builder's template + helpers — one theme, one link rule,
# no second copy to drift. (scripts/ is not a package; load by path.)
_spec = importlib.util.spec_from_file_location(
    "build_incident_pages", REPO / "scripts" / "build_incident_pages.py")
_inc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_inc)

import markdown  # noqa: E402  (dev-only dep, imported after the path shim)


def _scoreboard_md(root: Path) -> list[Path]:
    """Every tracked scoreboard markdown page: the index root + per-repo pages
    (docs/scoreboard/<org>/<name>.md), skipping methodology/report (those have
    their own hand-built site pages) — newest layout, two-deep org/name only."""
    pages = [root / "docs" / "scoreboard" / "README.md"]
    for p in sorted((root / "docs" / "scoreboard").glob("*/*.md")):
        pages.append(p)
    return [p for p in pages if p.exists()]


def _out_rel(md_path: Path, root: Path) -> str:
    """The page's path under the site, e.g. scoreboard/unslothai/unsloth.html or
    scoreboard/index.html for the README."""
    rel = md_path.relative_to(root / "docs" / "scoreboard")
    if rel.name == "README.md":
        return "scoreboard/index.html"
    return "scoreboard/" + str(rel.with_suffix(".html")).replace("\\", "/")


def render(root: Path, out_dir: Path, date: str) -> list[str]:
    pages = _scoreboard_md(root)
    written: list[str] = []
    for md_path in pages:
        md_text = md_path.read_text(encoding="utf-8")
        title = _inc._title_text(md_text)
        h1 = _inc._h1_text(md_text)
        desc = _inc._description(md_text)
        body_html = markdown.markdown(
            md_text, extensions=["tables", "fenced_code", "sane_lists"])
        # Reuse the incident link-rewriter (sibling .md → .html, repo paths →
        # blob URLs, anchors/abs left alone), but anchored at docs/scoreboard/
        # so a relative link like methodology.md / org/name.md resolves to its
        # real scoreboard path, not docs/incidents/ (which would 404). The
        # scoreboard pages link ../311_*.md, methodology.md, sibling
        # org/name.md — all handled.
        body_html = _inc._rewrite_html_links(
            body_html, set(), base_rel_dir="docs/scoreboard")
        out_rel = _out_rel(md_path, root)
        canonical = f"{SITE_BASE}/{out_rel}"
        # Provenance line (the sync rule: edit the markdown, re-render, never
        # hand-edit the HTML) — the same shape the incident pages stamp.
        import html as _html
        source_rel = str(md_path.relative_to(root)).replace("\\", "/")
        source_url = f"https://github.com/anthony-chaudhary/dos-kernel/blob/master/{source_rel}"
        page = _inc.PAGE_TEMPLATE.format(
            title=title, description=desc, canonical=canonical,
            og_title=title, headline_json=_inc._json_str(h1),
            description_json=_inc._json_str(desc), date=date,
            site_base=SITE_BASE, body=body_html,
            source_url=source_url, source_rel=_html.escape(source_rel))
        dest = out_dir / out_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page, encoding="utf-8")
        written.append(out_rel)
        print(f"  wrote {out_rel}")
    _write_sitemap_fragment(written, out_dir, date)
    return written


def _write_sitemap_fragment(out_rels: list[str], out_dir: Path, date: str) -> None:
    urls = "\n".join(
        f"  <url><loc>{SITE_BASE}/{r}</loc><lastmod>{date}</lastmod></url>"
        for r in out_rels)
    (out_dir / "_sitemap_scoreboard_fragment.xml").write_text(
        urls + "\n", encoding="utf-8")
    print("  wrote _sitemap_scoreboard_fragment.xml (merge into sitemap.xml)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="site-scoreboard",
                    help="output dir (gitignored staging; copy onto gh-pages)")
    ap.add_argument("--date", help="lastmod/datePublished (default: today UTC)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    date = args.date
    if not date:
        from datetime import datetime, timezone
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path(args.out)
    written = render(REPO, out_dir, date)
    print(f"rendered {len(written)} scoreboard page(s) into {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
