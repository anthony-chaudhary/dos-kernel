"""The provider-ledger witness rung — the THIRD_PARTY band (docs/261).

`content_diff` (docs/349) climbs `verify`'s OS_RECORDED band ("the file's content is
RIGHT"); `provider_ledger` climbs ONE rung higher, to THIRD_PARTY: an agent's
external-effect claim ("I charged X / sent Y / provisioned Z") witnessed against a
DIFFERENT PRINCIPAL's ledger the agent did not author. These tests pin the same three
load-bearing properties content_diff does, lifted one rung:

  1. **The floor falsifier (security-load-bearing).** A ledger row that matches an
     agent/plan-supplied (`inline:`/`plan:`) expectation is ATTESTED but at the
     AGENT_AUTHORED floor, and `believe_under_floor` REFUSES it — the agent cannot grade
     its own homework (docs/192 §4 row C). A `@WORKTREE`-read ledger is also the floor
     (actor==witness). This is the whole reason the rung is sound.
  2. **The sound climb.** A host-captured ledger committed to git (the agent cannot make
     `<sha>:<path>` resolve to other bytes) + a host-pinned expectation → ATTESTED @ a
     non-forgeable rung, believed; a field disagreement → REFUTED (the "you claimed X but
     the ledger says Y" disconfirmation, distinct from "no signal").
  3. **Fail-safe, never fail-open.** A ref absent from the ledger / unreadable ledger /
     unparseable subject → NO_SIGNAL, never a fabricated REFUTE that would fail an honest
     effect (an effect genuinely made may simply not be in THIS captured ledger).

The git-backed tests build a real temp repo (so `git cat-file` reads genuine blobs);
the fail-safe tests poison `subprocess.run` so they never spawn.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dos.drivers.provider_ledger import (
    ProviderLedgerEvidenceSource,
    _fields_match,
    _iter_rows,
    _lookup_ref,
    _parse_fields,
    _parse_subject,
)
from dos.evidence import Accountability, EvidenceStance, believe_under_floor


# ---------------------------------------------------------------------------
# A real temp git repo (the test_content_diff._init_repo idiom).
# ---------------------------------------------------------------------------


def _git_ok() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


gitmark = pytest.mark.skipif(not _git_ok(), reason="git not available")

# A host-captured ledger: a JSONL append-log of provider receipts (e.g. webhook dumps).
# The host committed it; the agent under adjudication authored none of these rows.
_LEDGER_JSONL = (
    b'{"id": "ch_1a2b", "status": "succeeded", "amount": 4200, "currency": "usd"}\n'
    b'{"id": "ch_9z8y", "status": "failed", "amount": 100}\n'
)
# The same ledger as a JSON array (the other shape a dump takes).
_LEDGER_ARRAY = (
    b'[{"id": "ch_1a2b", "status": "succeeded", "amount": 4200},'
    b' {"id": "ch_9z8y", "status": "failed"}]\n'
)


def _init_repo(d: Path, *, path: str = "receipts.jsonl", content: bytes = _LEDGER_JSONL) -> str:
    """Init a repo with one committed ledger file; return its HEAD sha."""
    def g(*a):
        subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("config", "commit.gpgsign", "false")
    fp = d / path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(content)  # write_bytes, never write_text — no CRLF surprise
    g("add", path)
    g("commit", "-qm", "capture provider receipts")
    sha = subprocess.run(
        ["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return sha


def _src(tmp: Path) -> ProviderLedgerEvidenceSource:
    return ProviderLedgerEvidenceSource(cwd=str(tmp))


# ---------------------------------------------------------------------------
# 1. The floor falsifier — the agent cannot grade its own homework.
# ---------------------------------------------------------------------------


@gitmark
def test_inline_expectation_that_MATCHES_is_attested_but_at_the_floor(tmp_path: Path):
    """A committed ledger row that matches an agent/plan-supplied (`inline:`) expectation
    ATTESTs — but at AGENT_AUTHORED, so `believe_under_floor` refuses it. The demotion
    that makes the rung sound: the record is real, but the agent CHOSE the expectation."""
    sha = _init_repo(tmp_path)
    facts = _src(tmp_path).gather(
        f"ch_1a2b@receipts.jsonl@{sha}#inline:status=succeeded,amount=4200", None)
    assert facts.stance is EvidenceStance.ATTESTED            # the row DOES confirm the fields
    assert facts.accountability is Accountability.AGENT_AUTHORED  # …but the expectation is forgeable
    assert believe_under_floor((facts,)).believe is False     # the floor refuses it


@gitmark
def test_plan_expectation_is_also_the_floor(tmp_path: Path):
    sha = _init_repo(tmp_path)
    facts = _src(tmp_path).gather(f"ch_1a2b@receipts.jsonl@{sha}#plan:status=succeeded", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.AGENT_AUTHORED
    assert believe_under_floor((facts,)).believe is False


@gitmark
def test_worktree_record_is_the_floor_even_with_a_third_party_expectation(tmp_path: Path, monkeypatch):
    """A `@WORKTREE`-read ledger is the forgeable floor (the agent could have written it
    this turn — actor==witness), so even a THIRD_PARTY expectation is capped at the floor
    by `min(record, expect)` and refused belief."""
    _init_repo(tmp_path)  # also leaves receipts.jsonl in the working tree

    class _ThirdPartyExpect:
        name = "tp_expect"
        accountability = Accountability.THIRD_PARTY

        def expected_fields(self, subject):
            return {"status": "succeeded"}

    import dos.drivers.provider_ledger as pl
    monkeypatch.setattr(pl, "resolve_evidence_source", lambda n: _ThirdPartyExpect())

    facts = _src(tmp_path).gather(
        "ch_1a2b@receipts.jsonl@WORKTREE#source:tp_expect:x", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.AGENT_AUTHORED  # min(floor record, TP expect)
    assert believe_under_floor((facts,)).believe is False


# ---------------------------------------------------------------------------
# 2. The sound climb — a host-captured committed ledger + a host-pinned expectation.
# ---------------------------------------------------------------------------


@gitmark
def test_committed_ledger_with_source_expectation_is_believed(tmp_path: Path, monkeypatch):
    """A committed ledger (OS_RECORDED) + a host-wired THIRD_PARTY expectation whose
    fields the row confirms → ATTESTED and BELIEVED. The derived rung is
    min(OS_RECORDED record, THIRD_PARTY expect) == OS_RECORDED — both non-forgeable."""
    sha = _init_repo(tmp_path)

    class _Expect:
        name = "pinned_expect"
        accountability = Accountability.THIRD_PARTY

        def expected_fields(self, subject):
            return {"status": "succeeded", "amount": 4200}

    import dos.drivers.provider_ledger as pl
    monkeypatch.setattr(pl, "resolve_evidence_source", lambda n: _Expect())

    facts = _src(tmp_path).gather(
        f"ch_1a2b@receipts.jsonl@{sha}#source:pinned_expect:ch_1a2b", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.OS_RECORDED
    assert believe_under_floor((facts,)).believe is True


@gitmark
def test_forgeable_disagreement_is_refuted_stance_but_does_not_redden(tmp_path: Path):
    """An inline (forgeable) expectation that DISAGREES with the row → stance REFUTED (the
    recomputation was done and disagreed), but `believe_under_floor` reports refuted=False:
    a forgeable-floor refutation cannot redden on its own (the symmetric floor rule — the
    agent could have authored the disagreement too). The honest safe-direction no-op."""
    sha = _init_repo(tmp_path)
    facts = _src(tmp_path).gather(
        f"ch_1a2b@receipts.jsonl@{sha}#inline:amount=5000", None)
    assert facts.stance is EvidenceStance.REFUTED
    assert facts.accountability is Accountability.AGENT_AUTHORED
    b = believe_under_floor((facts,))
    assert b.believe is False and b.refuted is False  # forgeable refute does not redden


@gitmark
def test_nonforgeable_disagreement_reddens(tmp_path: Path, monkeypatch):
    """The THIRD_PARTY value: a committed ledger (OS_RECORDED) + a host-pinned
    (THIRD_PARTY) expectation that DISAGREES → stance REFUTED at a non-forgeable rung, and
    `believe_under_floor` reddens (refuted=True): 'you claimed ch_1a2b charged 5000; the
    ledger the agent does not control says 4200.' A real disconfirmation, not 'no signal'."""
    sha = _init_repo(tmp_path)

    class _Expect:
        name = "pinned_expect"
        accountability = Accountability.THIRD_PARTY

        def expected_fields(self, subject):
            return {"amount": "5000"}  # disagrees with the recorded 4200

    import dos.drivers.provider_ledger as pl
    monkeypatch.setattr(pl, "resolve_evidence_source", lambda n: _Expect())

    facts = _src(tmp_path).gather(
        f"ch_1a2b@receipts.jsonl@{sha}#source:pinned_expect:ch_1a2b", None)
    assert facts.stance is EvidenceStance.REFUTED
    assert facts.accountability is Accountability.OS_RECORDED  # min(OS record, TP expect)
    b = believe_under_floor((facts,))
    assert b.believe is False and b.refuted is True


@gitmark
def test_json_array_ledger_parses_too(tmp_path: Path):
    """A JSON-array ledger (not JSONL) is read the same way."""
    sha = _init_repo(tmp_path, content=_LEDGER_ARRAY)
    facts = _src(tmp_path).gather(
        f"ch_1a2b@receipts.jsonl@{sha}#inline:status=succeeded", None)
    assert facts.stance is EvidenceStance.ATTESTED


# ---------------------------------------------------------------------------
# 2b. The abstract third-party validation boundary (`source:` ledger).
# ---------------------------------------------------------------------------


@gitmark
def test_source_ledger_trusts_the_boundary_rung(tmp_path: Path, monkeypatch):
    """A `source:<name>:<subj>` LEDGER resolves a host-wired provider poller exposing
    `ledger_bytes`, at the rung it declares. A THIRD_PARTY poller + an inline expectation
    → derived rung min(THIRD_PARTY, AGENT_AUTHORED) == AGENT_AUTHORED (the inline
    expectation is the weak operand), so the demotion still bites on the expectation."""
    class _LedgerPoll:
        name = "live_poll"
        accountability = Accountability.THIRD_PARTY

        def ledger_bytes(self, subject):
            return _LEDGER_JSONL

    import dos.drivers.provider_ledger as pl
    monkeypatch.setattr(pl, "resolve_evidence_source", lambda n: _LedgerPoll())

    facts = _src(tmp_path).gather(
        "ch_1a2b@source:live_poll:ch_1a2b#inline:status=succeeded", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.AGENT_AUTHORED  # capped by the inline expect
    assert believe_under_floor((facts,)).believe is False


def test_source_ledger_without_ledger_bytes_is_no_signal(monkeypatch):
    """A `source:` ledger validator that exposes no `ledger_bytes()` → no_signal (we
    cannot do a keyed lookup against it), never a fabricated read."""
    class _NoBytes:
        name = "no_bytes"
        accountability = Accountability.THIRD_PARTY

        def gather(self, subject, config):  # pragma: no cover - not reached
            raise AssertionError

    import dos.drivers.provider_ledger as pl
    monkeypatch.setattr(pl, "resolve_evidence_source", lambda n: _NoBytes())

    facts = ProviderLedgerEvidenceSource().gather(
        "ch_1a2b@source:no_bytes:x#inline:status=succeeded", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


def test_unknown_source_ledger_is_no_signal():
    facts = ProviderLedgerEvidenceSource().gather(
        "ch_1a2b@source:does_not_exist:x#inline:status=ok", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


# ---------------------------------------------------------------------------
# 3. Fail-safe, never fail-open.
# ---------------------------------------------------------------------------


@gitmark
def test_absent_reference_is_no_signal_not_refute(tmp_path: Path):
    """A reference not in the ledger → NO_SIGNAL, never a fabricated REFUTE: an effect
    genuinely made may simply not be in THIS captured ledger (absence-from-a-partial-
    ledger is not disconfirmation)."""
    sha = _init_repo(tmp_path)
    facts = _src(tmp_path).gather(
        f"ch_NOTHERE@receipts.jsonl@{sha}#inline:status=succeeded", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


@gitmark
def test_missing_ledger_path_is_no_signal(tmp_path: Path):
    sha = _init_repo(tmp_path)
    facts = _src(tmp_path).gather(
        f"ch_1a2b@no_such_ledger.jsonl@{sha}#inline:status=succeeded", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


@gitmark
def test_malformed_ledger_is_no_signal(tmp_path: Path):
    """A corrupt ledger (not JSON, not JSONL) → NO_SIGNAL, never a partial trusted read."""
    sha = _init_repo(tmp_path, content=b"this is not json at all\n{oops\n")
    facts = _src(tmp_path).gather(
        f"ch_1a2b@receipts.jsonl@{sha}#inline:status=succeeded", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


def test_unparseable_subject_is_no_signal():
    src = ProviderLedgerEvidenceSource()
    for bad in ("", "no-at-no-hash", "  ", "ref@ledger", "ref#expect", "@ledger#expect", "ref@#expect"):
        facts = src.gather(bad, None)
        assert facts.stance is EvidenceStance.NO_SIGNAL, bad
        assert facts.reachable is False, bad


def test_empty_inline_expectation_is_no_signal():
    """An `inline:` with no parseable k=v fields → no_signal (nothing to check), never a
    vacuous attest that would believe ANY row exists."""
    facts = ProviderLedgerEvidenceSource().gather("ch_1a2b@receipts.jsonl#inline:nofields", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL


def test_git_failure_degrades_to_no_signal(monkeypatch):
    """Poison `subprocess.run` to raise — a missing git / OS error → NO_SIGNAL, never a
    crash and never an attestation."""
    def _boom(*a, **k):
        raise FileNotFoundError("git missing")
    monkeypatch.setattr(subprocess, "run", _boom)
    facts = ProviderLedgerEvidenceSource().gather(
        "ch_1a2b@receipts.jsonl@HEAD#inline:status=ok", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False


# ---------------------------------------------------------------------------
# The pure helpers — subject grammar, field parse, keyed lookup, field match.
# ---------------------------------------------------------------------------


def test_parse_subject_defaults_and_full():
    assert _parse_subject("ch_1@led.jsonl#inline:a=b") == ("ch_1", "led.jsonl", "inline:a=b")
    # a source: ledger keeps its own colons; only the leading ref@ is peeled
    assert _parse_subject("ch_1@source:v:s#inline:a=b") == ("ch_1", "source:v:s", "inline:a=b")
    # only the FIRST '#' splits — an expectation value may contain '#'
    assert _parse_subject("ch_1@led#inline:note=a#b") == ("ch_1", "led", "inline:note=a#b")


def test_parse_subject_rejects_empty_parts():
    assert _parse_subject("") is None
    assert _parse_subject("ref@ledger") is None        # no '#'
    assert _parse_subject("ref#expect") is None        # no '@'
    assert _parse_subject("@ledger#expect") is None    # empty ref
    assert _parse_subject("ref@#expect") is None       # empty ledger
    assert _parse_subject("ref@ledger#") is None       # empty expect


def test_parse_fields():
    assert _parse_fields("a=1,b=two") == {"a": "1", "b": "two"}
    assert _parse_fields(" a = 1 , b=2 ") == {"a": "1", "b": "2"}
    assert _parse_fields("noequals,c=3") == {"c": "3"}  # a non-pair is skipped
    assert _parse_fields("") == {}


def test_lookup_ref_finds_by_any_ref_field():
    jsonl = b'{"sid": "SMx", "status": "delivered"}\n{"id": "ch_y", "status": "ok"}\n'
    assert _lookup_ref(jsonl, "SMx") == {"sid": "SMx", "status": "delivered"}
    assert _lookup_ref(jsonl, "ch_y") == {"id": "ch_y", "status": "ok"}
    assert _lookup_ref(jsonl, "absent") is None


def test_lookup_ref_handles_corrupt_and_binary():
    assert _lookup_ref(b"\xff\xfe not utf8", "x") is None
    assert _lookup_ref(b"{bad json}\n", "x") is None
    assert _lookup_ref(b"", "x") is None


def test_iter_rows_array_and_object_and_jsonl():
    assert _iter_rows('[{"a":1},{"b":2}]') == [{"a": 1}, {"b": 2}]
    assert _iter_rows('{"a":1}') == [{"a": 1}]            # a bare object → one row
    assert _iter_rows('{"a":1}\n{"b":2}') == [{"a": 1}, {"b": 2}]  # JSONL
    assert _iter_rows("   ") is None
    assert _iter_rows('{"a":1}\nGARBAGE') is None         # one bad line poisons it


def test_fields_match_subset_and_string_coercion():
    row = {"status": "succeeded", "amount": 4200}
    assert _fields_match(row, {"status": "succeeded"})[0] is True
    assert _fields_match(row, {"amount": "4200"})[0] is True   # int row value == str expect
    ok, why = _fields_match(row, {"amount": "5000"})
    assert ok is False and "amount" in why
    ok, why = _fields_match(row, {"missing": "x"})
    assert ok is False and "absent" in why


# ---------------------------------------------------------------------------
# The CLI boundary — `dos attest --ledger` routes through the driver and folds
# via `effect_witness.witness_effect`.
# ---------------------------------------------------------------------------


def _attest_args(claim: str, ledger: str):
    import argparse
    return argparse.Namespace(
        claim=claim, narrated="", accept_cmd=None, before=None, after=None,
        content=None, ledger=ledger, third_party=False,
    )


@gitmark
def test_attest_ledger_branch_confirms_a_sound_effect(tmp_path: Path, monkeypatch):
    """`_gather_attest_readback` with --ledger + a committed record + a host-pinned
    expectation → CONFIRMED."""
    import dataclasses

    import dos.config as _config
    import dos.drivers.provider_ledger as pl
    from dos import cli

    sha = _init_repo(tmp_path)

    class _Expect:
        name = "pinned_expect"
        accountability = Accountability.THIRD_PARTY

        def expected_fields(self, subject):
            return {"status": "succeeded"}

    monkeypatch.setattr(pl, "resolve_evidence_source", lambda n: _Expect())

    cfg = dataclasses.replace(_config.active(),
                              paths=dataclasses.replace(_config.active().paths, root=tmp_path))
    verdict, surface, err = cli._gather_attest_readback(
        _attest_args("charge ch_1a2b succeeded",
                     f"ch_1a2b@receipts.jsonl@{sha}#source:pinned_expect:ch_1a2b"), cfg)
    assert err == ""
    assert verdict is not None and verdict.verdict.value == "CONFIRMED"
    assert verdict.believe is True


@gitmark
def test_attest_ledger_branch_inline_expectation_is_unwitnessed(tmp_path: Path):
    """An inline (forgeable) expectation that matches → UNWITNESSED through the attest
    fold (the floor refuses to CONFIRM — the agent cannot grade its own homework)."""
    import dataclasses

    import dos.config as _config
    from dos import cli

    sha = _init_repo(tmp_path)
    cfg = dataclasses.replace(_config.active(),
                              paths=dataclasses.replace(_config.active().paths, root=tmp_path))
    verdict, surface, err = cli._gather_attest_readback(
        _attest_args("charge succeeded", f"ch_1a2b@receipts.jsonl@{sha}#inline:status=succeeded"), cfg)
    assert err == ""
    assert verdict.verdict.value == "UNWITNESSED"
    assert verdict.believe is False


def test_attest_rejects_ledger_with_another_surface():
    """--ledger is mutually exclusive with the other witness surfaces."""
    import argparse

    from dos import cli

    args = argparse.Namespace(
        claim="x", narrated="", accept_cmd="pytest -q", before=None, after=None,
        content=None, ledger="ch@led#inline:a=b", third_party=False,
    )
    verdict, surface, err = cli._gather_attest_readback(args, None)
    assert verdict is None
    assert "exactly ONE" in err
