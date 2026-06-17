"""`dos witness` — the unified, by-name witness verb (docs/381).

`dos verify` reads exactly one witness (git); `dos attest` joins a claim over a
hard-coded set of four surfaces. This verb resolves WHICHEVER `dos.evidence_sources`
backend the operator names — built-in or third-party plugin — gathers it against a
subject, and folds through the floor discipline (`believe_under_floor`). The whole
point: a new witness KIND becomes invocable with NO kernel edit, just an entry-point
registration.

These tests pin the verb's contract on frozen data: the listing, the four exit-code
branches (believe / refute / abstain / usage), the LOUD unknown-name failure, and —
the load-bearing one — that a forgeable-floor (AGENT_AUTHORED) source that ATTESTS
still does NOT grant belief at the verb level (the `believe_under_floor` discipline
restated for the CLI surface). `subprocess.run` is poisoned where an OS witness is
exercised so the suite never spawns a real process (the test_os_acceptance rule).
"""

from __future__ import annotations

import json
import subprocess

from dos import cli
from dos.evidence import Accountability, EvidenceFacts


# ---------------------------------------------------------------------------
# Helpers — a poisoned subprocess + a fake forgeable-floor source.
# ---------------------------------------------------------------------------


class _FakeProc:
    """A `subprocess.run` result stand-in carrying only the OS-recorded exit code."""

    def __init__(self, returncode: int):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class _ForgeableAttestSource:
    """A witness that ATTESTS but on the forgeable floor — to prove the verb honors the
    floor (an AGENT_AUTHORED attest is recorded but cannot move belief)."""

    name = "fake_floor"
    accountability = Accountability.AGENT_AUTHORED

    def gather(self, subject: str, config: object) -> EvidenceFacts:
        return EvidenceFacts.attest(
            self.name, self.accountability, subject,
            detail="self-reported attest — should be IGNORED for belief",
        )


# ---------------------------------------------------------------------------
# --list — the population view.
# ---------------------------------------------------------------------------


def test_list_shows_population_and_rungs(capsys):
    rc = cli.main(["witness", "--list"])
    out = capsys.readouterr().out
    assert rc == 0
    # built-ins are always present, with their declared rung
    assert "null" in out and "AGENT_AUTHORED" in out
    # the registered OS witness is discovered with its OS_RECORDED rung
    assert "os_acceptance" in out and "OS_RECORDED" in out
    # the forgeable-floor sources are flagged as such
    assert "forgeable floor" in out


def test_list_json_is_machine_readable(capsys):
    rc = cli.main(["witness", "--list", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    obj = json.loads(out)
    names = {r["name"]: r["accountability"] for r in obj["sources"]}
    assert names.get("null") == "AGENT_AUTHORED"
    assert names.get("os_acceptance") == "OS_RECORDED"


# ---------------------------------------------------------------------------
# The four exit-code branches.
# ---------------------------------------------------------------------------


def test_null_source_is_no_signal_and_abstains(capsys):
    """The built-in null witness reaches nothing → NO_SIGNAL, believe False, exit 3."""
    rc = cli.main(["witness", "null", "some-effect"])
    out = capsys.readouterr().out
    assert rc == 3
    assert "NO_SIGNAL" in out
    assert "BELIEVE   False" in out


def test_os_acceptance_attest_believes_exit_0(monkeypatch, capsys):
    """An OS_RECORDED witness that attests (exit 0) → BELIEVE, exit 0."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(0))
    rc = cli.main(["witness", "os_acceptance", "pytest -q"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ATTESTED" in out
    assert "BELIEVE   True" in out


def test_os_acceptance_refute_exit_1(monkeypatch, capsys):
    """A clean non-zero exit is a positive disconfirmation → REFUTED, exit 1."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(1))
    rc = cli.main(["witness", "os_acceptance", "pytest -q"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUTED" in out
    assert "refuted=True" in out


def test_forgeable_floor_attest_does_not_believe(monkeypatch, capsys):
    """THE FLOOR at the verb level: an AGENT_AUTHORED source that ATTESTS is recorded but
    structurally cannot grant belief → abstain, exit 3 (never a self-reported pass)."""
    from dos import evidence
    monkeypatch.setattr(evidence, "resolve_evidence_source",
                        lambda name, **k: _ForgeableAttestSource())
    rc = cli.main(["witness", "fake_floor", "effect"])
    out = capsys.readouterr().out
    assert rc == 3                       # attested, but on the floor → no belief
    assert "ATTESTED" in out             # the attest is shown (legible distrust)
    assert "BELIEVE   False" in out      # …and ignored for belief


# ---------------------------------------------------------------------------
# Usage / resolution errors — fail LOUD, never a silent degrade.
# ---------------------------------------------------------------------------


def test_unknown_source_fails_loud_with_known_list(capsys):
    rc = cli.main(["witness", "no-such-source", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown evidence source" in err
    assert "os_acceptance" in err        # the known list is offered


def test_no_source_and_no_list_is_usage_error(capsys):
    rc = cli.main(["witness"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "name a SOURCE" in err or "--list" in err


# ---------------------------------------------------------------------------
# --json verdict shape.
# ---------------------------------------------------------------------------


def test_json_verdict_carries_facts_and_belief(capsys):
    rc = cli.main(["witness", "null", "effect", "--json"])
    out = capsys.readouterr().out
    assert rc == 3
    obj = json.loads(out)
    assert obj["facts"]["accountability"] == "AGENT_AUTHORED"
    assert obj["facts"]["stance"] == "NO_SIGNAL"
    assert obj["belief"]["believe"] is False
