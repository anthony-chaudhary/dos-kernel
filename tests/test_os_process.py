"""dos.drivers.os_process — the process/port-liveness witness (docs/384).

The "OS stuff" rung: the OS authors the process-table entry and the TCP handshake;
the agent cannot keep a dead process listening or complete a connect to nothing. These
tests pin the contract on frozen data — `socket.create_connection` and
`proc_delta.probe` are monkeypatched so the suite NEVER opens a real socket or reads a
real process (the test_os_acceptance / test_http_probe poison discipline). The
load-bearing properties: a completed connect → ATTESTED at OS_RECORDED (grants belief);
a refused connect → REFUTED (nothing listening); a dropped/timed-out connect →
NO_SIGNAL (a firewall black-hole is "cannot tell", never a fabricated refute).
"""

from __future__ import annotations

import socket

from dos import proc_delta
from dos.drivers.os_process import OsProcessEvidenceSource, _parse_hostport
from dos.evidence import Accountability, EvidenceStance, believe_under_floor
from dos.proc_delta import ProcLiveness


def _src() -> OsProcessEvidenceSource:
    return OsProcessEvidenceSource(timeout_s=1)


class _DummySock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# Pure host:port parsing.
# ---------------------------------------------------------------------------


def test_parse_port_variants():
    assert _parse_hostport("port", "8080") == ("127.0.0.1", 8080)
    assert _parse_hostport("port", "example.com:443") == ("example.com", 443)
    assert _parse_hostport("listening", "9000") == ("127.0.0.1", 9000)


def test_parse_rejects_bad_port():
    assert _parse_hostport("port", "notaport")[1] is None
    assert _parse_hostport("port", "0")[1] is None
    assert _parse_hostport("port", "70000")[1] is None


# ---------------------------------------------------------------------------
# The TCP rung.
# ---------------------------------------------------------------------------


def test_port_connect_attests_and_grants_belief(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=None: _DummySock())
    facts = _src().gather("port:127.0.0.1:8080", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.OS_RECORDED
    assert believe_under_floor((facts,)).believe is True


def test_port_refused_refutes(monkeypatch):
    def _refuse(addr, timeout=None):
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", _refuse)
    facts = _src().gather("listening:8080", None)
    assert facts.stance is EvidenceStance.REFUTED
    assert believe_under_floor((facts,)).refuted is True


def test_port_timeout_is_no_signal(monkeypatch):
    def _timeout(addr, timeout=None):
        raise socket.timeout()
    monkeypatch.setattr(socket, "create_connection", _timeout)
    facts = _src().gather("port:10.0.0.1:8080", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    v = believe_under_floor((facts,))
    assert v.believe is False and v.refuted is False


def test_port_oserror_is_no_signal(monkeypatch):
    def _err(addr, timeout=None):
        raise OSError("network unreachable")
    monkeypatch.setattr(socket, "create_connection", _err)
    assert _src().gather("port:8080", None).stance is EvidenceStance.NO_SIGNAL


# ---------------------------------------------------------------------------
# The process-table rung (reuses proc_delta.probe).
# ---------------------------------------------------------------------------


def test_pid_alive_attests(monkeypatch):
    monkeypatch.setattr(proc_delta, "probe", lambda pid, **k: ProcLiveness(True, f"pid {pid} is alive"))
    facts = _src().gather("pid:4242", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.OS_RECORDED


def test_pid_gone_refutes(monkeypatch):
    monkeypatch.setattr(proc_delta, "probe", lambda pid, **k: ProcLiveness(False, f"pid {pid} is gone"))
    facts = _src().gather("pid:4242", None)
    assert facts.stance is EvidenceStance.REFUTED


def test_pid_unknown_is_no_signal(monkeypatch):
    monkeypatch.setattr(proc_delta, "probe", lambda pid, **k: ProcLiveness(None, "cannot tell"))
    facts = _src().gather("pid:4242", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL


def test_malformed_pid_is_no_signal():
    assert _src().gather("pid:notanumber", None).stance is EvidenceStance.NO_SIGNAL


# ---------------------------------------------------------------------------
# Malformed subjects → NO_SIGNAL (never a fabricated stance).
# ---------------------------------------------------------------------------


def test_unparseable_subject_is_no_signal():
    assert _src().gather("", None).stance is EvidenceStance.NO_SIGNAL
    assert _src().gather("garbage", None).stance is EvidenceStance.NO_SIGNAL


def test_unknown_kind_is_no_signal():
    assert _src().gather("frob:123", None).stance is EvidenceStance.NO_SIGNAL
