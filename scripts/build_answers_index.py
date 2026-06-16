#!/usr/bin/env python
"""build_answers_index.py — assemble docs/answers/index.jsonl from the answer corpus.

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four things
> live OUTSIDE the four layers"). Like `build_llms_full.py` and `build_readme.py`,
> this consumes the repo and the kernel is unaware it exists. It keeps ONE fact
> true: that `docs/answers/index.jsonl` is a faithful, machine-ingestible row per
> answer page, never a hand-edited fork that drifts from the prose.

Why an index at all
===================

The answer corpus (`docs/answers/*.md`) is 70+ sourced pages, each answering one
high-intent query. That corpus is optimized for a *human or answer-engine* that
fetches a page. The gap is *agents*: an agent that loads the DOS MCP server, or
fetches `llms.txt`, cannot RETRIEVE the corpus on demand — there is no structured
surface it can score a question against and pull the canonical answer.

This index is that surface. One JSON object per line (JSONL — streamable, one
`json.loads` per row, no full-file parse), each row carrying the page's machine
fields:

    slug      the filename stem (the stable id / the relative-link target)
    question  the page H1 (the canonical question the page answers)
    answer    the page's liftable one-line answer — its leading `>` blockquote,
              de-quoted and joined to a single line
    commands  the `dos <verb>` tokens the page names, sorted & de-duped
    path      the repo-relative path (docs/answers/<slug>.md)
    url       the GitHub blob URL (the fetchable spelling the corpus tests resolve)
    queries   every phrasing in discoverability_inventory.ARRIVAL_QUERIES that
              routes to this page — the searcher's own words, so a lexical match
              over this field hits the page even when the H1 is jargon

The query→page map is NOT re-keyed here — it is imported from
`discoverability_inventory.ARRIVAL_QUERIES`, the single source of that mapping, so
the two readers cannot disagree (the same discipline `build_llms_full.py` keeps by
PARSING `llms.txt` instead of re-listing it).

Assembly is pure on the repo root (no clock, no network), so the drift test
(`tests/test_answers_index.py`) can call `assemble()` and byte-compare. The set of
pages is a DERIVED glob (`docs/answers/*.md`), not a second hand-kept list — add a
page and the next build indexes it.

Usage
=====

    python scripts/build_answers_index.py            # regenerate index.jsonl
    python scripts/build_answers_index.py --check     # verify in sync (exit 1 if not); write nothing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# discoverability_inventory is a sibling dev-tooling script — the single source of
# the query→page map. Importing it (rather than re-listing the queries) keeps the
# index honest: a query added there appears here on the next build, and the two
# can never drift. Make the import work whether this script is run directly
# (cwd=repo) or imported by the test (which adds scripts/ to sys.path itself).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import discoverability_inventory as _inv  # noqa: E402

ANSWERS_DIR = Path("docs/answers")
INDEX = ANSWERS_DIR / "index.jsonl"

BLOB_URL = "https://github.com/anthony-chaudhary/dos-kernel/blob/master/{path}"

# A `dos <verb>` mention — the command(s) a page names. `[a-z][a-z-]*` is the verb
# grammar (lowercase + dashes, e.g. `commit-audit`); it deliberately stops at the
# verb, so `dos verify --workspace .` and `dos verify` both yield `dos verify`.
_DOS_CMD_RE = re.compile(r"\bdos ([a-z][a-z-]*)")


def _pages(repo_root: Path) -> list[Path]:
    """The answer pages, sorted, excluding the index README — a derived set."""
    return sorted(
        p for p in (repo_root / ANSWERS_DIR).glob("*.md") if p.name != "README.md"
    )


def _question(text: str) -> str:
    """The page H1 (first `# ` line), without the marker."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise ValueError("answer page has no H1 question line")


def _answer(text: str) -> str:
    """The leading blockquote answer — the contiguous `>` lines after the H1.

    De-quoted (`> ` stripped) and joined to a single liftable line. This is the
    page's own one-line answer (the `>` block the H1 is followed by), the part an
    answer-engine lifts verbatim.
    """
    lines = text.splitlines()
    # find the H1, then take the contiguous run of `>` lines that follows it
    out: list[str] = []
    seen_h1 = False
    for line in lines:
        if not seen_h1:
            if line.startswith("# "):
                seen_h1 = True
            continue
        stripped = line.strip()
        if stripped.startswith(">"):
            out.append(stripped.lstrip(">").strip())
        elif out:
            break  # the blockquote ended
        elif not stripped:
            continue  # a blank between the H1 and the blockquote
        else:
            break  # prose started with no blockquote (shouldn't happen — shape test guards)
    return " ".join(s for s in out if s).strip()


def _commands(text: str) -> list[str]:
    """The `dos <verb>` commands the page names, sorted and de-duped."""
    return sorted({f"dos {m}" for m in _DOS_CMD_RE.findall(text)})


def _queries_for(rel_path: str) -> list[str]:
    """Every ARRIVAL_QUERIES phrasing routing to this page, in declaration order."""
    return [q for q, page in _inv.ARRIVAL_QUERIES if page == rel_path]


def _row(repo_root: Path, page: Path) -> dict:
    text = page.read_text(encoding="utf-8")
    rel_path = str(page.relative_to(repo_root)).replace("\\", "/")
    return {
        "slug": page.stem,
        "question": _question(text),
        "answer": _answer(text),
        "commands": _commands(text),
        "path": rel_path,
        "url": BLOB_URL.format(path=rel_path),
        "queries": _queries_for(rel_path),
    }


def assemble(repo_root: Path) -> str:
    """The index.jsonl text — one sorted-keys JSON object per line.

    Pure on the repo root (no clock, no network) so the drift test can call it.
    LF-joined with a single trailing newline, byte-comparable across hosts.
    """
    rows = [_row(repo_root, page) for page in _pages(repo_root)]
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)


def _repo_root() -> Path:
    """The repo top-level — git's answer, NOT __file__ relative math (matches the
    sibling build scripts; this tool ships with the repo it operates on)."""
    import subprocess

    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true",
        help="verify index.jsonl matches the assembly (exit 1 if not); write nothing",
    )
    args = parser.parse_args(argv)

    # UTF-8 the streams — the report carries em-dashes; a cp1252 Windows console
    # must not crash the print (the same defensive move the sibling scripts make).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    root = _repo_root()
    expected = assemble(root)
    target = root / INDEX
    actual = target.read_text(encoding="utf-8") if target.exists() else None

    if args.check:
        if actual != expected:
            print(
                "docs/answers/index.jsonl is out of sync with the corpus — "
                "run: python scripts/build_answers_index.py",
                file=sys.stderr,
            )
            return 1
        print("docs/answers/index.jsonl is in sync with the corpus.")
        return 0

    if actual == expected:
        print("docs/answers/index.jsonl already up to date.")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(_pages(root))} answer pages indexed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
