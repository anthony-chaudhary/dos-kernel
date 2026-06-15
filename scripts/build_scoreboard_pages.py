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
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE_BASE = "https://anthony-chaudhary.github.io/dos-kernel"
BLOB_BASE = "https://github.com/anthony-chaudhary/dos-kernel/blob/master"

# Reuse the incident builder's TEMPLATE + text helpers (one theme), but NOT its
# link rewriter: that one resolves every repo-relative .md against docs/incidents/,
# which sends a scoreboard page's `<org>/<name>.md` link to a dead
# docs/incidents/<org>/<name>.md blob URL. The scoreboard has its own link shape
# (per-repo pages are HTML siblings; methodology/report/311 are blob-only), so it
# gets its own rewriter below. (scripts/ is not a package; load by path.)
_spec = importlib.util.spec_from_file_location(
    "build_incident_pages", REPO / "scripts" / "build_incident_pages.py")
_inc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_inc)

import html  # noqa: E402
import re  # noqa: E402  (re is also imported at module top; explicit for the rewriter)
import markdown  # noqa: E402  (dev-only dep, imported after the path shim)


def _rewrite_scoreboard_link(href: str, *, src_dir: Path, built: set[str]) -> str:
    """Rewrite one Markdown link from a scoreboard page for the Pages site.

    Resolution is relative to ``src_dir`` (the directory of the source .md), which
    is what a relative link in that file actually means. Targets:

    - in-page anchors, absolute URLs, mailto: unchanged.
    - a `.md` that resolves to a BUILT scoreboard page (in ``built``, the set of
      docs-relative .md paths we render) → its `.html` URL under ``/scoreboard/``,
      so per-repo pages and the index cross-link as live HTML.
    - any other repo-relative path (methodology.md, report-2026-06.md, ../311_*.md
      — none of which have a /scoreboard/ HTML twin) → the GitHub blob URL, so the
      link still works off the rendered page instead of 404-ing.
    """
    if href.startswith("#") or re.match(r"^[a-z]+://", href) or href.startswith("mailto:"):
        return href

    path_part, _, anchor = href.partition("#")
    anchor = f"#{anchor}" if anchor else ""

    # Resolve the link against the source file's directory, then make it
    # repo-relative (posix) so it can be compared / turned into a URL.
    try:
        target = (src_dir / path_part).resolve()
        rel = target.relative_to(REPO).as_posix()
    except ValueError:
        # escaped the repo — leave it untouched rather than emit a broken URL.
        return href

    # A built scoreboard page → its HTML sibling under the site.
    if rel in built:
        docs_rel = Path(rel).relative_to("docs/scoreboard")
        if docs_rel.name == "README.md":
            site_rel = "scoreboard/index.html"
        else:
            site_rel = "scoreboard/" + docs_rel.with_suffix(".html").as_posix()
        return f"{SITE_BASE}/{site_rel}{anchor}"

    # Everything else is blob-only (no /scoreboard/ HTML twin exists).
    return f"{BLOB_BASE}/{rel}{anchor}"


def _rewrite_scoreboard_html(body_html: str, *, src_dir: Path,
                             built: set[str]) -> str:
    def repl(m: "re.Match[str]") -> str:
        quote, href = m.group(1), m.group(2)
        new = _rewrite_scoreboard_link(href, src_dir=src_dir, built=built)
        return f'href={quote}{html.escape(new, quote=True)}{quote}'

    return re.sub(r'href=(["\'])(.*?)\1', repl, body_html)


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
    # The set of pages we actually render, as docs-relative posix paths — the
    # rewriter promotes a link to its .html twin only for these (everything else
    # is blob-only, so it never invents a missing HTML target).
    built = {p.relative_to(root).as_posix() for p in pages}
    written: list[str] = []
    for md_path in pages:
        md_text = md_path.read_text(encoding="utf-8")
        title = _inc._title_text(md_text)
        h1 = _inc._h1_text(md_text)
        desc = _inc._description(md_text)
        body_html = markdown.markdown(
            md_text, extensions=["tables", "fenced_code", "sane_lists"])
        # Scoreboard-aware link rewrite: cross-links between built pages become
        # live HTML; methodology/report/311 (no /scoreboard/ HTML twin) become
        # GitHub blob URLs. Links resolve relative to THIS page's directory.
        body_html = _rewrite_scoreboard_html(
            body_html, src_dir=md_path.parent, built=built)
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
