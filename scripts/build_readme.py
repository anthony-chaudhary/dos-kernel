#!/usr/bin/env python
"""build_readme.py — assemble README.md from its section parts.

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four things
> live OUTSIDE the four layers"). Like `build_plugin.py`, this consumes the repo and
> the kernel is unaware it exists. It keeps ONE fact true: that the rendered
> `README.md` is a faithful concatenation of its single-sourced section parts
> (`docs/readme/*.md`), never a hand-edited fork that drifts.

Why parts at all
================

The README is the front door and it is long — a dozen sections, two figure
blocks, a 70-line CLI listing. Editing one section inside an 800-line file means
re-reading the other 750 lines for collateral damage, and two concurrent edits
to different sections collide on the same path. Split into one file per section
(the same disjoint-lanes move the arbiter makes on the source tree) and each
edit touches only its own part; this script folds them back into the one
`README.md` GitHub renders.

Assembly is deliberately dumb: the parts concatenate in FILENAME order
(`00_…`, `10_…`, …, `95_…` — renumber to reorder sections), joined by a single
blank line, with a generated-file banner on top. No manifest, no templating —
the directory listing IS the table of contents.

It is idempotent: running it twice produces byte-identical output (LF line
endings, single trailing newline). `--check` makes no changes and exits
non-zero if README.md is out of sync — the mode `tests/test_readme_assembly.py`
runs, so a hand edit to README.md that bypasses the parts fails the suite
instead of silently forking the source of truth.

Usage
=====

    python scripts/build_readme.py            # regenerate README.md
    python scripts/build_readme.py --check    # verify in sync (exit 1 if not), write nothing
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PARTS_DIR = Path("docs") / "readme"
README = Path("README.md")

BANNER = (
    "<!-- GENERATED FILE — do not edit README.md directly.\n"
    "     The source of truth is docs/readme/ (one file per section, assembled\n"
    "     in filename order). Edit the part, then run:\n"
    "         python scripts/build_readme.py\n"
    "     tests/test_readme_assembly.py pins this file to the parts. -->\n"
)


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


def assemble(parts_dir: Path) -> str:
    """Concatenate the section parts, in filename order, into the README text.

    Pure on its inputs (no git, no cwd) so the drift test can call it directly.
    """
    parts = sorted(p for p in parts_dir.glob("*.md") if p.is_file())
    if not parts:
        raise FileNotFoundError(f"no README parts found under {parts_dir}")
    chunks = [p.read_text(encoding="utf-8").strip("\n") for p in parts]
    return BANNER + "\n" + "\n\n".join(chunks) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify README.md matches the parts (exit 1 if not); write nothing",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    expected = assemble(root / PARTS_DIR)
    readme = root / README
    actual = readme.read_text(encoding="utf-8") if readme.exists() else None

    if args.check:
        if actual != expected:
            print(
                "README.md is out of sync with docs/readme/ — "
                "run: python scripts/build_readme.py",
                file=sys.stderr,
            )
            return 1
        print("README.md is in sync with docs/readme/.")
        return 0

    if actual == expected:
        print("README.md already up to date.")
        return 0
    readme.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {readme} from {len(list((root / PARTS_DIR).glob('*.md')))} parts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
