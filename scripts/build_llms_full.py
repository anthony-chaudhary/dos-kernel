#!/usr/bin/env python
"""build_llms_full.py — assemble llms-full.txt from the documents llms.txt indexes.

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four things
> live OUTSIDE the four layers"). Like `build_readme.py`, this consumes the repo and
> the kernel is unaware it exists. It keeps ONE fact true: that `llms-full.txt` is a
> faithful concatenation of the documents `llms.txt` points at, never a hand-edited
> fork that drifts.

Why a full file at all
======================

`llms.txt` is the curated INDEX an arriving agent fetches first (the llmstxt.org
convention). The companion convention, `llms-full.txt`, is the one-fetch EXPANSION:
every indexed document inlined, so an agent (or an llms.txt directory's crawler)
gets the whole story in a single request instead of a dozen.

The roster is NOT a second hand-kept list — that would rot against the index the
way the CLAUDE.md module roster rotted against the source tree. Instead the
builder PARSES `llms.txt` and takes, in order, every repo-file link outside the
"Optional" section (the paper/benchmark/release links stay fetch-on-demand).
Add a doc to llms.txt and the next build inlines it; rename one and the llms.txt
rot pin (tests/test_llms_txt.py) goes red before this script even runs.

Assembly is deliberately dumb: a spec-shaped header (H1 + blockquote), a
generated-file banner, then each rostered document verbatim (opened by a `source:`
comment), THEN the full answer corpus — every `docs/answers/*.md` page inlined
(opened by a distinct `answer:` comment). The roster links only the corpus INDEX;
inlining the pages themselves is what lets an arriving agent get the whole
knowledge base in one fetch instead of having to walk the routing table. The
corpus pages are a DERIVED glob (not a second hand-kept list), so a page added to
the corpus is inlined on the next build. Idempotent: LF endings, single trailing
newline. `--check` makes no changes and exits non-zero on drift — the mode
`tests/test_llms_full.py` runs.

Usage
=====

    python scripts/build_llms_full.py            # regenerate llms-full.txt
    python scripts/build_llms_full.py --check    # verify in sync (exit 1 if not), write nothing
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

LLMS = Path("llms.txt")
LLMS_FULL = Path("llms-full.txt")
ANSWERS_DIR = Path("docs/answers")

# A link that names a FILE in this repo, in either fetchable spelling — the same
# pattern tests/test_llms_txt.py resolves, so the two readers cannot disagree.
REPO_FILE_RE = re.compile(
    r"https://(?:raw\.githubusercontent\.com/anthony-chaudhary/dos-kernel/master/"
    r"|github\.com/anthony-chaudhary/dos-kernel/blob/master/)(?P<path>[^)#?\s]+)"
)

# H2 sections of llms.txt whose links stay index-only (not inlined).
SKIP_SECTIONS = frozenset({"Optional"})

BANNER = (
    "<!-- GENERATED FILE — do not edit llms-full.txt directly.\n"
    "     It is assembled from the documents llms.txt indexes (the non-Optional\n"
    "     repo-file links, in index order). Edit the source document or llms.txt,\n"
    "     then run:\n"
    "         python scripts/build_llms_full.py\n"
    "     tests/test_llms_full.py pins this file to that assembly. -->\n"
)

HEADER = (
    "# DOS — the Dispatch Operating System (dos-kernel) — llms-full.txt\n"
    "\n"
    "> The one-fetch expansion of llms.txt: every document that index points at\n"
    "> (outside its Optional section), concatenated in index order, FOLLOWED BY the\n"
    "> full answer corpus (every docs/answers/*.md page inlined). Each section opens\n"
    "> with an HTML comment naming its source file in the repository\n"
    "> (https://github.com/anthony-chaudhary/dos-kernel).\n"
)


def roster(llms_text: str) -> list[str]:
    """The repo paths llms.txt indexes, in order, outside the skipped sections.

    Pure on its input (no I/O) so the drift test can call it directly.
    `llms-full.txt` itself is excluded — the index links the expansion, but the
    expansion must not try to inline itself.
    """
    paths: list[str] = []
    section = ""
    for line in llms_text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section in SKIP_SECTIONS:
            continue
        for match in REPO_FILE_RE.finditer(line):
            path = match.group("path")
            if path != LLMS_FULL.name and path not in paths:
                paths.append(path)
    if not paths:
        raise ValueError("llms.txt yielded an empty roster — is it index-shaped?")
    return paths


ANSWERS_HEADER = (
    "<!-- ====== the answer corpus (docs/answers/*.md) ====== -->\n"
    "\n"
    "# DOS answer corpus — the high-intent questions, answered in full\n"
    "\n"
    "> The llms.txt roster above links the answer-corpus INDEX (the routing\n"
    "> table). What follows is the corpus itself: every sourced, self-contained\n"
    "> answer page inlined, so an arriving agent gets the whole knowledge base in\n"
    "> this one fetch — each page a high-intent question answered with an evidence\n"
    "> table whose every number links to the file that proves it. The pages are a\n"
    "> DERIVED glob of docs/answers/*.md (not a second hand-kept list), so a page\n"
    "> added to the corpus is inlined here on the next build. Each opens with an\n"
    "> `answer:` marker naming its source file.\n"
)


def answer_pages(repo_root: Path) -> list[str]:
    """The answer-corpus pages, sorted, as repo-relative paths — a DERIVED glob.

    Excludes README.md (already inlined via the llms.txt roster) and the generated
    index.jsonl (machine data, not prose). Pure on the repo root so the drift test
    can call it. The same derived-set discipline `discoverability_inventory` keeps:
    the glob IS the list, never a parallel hand-kept one.
    """
    out: list[str] = []
    for p in sorted((repo_root / ANSWERS_DIR).glob("*.md")):
        if p.name == "README.md":
            continue
        out.append(str(p.relative_to(repo_root)).replace("\\", "/"))
    return out


def assemble(repo_root: Path) -> str:
    """Concatenate the rostered documents + the full answer corpus into the text."""
    llms_text = (repo_root / LLMS).read_text(encoding="utf-8")
    chunks = [HEADER, BANNER]
    for path in roster(llms_text):
        body = (repo_root / path).read_text(encoding="utf-8").strip("\n")
        chunks.append(f"<!-- ====== source: {path} ====== -->\n\n{body}\n")
    # The answer corpus, inlined in full after the roster. A DISTINCT `answer:`
    # marker (not the roster's `source:` marker) keeps the roster drift test exact
    # — the roster markers still equal llms.txt's roster, no more, no less.
    chunks.append(ANSWERS_HEADER)
    for path in answer_pages(repo_root):
        body = (repo_root / path).read_text(encoding="utf-8").strip("\n")
        chunks.append(f"<!-- ====== answer: {path} ====== -->\n\n{body}\n")
    return "\n".join(chunks)


def _repo_root() -> Path:
    """The repo top-level — git's answer, NOT __file__ relative math.

    Same rationale as the release scripts (CLAUDE.md): this tool ships with the
    repo it operates on, so the git top-level is the honest root.
    """
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify llms-full.txt matches the assembly (exit 1 if not); write nothing",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    expected = assemble(root)
    target = root / LLMS_FULL
    actual = target.read_text(encoding="utf-8") if target.exists() else None

    if args.check:
        if actual != expected:
            print(
                "llms-full.txt is out of sync with llms.txt's roster — "
                "run: python scripts/build_llms_full.py",
                file=sys.stderr,
            )
            return 1
        print("llms-full.txt is in sync with llms.txt's roster.")
        return 0

    if actual == expected:
        print("llms-full.txt already up to date.")
        return 0
    target.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {target} from {len(roster((root / LLMS).read_text(encoding='utf-8')))} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
