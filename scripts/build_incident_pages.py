#!/usr/bin/env python3
"""Render docs/incidents/*.md into styled HTML for the GitHub Pages site.

The incident pages are the highest-intent search surface DOS has: one page per
real-world failure mode, titled in the words someone would search the moment it
bit them ("my agent said it committed but there's no commit"). They are authored
as Markdown under ``docs/incidents/`` — that Markdown is the single source of
truth — and this script renders them to HTML on the same dark theme as the rest
of the Pages site (``index.html``, ``alternatives.html``).

What it does, deterministically:
  * read each ``docs/incidents/<slug>.md`` (skipping ``README.md``),
  * take the victim's-words H1 as the page ``<title>`` / ``<h1>``,
  * rewrite relative links so they work on the Pages site:
      - a sibling ``<other>.md`` incident → ``<other>.html``,
      - any other repo-relative ``*.md``/path → its absolute GitHub blob URL,
      - in-page ``#anchors`` and absolute ``http(s)`` links are left alone,
  * stamp a "rendered from <repo markdown>" provenance line on every page
    (the sync rule: edit the Markdown, re-run this, never hand-edit the HTML),
  * emit the four HTML files, a regenerated ``sitemap.xml``, and an
    ``index`` nav snippet, into an output directory (default: ``site/``).

The output is then published by copying it onto the ``gh-pages`` branch. This
script writes only into ``--out``; it never touches the working tree's tracked
files, so it is safe to run on a shared tree.

Usage:
    python scripts/build_incident_pages.py            # → site/
    python scripts/build_incident_pages.py --out _pub # → _pub/

Dependency: the ``markdown`` package (dev-only; ``pip install markdown``). It is
not a runtime dependency of dos-kernel — this is a docs-build tool that runs on
a developer machine / CI, not inside the kernel.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

REPO_RAW_BASE = "https://github.com/anthony-chaudhary/dos-kernel/blob/master"
SITE_BASE = "https://anthony-chaudhary.github.io/dos-kernel"
# The incidents Markdown lives at docs/incidents/<slug>.md, so a repo-relative
# link like ../FAQ.md resolves against that directory.
INCIDENTS_REL_DIR = "docs/incidents"

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — DOS</title>
  <meta name="description" content="{description}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{og_title}">
  <meta property="og:description" content="{description}">
  <meta property="og:url" content="{canonical}">
  <meta property="og:image" content="https://opengraph.githubassets.com/1/anthony-chaudhary/dos-kernel">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{og_title}">
  <meta name="twitter:description" content="{description}">
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "TechArticle",
    "headline": {headline_json},
    "description": {description_json},
    "url": "{canonical}",
    "datePublished": "{date}",
    "author": {{ "@type": "Person", "name": "Anthony Chaudhary", "url": "https://github.com/anthony-chaudhary" }},
    "isPartOf": {{ "@type": "WebSite", "url": "{site_base}/" }}
  }}
  </script>
  <style>
    :root {{
      --bg: #0d1117; --panel: #161b22; --border: #30363d;
      --fg: #e6edf3; --muted: #9198a1; --accent: #58a6ff; --green: #3fb950; --red: #f85149;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; background: var(--bg); color: var(--fg);
      font: 17px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    main {{ max-width: 760px; margin: 0 auto; padding: 48px 20px 64px; }}
    h1 {{ font-size: 2rem; line-height: 1.25; margin: 0 0 8px; }}
    h2 {{ font-size: 1.3rem; margin: 40px 0 8px; }}
    p {{ margin: 12px 0; }}
    blockquote {{
      margin: 16px 0; padding: 12px 16px; border-left: 3px solid var(--accent);
      background: var(--panel); border-radius: 0 8px 8px 0; color: var(--fg);
    }}
    blockquote p {{ margin: 6px 0; }}
    pre {{
      background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
      padding: 14px 16px; overflow-x: auto; font-size: 14px; line-height: 1.5;
    }}
    code {{ font-family: ui-monospace, "Cascadia Code", Consolas, monospace; }}
    p > code, li > code, blockquote code, td > code {{
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 4px; padding: 1px 5px; font-size: 0.9em;
    }}
    pre code {{ background: none; border: none; padding: 0; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    ul {{ padding-left: 22px; }} li {{ margin: 6px 0; }}
    table {{ border-collapse: collapse; margin: 16px 0; width: 100%; }}
    th, td {{ border: 1px solid var(--border); padding: 8px 12px; text-align: left; }}
    th {{ background: var(--panel); }}
    .source {{ color: var(--muted); font-size: 0.9rem; margin: 0 0 28px; }}
    .backhome {{ margin-top: 8px; }}
    footer {{ margin-top: 56px; color: var(--muted); font-size: 0.85rem; border-top: 1px solid var(--border); padding-top: 16px; }}
  </style>
</head>
<body>
<main>
  <p class="backhome"><a href="./">← DOS</a> · <a href="{site_base}/#incidents">all incidents</a></p>
  <p class="source">Rendered from <a href="{source_url}"><code>{source_rel}</code></a> — the
  Markdown in the repo is the source of truth; this page is generated by
  <code>scripts/build_incident_pages.py</code>, never hand-edited.</p>
{body}
  <footer>
    <p>DOS — the Dispatch Operating System. Open source under the
    <a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/LICENSE">MIT license</a>.
    Install with <code>pip install dos-kernel</code> (the bare <code>dos</code> name on PyPI is an
    unrelated package).</p>
  </footer>
</main>
</body>
</html>
"""


def _slugs(incidents_dir: Path) -> list[Path]:
    return sorted(
        p for p in incidents_dir.glob("*.md") if p.name.lower() != "readme.md"
    )


def _rewrite_link(href: str, known_slugs: set[str], base_rel_dir: str = INCIDENTS_REL_DIR) -> str:
    """Rewrite one Markdown link target for the Pages site.

    - in-page anchors and absolute URLs: unchanged
    - sibling page `<slug>.md[#anchor]` (a known slug): → `<slug>.html[#anchor]`
    - any other repo-relative path: → absolute GitHub blob URL

    ``base_rel_dir`` is the repo-relative directory the source Markdown lives in
    (``docs/incidents`` for incident pages, ``docs/scoreboard`` for scoreboard
    pages). Repo-relative links resolve against it, so the same rewriter serves
    both surfaces without sending one's links into the other's directory.
    """
    if href.startswith("#") or re.match(r"^[a-z]+://", href) or href.startswith("mailto:"):
        return href

    path_part, _, anchor = href.partition("#")
    anchor = f"#{anchor}" if anchor else ""

    # Sibling page (same directory, a known slug).
    if "/" not in path_part and path_part.endswith(".md"):
        stem = path_part[:-3]
        if stem in known_slugs:
            return f"{stem}.html{anchor}"

    # Everything else is repo-relative; resolve against the source directory and
    # point at the GitHub blob view so the link still works off-site.
    resolved = (Path(base_rel_dir) / path_part).resolve().relative_to(
        Path(base_rel_dir).resolve().parents[1]
    )
    rel = str(resolved).replace("\\", "/")
    return f"{REPO_RAW_BASE}/{rel}{anchor}"


def _rewrite_html_links(body_html: str, known_slugs: set[str],
                        base_rel_dir: str = INCIDENTS_REL_DIR) -> str:
    def repl(m: re.Match[str]) -> str:
        quote, href = m.group(1), m.group(2)
        return (f'href={quote}'
                f'{html.escape(_rewrite_link(href, known_slugs, base_rel_dir), quote=True)}'
                f'{quote}')

    return re.sub(r'href=(["\'])(.*?)\1', repl, body_html)


def _h1_text(md_text: str) -> str:
    for line in md_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "Incident"


def _title_text(md_text: str) -> str:
    """The H1 with the wrapping quotes stripped — for <title> / og:title.

    The incident H1 quotes the spoken phrase ("My agent said it committed…")
    on purpose, which is right inside the page body, but a browser tab and a
    search snippet read cleaner without the leading/trailing quote pair.
    """
    h1 = _h1_text(md_text)
    if h1.startswith('"') and '"' in h1[1:]:
        # Strip only a quote that wraps the whole leading phrase, keeping any
        # trailing clause (e.g. '… — or faked a green run').
        end = h1.index('"', 1)
        h1 = (h1[1:end] + h1[end + 1 :]).strip()
    return h1


def _description(md_text: str) -> str:
    """One-sentence description: the blockquote's first sentence, plain text."""
    # The blockquote can wrap across several `>` lines; join them first.
    block = re.findall(r"^>\s?(.*)$", md_text, re.MULTILINE)
    raw = " ".join(block).strip() if block else _h1_text(md_text)
    raw = re.sub(r"`([^`]*)`", r"\1", raw)  # drop code ticks
    raw = re.sub(r"\*+", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    sentence = re.split(r"(?<=[.?!])\s", raw)[0]
    return sentence.strip()


def _json_str(s: str) -> str:
    import json

    return json.dumps(s)


def render(incidents_dir: Path, out_dir: Path, date: str) -> list[str]:
    try:
        import markdown
    except ImportError:
        sys.exit(
            "error: the `markdown` package is required to render incident pages.\n"
            "       pip install markdown   (dev-only; not a dos-kernel runtime dep)"
        )

    slugs = _slugs(incidents_dir)
    known = {p.stem for p in slugs}
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for src in slugs:
        md_text = src.read_text(encoding="utf-8")
        title = _title_text(md_text)
        description = _description(md_text)
        body_html = markdown.markdown(
            md_text, extensions=["fenced_code", "tables"], output_format="html5"
        )
        body_html = _rewrite_html_links(body_html, known)
        source_rel = f"{INCIDENTS_REL_DIR}/{src.name}"
        page = PAGE_TEMPLATE.format(
            title=html.escape(title),
            og_title=html.escape(title),
            description=html.escape(description, quote=True),
            description_json=_json_str(description),
            headline_json=_json_str(title),
            canonical=f"{SITE_BASE}/incidents/{src.stem}.html",
            site_base=SITE_BASE,
            date=date,
            source_url=f"{REPO_RAW_BASE}/{source_rel}",
            source_rel=html.escape(source_rel),
            body=body_html,
        )
        dest = out_dir / "incidents" / f"{src.stem}.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page, encoding="utf-8", newline="\n")
        written.append(f"incidents/{src.stem}.html")
        print(f"  rendered {dest.relative_to(out_dir)}  ({title!r})")

    # Emit the index nav snippet + sitemap fragment for the publish step.
    _write_index_snippet(incidents_dir, slugs, out_dir)
    _write_sitemap_fragment(slugs, out_dir, date)
    return written


def _write_index_snippet(incidents_dir: Path, slugs: list[Path], out_dir: Path) -> None:
    items = []
    for src in slugs:
        title = _title_text(src.read_text(encoding="utf-8"))
        items.append(
            f'    <li><a href="incidents/{src.stem}.html">{html.escape(title)}</a></li>'
        )
    snippet = (
        '  <h2 id="incidents">It just happened to you?</h2>\n'
        "  <p>One page per real-world agent failure, titled in the words you'd\n"
        "  search the moment it bit you — each with the one command that catches it.</p>\n"
        "  <ul>\n" + "\n".join(items) + "\n  </ul>\n"
    )
    (out_dir / "_index_incidents_snippet.html").write_text(
        snippet, encoding="utf-8", newline="\n"
    )
    print("  wrote _index_incidents_snippet.html (paste into index.html)")


def _write_sitemap_fragment(slugs: list[Path], out_dir: Path, date: str) -> None:
    rows = []
    for src in slugs:
        rows.append(
            "  <url>\n"
            f"    <loc>{SITE_BASE}/incidents/{src.stem}.html</loc>\n"
            f"    <lastmod>{date}</lastmod>\n"
            "    <changefreq>monthly</changefreq>\n"
            "  </url>"
        )
    (out_dir / "_sitemap_incidents_fragment.xml").write_text(
        "\n".join(rows) + "\n", encoding="utf-8", newline="\n"
    )
    print("  wrote _sitemap_incidents_fragment.xml (merge into sitemap.xml)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out", default="site", help="output directory (default: site/)"
    )
    ap.add_argument(
        "--incidents-dir",
        default="docs/incidents",
        help="source Markdown directory (default: docs/incidents)",
    )
    ap.add_argument(
        "--date", default="2026-06-12", help="lastmod / datePublished date"
    )
    args = ap.parse_args(argv[1:])

    incidents_dir = Path(args.incidents_dir)
    if not incidents_dir.is_dir():
        sys.exit(f"error: {incidents_dir} is not a directory")
    out_dir = Path(args.out)
    print(f"rendering incident pages: {incidents_dir} -> {out_dir}")
    written = render(incidents_dir, out_dir, args.date)
    print(f"done — {len(written)} page(s): {', '.join(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
