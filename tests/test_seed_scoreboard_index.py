"""Pin the seeded-index orchestrator (`scripts/seed_scoreboard_index.py`, #98).

The orchestrator is the discovery fan-out: it fans the claim-vs-diff verdict out
across a corpus, rendering a NAMED page only where the verdict is CLEAN. Its two
load-bearing honesty rules are inherited from the renderer/sweep, and these tests
re-assert them at the orchestrator boundary so a future edit can't quietly break
them:

  * §2 — a non-CLEAN verdict is NEVER a named page, and the withheld repo's name
    appears NOWHERE (not the index, not the manifest). This is the rule that
    keeps a wall of green from carrying a soft accusation in its margins.
  * §4 — the corpus floor is mechanical: below-stars / stale / fork / archived /
    outreach-conflict / unreadable-metadata each fold to one closed reason.

Synthetic + no network: every `gh`/clone/sweep boundary is monkeypatched, so the
suite is deterministic and offline. The live proof is run by hand (recorded in
the commit body), not here.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "seed_scoreboard_index.py"
_spec = importlib.util.spec_from_file_location("seed_scoreboard_index", _HELPER)
ssi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ssi)

_B = "a" * 40
_H = "b" * 40


def _summary(*, unwitnessed: int):
    """A synthetic per-repo sweep summary with `unwitnessed` over-claims."""
    return {
        "commits": 50, "checkable": 30, "witnessed": 30 - unwitnessed,
        "unwitnessed": unwitnessed, "abstained": 20, "by_kind": {},
        "unwitnessed_shas": ["c" * 40] * unwitnessed,
    }


# ---------------------------------------------------------------------------
# §4 — the mechanical corpus floor, each exclusion to a closed reason.
# ---------------------------------------------------------------------------

_GOOD_META = {"stargazerCount": 600, "pushedAt": "2026-06-01",
              "isFork": False, "isArchived": False}


def test_floor_keeps_a_qualifying_repo():
    assert ssi.classify_candidate(
        "a/b", _GOOD_META, min_stars=500, active_days=90,
        excluded=set(), now_iso="2026-06-13") is None


def test_floor_excludes_below_stars():
    meta = {**_GOOD_META, "stargazerCount": 10}
    assert ssi.classify_candidate("a/b", meta, min_stars=500, active_days=90,
                                  excluded=set(), now_iso="2026-06-13") == ssi.EXCL_BELOW_STARS


def test_floor_excludes_stale():
    meta = {**_GOOD_META, "pushedAt": "2025-01-01"}
    assert ssi.classify_candidate("a/b", meta, min_stars=500, active_days=90,
                                  excluded=set(), now_iso="2026-06-13") == ssi.EXCL_STALE


def test_floor_excludes_fork():
    meta = {**_GOOD_META, "isFork": True}
    assert ssi.classify_candidate("a/b", meta, min_stars=500, active_days=90,
                                  excluded=set(), now_iso="2026-06-13") == ssi.EXCL_FORK


def test_floor_excludes_archived():
    meta = {**_GOOD_META, "isArchived": True}
    assert ssi.classify_candidate("a/b", meta, min_stars=500, active_days=90,
                                  excluded=set(), now_iso="2026-06-13") == ssi.EXCL_ARCHIVED


def test_floor_excludes_outreach_conflict():
    # the §4 'do not grade whom we court' rule — and it precedes the metadata
    # read (an in-flight repo is excluded even if we never fetch its stars).
    assert ssi.classify_candidate("a/b", None, min_stars=500, active_days=90,
                                  excluded={"a/b"}, now_iso="2026-06-13") == ssi.EXCL_OUTREACH


def test_floor_excludes_unreadable_metadata_conservatively():
    # a repo whose floor we can't read is NEVER paged (fail-closed).
    assert ssi.classify_candidate("a/b", None, min_stars=500, active_days=90,
                                  excluded=set(), now_iso="2026-06-13") == ssi.EXCL_META_FAIL


def test_floor_excludes_too_large_when_budget_set():
    # diskUsage is in KB; 2 GB repo with a 1000 MB budget → TOO_LARGE. This is
    # the guard for the measured failure: a 1GB+ repo can't clone in a session.
    meta = {**_GOOD_META, "diskUsage": 2_000_000}  # ~1.9 GB
    assert ssi.classify_candidate("a/b", meta, min_stars=500, active_days=90,
                                  excluded=set(), now_iso="2026-06-13",
                                  max_mb=1000) == ssi.EXCL_TOO_LARGE


def test_size_guard_off_by_default_keeps_large_repo():
    # max_mb=0 (the default) means the guard is off — a large repo is kept.
    meta = {**_GOOD_META, "diskUsage": 2_000_000}
    assert ssi.classify_candidate("a/b", meta, min_stars=500, active_days=90,
                                  excluded=set(), now_iso="2026-06-13",
                                  max_mb=0) is None


def test_every_exclusion_reason_is_from_the_closed_set():
    for r in ssi.EXCL_REASONS:
        assert r in {ssi.EXCL_BELOW_STARS, ssi.EXCL_STALE, ssi.EXCL_FORK,
                     ssi.EXCL_ARCHIVED, ssi.EXCL_OUTREACH, ssi.EXCL_META_FAIL,
                     ssi.EXCL_TOO_LARGE}


def test_floor_keeps_a_repo_with_no_push_events():
    # gh returns pushedAt:null (→ None) for a repo with no push events; the
    # floor must treat can't-prove-stale as fresh, never crash on int('None').
    meta = {**_GOOD_META, "pushedAt": None}
    assert ssi.classify_candidate(
        "a/b", meta, min_stars=500, active_days=90,
        excluded=set(), now_iso="2026-06-13") is None


# ---------------------------------------------------------------------------
# slug identity — org+name, so same-named repos from different orgs never
# collide onto one per-repo JSON / clone dir (one repo aliasing another's
# verdict is the §2 honesty rule the orchestrator stakes itself on).
# ---------------------------------------------------------------------------


def test_slug_keeps_org_so_same_name_different_org_dont_collide():
    a = ssi._slug("openai/foo")
    b = ssi._slug("microsoft/foo")
    assert a != b, "org-stripped slug aliases two distinct repos"
    # the URL form and the bare owner/repo form must agree — the sweep keys by
    # the clone URL, the orchestrator looks up by owner/repo.
    assert ssi._slug("https://github.com/openai/foo") == a
    assert ssi._slug("https://github.com/openai/foo.git") == a


# ---------------------------------------------------------------------------
# §2 — a CLEAN verdict renders a named page; a non-CLEAN one is withheld.
# ---------------------------------------------------------------------------


def test_clean_repo_renders_a_named_page():
    clean = {"repo": "https://github.com/good/repo", "full_name": "good/repo",
             "summary": _summary(unwitnessed=0)}
    result = ssi.render_one(clean, base_sha=_B, head_sha=_H,
                            rendered="2026-06-13", auditor="dos-kernel test")
    assert result is not None
    markdown, state = result
    assert state == "CLEAN"
    assert "good/repo" in markdown


def test_non_clean_repo_is_withheld_not_rendered():
    drift = {"repo": "https://github.com/bad/repo", "full_name": "bad/repo",
             "summary": _summary(unwitnessed=1)}  # a flag, no adjudication record
    # the renderer's §2 Refusal (seeded tier publishes CLEAN only) → None.
    assert ssi.render_one(drift, base_sha=_B, head_sha=_H,
                          rendered="2026-06-13", auditor="dos-kernel test") is None


def test_clean_but_thin_denominator_is_withheld():
    # a CLEAN verdict over too few checkable claims is not a real trust signal —
    # withheld by the small-denominator floor (the §4 ≥20 intent on the page's
    # checkable denominator). 3 checkable < a 20 floor → withheld.
    thin = {"repo": "https://github.com/tiny/repo", "full_name": "tiny/repo",
            "summary": {"commits": 5, "checkable": 3, "witnessed": 3,
                        "unwitnessed": 0, "abstained": 2, "by_kind": {},
                        "unwitnessed_shas": []}}
    assert ssi.render_one(thin, base_sha=_B, head_sha=_H, rendered="2026-06-13",
                          auditor="dos-kernel test", min_checkable=20) is None
    # …but with the floor off (0) the same CLEAN page renders.
    assert ssi.render_one(thin, base_sha=_B, head_sha=_H, rendered="2026-06-13",
                          auditor="dos-kernel test", min_checkable=0) is not None


def test_clean_with_substantive_denominator_renders():
    # the synthetic _summary has 30 checkable, 0 unwitnessed → clears a 20 floor.
    clean = {"repo": "https://github.com/big/repo", "full_name": "big/repo",
             "summary": _summary(unwitnessed=0)}
    r = ssi.render_one(clean, base_sha=_B, head_sha=_H, rendered="2026-06-13",
                       auditor="dos-kernel test", min_checkable=20)
    assert r is not None and r[1] == "CLEAN"


# ---------------------------------------------------------------------------
# the index root — published (named) pages only; coverage is a count.
# ---------------------------------------------------------------------------


def test_index_lists_published_only_withheld_is_a_count():
    idx = ssi.render_index(["good/repo"], audited=3, withheld=2,
                           rendered="2026-06-13")
    assert "good/repo" in idx          # the clean page is named + linked
    assert "[good/repo](good/repo.md)" in idx
    assert "2 withheld" in idx         # withheld is reported as a NUMBER
    assert "3 repositories audited" in idx


def test_index_never_names_a_withheld_repo():
    # the structural rule: a non-published repo's name appears nowhere on the
    # index, not even as 'pending' (docs/311 §2).
    idx = ssi.render_index(["good/repo"], audited=2, withheld=1,
                           rendered="2026-06-13")
    assert "bad/repo" not in idx
    # 'pending' must not appear as a per-repo status line (the soft-accusation
    # the §2 rule forbids). The empty-seeded placeholder is a different word.
    assert "pending" not in idx.lower()


def test_index_self_page_is_a_distinct_section_not_under_clean():
    # page #1 (the auditor's own repo) publishes its own verdict whatever it is;
    # it is NOT listed under the seeded CLEAN-only section.
    idx = ssi.render_index([], audited=1, withheld=0, rendered="2026-06-13",
                           self_page="anthony-chaudhary/dos-kernel")
    assert "Page #1" in idx
    assert "anthony-chaudhary/dos-kernel" in idx
    # the seeded section is honest that it is empty until the corpus run publishes
    assert "none yet" in idx


# ---------------------------------------------------------------------------
# end-to-end run() — monkeypatched gh/sweep; the manifest carries no
# un-published foreign name (the anonymization invariant at the orchestrator).
# ---------------------------------------------------------------------------


def test_run_publishes_clean_withholds_drift_and_leaks_no_withheld_name(tmp_path, monkeypatch):
    # distinct names so the per-repo slugs don't collide.
    candidates = [("good/alpha", "claude"), ("bad/beta", "devin"),
                  ("small/gamma", "aider")]

    # gh metadata: good + bad qualify; small is below stars (excluded pre-sweep).
    def fake_meta(full, timeout=30):
        if full == "small/gamma":
            return {**_GOOD_META, "stargazerCount": 10}
        return dict(_GOOD_META)
    monkeypatch.setattr(ssi, "fetch_meta", fake_meta)

    # the sweep boundary: write per-repo JSON for the two KEPT repos (good
    # clean, bad drift) + create their clone dirs so the real .exists()/range
    # path runs. Only `--corpus` invocations are faked; nothing else shells out.
    def fake_sweep(args_list, **kw):
        out = Path(args_list[args_list.index("--out") + 1])
        per = out / "per-repo"
        per.mkdir(parents=True, exist_ok=True)
        # key the per-repo JSON by the SAME slug the orchestrator looks up by
        # (org+name) — never a bare name, or the lookup would miss.
        (per / f"{ssi._slug('good/alpha')}.json").write_text(json.dumps(
            {"repo": "https://github.com/good/alpha",
             "summary": _summary(unwitnessed=0)}), encoding="utf-8")
        (per / f"{ssi._slug('bad/beta')}.json").write_text(json.dumps(
            {"repo": "https://github.com/bad/beta",
             "summary": _summary(unwitnessed=1)}), encoding="utf-8")
        # the clone cache dirs the range step looks for (slug of the clone URL).
        for full in ("good/alpha", "bad/beta"):
            (out / "clones" / ssi._slug(f"https://github.com/{full}")).mkdir(
                parents=True, exist_ok=True)
        return None
    monkeypatch.setattr(ssi.subprocess, "run", fake_sweep)

    # range: any existing clone reports a pinned range (no real git in the test).
    monkeypatch.setattr(ssi, "repo_range", lambda clone, scan: (_B, _H))

    manifest = ssi.run(
        candidates=candidates, excluded=set(), out=tmp_path,
        min_stars=500, active_days=90, audit_limit=10, scan_limit=100,
        rendered="2026-06-13", auditor="dos-kernel test",
        now_iso="2026-06-13", limit=None)

    assert manifest["enumerated"] == 3
    assert manifest["kept_after_floor"] == 2          # small/gamma dropped
    assert manifest["excluded_by_reason"][ssi.EXCL_BELOW_STARS] == 1
    assert manifest["published"] == ["good/alpha"]     # only the CLEAN repo
    assert manifest["withheld"] == 1                   # bad/beta, unnamed

    # the load-bearing leak check: no WITHHELD foreign name anywhere in the
    # manifest (mirrors test_aggregate_is_identity_stripped for this tool).
    blob = json.dumps(manifest).lower()
    assert "bad/beta" not in blob
    assert "beta" not in blob
    # small/gamma was excluded pre-sweep; it may appear only as a COUNT, never
    # named in published.
    assert "gamma" not in json.dumps(manifest["published"]).lower()


# ---------------------------------------------------------------------------
# --write-index — the self-tier-only contract: the TRACKED index commits page
# #1 only; a sweep's foreign CLEAN names stage for review, never the committed
# file (no soft accusation, no dangling links, no contradictory count).
# ---------------------------------------------------------------------------


def test_write_index_commits_self_tier_only_never_foreign_names(tmp_path, monkeypatch):
    # a sweep that found foreign CLEAN pages — exactly the case where the old
    # branch leaked their names into the tracked file.
    monkeypatch.setattr(ssi, "_auditor_string", lambda: "dos-kernel test")
    monkeypatch.setattr(ssi, "run", lambda **kw: {
        "enumerated": 13, "kept_after_floor": 9, "excluded": 4,
        "audited": 9, "published": ["big-org/foo", "other-org/bar"], "withheld": 4})
    # don't shell out to gh / a live enumerate for the candidate read.
    cand = tmp_path / "c.txt"
    cand.write_text("big-org/foo\tclaude\n", encoding="utf-8")
    # redirect the tracked-index write into tmp so the real repo file is untouched.
    monkeypatch.setattr(ssi, "REPO", tmp_path)
    (tmp_path / "docs" / "scoreboard").mkdir(parents=True)

    rc = ssi.main(["--candidates", str(cand), "--out", str(tmp_path / "out"),
                   "--rendered", "2026-06-13", "--write-index"])
    assert rc == 0
    written = (tmp_path / "docs" / "scoreboard" / "README.md").read_text(
        encoding="utf-8")
    # the self-page IS committed; the foreign sweep names are NOT.
    assert ssi._sp._AUDITOR_REPO in written
    assert "big-org/foo" not in written
    assert "other-org/bar" not in written
    # the coverage count reflects the self-page only (1), not the foreign 9 —
    # and reads grammatically.
    assert "1 repository audited" in written
    assert "9 repositories" not in written
