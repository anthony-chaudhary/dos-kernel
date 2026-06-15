"""The BINDING completion rung — the OS exit code, not the forgeable subject (docs/342 M3).

docs/342 Milestone 3 (P-CHECK) makes `verify`'s COMPLETION decision bind on an
UN-AUTHORED rung — the OS exit code of a host-supplied acceptance command, launched by
the kernel through `drivers/os_acceptance` — wherever the host declares one. The hole it
closes: the default binding rung in many flows is `grep-subject`, the commit SUBJECT the
agent typed, which an `--allow-empty -m '<stamp>'` clears with zero work shipped
(docs/335 §3.2, docs/138 "Where truth is still forgeable"). The OS authored the exit
code; the judged agent did not — so a non-zero exit at the very commit the git rung
matched DEMOTES a forged-subject `shipped=True` to NOT_SHIPPED.

These tests pin the driver → `EvidenceFacts` → `oracle._apply_exec_rung` chain on
frozen data: `subprocess.run` is poisoned so the suite NEVER spawns a real process (the
`test_evidence.py::TestOsAcceptanceDriver` discipline). The CONFORMANCE-not-correctness
boundary is explicit: a green exit proves the host's check passed, never that the work is
right (Rice — docs/183; green-on-wrong-tests is still forgeable — docs/85).
"""

from __future__ import annotations

import subprocess

from dos import oracle
from dos.evidence import Accountability, EvidenceFacts, EvidenceStance, believe_under_floor
from dos.oracle import ShipVerdict, _apply_exec_rung


# ---------------------------------------------------------------------------
# The driver: a poisoned subprocess maps an exit code → a fixed-rung EvidenceFacts.
# (Mirrors test_evidence.py::TestOsAcceptanceDriver so the suite never spawns.)
# ---------------------------------------------------------------------------


class _FakeProc:
    """A `subprocess.run` result stand-in carrying only the OS-recorded exit code."""

    def __init__(self, returncode: int):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _source():
    from dos.drivers.os_acceptance import OsAcceptanceEvidenceSource

    return OsAcceptanceEvidenceSource()


def _gather(cmd: str, monkeypatch, *, returncode: int) -> EvidenceFacts:
    """Run the os_acceptance source over `cmd` with a poisoned exit code."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(returncode))
    return _source().gather(cmd, None)


# ---------------------------------------------------------------------------
# The pure fold — `oracle._apply_exec_rung`. PURE, no I/O, frozen EvidenceFacts.
# ---------------------------------------------------------------------------


def _os_attest() -> EvidenceFacts:
    return EvidenceFacts.attest("os_acceptance", Accountability.OS_RECORDED, "pytest -q",
                                detail="`pytest` exited 0 — acceptance check passed")


def _os_refute() -> EvidenceFacts:
    return EvidenceFacts.refute("os_acceptance", Accountability.OS_RECORDED, "pytest -q",
                                detail="`pytest` exited 1 — acceptance check failed (OS-recorded)")


def _os_no_signal() -> EvidenceFacts:
    return EvidenceFacts.no_signal("os_acceptance", Accountability.OS_RECORDED, "pytest -q",
                                   detail="command not found — no signal")


class TestApplyExecRungBinds:
    """`_apply_exec_rung` BINDS the ship on the un-authored OS exit code."""

    def test_refuted_demotes_a_forged_subject_ship_to_not_shipped(self):
        """THE P-CHECK WITNESS: a `grep-subject` (forgeable) ship + an OS REFUTE → DEMOTED.

        An `--allow-empty -m '<stamp>'` commit clears grep-subject (`shipped=True`), but
        the host's exec command exits non-zero — the OS exit code (un-authored)
        disagrees with the subject the agent typed, so the ship is reversed."""
        forged = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc123")
        out = _apply_exec_rung(forged, _os_refute())
        assert out.shipped is False                 # demoted — the forgery is caught
        assert out.source == "exec-refuted"
        assert "DEMOTED" in out.summary
        assert "OS-recorded" in out.summary

    def test_attested_upgrades_source_to_exec_attested(self):
        """An OS ATTEST (exit 0) mints the un-authored binding source `exec-attested`."""
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc123")
        out = _apply_exec_rung(v, _os_attest())
        assert out.shipped is True
        assert out.source == "exec-attested"        # the un-authored binding mint
        assert "exited 0" in out.summary            # the why is legible
        assert out.sha == "abc123"                  # everything else preserved

    def test_no_signal_passes_through_byte_identical(self):
        """A NO_SIGNAL (command missing / timeout) is NEVER a refutation — fail-safe.

        Inability to RUN the check degrades to the git answer, byte-identical; it must
        not be mistaken for a failing check (which would manufacture a false demote)."""
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc123",
                        summary="orig", rung="direct")
        assert _apply_exec_rung(v, _os_no_signal()) == v

    def test_none_facts_is_identity(self):
        """No exec command declared → `None` facts → identity (gate-OFF, byte-identical)."""
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc")
        assert _apply_exec_rung(v, None) == v

    def test_exec_rung_never_promotes_a_false_verdict(self):
        """THE §1 invariant: a git `shipped=False` + an OS ATTEST STAYS `shipped=False`.

        There is no commit for the exec rung to be green ABOUT — the binding rung can
        only DEMOTE a ship, never manufacture one (refuse-MORE only)."""
        not_shipped = ShipVerdict("DOCS", "M3", shipped=False, source="none")
        assert _apply_exec_rung(not_shipped, _os_attest()) == not_shipped
        # even a refute on a false verdict is a no-op (nothing to demote)
        assert _apply_exec_rung(not_shipped, _os_refute()) == not_shipped

    def test_forgeable_floor_facts_cannot_bind(self):
        """An AGENT_AUTHORED exec fact (a pasted "it passed") can NEITHER demote NOR
        upgrade — the binding rung is the UN-AUTHORED one (the `believe_under_floor`
        floor discipline, restated for the ship bit)."""
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc")
        forged_pass = EvidenceFacts.attest("paste", Accountability.AGENT_AUTHORED, "pytest -q")
        forged_fail = EvidenceFacts.refute("paste", Accountability.AGENT_AUTHORED, "pytest -q")
        assert _apply_exec_rung(v, forged_pass) == v   # cannot upgrade off a self-report
        assert _apply_exec_rung(v, forged_fail) == v   # cannot demote off a self-report

    def test_third_party_attest_also_binds(self):
        """A THIRD_PARTY witness (also non-forgeable) binds the same way as OS_RECORDED."""
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc")
        tp = EvidenceFacts.refute("registry_digest", Accountability.THIRD_PARTY, "probe")
        out = _apply_exec_rung(v, tp)
        assert out.shipped is False
        assert out.source == "exec-refuted"


class TestExecRungEndToEndViaDriver:
    """The driver → facts → fold chain, with `subprocess.run` poisoned (never spawns)."""

    def test_exit_zero_facts_bind_to_attested(self, monkeypatch):
        facts = _gather("pytest -q", monkeypatch, returncode=0)
        assert facts.stance is EvidenceStance.ATTESTED
        assert facts.accountability is Accountability.OS_RECORDED
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc")
        out = _apply_exec_rung(v, facts)
        assert out.shipped is True and out.source == "exec-attested"

    def test_exit_nonzero_facts_demote_a_forged_ship(self, monkeypatch):
        """End-to-end P-CHECK: the OS exit code (poisoned to 1) reverses the forged ship.

        The witness is the exit code the kernel captured — which the agent did not
        author — disagreeing with the (forgeable) commit subject."""
        facts = _gather("pytest -q", monkeypatch, returncode=1)
        assert facts.stance is EvidenceStance.REFUTED
        # the OS-recorded refute DOES redden through the floor (the population proof)
        assert believe_under_floor((facts,)).refuted is True
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc")
        out = _apply_exec_rung(v, facts)
        assert out.shipped is False and out.source == "exec-refuted"

    def test_missing_command_is_no_signal_and_passes_through(self, monkeypatch):
        """A missing binary → NO_SIGNAL → the git verdict untouched (fail-safe)."""
        def _boom(*a, **k):
            raise FileNotFoundError()

        monkeypatch.setattr(subprocess, "run", _boom)
        facts = _source().gather("definitely-not-a-real-binary --check", None)
        assert facts.stance is EvidenceStance.NO_SIGNAL
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc")
        assert _apply_exec_rung(v, facts) == v


class TestConformanceNotCorrectness:
    """The exec rung binds CONFORMANCE (an un-authored completion signal), not CORRECTNESS.

    Rice's theorem forecloses a mechanical correctness oracle (docs/183); a green exit
    proves the host's declared check passed, never that the work is RIGHT
    (green-on-wrong-tests is still forgeable — docs/85). These tests document that the
    fold makes no correctness claim — it only relabels the SOURCE by who authored the
    signal."""

    def test_attest_is_a_signal_label_not_a_correctness_claim(self):
        # `exec-attested` names the witness (the OS exit code), not "the work is good".
        v = ShipVerdict("DOCS", "M3", shipped=True, source="grep-subject", sha="abc")
        out = _apply_exec_rung(v, _os_attest())
        assert out.source == "exec-attested"  # provenance, not a quality verdict
        # the ship bit is still the git rung's — the exec rung only re-authored the label
        assert out.shipped is True
