"""Answer-corpus retrieval for the MCP server — the agent's "how do I X?" surface.

The DOS answer corpus (`docs/answers/*.md`) is 70+ sourced pages, each answering
one high-intent query. Every verdict tool here lets an agent CHECK a claim; this
module lets an agent ASK — score a question against the corpus and pull the
canonical, evidence-backed answer. It reads the generated machine index
(`docs/answers/index.jsonl`, built by `scripts/build_answers_index.py`), so it
serves whatever the corpus says, never a copy that drifts.

Design fence (CLAUDE.md): this lives in `dos_mcp`, the consumer package — NOT in
`src/dos/`. The kernel does not read `docs/` at runtime; the MCP server (which
already consumes the repo) does. Matching is deterministic lexical scoring (token
overlap + a small `difflib` ratio), no embedding dependency — the same default
`src/dos/drivers/similarity_judge.py` keeps (embeddings stay an optional seam).

Fail-soft: if the index is absent (an installed wheel ships no `docs/`), every
function returns an honest empty result with a one-line note, never raising — the
retrieval surface degrades to "not available here," exactly as the host registry
degrades to an empty list when the CLI is missing.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

# The index lives at <repo>/docs/answers/index.jsonl. From this module
# (src/dos_mcp/answers.py) the repo root is parents[2]. In an installed wheel
# there is no docs/ tree, so the file is simply absent and we fail soft.
_INDEX_PATH = Path(__file__).resolve().parents[2] / "docs" / "answers" / "index.jsonl"

_WORD_RE = re.compile(r"[a-z0-9]+")
# Tokens too generic to carry signal — every AEO query has them, so counting them
# would flatten the ranking toward "the longest answer wins."
_STOP = frozenset(
    "a an and the to of for in on is it my me i how do does can what why "
    "with that this you your they them their or not no as at be".split()
)


def _index_path() -> Path:
    return _INDEX_PATH


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(text.lower()) if t not in _STOP and len(t) > 1}


def load_rows() -> list[dict]:
    """The index rows, or [] if the index is absent (fail-soft, never raises)."""
    path = _index_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a corrupt line never sinks the whole corpus
    return rows


def _haystack(row: dict) -> str:
    """The searchable text of a row: its question + every query phrasing + answer.

    The query phrasings are the searcher's OWN words (the cheapest discovery win),
    so they carry the most weight for a colloquial question — included verbatim.
    """
    parts = [row.get("question", ""), *row.get("queries", []), row.get("answer", "")]
    return " ".join(parts)


def score(query: str, row: dict) -> float:
    """A deterministic relevance score in [0, 1] for `query` against one row.

    Token overlap (Jaccard-ish, weighted by how much of the QUERY is covered)
    plus a small `difflib` sequence ratio against the closest single query
    phrasing — overlap catches keyword hits, the ratio rewards a near-verbatim
    phrasing match. No clock, no network, no embedding: same input → same score.
    """
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0
    hay_tokens = _tokens(_haystack(row))
    overlap = len(q_tokens & hay_tokens) / len(q_tokens)  # fraction of query covered

    # best near-verbatim match against the question or any registered phrasing
    candidates = [row.get("question", ""), *row.get("queries", [])]
    q_low = query.lower().strip()
    best_ratio = max(
        (SequenceMatcher(None, q_low, c.lower()).ratio() for c in candidates if c),
        default=0.0,
    )
    # overlap dominates (keyword recall), the ratio breaks ties toward a phrasing hit
    return round(0.75 * overlap + 0.25 * best_ratio, 4)


def search(query: str, k: int = 3) -> list[dict]:
    """The top-`k` answer rows for `query`, each with its score, best first.

    Returns [] when the corpus is unavailable or the query is empty. Never raises.
    Each returned dict is the index row plus a `"score"` key.
    """
    rows = load_rows()
    if not rows or not query.strip():
        return []
    scored = sorted(
        ((score(query, r), r) for r in rows),
        key=lambda sr: sr[0],
        reverse=True,
    )
    out: list[dict] = []
    for s, r in scored[: max(1, k)]:
        if s <= 0.0 and out:
            break  # don't pad with zero-signal rows once we have at least one hit
        out.append({**r, "score": s})
    return out
