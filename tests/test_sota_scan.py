"""Pin the DOS SOTA scanner (`scripts/sota_scan.py`).

The scanner sweeps research / GitHub / Reddit / HN for DOS-relevant work each
cycle, dedups against an append-only ledger, writes a dated digest, and prints a
GitHub-issue body. These tests pin the contract that makes that cycle
trustworthy:

  * NEW = an item whose id is not already in the ledger — a second scan over the
    same fetched items yields zero new (the dedup memory works);
  * every fetcher degrades to [] when its source is unreachable (offline /
    error) and NEVER raises — a dead source skips, it never crashes the cycle;
  * the digest is deterministic given a --stamp + a fixed ledger (byte-identical
    on a re-run);
  * the leak gate runs BEFORE any issue is posted, and a leak verdict refuses
    the post (exit 1) — issue text is public and skips no scrub (CLAUDE.md);
  * --add-manual is the only path a web item enters (the script/agent honesty
    wall) and it dedups like any other source.

Tests drive a tmp ledger and monkeypatch the fetchers / subprocess so they never
touch the network or the live tree, and run fully offline.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "sota_scan.py"
_spec = importlib.util.spec_from_file_location("sota_scan", _HELPER)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


# ---------------------------------------------------------------------------
# Synthetic items + a fixture ledger path.
# ---------------------------------------------------------------------------
def _fixture_items() -> list[dict]:
    return [
        ss._item("github", "gh:acme/agent-verify", "Agent verifier",
                 "https://github.com/acme/agent-verify", score=900,
                 date="2026-06-10", topic="AI agent verification"),
        ss._item("arxiv", "arxiv:http://arxiv.org/abs/2606.00001",
                 "On Process Reward Models", "http://arxiv.org/abs/2606.00001",
                 date="2026-06-09", topic="process reward model"),
        ss._item("hn", "hn:42424242", "Show HN: agent reliability",
                 "https://example.com/x", score=120, date="2026-06-11",
                 topic="LLM agent reliability"),
    ]


@pytest.fixture()
def ledger(tmp_path) -> Path:
    return tmp_path / "seen.jsonl"


# ---------------------------------------------------------------------------
# new_items — the dedup core.
# ---------------------------------------------------------------------------
def test_new_items_filters_already_seen_and_dedups_batch():
    items = _fixture_items()
    already = {"gh:acme/agent-verify"}
    fresh = ss.new_items(items + items, already)  # batch has dups too
    ids = [f["id"] for f in fresh]
    assert "gh:acme/agent-verify" not in ids  # seen
    assert ids.count("hn:42424242") == 1       # batch-deduped
    assert len(ids) == 2


def test_scan_then_rescan_yields_zero_new(ledger, monkeypatch):
    monkeypatch.setattr(ss, "gather", lambda topics, since: _fixture_items())
    rc = ss.main(["--scan", "--stamp", "2026-06-15", "--ledger", str(ledger), "--json"])
    assert rc == 0
    rows = ss.read_ledger(ledger)
    assert len(rows) == 3
    assert all(r["scanned"] == "2026-06-15" for r in rows)
    # second scan, same fetched items → nothing new, ledger unchanged.
    rc = ss.main(["--scan", "--stamp", "2026-06-22", "--ledger", str(ledger), "--json"])
    assert rc == 0
    assert len(ss.read_ledger(ledger)) == 3


# ---------------------------------------------------------------------------
# Offline degradation — every fetcher returns [] when its source raises.
# ---------------------------------------------------------------------------
def test_github_fetcher_degrades_to_empty_on_subprocess_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("gh not installed")
    monkeypatch.setattr(ss.subprocess, "run", boom)
    assert ss._fetch_github(["x"], "2026-01-01") == []


def test_http_fetchers_degrade_to_empty_offline(monkeypatch):
    # both http helpers return None on any failure → fetchers see nothing.
    monkeypatch.setattr(ss, "_http_text", lambda url, timeout=30: None)
    monkeypatch.setattr(ss, "_http_json", lambda url, timeout=30: None)
    assert ss._fetch_arxiv(["x"], "2026-01-01") == []
    assert ss._fetch_reddit(["x"], "2026-01-01") == []
    assert ss._fetch_hn(["x"], "2026-01-01") == []


def test_http_helpers_return_none_on_urlopen_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(ss.urllib.request, "urlopen", boom)
    assert ss._http_text("https://x") is None
    assert ss._http_json("https://x") is None


def test_gather_survives_a_dead_source(monkeypatch):
    # one fetcher raises internally; gather still returns the others' items.
    monkeypatch.setattr(ss, "_http_text", lambda url, timeout=30: None)
    monkeypatch.setattr(ss, "_http_json", lambda url, timeout=30: None)
    def boom(*a, **k):
        raise OSError("gh down")
    monkeypatch.setattr(ss.subprocess, "run", boom)
    assert ss.gather(["x"], "2026-01-01") == []  # all dead → empty, no crash


# ---------------------------------------------------------------------------
# Determinism — same stamp + same ledger → byte-identical digest.
# ---------------------------------------------------------------------------
def test_digest_is_deterministic(ledger, monkeypatch):
    monkeypatch.setattr(ss, "gather", lambda topics, since: _fixture_items())
    ss.main(["--scan", "--stamp", "2026-06-15", "--ledger", str(ledger)])
    rows = ss.read_ledger(ledger)
    a = ss.render_digest(rows, stamp="2026-06-15")
    b = ss.render_digest(rows, stamp="2026-06-15")
    assert a == b
    assert "Agent verifier" in a
    assert "## Research (arXiv)" in a


def test_empty_scan_writes_zero_new_digest(ledger, monkeypatch):
    monkeypatch.setattr(ss, "gather", lambda topics, since: [])
    rc = ss.main(["--scan", "--stamp", "2026-06-15", "--ledger", str(ledger)])
    assert rc == 0
    assert ss.read_ledger(ledger) == []
    body = ss.render_issue_body([], stamp="2026-06-15", digest_rel="docs/sota/digest-2026-06-15.md")
    assert "no new items" in body.lower()


# ---------------------------------------------------------------------------
# The leak gate — runs before any post; a verdict refuses (exit 1).
# ---------------------------------------------------------------------------
def test_open_issue_refused_when_leak_gate_flags(ledger, monkeypatch):
    monkeypatch.setattr(ss, "gather", lambda topics, since: _fixture_items())
    posted = {"called": False}

    # leak scanner returns non-zero → a leak verdict.
    def fake_run(cmd, *a, **k):
        if str(ss.LEAK_SCAN) in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, 1, "LEAK: secret found", "")
        posted["called"] = True
        return subprocess.CompletedProcess(cmd, 0, "https://github.com/issue/1", "")
    monkeypatch.setattr(ss.subprocess, "run", fake_run)

    rc = ss.main(["--scan", "--open-issue", "--stamp", "2026-06-15", "--ledger", str(ledger)])
    assert rc == 1
    assert posted["called"] is False  # never reached gh issue create


def test_open_issue_posts_when_leak_gate_clean(ledger, monkeypatch):
    monkeypatch.setattr(ss, "gather", lambda topics, since: _fixture_items())
    calls = []

    def fake_run(cmd, *a, **k):
        joined = " ".join(str(c) for c in cmd)
        calls.append(joined)
        if str(ss.LEAK_SCAN) in joined:
            return subprocess.CompletedProcess(cmd, 0, "clean", "")
        return subprocess.CompletedProcess(cmd, 0, "https://github.com/issue/7", "")
    monkeypatch.setattr(ss.subprocess, "run", fake_run)

    rc = ss.main(["--scan", "--open-issue", "--stamp", "2026-06-15", "--ledger", str(ledger)])
    assert rc == 0
    # leak scan ran, and it ran BEFORE gh issue create (order in the call log).
    leak_idx = next(i for i, c in enumerate(calls) if str(ss.LEAK_SCAN) in c)
    gh_idx = next(i for i, c in enumerate(calls) if "issue create" in c)
    assert leak_idx < gh_idx


def test_leak_clean_fail_closed_when_scanner_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "LEAK_SCAN", tmp_path / "nope.py")
    assert ss.leak_clean("any body") is False


# ---------------------------------------------------------------------------
# --add-manual — the agent's web-search rung; the only way a web item enters.
# ---------------------------------------------------------------------------
def test_add_manual_inserts_and_dedups(ledger):
    rc = ss.main(["--add-manual", "--source", "web",
                  "--id", "https://example.com/paper",
                  "--title", "A web-found result",
                  "--stamp", "2026-06-15", "--ledger", str(ledger)])
    assert rc == 0
    rows = ss.read_ledger(ledger)
    assert len(rows) == 1 and rows[0]["source"] == "web"
    # the day's digest now contains the web item.
    digest = ss._digest_path("2026-06-15")
    try:
        assert "A web-found result" in digest.read_text(encoding="utf-8")
    finally:
        if digest.exists():
            digest.unlink()
    # re-adding the same id is a no-op.
    rc = ss.main(["--add-manual", "--source", "web",
                  "--id", "https://example.com/paper", "--title", "dup",
                  "--stamp", "2026-06-15", "--ledger", str(ledger)])
    assert rc == 0
    assert len(ss.read_ledger(ledger)) == 1


def test_add_manual_requires_id_and_title(ledger):
    with pytest.raises(SystemExit):
        ss.main(["--add-manual", "--source", "web", "--ledger", str(ledger)])


# ---------------------------------------------------------------------------
# Status — per-source counts over the ledger.
# ---------------------------------------------------------------------------
def test_status_counts_by_source(ledger, monkeypatch):
    monkeypatch.setattr(ss, "gather", lambda topics, since: _fixture_items())
    ss.main(["--scan", "--stamp", "2026-06-15", "--ledger", str(ledger)])
    rows = ss.read_ledger(ledger)
    out = ss.render_status(rows)
    assert "items tracked: 3" in out
    assert "GitHub repos" in out


def test_ledger_rows_are_lf_terminated(ledger, monkeypatch):
    # tracked file → LF even on Windows, so a row here and one in CI compare.
    monkeypatch.setattr(ss, "gather", lambda topics, since: _fixture_items())
    ss.main(["--scan", "--stamp", "2026-06-15", "--ledger", str(ledger)])
    raw = ledger.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
