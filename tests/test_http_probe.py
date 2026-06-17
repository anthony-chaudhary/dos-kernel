"""dos.drivers.http_probe — the live-endpoint read-back witness (docs/383).

`evidence.py` names "a deploy shipped" as a witness the git rung is blind to; the
accountable witness is the server that answers the request. These tests pin the
driver's contract on frozen data: `urllib.request.urlopen` is monkeypatched so the
suite NEVER makes a real network call (the test_os_acceptance subprocess-poison
discipline, restated for the network boundary). The load-bearing properties:

  * a reached 2xx → ATTESTED at THIRD_PARTY (the server authored the status line,
    which the agent cannot forge) → grants belief;
  * a reached non-2xx → REFUTED (a positive disconfirmation — the deploy is up but
    UNHEALTHY — the silent-fail made visible), not "could not tell";
  * an HTTP error status (4xx/5xx) is a REACHED response, mapped by the assertion —
    never confused with unreachability;
  * unreachable (DNS / refused / timeout / bad URL) → NO_SIGNAL → abstain, never a
    fabricated REFUTE that would falsely fail a healthy deploy on a network blip.
"""

from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request

from dos.drivers.http_probe import (
    HttpProbeEvidenceSource,
    _check_assertion,
    _parse_subject,
)
from dos.evidence import Accountability, EvidenceStance, believe_under_floor


# ---------------------------------------------------------------------------
# Fakes — a response stand-in + urlopen poisons (the suite never hits the net).
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int, body: bytes = b""):
        self.status = status
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _src() -> HttpProbeEvidenceSource:
    return HttpProbeEvidenceSource(timeout_s=1)


def _ok(monkeypatch, status: int, body: bytes = b""):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(status, body))


def _httperror(monkeypatch, code: int, body: bytes = b""):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))
    monkeypatch.setattr(urllib.request, "urlopen", _raise)


def _unreachable(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", _raise)


# ---------------------------------------------------------------------------
# Pure subject parsing.
# ---------------------------------------------------------------------------


def test_parse_defaults_to_get():
    assert _parse_subject("https://app/health") == ("GET", "https://app/health", "")


def test_parse_explicit_method_and_assertion():
    assert _parse_subject("HEAD https://app/x#status:204") == (
        "HEAD", "https://app/x", "status:204")


def test_parse_rejects_non_http():
    assert _parse_subject("ftp://app/x") is None
    assert _parse_subject("not-a-url") is None
    assert _parse_subject("") is None


# ---------------------------------------------------------------------------
# Reached responses → ATTEST / REFUTE at THIRD_PARTY.
# ---------------------------------------------------------------------------


def test_2xx_is_attested_and_third_party(monkeypatch):
    _ok(monkeypatch, 200)
    facts = _src().gather("https://app/health", None)
    assert facts.stance is EvidenceStance.ATTESTED
    assert facts.accountability is Accountability.THIRD_PARTY
    # THIRD_PARTY is non-forgeable → the attest grants belief.
    assert believe_under_floor((facts,)).believe is True


def test_non_2xx_default_is_refuted(monkeypatch):
    _ok(monkeypatch, 503)
    facts = _src().gather("https://app/health", None)
    assert facts.stance is EvidenceStance.REFUTED
    # an accountable REFUTE reddens the floor verdict.
    assert believe_under_floor((facts,)).refuted is True


def test_status_assertion_match_attests(monkeypatch):
    _ok(monkeypatch, 204)
    facts = _src().gather("https://app/x#status:204", None)
    assert facts.stance is EvidenceStance.ATTESTED


def test_status_assertion_mismatch_refutes(monkeypatch):
    _ok(monkeypatch, 200)
    facts = _src().gather("https://app/x#status:204", None)
    assert facts.stance is EvidenceStance.REFUTED


def test_contains_assertion(monkeypatch):
    _ok(monkeypatch, 200, b'{"status":"ok"}')
    assert _src().gather("https://app/x#contains:ok", None).stance is EvidenceStance.ATTESTED
    _ok(monkeypatch, 200, b'{"status":"down"}')
    assert _src().gather("https://app/x#contains:ok", None).stance is EvidenceStance.REFUTED


def test_sha256_body_assertion(monkeypatch):
    body = b"the canonical health body\n"
    digest = hashlib.sha256(body).hexdigest()
    _ok(monkeypatch, 200, body)
    assert _src().gather(f"https://app/x#sha256:{digest}", None).stance is EvidenceStance.ATTESTED
    _ok(monkeypatch, 200, b"tampered")
    assert _src().gather(f"https://app/x#sha256:{digest}", None).stance is EvidenceStance.REFUTED


# ---------------------------------------------------------------------------
# An HTTP error status is a REACHED response (mapped by the assertion).
# ---------------------------------------------------------------------------


def test_http_error_status_is_reached_not_unreachable(monkeypatch):
    """A 500 is the server ANSWERING — a reached response. Default (2xx) → REFUTED."""
    _httperror(monkeypatch, 500, b"oops")
    facts = _src().gather("https://app/health", None)
    assert facts.reachable is True
    assert facts.stance is EvidenceStance.REFUTED


def test_http_error_status_can_attest_when_asserted(monkeypatch):
    """`#status:500` over a 500 → ATTESTED (the effect WAS 'return a 500')."""
    _httperror(monkeypatch, 500, b"oops")
    facts = _src().gather("https://app/x#status:500", None)
    assert facts.stance is EvidenceStance.ATTESTED


# ---------------------------------------------------------------------------
# Unreachable → NO_SIGNAL (abstain), never a fabricated refute.
# ---------------------------------------------------------------------------


def test_unreachable_is_no_signal(monkeypatch):
    _unreachable(monkeypatch)
    facts = _src().gather("https://app/health", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL
    assert facts.reachable is False
    v = believe_under_floor((facts,))
    assert v.believe is False and v.refuted is False


def test_unparseable_subject_is_no_signal():
    facts = _src().gather("not-a-url", None)
    assert facts.stance is EvidenceStance.NO_SIGNAL


# ---------------------------------------------------------------------------
# Pure assertion checks.
# ---------------------------------------------------------------------------


def test_check_assertion_status_classes():
    assert _check_assertion("", 200, b"")[0] is True
    assert _check_assertion("status:2xx", 299, b"")[0] is True
    assert _check_assertion("status:2xx", 300, b"")[0] is False
    assert _check_assertion("status:5xx", 503, b"")[0] is True
    assert _check_assertion("status:404", 404, b"")[0] is True
    assert _check_assertion("status:404", 200, b"")[0] is False


def test_check_assertion_unknown_kind_fails_closed():
    ok, why = _check_assertion("frobnicate:x", 200, b"")
    assert ok is False
    assert "unknown assertion" in why
