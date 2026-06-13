"""dos.drivers.export_otlp — the OpenTelemetry occupant of `dos.exporter` (docs/266).

The third connector behind the verdict exporter, and the native TRACES/LOGS path. Where
`export_file` writes JSONL for a shipper to parse and `export_statsd` emits counters,
THIS driver maps each `VerdictEvent` to an OTLP **log record** — `run_id` as a correlated
attribute, the byte-clean `detail` counts as attributes — and ships it to an
OpenTelemetry collector over OTLP/HTTP. So a shop already running an OTel collector
(Honeycomb, Grafana Tempo/Loki, Datadog's OTLP intake, Jaeger) ingests the verdict
stream natively, correlated by `run_id`, without a log-tailing or StatsD hop. It
registers through the `dos.exporters` entry-point group, so `resolve_exporter("otlp")`
finds it by name and no kernel module imports it.

Why this one needs an extra (`[export-otlp]`)
=============================================

Unlike `file` + `statsd` (stdlib-only, in the core), OTLP needs a real SDK — the
`opentelemetry-sdk` + the OTLP/HTTP log exporter. Those live behind the `[export-otlp]`
extra (the `[mcp]` / `[notify-slack]` precedent), so `pip install dos-kernel` stays
near-stdlib. The SDK is imported **lazily, inside the transport's `emit`** — never at
module load — so:

  * entry-point discovery of this driver NEVER fails when the extra is absent (the
    `notify_slack` lazy-import discipline — importing the driver must be free);
  * an `export` with the extra absent returns a non-exported `ExportResult` carrying the
    install hint (`pip install dos-kernel[export-otlp]`), NEVER an ImportError.

The pure part is import-free
============================

`build_records(events)` turns the batch into a list of NEUTRAL record dicts (severity +
body + attributes) with NO OpenTelemetry import — so the mapping is golden-shape testable
without the SDK, and the OTLP-specific construction (LoggerProvider, LogRecord, the HTTP
exporter) is confined to `_OtlpTransport.emit`, which a test replaces with a fake.

Disciplines (inherited from the seam — the `export_file`/`export_statsd` posture)
=================================================================================

  * **Fail-soft.** `export` returns an `ExportResult`, never raises — an absent extra, a
    down collector, a bad endpoint, an SDK error all degrade to `exported=0` with a
    one-line reason. (The seam's `export_safely` is the outer net; this is the inner one.)
  * **Advisory only.** It reads a batch → emits records. It mutates no DOS state, takes no
    lease, stops no run, adjudicates nothing.

Routing
=======

  * **endpoint**: explicit arg › `$DOS_OTLP_ENDPOINT` › `$OTEL_EXPORTER_OTLP_ENDPOINT`
    (the OTel standard env) › `<root>/.env` › `http://localhost:4318` (the OTLP/HTTP
    default). The `/v1/logs` path is appended by the SDK exporter if not present.
"""

from __future__ import annotations

import os
from pathlib import Path

from dos.exporter import ExportResult, _max_seq_cursor

_DEFAULT_ENDPOINT = "http://localhost:4318"
_INSTRUMENTATION_SCOPE = "dos.verdict"

# Map the kernel's verdict severity onto the OTLP severity scale. Most verdict tokens are
# neutral status (ADVANCING/SHIPPED → INFO); the "something is wrong" tokens map to WARN,
# and the hardest (a run hung / poison) to ERROR. A token not listed is INFO — a verdict
# is a status record, not inherently an error. (Used by build_records as a NUMBER so the
# pure part needs no OTel SeverityNumber import; the transport maps it to the enum.)
_SEVERITY_WARN = {"SPINNING", "DIMINISHING", "COSTLY", "OPEN", "WARN", "DEFER",
                  "RECENTLY_ATTEMPTED", "QUIET_INCOMPLETE", "HELD", "DIVERGED"}
_SEVERITY_ERROR = {"STALLED", "WASTEFUL", "BLOCK", "REJECT_POISON", "UNRESUMABLE",
                   "NOT_SHIPPED"}
# OTLP SeverityNumber values (stable ints from the spec): INFO=9, WARN=13, ERROR=17.
_SEV_INFO, _SEV_WARN, _SEV_ERROR = 9, 13, 17

# #39 — map the journal's spend-breakdown counts onto OpenTelemetry's GenAI
# semantic-convention usage attributes (standardized Jan 2026), so a fleet's
# efficiency events land in any GenAI-aware dashboard WITHOUT a translation
# layer. The source keys are the two-level fossil the observe flatten now
# records (`evidence.breakdown.<field>`, docs/300 + #39 half 1); the targets are
# the OTel `gen_ai.usage.*` names. Emitted ALONGSIDE the `dos.detail.*` forms
# (additive — a DOS-native reader keeps its keys, a GenAI dashboard reads the
# standard ones). Only the canonical, OTel-standard counts are mapped; the
# DOS-derived diagnostics (total/prefill/cache_hit_ratio/…) keep `dos.detail.*`
# only, since they have no OTel-standard name. A target is emitted ONLY when its
# source key is present (no zero-stuffing — an event with no breakdown gets no
# gen_ai.* attribute). This dict is the single source of truth, asserted directly.
_GENAI_ATTR_MAP: dict[str, str] = {
    "evidence.breakdown.input": "gen_ai.usage.input_tokens",
    "evidence.breakdown.output": "gen_ai.usage.output_tokens",
    "evidence.breakdown.cache_read": "gen_ai.usage.cache_read.input_tokens",
    "evidence.breakdown.cache_creation": "gen_ai.usage.cache_creation.input_tokens",
    "evidence.breakdown.reasoning": "gen_ai.usage.reasoning.output_tokens",
}


def _read_env_file(root: Path) -> dict[str, str]:
    """Best-effort parse of `<root>/.env` → {KEY: value}. Never raises."""
    out: dict[str, str] = {}
    try:
        text = (root / ".env").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def resolve_endpoint(explicit: str | None, *, root: Path | None) -> str:
    """OTLP endpoint: explicit › `$DOS_OTLP_ENDPOINT` › `$OTEL_EXPORTER_OTLP_ENDPOINT`
    › `<root>/.env` › http://localhost:4318."""
    if explicit:
        return explicit
    for env_key in ("DOS_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        v = os.environ.get(env_key)
        if v:
            return v
    if root is not None:
        v = _read_env_file(root).get("DOS_OTLP_ENDPOINT")
        if v:
            return v
    return _DEFAULT_ENDPOINT


def _severity_number(verdict: str) -> int:
    """The OTLP severity NUMBER for a verdict token (INFO/WARN/ERROR). Import-free."""
    v = (verdict or "").upper()
    if v in _SEVERITY_ERROR:
        return _SEV_ERROR
    if v in _SEVERITY_WARN:
        return _SEV_WARN
    return _SEV_INFO


def build_records(events) -> list[dict]:
    """A batch of `VerdictEvent`s → neutral OTLP-shaped record dicts (pure; no OTel import).

    Each record carries `body` (a human line `"<syscall> <verdict>"`), `severity_number`
    (mapped from the verdict token), and `attributes` — the structured, queryable fields a
    trace/log backend filters on: `dos.syscall`, `dos.verdict`, `dos.run_id` (the
    correlation key), `dos.lane`/`dos.subject` when present, and each byte-clean
    `detail.<k>` count flattened with a `dos.detail.` prefix (never the agent's narration —
    the docs/138 invariant the journal already enforces, carried onto the wire). When the
    detail carries a spend breakdown (`evidence.breakdown.*`, #39), the canonical token
    counts are ALSO emitted under their OpenTelemetry GenAI names (`gen_ai.usage.*`,
    `_GENAI_ATTR_MAP`) so the event lands in a GenAI-aware dashboard with no translation
    layer — alongside the `dos.detail.*` forms, never replacing them. The transport turns
    each dict into an OTLP LogRecord; this stays import-free + golden testable, the spine's
    analogue of `export_statsd.build_lines`.
    """
    out: list[dict] = []
    for e in events:
        syscall = getattr(e, "syscall", "") or ""
        verdict = getattr(e, "verdict", "") or ""
        attrs: dict[str, object] = {
            "dos.syscall": syscall,
            "dos.verdict": verdict,
        }
        run_id = getattr(e, "run_id", "") or ""
        if run_id:
            attrs["dos.run_id"] = run_id
        lane = getattr(e, "lane", "") or ""
        if lane:
            attrs["dos.lane"] = lane
        subject = getattr(e, "subject", "") or ""
        if subject:
            attrs["dos.subject"] = subject
        source = getattr(e, "source", "") or ""
        if source:
            attrs["dos.source"] = source
        detail = getattr(e, "detail", None) or {}
        if isinstance(detail, dict):
            for k, v in detail.items():
                # OTLP attribute values must be a scalar (or a homogeneous list); coerce
                # anything else to its string form so a nested/odd value still rides along.
                attrs[f"dos.detail.{k}"] = v if isinstance(v, (str, int, float, bool)) else str(v)
            # #39: also emit the OTel GenAI usage names for the canonical spend
            # counts present, ALONGSIDE the dos.detail.* forms above. Skip a
            # mapped key that is absent (no zero-stuffing) or non-numeric.
            for src_key, genai_attr in _GENAI_ATTR_MAP.items():
                val = detail.get(src_key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    attrs[genai_attr] = val
        out.append({
            "body": f"{syscall} {verdict}".strip(),
            "severity_number": _severity_number(verdict),
            "attributes": attrs,
            "ts": getattr(e, "ts", "") or "",
        })
    return out


class _OtlpTransport:
    """The lazy OTLP/HTTP log emitter. The ONLY place the OpenTelemetry SDK is imported.

    `emit(records, endpoint)` returns the count emitted; raises `ImportError` when the
    `[export-otlp]` extra is absent (the driver converts that to an install hint) and any
    other exception on a transport failure (the driver converts that to a soft error).
    Kept behind a method so tests inject a fake with the same `emit(records, endpoint)`
    shape instead of standing up a real collector (the `export_statsd._UdpTransport` /
    `notify_webhook._UrllibTransport` posture).
    """

    def emit(self, records: list[dict], endpoint: str) -> int:
        # Lazy import — absent extra raises ImportError HERE (caught by the driver), never
        # at module load, so discovering this driver is always free.
        from opentelemetry._logs import SeverityNumber
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LogRecord
        from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": _INSTRUMENTATION_SCOPE})
        provider = LoggerProvider(resource=resource)
        exporter = OTLPLogExporter(endpoint=endpoint)
        processor = SimpleLogRecordProcessor(exporter)
        provider.add_log_record_processor(processor)
        logger = provider.get_logger(_INSTRUMENTATION_SCOPE)

        sent = 0
        for rec in records:
            logger.emit(LogRecord(
                body=rec.get("body", ""),
                severity_number=SeverityNumber(int(rec.get("severity_number", _SEV_INFO))),
                attributes=dict(rec.get("attributes", {})),
            ))
            sent += 1
        # Flush so the records leave before the provider is torn down (a short-lived drain,
        # unlike a long-running app's batching processor).
        try:
            provider.force_flush()
            provider.shutdown()
        except Exception:  # pragma: no cover - shutdown is best-effort
            pass
        return sent


class OtlpExporter:
    """Drain a batch of `VerdictEvent`s as OTLP log records to an OpenTelemetry collector.

    Parameters
    ----------
    endpoint:
        OTLP/HTTP endpoint; defaults to `$DOS_OTLP_ENDPOINT` /
        `$OTEL_EXPORTER_OTLP_ENDPOINT` / `.env` / http://localhost:4318.
    root:
        Workspace root for `.env` resolution (the `SubstrateConfig.root`).
    dry_run:
        Build the records + report, emit NOTHING (and never imports the SDK).
    transport:
        Inject a fake with an `emit(records, endpoint) -> int` method in tests; None uses
        the lazy OTLP transport (which needs the `[export-otlp]` extra).

    The constructor accepts-and-ignores the export CLI's `path`/`host`/`port` superset
    kwargs by NOT declaring them — `exporter._accepted_kwargs` filters the bag to the
    params below, so a caller hands the same kwargs to any transport without branching.
    """

    name = "otlp"

    _INSTALL_HINT = ("OTLP exporter needs the [export-otlp] extra — "
                     "`pip install dos-kernel[export-otlp]`")

    def __init__(self, *, endpoint: str = "",
                 root: "os.PathLike[str] | str | None" = None,
                 dry_run: bool = False, transport=None):
        self._endpoint_arg = endpoint
        self._root = Path(root) if root is not None else None
        self._dry_run = bool(dry_run)
        self._transport = transport

    def export(self, events) -> ExportResult:
        """Emit one OTLP log record per event. Returns an `ExportResult`; NEVER raises."""
        cursor = _max_seq_cursor(events)
        n = len(events)
        endpoint = resolve_endpoint(self._endpoint_arg, root=self._root)

        if n == 0:
            return ExportResult(
                exported=0, detail=f"no new events for {endpoint}", cursor=cursor)

        records = build_records(events)

        if self._dry_run:
            return ExportResult(
                exported=0,
                detail=f"[dry-run] would emit {n} OTLP log record(s) to {endpoint}",
                cursor=cursor,
            )

        transport = self._transport if self._transport is not None else _OtlpTransport()
        try:
            sent = transport.emit(records, endpoint)
        except ImportError:
            # The extra is absent — degrade to the install hint, never an ImportError.
            return ExportResult(exported=0, detail=self._INSTALL_HINT, cursor=cursor)
        except Exception as e:  # noqa: BLE001 - advisory; report, don't crash the producer
            return ExportResult(exported=0, detail=f"error: {e}", cursor=cursor)

        return ExportResult(
            exported=int(sent),
            detail=f"emitted {sent} OTLP log record(s) to {endpoint}",
            cursor=cursor,
        )
