"""Tests for `dos.loop_classify` — the /dispatch stamp grammar, pinned.

This kernel is the ONE versioned home for the grammar that turns a `/dispatch`
run's archive-commit subject (and its chained-run README) into a verdict token.
It was lifted out of the job repo's `scripts/dispatch_loop_iter_driver.py`
(job `docs/62b` MQ3Y P1), where two consumers carried two drifting regex copies.

Three contracts:

  1. **The grammar regexes match what the /dispatch stamp actually emits** — and
     reject the two illegitimate look-alikes that manufactured false ships: the
     SKILL.md `<UTC-ts>` template line and a parent's inline quote of child1's
     GATE verdict (job FQ-617).
  2. **`classify_outcome_token`'s branch ORDER is fixed**: rate-limit outranks
     everything (a stale sidecar cannot mask a usage rejection) → typed sidecar
     beats prose → prose verdict beats the pre-QWB8 fallbacks → no concrete
     archive ts at all is INTERIM regardless of duration.
  3. **The dependency arrow is one-way** (the MQ3X "dos circular import" risk
     row): the module imports only stdlib + sibling `dos` vocabulary, never back
     into a host's `scripts.*`, and its body performs no I/O.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from dos.loop_classify import (
    ARCHIVE_PREFIX,
    ARCHIVE_TS_RE,
    CHILD2_SKIPPED_RE,
    CHILD2_SKIPPED_REPLAN_RE,
    FANOUT_TS_RE,
    INTERIM_MAX_MIN,
    PICKS_SHIPPED_RE,
    README_REASON_CLASS_RE,
    REASON_CLASS_RE,
    RUNS_DIR_TS_RE,
    VERDICT_RE,
    RateLimitFacts,
    SidecarFacts,
    classify_outcome_token,
    is_gate_verdict,
    is_terminal_text,
)

SHIPPED_SUBJECT = ("docs/dispatch: archive 20260620T010101Z — apply → 1/1 picks "
                   "shipped (verdict=LIVE)")
# The SKILL.md template line a child echoes into its stream-json — a matchable
# archive substring with a PLACEHOLDER ts, not a concrete one.
TEMPLATE_SUBJECT = ("docs/dispatch: archive <UTC-ts> — <packet-tag> → <N/T> picks "
                    "shipped (verdict=LIVE)")


def _terminal(result_text: str) -> dict:
    return {"type": "result", "subtype": "success", "is_error": False,
            "num_turns": 10, "duration_ms": 90_000, "result": result_text}


# --- 1. the grammar ---------------------------------------------------------

def test_archive_ts_re_requires_a_concrete_stamp():
    assert ARCHIVE_TS_RE.search(SHIPPED_SUBJECT).group(1) == "20260620T010101Z"
    assert ARCHIVE_TS_RE.search(TEMPLATE_SUBJECT) is None


def test_verdict_re_requires_the_archive_prefix():
    """The driver's copy is anchored on the archive subject on purpose — a bare
    `verdict=LIVE` elsewhere in a log is a quote, not a ship."""
    assert VERDICT_RE.search(SHIPPED_SUBJECT).group(1) == "LIVE"
    assert VERDICT_RE.search("… (1 BRX depth pick, verdict=LIVE)") is None
    assert ARCHIVE_PREFIX in SHIPPED_SUBJECT


def test_reason_class_regexes_read_both_spellings():
    assert REASON_CLASS_RE.search(
        "verdict=DRAIN reason_class=LANE_ALL_INFLIGHT_OR_DEFERRED"
    ).group(1) == "LANE_ALL_INFLIGHT_OR_DEFERRED"
    assert README_REASON_CLASS_RE.search(
        "- Reason class: LANE_STALE_DRAIN"
    ).group(1) == "LANE_STALE_DRAIN"


def test_pre_qwb8_and_ts_regexes():
    assert CHILD2_SKIPPED_RE.search(
        "docs/dispatch: archive 20260620T010202Z — apply → child2 skipped")
    assert CHILD2_SKIPPED_REPLAN_RE.search("child2 skipped (/replan recommended)")
    # A bare "child2 skipped." carries no /replan recommendation → stays ambiguous.
    assert CHILD2_SKIPPED_REPLAN_RE.search("child2 skipped.") is None
    assert PICKS_SHIPPED_RE.search(SHIPPED_SUBJECT)
    assert FANOUT_TS_RE.search(
        "docs/_fanout_runs/20260620T111213Z").group(1) == "20260620T111213Z"
    assert RUNS_DIR_TS_RE.search(
        "docs/_chained_runs/20260620T111213Z").group(1) == "20260620T111213Z"


def test_is_terminal_text_keys_on_the_same_markers_as_the_verdict_grep():
    assert is_terminal_text(SHIPPED_SUBJECT)
    assert is_terminal_text("… verdict=DRAIN")
    assert not is_terminal_text("Child2 running detached — waiting")
    assert not is_terminal_text("")


def test_is_gate_verdict_rejects_truncated_fragments():
    for token in ("LIVE", "DRAIN", "BLOCKED", "RACE", "STALE-STAMP"):
        assert is_gate_verdict(token)
    # `verdict=BLO` is what a mid-stream --include-partial-messages chunk emits.
    assert not is_gate_verdict("BLO")
    assert not is_gate_verdict("")


# --- 2. classify_outcome_token branch order ---------------------------------

def test_rate_limit_outranks_a_live_sidecar():
    """A stale sidecar claiming LIVE must NOT mask a real usage rejection."""
    token, detail = classify_outcome_token(
        "", _terminal(""), 1.0,
        rate_limit=RateLimitFacts(hit=True, kind="RATE_LIMITED",
                                  reason="usage limit reached",
                                  reset_at="2026-06-20T18:00:00Z"),
        sidecar=SidecarFacts(is_gate=True, verdict="LIVE", ship_count=3))
    assert token == "RATE_LIMITED kind=RATE_LIMITED"
    assert detail["reset_at"] == "2026-06-20T18:00:00Z"
    # PLC1 — the shared provider-limit vocabulary rides along with the token.
    assert detail["provider_limit"] == "usage_window"
    assert detail["provider_limit_retryable"] is False


def test_overloaded_is_its_own_token_and_is_retryable():
    token, detail = classify_outcome_token(
        "", _terminal(""), 1.0,
        rate_limit=RateLimitFacts(hit=True, kind="OVERLOADED", reason="529"))
    assert token == "OVERLOADED kind=OVERLOADED"
    assert detail["provider_limit"] == "transient_overload"
    assert detail["provider_limit_retryable"] is True


def test_sidecar_beats_prose():
    """The data channel beats the diagnostic channel: sidecar LIVE wins over a
    prose subject that says BLOCKED."""
    prose = "docs/dispatch: archive 20260620T010101Z … verdict=BLOCKED"
    token, detail = classify_outcome_token(
        prose, _terminal(prose), 5.0,
        sidecar=SidecarFacts(is_gate=True, verdict="LIVE", ship_count=1))
    assert token == "SHIPPED verdict=LIVE"
    assert detail["verdict_source"] == "sidecar"
    assert detail["ship_count"] == 1


def test_non_gate_sidecar_falls_through_to_prose():
    """A /replan sidecar carries an exit_reason but no gate verdict — it is not
    decisive, so the prose path still owns the call."""
    prose = "docs/dispatch: archive 20260620T010101Z … verdict=DRAIN"
    token, detail = classify_outcome_token(
        prose, _terminal(prose), 5.0, sidecar=SidecarFacts(is_gate=False))
    assert token == "GATE verdict=DRAIN"
    assert detail["verdict_source"] == "prose"


def test_prose_verdict_carries_the_reason_class():
    subject = ("docs/dispatch: archive 20260620T010202Z — apply → verdict=DRAIN "
               "reason_class=LANE_ALL_INFLIGHT_OR_DEFERRED, child2 skipped")
    token, detail = classify_outcome_token(subject, _terminal(subject), 1.2)
    assert token == "GATE verdict=DRAIN"
    assert detail["reason_class"] == "LANE_ALL_INFLIGHT_OR_DEFERRED"


def test_truncated_fragment_is_skipped_for_the_complete_token():
    log = ("docs/dispatch: archive 20260620T010303Z … verdict=BLO\n"
           "docs/dispatch: archive 20260620T010303Z … verdict=BLOCKED")
    token, detail = classify_outcome_token(log, _terminal(""), 5.0)
    assert token == "GATE verdict=BLOCKED"
    assert detail["verdict"] == "BLOCKED"


def test_template_line_cannot_manufacture_a_ship():
    """FQ-617: the SKILL.md template's `<UTC-ts>` placeholder fails the
    concrete-ts guard, so a parked parent stays INTERIM instead of latching a
    false SHIPPED."""
    log = TEMPLATE_SUBJECT + "\ndocs/dispatch: archive <UTC-ts> … child2 skipped"
    token, detail = classify_outcome_token(
        log, _terminal("Child2 running detached — waiting"), 7.7)
    assert token == "INTERIM"
    assert detail["interim_reason"] == "parked-no-envelope"


def test_pre_qwb8_fallbacks_need_a_concrete_archive_line():
    skipped = "docs/dispatch: archive 20260620T010404Z — apply → child2 skipped"
    token, detail = classify_outcome_token(skipped, _terminal(""), 1.0)
    assert token == "GATE verdict=DRAIN"
    assert detail["verdict"] == "DRAIN"

    token, detail = classify_outcome_token(
        SHIPPED_SUBJECT.replace("(verdict=LIVE)", ""), _terminal(""), 1.0)
    assert token == "SHIPPED verdict=LIVE"
    assert detail["verdict"] == "LIVE"


def test_interim_is_structural_not_durational():
    """The old `dur_min < INTERIM_MAX_MIN` guard is superseded by the
    no-concrete-archive-ts test: a parked parent well past 3 minutes is still
    INTERIM (recoverable), never a causeless UNCLEAR→BLOCKED hard stop."""
    assert INTERIM_MAX_MIN == 3.0
    for dur in (0.4, 7.7, 42.0):
        token, _ = classify_outcome_token("", _terminal(""), dur)
        assert token == "INTERIM"


# --- 3. the one-way dependency arrow ----------------------------------------

_BANNED_IO_NAMES = {"open", "subprocess", "Path", "os", "shutil", "socket",
                    "urllib", "requests"}


def _kernel_tree() -> ast.Module:
    from dos import loop_classify

    return ast.parse(Path(loop_classify.__file__).read_text(encoding="utf-8"))


def test_kernel_imports_only_stdlib_and_sibling_dos():
    """The MQ3X 'dos circular import' risk row, pinned: the kernel never reaches
    back into a host's `scripts.*` (or any non-stdlib third party)."""
    roots = set()
    for node in ast.walk(_kernel_tree()):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert "scripts" not in roots, f"kernel imports back into a host: {roots}"
    allowed = set(sys.stdlib_module_names) | {"dos"}
    assert roots <= allowed, f"non-stdlib, non-dos imports: {roots - allowed}"


def test_kernel_body_performs_no_io():
    """The docs/62b §0a boundary litmus: zero Path/open/subprocess/os.environ/
    json.load-from-disk in the body. If the grammar needs a file, the read is
    reduced to a scalar at the adapter edge and injected."""
    hits = []
    for node in ast.walk(_kernel_tree()):
        if isinstance(node, ast.Name) and node.id in _BANNED_IO_NAMES:
            hits.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in {
                "read_text", "read_bytes", "write_text", "load", "run", "system"}:
            hits.append(node.attr)
    assert not hits, f"I/O primitives in a pure kernel: {sorted(set(hits))}"
