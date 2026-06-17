"""account_ledger + run_id.account_name — the independent-session-tracking surface.

Two halves of the goal's "better independent session tracking": (1) a run can be
STAMPED with the seat it used (`run_id.RunId.account_name`, carried across a child
boundary via `CID_ACCOUNT`), and (2) a per-account append-only ledger
(`.dos/accounts/<name>/{runs,failures,tokens}.jsonl`) folds "which runs / walls /
spend belong to this seat" without scanning the run tree.

Both are vendor-neutral: the account name is a caller-supplied label, never derived
(the kernel names no account mechanism). The ledger is fail-soft I/O — a write never
breaks a launch, a read never breaks a report.
"""
from __future__ import annotations

from pathlib import Path

from dos import account_ledger as al
from dos import config as _config
from dos import run_id as rid


def _cfg(tmp_path: Path, monkeypatch):
    # default_config(root) builds a FRESH config rooted at tmp_path — unlike the
    # cached _config.active(), so each test's .dos/accounts/ is isolated.
    return _config.default_config(tmp_path)


# --------------------------------------------------------------------------- #
# run_id.account_name — the seat stamp
# --------------------------------------------------------------------------- #
def test_runid_account_name_default_none_and_absent_from_dict():
    r = rid.mint("dispatch", clock_ms=lambda: 1, entropy=lambda: 2)
    assert r.account_name is None
    # additive schema: a run with no seat label produces the legacy run.json shape
    assert "account_name" not in r.to_dict()


def test_runid_account_name_stamped_and_emitted():
    r = rid.mint("dispatch", account_name="acctA", clock_ms=lambda: 1, entropy=lambda: 2)
    assert r.account_name == "acctA"
    assert r.to_dict()["account_name"] == "acctA"


def test_runid_child_inherits_parent_account():
    parent = rid.mint("root", account_name="acctA", clock_ms=lambda: 1, entropy=lambda: 2)
    child = rid.mint("child", parent=parent, clock_ms=lambda: 2, entropy=lambda: 3)
    assert child.account_name == "acctA"  # a child runs on the parent's seat by default


def test_runid_child_can_override_account():
    parent = rid.mint("root", account_name="acctA", clock_ms=lambda: 1, entropy=lambda: 2)
    child = rid.mint("child", parent=parent, account_name="acctB",
                     clock_ms=lambda: 2, entropy=lambda: 3)
    assert child.account_name == "acctB"


def test_runid_lineage_env_carries_account():
    r = rid.mint("dispatch", account_name="acctA", clock_ms=lambda: 1, entropy=lambda: 2)
    env = rid.lineage_env(r)
    assert env[rid.ENV_ACCOUNT] == "acctA"
    # round-trips: a child minted from that env inherits the seat
    child = rid.mint_child_from_env("child", env=env, clock_ms=lambda: 2, entropy=lambda: 3)
    assert child.account_name == "acctA"


def test_runid_lineage_env_omits_account_when_unset():
    r = rid.mint("dispatch", clock_ms=lambda: 1, entropy=lambda: 2)
    assert rid.ENV_ACCOUNT not in rid.lineage_env(r)


# --------------------------------------------------------------------------- #
# account_ledger — append-only per-seat streams
# --------------------------------------------------------------------------- #
def test_record_run_and_read_back(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert al.record_run(cfg, "acctA", "CID-1", process_id="PROC-dispatch", now_ms=1000)
    runs = al.read(cfg, "acctA", al.LEDGER_RUNS)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "CID-1"
    assert runs[0]["process_id"] == "PROC-dispatch"
    assert runs[0]["ts_ms"] == 1000
    # the file lands under the workspace's own .dos/accounts/, never the real repo
    assert (al.account_dir(cfg, "acctA") / "runs.jsonl").exists()
    assert al.accounts_dir(cfg) == tmp_path / ".dos" / "accounts"


def test_append_is_append_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    al.record_run(cfg, "acctA", "CID-1", now_ms=1)
    al.record_run(cfg, "acctA", "CID-2", now_ms=2)
    runs = al.read(cfg, "acctA", al.LEDGER_RUNS)
    assert [r["run_id"] for r in runs] == ["CID-1", "CID-2"]  # ordered, both kept


def test_failures_and_tokens_streams(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    al.record_failure(cfg, "acctA", reason="walled", category="transient_overload",
                      run_id="CID-1", now_ms=1)
    al.record_tokens(cfg, "acctA", tokens=1234, run_id="CID-1", now_ms=2)
    al.record_tokens(cfg, "acctA", tokens=766, run_id="CID-2", now_ms=3)
    fails = al.read(cfg, "acctA", al.LEDGER_FAILURES)
    assert fails[0]["category"] == "transient_overload"
    s = al.summary(cfg, "acctA")
    assert s == {"account": "acctA", "runs": 0, "failures": 1, "tokens": 2000}


def test_known_accounts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    al.record_run(cfg, "acctA", "CID-1")
    al.record_run(cfg, "acctB", "CID-2")
    assert al.known_accounts(cfg) == ["acctA", "acctB"]


def test_unsafe_name_is_sanitized_not_escaped(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # a path-traversal-ish label is sanitized to a flat safe stem, never escapes
    assert al.record_run(cfg, "../../etc", "CID-1", now_ms=1)
    d = al.account_dir(cfg, "../../etc")
    assert d is not None
    assert d.parent == al.accounts_dir(cfg)        # stays under .dos/accounts/
    assert ".." not in d.name


def test_empty_name_is_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert al.account_dir(cfg, "") is None
    assert al.record_run(cfg, "", "CID-1") is False


def test_unknown_ledger_is_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert al.append(cfg, "acctA", "bogus", {"x": 1}) is False
    assert al.read(cfg, "acctA", "bogus") == []


def test_read_missing_is_empty(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert al.read(cfg, "never-seen", al.LEDGER_RUNS) == []
    assert al.summary(cfg, "never-seen") == {
        "account": "never-seen", "runs": 0, "failures": 0, "tokens": 0}


def test_torn_line_is_skipped(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    al.record_run(cfg, "acctA", "CID-1", now_ms=1)
    # simulate a crash mid-write: a torn/garbage trailing line
    path = al.account_dir(cfg, "acctA") / "runs.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"run_id": "CID-2", "ts_ms": 2\n')  # no closing brace
    runs = al.read(cfg, "acctA", al.LEDGER_RUNS)
    assert [r["run_id"] for r in runs] == ["CID-1"]  # torn tail skipped, prefix intact


def test_ts_ms_autostamped_when_absent(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    al.record_run(cfg, "acctA", "CID-1")  # no now_ms
    runs = al.read(cfg, "acctA", al.LEDGER_RUNS)
    assert isinstance(runs[0]["ts_ms"], int) and runs[0]["ts_ms"] > 0
