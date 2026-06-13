"""The `otlp` exporter driver (`dos.drivers.export_otlp`) — pure mapping + lazy extra (docs/266).

Pins the OTLP log-record MAPPING against a fixed batch (the pure `build_records`, which
imports no OpenTelemetry — so the shape is testable with or without the extra), plus the
seam disciplines: the lazy-import + install-hint contract (an absent `[export-otlp]`
extra → a non-exported result, NEVER an ImportError), fail-soft on a raising transport,
dry-run, the endpoint resolution ladder, and discovery-by-name.

No real collector is ever contacted: the OTLP construction is confined to
`_OtlpTransport.emit`, which these tests replace with a fake.
"""

from __future__ import annotations

import pytest

from dos.exporter import resolve_exporter, export_safely, active_exporter_names
from dos.drivers.export_otlp import (
    OtlpExporter,
    build_records,
    resolve_endpoint,
    _severity_number,
    _SEV_INFO,
    _SEV_WARN,
    _SEV_ERROR,
    _GENAI_ATTR_MAP,
)
from dos.verdict_journal import VerdictEvent


def _ev(syscall, verdict, *, seq=0, run_id="", lane="", subject="", detail=None):
    return VerdictEvent(
        syscall=syscall, verdict=verdict, run_id=run_id, lane=lane, subject=subject,
        detail=detail or {}, seq=seq, ts="2026-06-09T00:00:00Z",
    )


class _FakeOtlp:
    """Records the (records, endpoint) handed to emit; the injected transport."""

    def __init__(self, *, boom=None):
        self.calls: list[tuple[list, str]] = []
        self._boom = boom  # an exception instance to raise, or None

    def emit(self, records, endpoint) -> int:
        if self._boom is not None:
            raise self._boom
        self.calls.append((list(records), endpoint))
        return len(records)


# =====================================================================================
# pure mapping — build_records is import-free + golden-shape testable
# =====================================================================================


def test_build_records_golden_shape():
    batch = [
        _ev("liveness", "STALLED", seq=1, run_id="RID-a", lane="src",
            detail={"idle_s": 600, "heartbeats": 0}),
    ]
    recs = build_records(batch)
    assert len(recs) == 1
    r = recs[0]
    assert r["body"] == "liveness STALLED"
    assert r["severity_number"] == _SEV_ERROR        # STALLED → ERROR
    assert r["attributes"]["dos.syscall"] == "liveness"
    assert r["attributes"]["dos.verdict"] == "STALLED"
    assert r["attributes"]["dos.run_id"] == "RID-a"  # the correlation key
    assert r["attributes"]["dos.lane"] == "src"
    # byte-clean detail counts ride along, flattened under dos.detail.*
    assert r["attributes"]["dos.detail.idle_s"] == 600
    assert r["attributes"]["dos.detail.heartbeats"] == 0


def test_efficiency_spend_maps_to_otel_genai_names():
    """#39: an efficiency event carrying the spend fossil (evidence.breakdown.*)
    emits the OTel GenAI usage attributes ALONGSIDE the dos.detail.* forms."""
    batch = [
        _ev("efficiency", "EFFICIENT", seq=1, run_id="RID-x", detail={
            "evidence.work": 5,
            "evidence.tokens": 85_000,
            "evidence.breakdown.input": 10_000,
            "evidence.breakdown.output": 20_000,
            "evidence.breakdown.cache_read": 50_000,
            "evidence.breakdown.cache_creation": 5_000,
            "evidence.breakdown.reasoning": 8_000,
            "evidence.breakdown.cache_hit_ratio": 0.83,  # a derived diagnostic
        }),
    ]
    a = build_records(batch)[0]["attributes"]
    # The standardized GenAI names carry the canonical counts, matching the record.
    assert a["gen_ai.usage.input_tokens"] == 10_000
    assert a["gen_ai.usage.output_tokens"] == 20_000
    assert a["gen_ai.usage.cache_read.input_tokens"] == 50_000
    assert a["gen_ai.usage.cache_creation.input_tokens"] == 5_000
    assert a["gen_ai.usage.reasoning.output_tokens"] == 8_000
    # Alongside, not replacing — the dos.detail.* forms are still present.
    assert a["dos.detail.evidence.breakdown.input"] == 10_000
    # The DERIVED diagnostic has no OTel-standard name — dos.detail.* only.
    assert a["dos.detail.evidence.breakdown.cache_hit_ratio"] == 0.83
    assert not any(k.startswith("gen_ai") and "cache_hit_ratio" in k for k in a)


def test_no_breakdown_emits_no_genai_attributes():
    """An event with no spend breakdown gets no gen_ai.* attribute (no
    zero-stuffing — the fire-only-when-present rule)."""
    batch = [_ev("liveness", "ADVANCING", seq=1, detail={"idle_s": 5})]
    a = build_records(batch)[0]["attributes"]
    assert not any(k.startswith("gen_ai") for k in a)


def test_genai_attr_map_is_the_canonical_otel_vocabulary():
    """The mapping constant is the single source of truth (#39): the five
    canonical OTel GenAI usage names, keyed by the journal fossil keys."""
    assert _GENAI_ATTR_MAP == {
        "evidence.breakdown.input": "gen_ai.usage.input_tokens",
        "evidence.breakdown.output": "gen_ai.usage.output_tokens",
        "evidence.breakdown.cache_read": "gen_ai.usage.cache_read.input_tokens",
        "evidence.breakdown.cache_creation": "gen_ai.usage.cache_creation.input_tokens",
        "evidence.breakdown.reasoning": "gen_ai.usage.reasoning.output_tokens",
    }


def test_build_records_omits_empty_optional_attributes():
    """No run_id/lane/subject → those attribute keys are absent (not empty strings).
    `dos.source` is always present (VerdictEvent.source defaults to 'kernel')."""
    r = build_records([_ev("verify", "SHIPPED")])[0]
    assert "dos.run_id" not in r["attributes"]
    assert "dos.lane" not in r["attributes"]
    assert "dos.subject" not in r["attributes"]
    assert r["attributes"] == {
        "dos.syscall": "verify", "dos.verdict": "SHIPPED", "dos.source": "kernel"}


def test_build_records_coerces_nonscalar_detail():
    """An OTLP attribute must be a scalar; a nested/odd detail value is coerced to str."""
    r = build_records([_ev("x", "Y", detail={"nested": {"a": 1}, "n": 5})])[0]
    assert r["attributes"]["dos.detail.nested"] == "{'a': 1}"  # coerced
    assert r["attributes"]["dos.detail.n"] == 5                # scalar kept


def test_severity_mapping():
    assert _severity_number("SHIPPED") == _SEV_INFO       # neutral status
    assert _severity_number("ADVANCING") == _SEV_INFO
    assert _severity_number("SPINNING") == _SEV_WARN       # something off
    assert _severity_number("WASTEFUL") == _SEV_ERROR      # hard failure
    assert _severity_number("STALLED") == _SEV_ERROR
    assert _severity_number("anything-unknown") == _SEV_INFO  # default INFO


# =====================================================================================
# the lazy-extra contract — the heart of Phase 3
# =====================================================================================


def test_absent_extra_returns_install_hint_not_importerror():
    """A fake transport that raises ImportError (== the [export-otlp] extra absent) →
    a non-exported ExportResult with the install hint, NEVER a propagated ImportError."""
    ex = OtlpExporter(transport=_FakeOtlp(boom=ImportError("No module named 'opentelemetry'")))
    res = ex.export([_ev("liveness", "STALLED")])
    assert res.exported == 0
    assert "export-otlp" in res.detail
    assert "pip install" in res.detail


def test_real_transport_absent_extra_is_install_hint():
    """The REAL transport against THIS environment (which lacks the OTLP HTTP exporter
    package) must also degrade to the install hint — the live proof the lazy import is
    caught, not crashed. (If the extra is ever installed in CI, this still passes: a
    reachable-but-refused localhost:4318 would be a soft 'error:' result, also exported=0.)"""
    res = OtlpExporter(endpoint="http://localhost:4318").export([_ev("liveness", "STALLED")])
    assert res.exported == 0
    # either the install hint (extra absent) or a soft connection error (extra present,
    # no collector) — both are fail-soft, neither raised.
    assert ("export-otlp" in res.detail) or ("error:" in res.detail)


def test_module_imports_without_the_extra():
    """Importing the driver must never require the SDK (entry-point discovery imports
    it). The fact this test module imported `export_otlp` at the top already proves it,
    but assert the transport class exists without having imported any OTel symbol."""
    from dos.drivers import export_otlp
    assert hasattr(export_otlp, "OtlpExporter")
    assert hasattr(export_otlp, "_OtlpTransport")


# =====================================================================================
# seam disciplines — fail-soft, dry-run, empty, emit, resolution
# =====================================================================================


def test_clean_emit_over_fake_transport():
    sock = _FakeOtlp()
    ex = OtlpExporter(endpoint="http://collector:4318", transport=sock)
    res = ex.export([_ev("liveness", "STALLED", seq=5), _ev("verify", "SHIPPED", seq=6)])
    assert res.exported == 2
    assert res.cursor == "6"
    assert "to http://collector:4318" in res.detail
    assert len(sock.calls) == 1
    records, endpoint = sock.calls[0]
    assert endpoint == "http://collector:4318"
    assert len(records) == 2


def test_fail_soft_on_a_non_import_raise():
    """A non-ImportError raise (a down collector) → a soft 'error:' result."""
    ex = OtlpExporter(transport=_FakeOtlp(boom=ConnectionError("refused")))
    res = ex.export([_ev("liveness", "STALLED")])
    assert res.exported == 0
    assert "error:" in res.detail and "refused" in res.detail
    # and through the seam wrapper too
    assert export_safely(ex, [_ev("liveness", "STALLED")]).exported == 0


def test_dry_run_emits_nothing_and_never_imports():
    sock = _FakeOtlp()
    ex = OtlpExporter(endpoint="http://c:4318", transport=sock, dry_run=True)
    res = ex.export([_ev("liveness", "STALLED", seq=3)])
    assert res.exported == 0
    assert "[dry-run]" in res.detail
    assert sock.calls == []
    assert res.cursor == "3"


def test_empty_batch_is_a_clean_noop():
    sock = _FakeOtlp()
    res = OtlpExporter(transport=sock).export([])
    assert res.exported == 0
    assert "no new events" in res.detail
    assert sock.calls == []


def test_endpoint_resolution_ladder(tmp_path, monkeypatch):
    monkeypatch.delenv("DOS_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert resolve_endpoint("", root=None) == "http://localhost:4318"      # default
    assert resolve_endpoint("http://x:1", root=None) == "http://x:1"        # explicit
    monkeypatch.setenv("DOS_OTLP_ENDPOINT", "http://dos:2")
    assert resolve_endpoint("", root=tmp_path) == "http://dos:2"            # DOS env
    monkeypatch.delenv("DOS_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:3")
    assert resolve_endpoint("", root=tmp_path) == "http://otel:3"           # OTel std env
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    (tmp_path / ".env").write_text("DOS_OTLP_ENDPOINT=http://dot:4\n", encoding="utf-8")
    assert resolve_endpoint("", root=tmp_path) == "http://dot:4"            # .env


def test_resolvable_by_name_and_kwarg_filtered():
    """`resolve_exporter("otlp")` finds it by name and ignores file/statsd kwargs."""
    ex = resolve_exporter("otlp", endpoint="http://x:1", path="/ignored", host="ignored")
    assert isinstance(ex, OtlpExporter)
    assert ex.name == "otlp"


def test_otlp_in_active_names():
    assert "otlp" in active_exporter_names()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
