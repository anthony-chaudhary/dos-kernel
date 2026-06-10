"""`dos helped` — the operator-facing "what did DOS catch for me?" projection.

Pins the read-only-projection contract: `summarize` folds a set of OP_ENFORCE
records into a "DOS caught N things" rollup (BLOCK/WARN/DEFER only — never a passive
OBSERVE), the cadence helper fires on the 1st + every 5th help, and the renderers
produce deterministic text. The fold + cadence + render are pure (entries in, value
out, no disk — the `observe`/`verdict_journal` test posture); the CLI verb is smoked
against a hand-seeded lane journal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dos import help_summary as hs


# ---------------------------------------------------------------------------
# Helpers — build OP_ENFORCE records the way `lane_journal.enforce_entry` does.
# ---------------------------------------------------------------------------


def _enforce(*, intervention="BLOCK", reason_class="SELF_MODIFY", tool="Write",
             withheld=True, holder="S1", ts="2026-06-10T10:00:00Z", handler="admission",
             reason="", proposal=None):
    rec = {
        "op": "ENFORCE",
        "intervention": intervention,
        "reason_class": reason_class,
        "tool": tool,
        "withheld": withheld,
        "holder": holder,
        "ts": ts,
        "handler": handler,
        "reason": reason,
    }
    if proposal is not None:
        rec["proposal"] = proposal
    return rec


# ---------------------------------------------------------------------------
# The fold — BLOCK/WARN/DEFER count; OBSERVE does not; counts are byte-clean.
# ---------------------------------------------------------------------------


def test_help_counts_block_warn_defer_not_observe():
    """A help is BLOCK / WARN / DEFER; a passive OBSERVE is recorded but is NOT a help."""
    recs = [
        _enforce(intervention="BLOCK"),
        _enforce(intervention="WARN", withheld=False),
        _enforce(intervention="DEFER", withheld=False),
        _enforce(intervention="OBSERVE", withheld=False),  # recorded, not a help
    ]
    s = hs.summarize(recs)
    assert s.total == 3            # BLOCK + WARN + DEFER
    assert s.enforced == 4         # all four firings recorded
    assert s.blocked == 1 and s.warned == 1 and s.deferred == 1
    assert s.by_rung == {"BLOCK": 1, "DEFER": 1, "WARN": 1}


def test_withheld_subset_counts_only_refused_calls():
    """`withheld` is the strict subset — calls actually refused (withheld is True)."""
    recs = [
        _enforce(intervention="BLOCK", withheld=True),
        _enforce(intervention="BLOCK", withheld=True),
        _enforce(intervention="WARN", withheld=False),
    ]
    s = hs.summarize(recs)
    assert s.total == 3
    assert s.withheld == 2  # only the two BLOCKs were withheld


def test_non_enforce_records_ignored():
    """ACQUIRE / REFUSE / HEARTBEAT lines are not enforcement helps."""
    recs = [
        {"op": "ACQUIRE", "holder": "S1"},
        {"op": "REFUSE", "holder": "S1", "reason_class": "COLLISION"},
        {"op": "HEARTBEAT", "holder": "S1"},
        _enforce(intervention="BLOCK"),
    ]
    s = hs.summarize(recs)
    assert s.total == 1
    assert s.enforced == 1


def test_by_reason_falls_back_to_handler_then_unclassified():
    """An absent reason_class degrades to the env-authored handler, never agent text."""
    recs = [
        _enforce(reason_class="SELF_MODIFY"),
        _enforce(reason_class="", handler="admission"),      # → admission
        _enforce(reason_class=None, handler=""),             # → UNCLASSIFIED
    ]
    s = hs.summarize(recs)
    assert s.by_reason == {"SELF_MODIFY": 1, "admission": 1, "UNCLASSIFIED": 1}


def test_holder_filter_scopes_to_one_session():
    """`holder` scopes the count to one session (the OP_ENFORCE holder/session id)."""
    recs = [
        _enforce(holder="S1"),
        _enforce(holder="S1"),
        _enforce(holder="S2"),
    ]
    assert hs.summarize(recs, holder="S1").total == 2
    assert hs.summarize(recs, holder="S2").total == 1
    assert hs.summarize(recs).total == 3  # unfiltered


def test_since_filter_is_a_lexical_window():
    """`since` keeps records at/after an ISO timestamp (ISO sorts lexically)."""
    recs = [
        _enforce(ts="2026-06-09T00:00:00Z"),
        _enforce(ts="2026-06-10T00:00:00Z"),
        _enforce(ts="2026-06-11T00:00:00Z"),
    ]
    s = hs.summarize(recs, since="2026-06-10T00:00:00Z")
    assert s.total == 2  # the 10th and 11th


def test_since_and_latest_track_the_window():
    """The summary echoes the first/last ts it actually counted."""
    recs = [
        _enforce(ts="2026-06-10T08:00:00Z"),
        _enforce(ts="2026-06-10T12:00:00Z"),
        _enforce(ts="2026-06-10T10:00:00Z"),
    ]
    s = hs.summarize(recs)
    assert s.since == "2026-06-10T08:00:00Z"
    assert s.latest == "2026-06-10T12:00:00Z"


def test_empty_records_is_a_clean_zero():
    """No records → an honest zero, not an error."""
    s = hs.summarize([])
    assert s.total == 0 and s.enforced == 0
    assert s.by_rung == {} and s.by_reason == {}


def test_rung_token_is_case_folded():
    """A lower-case `block` rung still counts (defensive against a non-CC writer)."""
    recs = [_enforce(intervention="block"), _enforce(intervention="Warn")]
    s = hs.summarize(recs)
    assert s.total == 2 and s.blocked == 1 and s.warned == 1


# ---------------------------------------------------------------------------
# The cadence — first + every 5th.
# ---------------------------------------------------------------------------


def test_should_nudge_first_and_every_fifth():
    """Fires on the 1st help, then every 5th: 1, 5, 10, 15, 20 — silent in between."""
    firing = [i for i in range(1, 21) if hs.should_nudge(i)]
    assert firing == [1, 5, 10, 15, 20]


def test_should_nudge_zero_and_negative_never_fire():
    assert not hs.should_nudge(0)
    assert not hs.should_nudge(-3)


def test_should_nudge_custom_interval():
    firing = [i for i in range(1, 11) if hs.should_nudge(i, every=3)]
    assert firing == [1, 3, 6, 9]


# ---------------------------------------------------------------------------
# Rendering — the one-line nudge + the full operator rollup.
# ---------------------------------------------------------------------------


def test_nudge_line_singular_and_plural():
    one = hs.summarize([_enforce(intervention="BLOCK")])
    assert "caught 1 thing this session" in hs.nudge_line(one)
    assert "(1 blocked)" in hs.nudge_line(one)
    many = hs.summarize([_enforce(intervention="BLOCK"),
                         _enforce(intervention="WARN", withheld=False)])
    line = hs.nudge_line(many)
    assert "caught 2 things this session" in line
    assert "1 blocked" in line and "1 warned" in line


def test_render_summary_headline_and_breakdowns():
    recs = [
        _enforce(reason_class="SELF_MODIFY", tool="Write"),
        _enforce(reason_class="SELF_MODIFY", tool="Edit"),
        _enforce(reason_class="COLLISION", tool="Edit", withheld=True),
    ]
    text = hs.render_summary_text(hs.summarize(recs))
    assert "DOS has caught 3 things" in text
    assert "by reason" in text and "SELF_MODIFY" in text and "COLLISION" in text
    assert "by tool" in text and "Edit" in text and "Write" in text
    assert "3 calls actually refused" in text


def test_render_summary_observe_only_is_honest():
    """A summary with only OBSERVE firings says so, without a fake 'caught 0' table."""
    recs = [_enforce(intervention="OBSERVE", withheld=False) for _ in range(3)]
    text = hs.render_summary_text(hs.summarize(recs))
    assert "no behavior-changing interventions" in text
    assert "3 enforcement record(s) seen" in text


# ---------------------------------------------------------------------------
# CLI smoke — `dos helped` against a hand-seeded lane journal.
# ---------------------------------------------------------------------------


def test_cli_helped_reads_lane_journal(tmp_path, monkeypatch, capsys):
    """`dos helped` folds the workspace lane journal into the operator rollup."""
    import json
    from dos import cli

    ws = tmp_path
    dos_dir = ws / ".dos"
    dos_dir.mkdir()
    journal = dos_dir / "lane-journal.jsonl"
    recs = [
        _enforce(intervention="BLOCK", reason_class="SELF_MODIFY", tool="Write"),
        _enforce(intervention="WARN", reason_class="", handler="provenance",
                 tool="Edit", withheld=False),
        _enforce(intervention="OBSERVE", withheld=False),
    ]
    journal.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    rc = cli.main(["helped", "--workspace", str(ws)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DOS has caught 2 things" in out  # BLOCK + WARN, not OBSERVE
    assert "1 blocked" in out and "1 warned" in out


def test_cli_helped_json(tmp_path, capsys):
    import json
    from dos import cli

    ws = tmp_path
    dos_dir = ws / ".dos"
    dos_dir.mkdir()
    journal = dos_dir / "lane-journal.jsonl"
    journal.write_text(
        json.dumps(_enforce(intervention="BLOCK", reason_class="SELF_MODIFY")) + "\n",
        encoding="utf-8")

    rc = cli.main(["helped", "--workspace", str(ws), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 1
    assert payload["blocked"] == 1
    assert payload["by_reason"] == {"SELF_MODIFY": 1}


def test_cli_helped_empty_journal_is_clean(tmp_path, capsys):
    """No journal at all → an honest 'DOS has been observing' message, exit 0."""
    from dos import cli

    rc = cli.main(["helped", "--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DOS has caught 0 things" in out


# ---------------------------------------------------------------------------
# Reason-class recovery — the misleading "admission 597 / SELF_MODIFY 13" fix.
# ---------------------------------------------------------------------------


def test_reason_class_recovered_from_proposal_body():
    """A record whose TOP-LEVEL reason_class is null but whose proposal body carries
    the typed token resolves to the typed class, NOT the bare handler name. This is
    the fix that collapses the misleading 'admission N / SELF_MODIFY M' split — both
    are SELF_MODIFY, the older ones just lost their top-level token."""
    recs = [
        # The "older" shape: top-level token dropped (the 092ad29 gap), but the
        # env-authored proposal body still carries it.
        _enforce(reason_class=None, handler="admission",
                 proposal={"reason_class": "SELF_MODIFY"}),
        # The "newer" shape: top-level token present.
        _enforce(reason_class="SELF_MODIFY"),
    ]
    s = hs.summarize(recs)
    # Both collapse into the one honest bucket — no opaque "admission" split.
    assert s.by_reason == {"SELF_MODIFY": 2}


def test_handler_fallback_still_works_without_a_proposal_token():
    """With neither a top-level nor a proposal reason_class, the handler name is the
    fallback (then UNCLASSIFIED) — the prose `reason` is NEVER mined."""
    recs = [
        _enforce(reason_class="", handler="provenance", proposal={}),
        _enforce(reason_class=None, handler=""),
    ]
    s = hs.summarize(recs)
    assert s.by_reason == {"provenance": 1, "UNCLASSIFIED": 1}


# ---------------------------------------------------------------------------
# The glossary — plain-English meaning of a reason class.
# ---------------------------------------------------------------------------


def test_explain_reason_glosses_known_classes_case_insensitively():
    assert "kernel's own running code" in hs.explain_reason("SELF_MODIFY")
    assert "kernel's own running code" in hs.explain_reason("self_modify")
    assert "doesn't declare" in hs.explain_reason("UNKNOWN_LANE")
    assert "lane-admission" in hs.explain_reason("admission")


def test_explain_reason_unknown_class_returns_empty_never_invents():
    """An unknown class gets no gloss — we never invent an explanation."""
    assert hs.explain_reason("TOTALLY_MADE_UP") == ""
    assert hs.explain_reason("") == ""


def test_glossary_keys_are_real_tokens_not_phantoms():
    """Every glossed class is a REAL vocabulary token (BASE_REASONS + the named
    arbiter refuse) or a known env-authored handler fallback — never an invented
    class that no record could ever carry. Keeps the gloss honest as the vocabulary
    evolves: a phantom key here would explain something the kernel never emits."""
    from dos.reasons import BASE_REASONS

    real_tokens = {s.token for s in BASE_REASONS.specs}
    real_tokens.add("CLASS_BUDGET_EXHAUSTED")  # the named arbiter refuse (arbiter.py)
    handler_fallbacks = {"admission", "provenance", "UNCLASSIFIED"}
    for key in hs.REASON_GLOSSARY:
        assert key in real_tokens or key in handler_fallbacks, (
            f"glossary key {key!r} matches no real reason token or handler fallback")


def test_render_summary_shows_the_gloss_inline():
    """The rollup glosses each reason class in plain English, and points at --explain."""
    text = hs.render_summary_text(hs.summarize([_enforce(reason_class="SELF_MODIFY")]))
    assert "kernel's own running code" in text
    assert "dos helped --explain" in text


# ---------------------------------------------------------------------------
# Examples / path extraction — WHICH file, from the kernel's own reason text.
# ---------------------------------------------------------------------------

_SM_REASON = ("lane 'Write' would edit the orchestrator's own running code "
              "(src/dos/arbiter.py) — refusing to let a live loop rewrite the kernel "
              "that is adjudicating it (SELF_MODIFY). Pass --force only if you are "
              "deliberately editing the kernel between loop runs.")


def test_examples_extract_target_path_from_env_authored_reason():
    """`--explain` banks the concrete path(s) the refusal was about, pulled from the
    kernel-written `reason` (the parenthesized path list), never agent narration."""
    recs = [_enforce(reason_class="SELF_MODIFY", tool="Write", reason=_SM_REASON)]
    s = hs.summarize(recs, with_examples=True)
    exs = s.examples["SELF_MODIFY"]
    assert len(exs) == 1
    assert exs[0].target == "src/dos/arbiter.py"
    assert exs[0].tool == "Write"
    # The example reason is the FIRST clause (the trailer is dropped).
    assert "would edit the orchestrator's own running code" in exs[0].reason
    assert "--force" not in exs[0].reason


def test_examples_prefer_distinct_targets_and_cap_count():
    """A few DISTINCT examples, not the same file repeated, capped at the per-reason cap."""
    def sm(path):
        return _enforce(reason_class="SELF_MODIFY",
                        reason=f"lane 'Edit' would edit ... ({path}) — refusing")
    recs = [sm("src/dos/a.py"), sm("src/dos/a.py"), sm("src/dos/b.py"),
            sm("src/dos/c.py"), sm("src/dos/d.py")]
    s = hs.summarize(recs, with_examples=True)
    targets = [e.target for e in s.examples["SELF_MODIFY"]]
    assert targets == ["src/dos/a.py", "src/dos/b.py", "src/dos/c.py"]  # distinct, capped at 3
    assert s.by_reason == {"SELF_MODIFY": 5}  # the COUNT is still all five


def test_examples_off_by_default_keeps_rollup_cheap():
    s = hs.summarize([_enforce(reason="x (src/dos/a.py) — y")])
    assert s.examples == {}


def test_render_explain_shows_meaning_and_examples():
    recs = [_enforce(reason_class="SELF_MODIFY", tool="Write", reason=_SM_REASON)]
    text = hs.render_explain_text(hs.summarize(recs, with_examples=True))
    assert "SELF_MODIFY" in text
    assert "means:" in text and "kernel's own running code" in text
    assert "src/dos/arbiter.py" in text and "via Write" in text


def test_by_reason_rung_splits_each_bucket_by_rung():
    """`by_reason_rung` carries the per-bucket BLOCK/WARN split `--explain` labels with."""
    recs = [
        _enforce(reason_class="SELF_MODIFY", intervention="BLOCK"),
        _enforce(reason_class="admission", intervention="BLOCK"),
        _enforce(reason_class="admission", intervention="WARN", withheld=False),
        _enforce(reason_class="admission", intervention="WARN", withheld=False),
    ]
    s = hs.summarize(recs)
    assert s.by_reason_rung == {"SELF_MODIFY": {"BLOCK": 1},
                                "admission": {"WARN": 2, "BLOCK": 1}}
    assert s.to_dict()["by_reason_rung"]["admission"] == {"WARN": 2, "BLOCK": 1}


def test_explain_bucket_label_is_rung_honest():
    """A mixed bucket reads "1 block, 2 warns", never a flat "3 blocks" (issue #9)."""
    recs = [
        _enforce(reason_class="admission", intervention="BLOCK"),
        _enforce(reason_class="admission", intervention="WARN", withheld=False),
        _enforce(reason_class="admission", intervention="WARN", withheld=False),
    ]
    text = hs.render_explain_text(hs.summarize(recs, with_examples=True))
    assert "admission  (1 block, 2 warns)" in text
    assert "3 blocks" not in text


def test_explain_warn_only_bucket_never_says_blocks():
    """The issue #9 pin: a WARN-only bucket is never rendered with the word "blocks"."""
    recs = [
        _enforce(reason_class="admission", intervention="WARN", withheld=False),
        _enforce(reason_class="admission", intervention="WARN", withheld=False),
    ]
    text = hs.render_explain_text(hs.summarize(recs, with_examples=True))
    assert "admission  (2 warns)" in text
    assert "blocks" not in text


def test_explain_label_falls_back_to_neutral_catches():
    """A summary built without the per-rung split degrades to the neutral noun."""
    s = hs.HelpSummary(total=2, by_reason={"admission": 2})
    text = hs.render_explain_text(s)
    assert "admission  (2 catches)" in text
    assert "blocks" not in text


def test_explain_json_carries_examples_and_glossary(tmp_path, capsys):
    import json
    from dos import cli

    ws = tmp_path
    (ws / ".dos").mkdir()
    (ws / ".dos" / "lane-journal.jsonl").write_text(
        json.dumps(_enforce(reason_class="SELF_MODIFY", tool="Write", reason=_SM_REASON)) + "\n",
        encoding="utf-8")

    rc = cli.main(["helped", "--workspace", str(ws), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["examples"]["SELF_MODIFY"][0]["target"] == "src/dos/arbiter.py"
    assert "kernel's own running code" in payload["glossary"]["SELF_MODIFY"]


def test_cli_helped_explain_renders_examples(tmp_path, capsys):
    import json
    from dos import cli

    ws = tmp_path
    (ws / ".dos").mkdir()
    (ws / ".dos" / "lane-journal.jsonl").write_text(
        json.dumps(_enforce(reason_class="SELF_MODIFY", tool="Write", reason=_SM_REASON)) + "\n",
        encoding="utf-8")

    rc = cli.main(["helped", "--workspace", str(ws), "--explain"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "means:" in out
    assert "src/dos/arbiter.py" in out
