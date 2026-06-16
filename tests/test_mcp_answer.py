"""The agent-retrieval surface — dos_answer, dos://answers, and verdict back-links.

The DOS answer corpus is sourced and self-contained, but until now an agent that
loaded the MCP server could only CHECK claims (verify / arbitrate / refuse); it
could not ASK "how do I X?" and get the canonical answer. These tests pin the
three surfaces that close that gap:

  * `dos_answer(query)` — scores a question against the corpus index and returns
    the best-matching sourced pages (the primary retrieval surface);
  * `dos://answers` — the same corpus as a browsable question → URL resource;
  * `learn_more` — every verdict tool's return links to its canonical answer page,
    and every slug in the mapping resolves to a page that exists on disk (so a
    renamed/deleted page fails loudly, like test_answers.py's link resolver).

Source-tree-only via the index: an installed wheel ships no corpus, so the
retrieval degrades to an honest "not available here" — which these tests also
assert is non-crashing. If the `mcp` extra isn't installed, the module skips.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="dos-mcp needs the optional `mcp` extra")

from dos_mcp import answers as _answers
from dos_mcp.server import _ANSWER_FOR_TOOL, build_server

_REPO = Path(__file__).resolve().parents[1]
_ANSWERS_DIR = _REPO / "docs" / "answers"

_corpus_present = (_ANSWERS_DIR / "index.jsonl").is_file()
_needs_corpus = pytest.mark.skipif(
    not _corpus_present, reason="answer-corpus index only exists in the source tree"
)


def _tools(server) -> dict:
    return {t.name: t.fn for t in server._tool_manager.list_tools()}


# ---------------------------------------------------------------------------
# dos_answer — the retrieval tool
# ---------------------------------------------------------------------------
@_needs_corpus
def test_answer_tool_finds_the_canonical_page():
    """A real question's top hit is its canonical answer page."""
    answer = _tools(build_server())["dos_answer"]
    out = answer(query="how do I verify an AI agent actually did the work")
    assert out["count"] >= 1
    top = out["results"][0]
    assert top["slug"] == "how-to-verify-an-ai-agent-actually-did-the-work"
    assert top["url"].endswith("how-to-verify-an-ai-agent-actually-did-the-work.md")
    assert top["score"] > 0.0
    assert top["answer"].strip()  # the liftable one-line answer is carried


@_needs_corpus
@pytest.mark.parametrize(
    "query, expect_slug",
    [
        ("how do I stop two AI agents from overwriting each other's files",
         "how-to-stop-two-ai-agents-overwriting-each-other"),
        ("my AI writes tests that pass but assert nothing",
         "ai-generated-tests-that-pass-but-test-nothing"),
        ("is a cited legal case actually real",
         "how-to-verify-a-cited-legal-case-exists"),
        ("does my agent's commit message match what it actually changed",
         "does-the-commit-message-match-what-changed"),
    ],
)
def test_answer_tool_routes_colloquial_queries(query, expect_slug):
    """Colloquial, in-the-searcher's-words queries route to the right page."""
    answer = _tools(build_server())["dos_answer"]
    out = answer(query=query)
    assert out["results"], f"no hit for {query!r}"
    assert out["results"][0]["slug"] == expect_slug


@_needs_corpus
def test_answer_tool_respects_k():
    answer = _tools(build_server())["dos_answer"]
    out = answer(query="agent verification", k=2)
    assert len(out["results"]) <= 2


def test_answer_tool_never_raises_on_empty_or_junk():
    """An empty / nonsense query returns a valid envelope, never an exception."""
    answer = _tools(build_server())["dos_answer"]
    for q in ["", "   ", "zzzxqq nonsense token soup 12345"]:
        out = answer(query=q)
        assert out["query"] == q
        assert isinstance(out["results"], list)
        assert out["count"] == len(out["results"])
        # an empty result must carry a note explaining why (no silent blank)
        if not out["results"]:
            assert out.get("note")


@_needs_corpus
def test_answer_results_carry_machine_fields():
    """Each result is the index row (commands, queries, path) plus a score."""
    answer = _tools(build_server())["dos_answer"]
    out = answer(query="how do I prove a phase shipped from git history")
    r = out["results"][0]
    for field in ("slug", "question", "answer", "commands", "path", "url", "queries", "score"):
        assert field in r, f"result missing {field}"
    assert (_REPO / r["path"]).is_file()


# ---------------------------------------------------------------------------
# dos://answers — the browsable resource
# ---------------------------------------------------------------------------
def test_answers_resource_is_registered():
    server = build_server()
    static = {str(r.uri) for r in server._resource_manager.list_resources()}
    assert "dos://answers" in static


@_needs_corpus
def test_answers_resource_lists_every_page():
    """The resource renders one browsable link per corpus page."""
    server = build_server()
    contents = asyncio.run(server.read_resource("dos://answers"))
    body = contents[0].content if isinstance(contents, list) else contents
    text = body if isinstance(body, str) else getattr(body, "text", str(body))
    rows = _answers.load_rows()
    assert f"{len(rows)} sourced" in text
    # every page's URL appears in the rendered index
    for r in rows:
        assert r["url"] in text, f"{r['slug']} missing from dos://answers"


# ---------------------------------------------------------------------------
# learn_more — per-verdict deep-answer back-links
# ---------------------------------------------------------------------------
def test_every_mapped_slug_resolves_to_a_real_page():
    """Every slug in _ANSWER_FOR_TOOL names a page that exists on disk.

    The forgery-proof half: a renamed/deleted answer page fails HERE, the same
    discipline test_answers.py's link resolver enforces for the corpus itself.
    """
    missing = [
        slug for slug in set(_ANSWER_FOR_TOOL.values())
        if not (_ANSWERS_DIR / f"{slug}.md").is_file()
    ]
    assert not missing, f"learn_more slugs with no page on disk: {missing}"


def test_mapping_covers_every_verdict_tool():
    """Every verdict tool (all tools except the retrieval tool) has a back-link."""
    names = {t.name for t in build_server()._tool_manager.list_tools()}
    verdict_tools = names - {"dos_answer"}
    uncovered = verdict_tools - set(_ANSWER_FOR_TOOL)
    assert not uncovered, f"verdict tools with no learn_more mapping: {uncovered}"


def test_verify_return_carries_learn_more(tmp_path: Path):
    """A verdict tool's return is stamped with its canonical answer-page URL."""
    import subprocess

    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)

    verify = _tools(build_server())["dos_verify"]
    out = verify(plan="X", phase="Y", workspace=str(tmp_path))
    assert out["learn_more"].endswith("how-to-verify-an-ai-agent-actually-did-the-work.md")


def test_answer_tool_has_no_learn_more():
    """The retrieval tool itself carries no back-link (it IS the answer surface)."""
    answer = _tools(build_server())["dos_answer"]
    out = answer(query="how do I verify the work")
    assert "learn_more" not in out
