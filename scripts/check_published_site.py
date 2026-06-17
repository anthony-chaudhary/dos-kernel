#!/usr/bin/env python3
"""check_published_site — verify the PUBLISHED site, not the build that made it.

DOS's whole thesis is: don't believe the worker's "I shipped it" — read the
effect. The scoreboard's HTML build is unit-tested (`test_build_scoreboard_pages`
pins that every ``<img>`` resolves in the freshly-rendered tree), and yet the
live site still shipped three 404'ing charts: the last republish predated the
asset-copy fix, so nothing the build did was wrong — but nothing checked the
DEPLOYED tree either. The build was verified; the effect was not. That gap is
the exact shape DOS exists to close, turned on our own publish step.

This is that missing check — the ``dos verify`` for the site. Given a published
tree (a directory, or a git ref like ``origin/gh-pages``), it reads every HTML
page and asserts every LOCAL reference it makes (``<img src>``, ``<a href>``,
stylesheet/script ``src``) resolves to a file — or a directory — that is
actually in the tree. A dangling local ref (an image, page, or asset the HTML
points at but the deploy never shipped) is a dead resource on the live site: it
exits non-zero and names each one, so a broken publish fails LOUD instead of
404'ing silently in a browser nobody on the team opened.

Out of scope by design: external URLs (``http(s)://``, protocol-relative
``//``), ``mailto:``/``tel:``/``data:``, and in-page ``#`` anchors. This gate
verifies what the deploy itself must CONTAIN — not the reachability of the whole
web, which it cannot witness from the tree.

Dev tooling that operates ON the published artifact: stdlib only (no `markdown`,
no `requests`), so it runs on a bare CI install — and `build_scoreboard_pages`
imports `dangling_local_refs` to self-check its own output before it is ever
copied onto `gh-pages`. Nothing under `src/dos/` imports it (the one-way arrow).

Usage:
    python scripts/check_published_site.py --ref origin/gh-pages   # the live tree
    python scripts/check_published_site.py --dir site-scoreboard   # a staged build
"""
from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from pathlib import Path

# src|href="…" or '…' — the two attributes that pull a resource onto the page.
_REF_RE = re.compile(r"""(?:src|href)\s*=\s*(["'])(.*?)\1""", re.IGNORECASE)

# A ref we do NOT try to resolve against the tree: an external URL, a
# protocol-relative URL, a non-http scheme, or an in-page anchor.
_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.IGNORECASE)


def local_refs(html: str) -> list[str]:
    """Every LOCAL src/href in one HTML page, in document order.

    External URLs (scheme: or //), mailto/tel/data, and bare #anchors are
    dropped — this gate only verifies what the deploy must itself contain.
    The query string and fragment are stripped from each ref (``a.html?x#y``
    resolves on ``a.html``); a pure ``#frag`` is dropped as in-page.
    """
    out: list[str] = []
    for _q, ref in _REF_RE.findall(html):
        ref = ref.strip()
        if not ref or _EXTERNAL_RE.match(ref):
            continue
        ref = ref.split("#", 1)[0].split("?", 1)[0]
        if ref:  # a ref that was nothing but a #fragment is now empty
            out.append(ref)
    return out


def _resolve(page_path: str, ref: str) -> str | None:
    """Resolve ``ref`` against the page's directory, tree-relative (posix).

    Returns the normalized tree-relative path, or None if the ref escapes the
    tree root (``../`` past the top) — which this gate cannot witness and so
    does not flag (it is neither provably dead nor provably live here).
    """
    base = posixpath.dirname(page_path.replace("\\", "/"))
    joined = posixpath.normpath(posixpath.join(base, ref))
    if joined in (".", ""):
        return ""  # the current directory — always satisfied by the page itself
    if joined == ".." or joined.startswith("../"):
        return None  # escaped the tree; out of what we can verify
    return joined


def dangling_local_refs(pages: dict[str, str]) -> list[tuple[str, str, str]]:
    """The dead local references across a whole published tree.

    ``pages`` maps every tree-relative path (posix) to its bytes-decoded
    content; only ``*.html`` entries are scanned, but the full path set is the
    resolution target, so an HTML link to a non-HTML asset (an SVG, a JSON
    badge, a sibling page) is verified too. A ref is SATISFIED when it resolves
    to a file in the tree, OR to a directory in the tree (a ``foo/`` /
    ``./`` link that a static host serves as ``foo/index.html``).

    Returns ``[(page, ref, resolved), …]`` for every UNsatisfied local ref,
    sorted, deduped — empty list means the published effect matches every link
    it makes.
    """
    files = set(pages)
    dirs: set[str] = set()
    for p in files:
        parent = posixpath.dirname(p)
        while parent:
            dirs.add(parent)
            parent = posixpath.dirname(parent)

    def satisfied(resolved: str) -> bool:
        if resolved == "":  # current-dir ref (./, the page's own folder)
            return True
        return resolved in files or resolved in dirs

    bad: set[tuple[str, str, str]] = set()
    for page, html in pages.items():
        if not page.lower().endswith(".html"):
            continue
        for ref in local_refs(html):
            resolved = _resolve(page, ref)
            if resolved is None:
                continue  # escapes the tree — not witnessable here
            if not satisfied(resolved):
                bad.add((page, ref, resolved))
    return sorted(bad)


# ---------------------------------------------------------------------------
# Loaders — a published tree is either a directory on disk or a git ref. Both
# return the {tree-relative posix path: text} map `dangling_local_refs` folds.
# ---------------------------------------------------------------------------


def load_dir(root: Path) -> dict[str, str]:
    pages: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            try:
                pages[rel] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pages[rel] = ""
    return pages


def _git(args: list[str]) -> str:
    # Force UTF-8 decode — a published page carries non-ASCII (the `→` arrows,
    # the em dashes), and Git's bytes must not be decoded through a Windows
    # cp1252 locale, which raises on the first such byte.
    return subprocess.run(
        ["git", *args], capture_output=True, check=True,
        encoding="utf-8", errors="replace").stdout


def load_ref(ref: str) -> dict[str, str]:
    names = _git(["ls-tree", "-r", "--name-only", ref]).splitlines()
    pages: dict[str, str] = {}
    for name in names:
        name = name.strip()
        if not name:
            continue
        # Only HTML pages need their CONTENT (they're what we scan); every other
        # path only needs to be PRESENT in the target set, so store an empty
        # body for non-HTML to keep this to one cheap `git show` per page.
        if name.lower().endswith(".html"):
            pages[name] = _git(["show", f"{ref}:{name}"])
        else:
            pages[name] = ""
    return pages


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--ref", help="a git ref to verify (e.g. origin/gh-pages)")
    src.add_argument("--dir", type=Path, help="a built site directory to verify")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    if args.dir is not None:
        if not args.dir.is_dir():
            print(f"no such directory: {args.dir}", file=sys.stderr)
            return 2
        pages = load_dir(args.dir)
        label = str(args.dir)
    else:
        ref = args.ref or "origin/gh-pages"
        try:
            pages = load_ref(ref)
        except subprocess.CalledProcessError as e:
            print(f"cannot read git ref {ref!r}: {e.stderr or e}", file=sys.stderr)
            return 2
        label = ref

    n_html = sum(1 for p in pages if p.lower().endswith(".html"))
    bad = dangling_local_refs(pages)
    if bad:
        print(f"DEAD RESOURCES in {label}: {len(bad)} local reference(s) point "
              f"at something the published tree does not contain:", file=sys.stderr)
        for page, ref, resolved in bad:
            print(f"  {page}  →  {ref!r}  (resolves to {resolved!r}, missing)",
                  file=sys.stderr)
        print("The publish shipped HTML whose own links 404. Re-render and "
              "republish the missing assets/pages.", file=sys.stderr)
        return 1
    print(f"OK: {label} — {n_html} HTML page(s), every local reference resolves "
          "to a file or directory in the published tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
