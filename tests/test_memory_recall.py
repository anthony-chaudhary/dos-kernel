"""Tests for the recall-honesty driver (`dos.drivers.memory_recall`, docs/103).

Three layers, mirroring the kernel's own test discipline:

  * PURE classifier — `classify_recall` over frozen `RecallEvidence` fixtures, no
    git, no file read (the `liveness.classify` replay-test shape).
  * PURE extractor — `extract_claims` / `strip_recall_banner` / the SHA regex over
    body strings, no I/O.
  * The DOGFOOD litmus (docs/103 §7) — a synthetic memory carrying the real
    `cli.py` RED-breach claim, re-checked against THIS repo's actual git +
    working tree, MUST return RECALL_STALE with the real `a7a145d` evidence
    obtained BY RE-CHECK. Skipped when not run inside the kernel repo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dos import config as _config
from dos.drivers import memory_recall as mr
from dos.drivers.memory_recall import (
    ClaimEvidence,
    ClaimKind,
    MemoryClaim,
    Polarity,
    ProbeStatus,
    Recall,
    RecallEvidence,
    classify_recall,
)


# ---------------------------------------------------------------------------
# helpers for the pure-classifier fixtures
# ---------------------------------------------------------------------------


def _claim(kind=ClaimKind.CODE_TOKEN, pol=Polarity.ASSERTS_PRESENT, raw="x") -> MemoryClaim:
    return MemoryClaim(raw=raw, kind=kind, polarity=pol, target_file="src/dos/x.py")


def _ev(claim, status, gt="", source="grep") -> ClaimEvidence:
    return ClaimEvidence(claim=claim, status=status, ground_truth=gt, source=source)


def _evidence(*evidences, mem_type="project", name="m") -> RecallEvidence:
    return RecallEvidence(mem_name=name, mem_type=mem_type, evidences=tuple(evidences))


# ---------------------------------------------------------------------------
# PURE classifier — the ladder
# ---------------------------------------------------------------------------


def test_opinion_type_is_unverifiable_even_with_a_confirming_claim():
    """Rung-0: an opinion-typed memory is UNVERIFIABLE regardless of body content.

    Trust STRUCTURE not CONTENT — an incidental confirming claim inside a feedback
    note must not drag it onto the verifiable ladder (docs/103 §4 clause-1)."""
    ev = _evidence(_ev(_claim(), ProbeStatus.CONFIRMS), mem_type="feedback")
    v = classify_recall(ev)
    assert v.verdict is Recall.RECALL_UNVERIFIABLE
    assert "feedback" in v.reason


def test_user_type_is_also_unverifiable():
    ev = _evidence(_ev(_claim(), ProbeStatus.CONTRADICTS), mem_type="user")
    assert classify_recall(ev).verdict is Recall.RECALL_UNVERIFIABLE


def test_no_checkable_claims_is_unverifiable():
    """Names nothing checkable → UNVERIFIABLE (the §7 'names nothing' floor)."""
    assert classify_recall(_evidence()).verdict is Recall.RECALL_UNVERIFIABLE


def test_all_unknown_probes_is_unverifiable_never_fresh():
    """Every probe abstained → UNVERIFIABLE, NEVER FRESH (the cardinal-sin guard).

    An UNKNOWN claim is excluded from `checkable`, so it cannot satisfy FRESH (which
    requires every checkable claim to affirmatively CONFIRM)."""
    ev = _evidence(
        _ev(_claim(raw="a"), ProbeStatus.UNKNOWN),
        _ev(_claim(raw="b"), ProbeStatus.UNKNOWN),
    )
    assert classify_recall(ev).verdict is Recall.RECALL_UNVERIFIABLE


def test_all_confirm_is_fresh():
    ev = _evidence(
        _ev(_claim(raw="a"), ProbeStatus.CONFIRMS),
        _ev(_claim(raw="b"), ProbeStatus.CONFIRMS),
    )
    assert classify_recall(ev).verdict is Recall.RECALL_FRESH


def test_one_contradicts_among_many_confirms_is_stale():
    """Worst-checkable-claim-wins, NOT majority — the 9-fresh-1-stale launder defeat."""
    bad = _claim(raw="from dos.drivers import watchdog", pol=Polarity.ASSERTS_PRESENT)
    ev = _evidence(
        _ev(_claim(raw="a"), ProbeStatus.CONFIRMS),
        _ev(_claim(raw="b"), ProbeStatus.CONFIRMS),
        _ev(bad, ProbeStatus.CONTRADICTS, gt="removed by a7a145d"),
        _ev(_claim(raw="c"), ProbeStatus.CONFIRMS),
    )
    v = classify_recall(ev)
    assert v.verdict is Recall.RECALL_STALE
    assert v.culprit is not None and v.culprit.claim.raw == "from dos.drivers import watchdog"
    assert "a7a145d" in v.reason


def test_unknown_does_not_block_fresh_when_a_real_claim_confirms():
    """An UNKNOWN claim is ignored; a lone CONFIRMS still yields FRESH."""
    ev = _evidence(
        _ev(_claim(raw="a"), ProbeStatus.UNKNOWN),
        _ev(_claim(raw="b"), ProbeStatus.CONFIRMS),
    )
    assert classify_recall(ev).verdict is Recall.RECALL_FRESH


def test_to_dict_round_trips_the_verdict_and_evidence():
    bad = _claim(raw="tok", pol=Polarity.ASSERTS_PRESENT)
    ev = _evidence(_ev(bad, ProbeStatus.CONTRADICTS, gt="gone", source="grep"))
    d = classify_recall(ev).to_dict()
    assert d["verdict"] == "RECALL_STALE"
    assert d["culprit"]["status"] == "CONTRADICTS"
    assert d["culprit"]["source"] == "grep"
    assert d["claims"][0]["claim"]["polarity"] == "ASSERTS_PRESENT"


# ---------------------------------------------------------------------------
# PURE extractor — regexes, polarity, banner strip
# ---------------------------------------------------------------------------


def test_sha_regex_rejects_english_hex_words():
    """`facade`/`decade`/`defaced` (all-letter or no-digit) are NOT SHAs."""
    claims = mr.extract_claims("the facade of a decade defaced nothing", "project")
    assert not [c for c in claims if c.kind is ClaimKind.SHA]


def test_sha_regex_rejects_8_to_12_char_band():
    """A session-id fragment (`7d0fa2aa`, 8 chars) is not a 7- or 40-hex SHA."""
    claims = mr.extract_claims("origin session 7d0fa2aa here", "project")
    assert not [c for c in claims if c.kind is ClaimKind.SHA]


def test_sha_regex_accepts_backticked_seven_hex():
    """The common backticked citation `` `a7a145d` `` MUST match (it was the bug)."""
    claims = mr.extract_claims("FIXED in `a7a145d` last week", "project")
    shas = [c for c in claims if c.kind is ClaimKind.SHA]
    assert [c.raw for c in shas] == ["a7a145d"]
    assert shas[0].polarity is Polarity.ASSERTS_SHIPPED  # ship verb tight before


def test_sha_ship_verb_must_be_tight_before_not_window_wide():
    """A ship verb describing a DIFFERENT subject must not flip a bare SHA.

    "master re-landed their content … feat/x (b571fc6)" — `landed` is about the
    content, the SHA is a parenthetical branch tip → NEUTRAL, not ASSERTS_SHIPPED."""
    body = "master re-landed their content via v0.4.0):\nfeat/stamp-convention (b571fc6), more"
    shas = [c for c in mr.extract_claims(body, "project") if c.kind is ClaimKind.SHA]
    assert shas and shas[0].raw == "b571fc6"
    assert shas[0].polarity is Polarity.NEUTRAL  # parenthetical → bare reference


def test_generic_import_is_not_a_code_token_claim():
    """`import dos` / `import re` are too generic to bind to a file claim."""
    claims = mr.extract_claims("config.py does `import dos` somewhere", "project")
    assert not [c for c in claims if c.kind is ClaimKind.CODE_TOKEN]


def test_specific_import_is_a_code_token_claim():
    body = "cli.py:10 does `from dos.drivers import watchdog` today"
    toks = [c for c in mr.extract_claims(body, "project") if c.kind is ClaimKind.CODE_TOKEN]
    assert toks and toks[0].raw == "from dos.drivers import watchdog"
    assert toks[0].polarity is Polarity.ASSERTS_PRESENT  # "does" present cue


def test_bare_path_defaults_neutral_without_a_creation_cue():
    """A bare path mention is a REFERENCE, not a present-claim → NEUTRAL."""
    body = "the plan in docs/77_hardware-resource-manager-plan.md is interesting"
    paths = [c for c in mr.extract_claims(body, "project") if c.kind is ClaimKind.PATH]
    assert paths and paths[0].polarity is Polarity.NEUTRAL


def test_bare_path_with_strong_cue_is_asserts_present():
    body = "Committed plan `docs/77_hardware-resource-manager-plan.md` (proposed)"
    paths = [c for c in mr.extract_claims(body, "project") if c.kind is ClaimKind.PATH]
    assert paths and paths[0].polarity is Polarity.ASSERTS_PRESENT


def test_cross_repo_prefix_is_not_extracted_as_a_bare_path():
    """`job/scripts/ship_oracle.py` must NOT strip to `scripts/ship_oracle.py`."""
    body = "e.g. `job/scripts/ship_oracle.py` is `from dos.X import *`"
    paths = [c.raw for c in mr.extract_claims(body, "project") if c.kind is ClaimKind.PATH]
    assert "scripts/ship_oracle.py" not in paths


def test_strip_recall_banner_removes_leading_self_annotation():
    """The verdict must never be read off the file's own RECALL_* banner."""
    body = (
        "> **RECALL_STALE — re-verified.** FIXED in `a7a145d`; only a comment left.\n"
        "> second banner line.\n"
        "\n"
        "The real audit prose: cli.py does `from dos.drivers import watchdog`.\n"
    )
    stripped = mr.strip_recall_banner(body)
    assert "RECALL_STALE" not in stripped
    assert "a7a145d" not in stripped  # the banner's SHA is gone
    assert "real audit prose" in stripped  # the body below survives


def test_strip_recall_banner_leaves_a_plain_body_untouched():
    body = "no banner here, just `from dos.drivers import watchdog` prose.\n"
    assert mr.strip_recall_banner(body) == body


def test_clause_window_does_not_bleed_a_cue_from_an_adjacent_clause():
    """`stamp.py now exists` must stay PRESENT even with 'absent' elsewhere nearby."""
    body = "the plumbing is absent. But `src/dos/stamp.py` now exists and is complete."
    paths = [c for c in mr.extract_claims(body, "project") if c.kind is ClaimKind.PATH]
    assert paths and paths[0].polarity is Polarity.ASSERTS_PRESENT


# ---------------------------------------------------------------------------
# Probe-level (boundary) tests with a synthetic tmp repo
# ---------------------------------------------------------------------------


def test_neutral_claim_probes_unknown(tmp_path):
    cfg = _config.default_config(tmp_path)
    c = MemoryClaim(raw="x", kind=ClaimKind.CODE_TOKEN, polarity=Polarity.NEUTRAL,
                    target_file="src/x.py")
    assert mr.probe(c, cfg).status is ProbeStatus.UNKNOWN


def test_path_never_tracked_abstains_not_contradicts(tmp_path):
    """A path that was never in the repo (no git history) → UNKNOWN, not STALE.

    Foreign/illustrative references (the job repo's scripts/, an `src/foo.py`
    example) must abstain, never manufacture a false CONTRADICTS."""
    cfg = _config.default_config(tmp_path)
    c = MemoryClaim(raw="src/foo.py", kind=ClaimKind.PATH, polarity=Polarity.NEUTRAL,
                    target_file="src/foo.py")
    ev = mr.probe(c, cfg)
    assert ev.status is ProbeStatus.UNKNOWN


# ---------------------------------------------------------------------------
# THE DOGFOOD LITMUS (docs/103 §7) — runs against THIS repo's real git/tree
# ---------------------------------------------------------------------------


def _is_kernel_repo(cfg) -> bool:
    facts = getattr(cfg, "workspace", None)
    return bool(getattr(facts, "is_kernel_repo", False))


def _a7a145d_on_trunk(root: Path) -> bool:
    try:
        r = subprocess.run(["git", "merge-base", "--is-ancestor", "a7a145d", "HEAD"],
                           cwd=str(root), capture_output=True, check=False, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def test_dogfood_cli_red_breach_is_recall_stale_with_a7a145d(tmp_path):
    """docs/103 §7: the cli.py RED-breach claim re-checks to RECALL_STALE.

    Builds a synthetic memory carrying the EXACT stale claim from
    `project-dos-quality-audit-2026-06-02` (the import `cli.py` once had), then runs
    the driver against THIS repo's real git + working tree. The driver MUST return
    RECALL_STALE with `a7a145d` evidence obtained BY RE-CHECK (pickaxe) — not
    parroted from any banner. If it ever returns FRESH, the comment-aware grep or
    the polarity extraction has drifted (the §7 tripwire).

    Self-contained: the memory is synthetic (tmp_path), the ground truth is real.
    Skipped unless run inside the kernel repo with `a7a145d` on trunk.
    """
    cfg = _config.active()
    if not _is_kernel_repo(cfg) or not _a7a145d_on_trunk(cfg.paths.root):
        pytest.skip("dogfood litmus runs only inside the kernel repo with a7a145d on trunk")

    store = tmp_path / "memory"
    store.mkdir()
    (store / "quality-audit.md").write_text(
        "---\n"
        "name: quality-audit\n"
        "metadata:\n"
        "  type: project\n"
        "---\n"
        "\n"
        "> **RECALL_STALE banner that MUST be ignored — FIXED in `deadbeef`.**\n"
        "\n"
        "RED SUITE (must-fix): `cli.py:1000` does "
        "`from dos.drivers import watchdog` → 2 failed.\n",
        encoding="utf-8",
    )
    v = mr.recall_one("quality-audit", cfg=cfg, store=str(store))

    assert v.verdict is Recall.RECALL_STALE
    assert v.culprit is not None
    assert v.culprit.claim.kind is ClaimKind.CODE_TOKEN
    assert v.culprit.claim.polarity is Polarity.ASSERTS_PRESENT
    assert v.culprit.status is ProbeStatus.CONTRADICTS
    assert v.culprit.source == "grep"
    # the git evidence, obtained BY RE-CHECK (pickaxe) — NOT the banner's `deadbeef`
    assert "a7a145d" in v.culprit.ground_truth
    assert "deadbeef" not in v.culprit.ground_truth


def test_interpret_gloss_for_each_verdict():
    """The driver-owned gloss covers all three verdicts (the CLI/MCP parity source)."""
    expect = {
        "RECALL_FRESH": "FRESH",
        "RECALL_STALE": "STALE",
        "RECALL_UNVERIFIABLE": "UNVERIFIABLE",
    }
    for verdict, word in expect.items():
        g = mr.interpret({"verdict": verdict, "culprit": None})
        assert word in g.upper()
    # an unknown verdict degrades to a hedge, never silence
    assert mr.interpret({"verdict": "???"}).strip()


# ---------------------------------------------------------------------------
# Outcome-driven retirement (docs/350) — the EARNS-ITS-PLACE sweep. The sibling
# of recall's staleness sweep: it folds host-measured contribution through the
# pure `retire.classify` leaf and PROPOSES (never deletes). Distinct from
# staleness — a memory can be true and still no longer earn its place.
# ---------------------------------------------------------------------------


def _retire_pol():
    from dos import retire
    return retire.RetirePolicy(min_contribution=1, min_trials=5)


def test_retire_sweep_ranks_retire_first_and_proposes():
    """RETIRE rows lead, then PROBATION, then KEEP — and every row is a proposal."""
    from dos import retire
    contributions = {
        "good": retire.RetireEvidence(contribution=42, trials=30),
        "dead": retire.RetireEvidence(contribution=0, trials=30),
        "new": retire.RetireEvidence(contribution=0, trials=2),
    }
    props = mr.retire_sweep(contributions, policy=_retire_pol())
    verdicts = [p.verdict.verdict for p in props]
    # ranked RETIRE → PROBATION → KEEP
    assert verdicts == [retire.Retire.RETIRE, retire.Retire.PROBATION, retire.Retire.KEEP]
    by_name = {p.mem_name: p for p in props}
    assert by_name["dead"].verdict.verdict is retire.Retire.RETIRE
    assert by_name["new"].verdict.verdict is retire.Retire.PROBATION
    assert by_name["good"].verdict.verdict is retire.Retire.KEEP
    # every RETIRE is an ARCHIVE PROPOSAL, never a delete — the docs/103 §6 stance
    assert "propose-archive" in by_name["dead"].action
    assert "human" in by_name["dead"].action.lower()


def test_retire_sweep_leaves_unmeasured_memories_untouched():
    """A memory with no measured contribution gets NO proposal — abstain on silence.

    The host measures contribution (it is host policy); a memory absent from the
    map is never retired on silence (the abstain-first default at sweep scale)."""
    from dos import retire
    props = mr.retire_sweep(
        {"measured": retire.RetireEvidence(contribution=0, trials=30)},
        policy=_retire_pol(),
    )
    names = {p.mem_name for p in props}
    assert names == {"measured"}  # only the one we supplied evidence for


def test_retire_sweep_proposal_to_dict_shape():
    """The `dos decisions`-facing JSON shape: memory id + verdict facts + action."""
    from dos import retire
    props = mr.retire_sweep(
        {"dead": retire.RetireEvidence(contribution=0, trials=30, narrated="my pitch")},
        policy=_retire_pol(),
    )
    d = props[0].to_dict()
    assert d["memory"] == "dead"
    assert d["verdict"] == "RETIRE"
    assert d["retire_cause"] == "underperformed"
    assert "action" in d
    # the item's self-pitch rides for the operator but is parsed for nothing
    assert d["evidence"]["narrated"] == "my pitch"


def test_retire_sweep_over_cap_action_names_the_cap():
    """An OVER_CAP retire's action explains it is a cap eviction, not a demerit."""
    from dos import retire
    props = mr.retire_sweep(
        {"marginal": retire.RetireEvidence(contribution=10, trials=40,
                                           active_count=51, is_marginal=True)},
        policy=retire.RetirePolicy(min_contribution=1, min_trials=5, max_active=50),
    )
    p = props[0]
    assert p.verdict.retire_cause is retire.RetireCause.OVER_CAP
    assert "cap" in p.action.lower()
