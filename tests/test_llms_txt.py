"""The llms.txt rot pin (docs/299 P1).

llms.txt is the curated index an arriving agent fetches first (the llmstxt.org
convention): an H1, a blockquote summary, then H2 sections of links. Nobody
re-reads it after it ships, so a renamed doc would leave a dead link there
forever — this suite resolves every repo-file link against the working tree and
pins the shape an llms.txt consumer parses. The README-assembly discipline
(tests/test_readme_assembly.py), applied to the agent-facing index.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LLMS = REPO / "llms.txt"

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
# A link that names a FILE in this repo, in either fetchable spelling.
REPO_FILE_RE = re.compile(
    r"https://(?:raw\.githubusercontent\.com/anthony-chaudhary/dos-kernel/master/"
    r"|github\.com/anthony-chaudhary/dos-kernel/blob/master/)(?P<path>[^)#?\s]+)"
)
# A link that names a DIRECTORY in this repo.
REPO_TREE_RE = re.compile(
    r"https://github\.com/anthony-chaudhary/dos-kernel/tree/master/(?P<path>[^)#?\s]+)"
)


def _text() -> str:
    return LLMS.read_text(encoding="utf-8")


def _links() -> list[tuple[str, str]]:
    return LINK_RE.findall(_text())


def test_llms_txt_is_spec_shaped() -> None:
    lines = [line for line in _text().splitlines() if line.strip()]
    assert lines[0].startswith("# "), "llms.txt must open with an H1"
    assert lines[1].startswith(">"), "the H1 must be followed by the blockquote summary"
    assert any(line.startswith("## ") for line in lines), "llms.txt must carry H2 link sections"


def test_llms_txt_has_links() -> None:
    assert len(_links()) >= 8, "llms.txt is an index; a handful of links is the point"


def test_every_link_target_is_absolute() -> None:
    # A consumer fetches llms.txt as raw bytes; a relative target resolves nowhere.
    relative = [url for _, url in _links() if not url.startswith("https://")]
    assert not relative, f"non-absolute link targets in llms.txt: {relative}"


def test_every_repo_file_link_resolves() -> None:
    dead = []
    for _, url in _links():
        file_match = REPO_FILE_RE.match(url)
        if file_match and not (REPO / file_match.group("path")).is_file():
            dead.append(url)
        tree_match = REPO_TREE_RE.match(url)
        if tree_match and not (REPO / tree_match.group("path")).is_dir():
            dead.append(url)
    assert not dead, f"dead repo links in llms.txt: {dead}"


def test_no_local_machine_paths() -> None:
    # The route-privacy-at-authoring-time rule, pinned for the agent-facing index.
    assert not re.search(r"[A-Za-z]:\\", _text()), "llms.txt must carry no local absolute path"
