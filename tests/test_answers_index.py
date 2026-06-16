"""The docs/answers/index.jsonl assembly drift gate — the machine-ingestible corpus.

`index.jsonl` is generated: one JSON row per answer page, built by
`scripts/build_answers_index.py` from the corpus + the ARRIVAL_QUERIES map. It is
the surface an agent (the MCP `dos_answer` tool, a retrieval client) scores a
question against and pulls the canonical answer — so a drifted index would serve
stale or dead answers. This gate pins the one fact that prevents that: the file
byte-equals the assembly, and every row is structurally sound (all fields present,
the path on disk, the url in the corpus's blob spelling).

The sibling of `tests/test_llms_full.py` and `tests/test_readme_assembly.py`,
aimed at the agent-retrieval index. Source-tree-only: an installed wheel ships
neither the corpus nor the builder, so the module skips when they're absent.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_BUILD_PY = _REPO / "scripts" / "build_answers_index.py"
_ANSWERS = _REPO / "docs" / "answers"
_INDEX = _ANSWERS / "index.jsonl"

pytestmark = pytest.mark.skipif(
    not (_BUILD_PY.exists() and _ANSWERS.exists()),
    reason="the answer corpus / build script only exist in the source tree",
)

_BLOB_RE = re.compile(
    r"^https://github\.com/anthony-chaudhary/dos-kernel/blob/master/(?P<path>\S+)$"
)
_REQUIRED_FIELDS = {"slug", "question", "answer", "commands", "path", "url", "queries"}


def _load_builder():
    spec = importlib.util.spec_from_file_location("_build_answers_index", _BUILD_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows() -> list[dict]:
    return [json.loads(line) for line in _INDEX.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_index_matches_assembly() -> None:
    """index.jsonl is byte-identical to the assembly of the corpus."""
    mod = _load_builder()
    expected = mod.assemble(_REPO)
    actual = _INDEX.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/answers/index.jsonl is out of sync with the corpus — run: "
        "python scripts/build_answers_index.py"
    )


def test_row_count_matches_the_glob() -> None:
    """One row per answer page — the index covers the corpus, no more, no less."""
    pages = sorted(
        p.stem for p in _ANSWERS.glob("*.md") if p.name != "README.md"
    )
    slugs = sorted(r["slug"] for r in _rows())
    assert slugs == pages
    assert len(pages) >= 5, "the shipped corpus floor (docs/325)"


def test_every_row_is_structurally_sound() -> None:
    """Each row carries every field, a question, an answer, and a real path."""
    for r in _rows():
        assert _REQUIRED_FIELDS <= set(r), f"{r.get('slug')}: missing fields {_REQUIRED_FIELDS - set(r)}"
        assert r["question"].strip(), f"{r['slug']}: empty question"
        assert r["answer"].strip(), f"{r['slug']}: empty answer (no blockquote lifted)"
        assert (_REPO / r["path"]).is_file(), f"{r['slug']}: path does not resolve: {r['path']}"
        assert isinstance(r["commands"], list)
        assert isinstance(r["queries"], list)


def test_every_url_is_the_blob_spelling_and_resolves() -> None:
    """The url is the fetchable github blob spelling, and names the row's own path."""
    for r in _rows():
        m = _BLOB_RE.match(r["url"])
        assert m, f"{r['slug']}: url is not the blob spelling: {r['url']}"
        assert m.group("path") == r["path"], f"{r['slug']}: url path != row path"
        assert (_REPO / m.group("path")).is_file(), f"{r['slug']}: url path missing on disk"


def test_questions_are_unique() -> None:
    """No two pages claim the same canonical question (the index is a 1:1 map)."""
    questions = [r["question"] for r in _rows()]
    dupes = {q for q in questions if questions.count(q) > 1}
    assert not dupes, f"duplicate questions in the index: {dupes}"


def test_no_local_machine_paths() -> None:
    """The route-privacy-at-authoring-time rule, pinned for the index."""
    assert not re.search(r"[A-Za-z]:\\", _INDEX.read_text(encoding="utf-8")), (
        "index.jsonl must carry no local absolute path"
    )
