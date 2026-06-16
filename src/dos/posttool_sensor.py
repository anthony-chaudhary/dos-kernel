"""posttool-sensor — the boundary I/O for the tool_stream axis (docs/173 §4, §5).

> **`tool_stream.classify_stream` is a PURE verdict over a frozen `ToolStream`.
> SOMETHING has to turn a live PostToolUse hook event into a `StreamStep`, persist
> the accumulating stream across the many short-lived hook invocations of one
> session, and turn a REPEATING/STALLED verdict into the exact bytes real Claude
> Code honors. That is this module — the tool_stream axis's `resume_evidence`:
> boundary I/O (the hook event, the session-scoped `.dos/streams/<sid>.jsonl`)
> feeding the pure core, never inside the verdict.**

`liveness` reads git/journal at the CLI boundary and hands the already-gathered
delta to the pure `classify`. `resume_evidence` reads git ancestry at the boundary
and hands the frozen `AncestryFacts` to the pure `resume_plan`. This module is the
SAME shape one rung over, for `tool_stream`: the PostToolUse hook fires once per
tool call, so the "stream" exists only as an accumulating fossil — we APPEND one
`StreamStep` per fire (the impure part, the WAL idiom borrowed from `intent_ledger`)
and REPLAY the whole session's steps back into a `ToolStream` the pure
`classify_stream` folds. The kernel hashes nothing live inside the verdict; the
hashing of the event's args + result bytes happens HERE, at the boundary, exactly
as `tool_stream`'s docstring requires ("the CALLER computes the `args_digest` /
`result_digest` at the boundary").

Two pure halves + one impure (boundary) half:

  * **`step_from_event(event)`** — PURE. Turn one PostToolUse hook event into a
    `StreamStep`: the agent-authored `tool_name`/`tool_input` → `args_digest`, and
    the ENV-authored result bytes → `result_digest`. Hashes pre-supplied event
    fields (no disk, no clock, no network) — the same "I/O is the caller's, the
    transform is pure" line `tool_stream` draws around `classify_stream`.
  * **`warn_payload(verdict)`** — PURE. Turn a REPEATING/STALLED `StreamVerdict`
    into the exact Claude-Code PostToolUse `additionalContext` dialect (the
    `hookSpecificOutput` envelope), or None for ADVANCING (emit nothing).
  * **the accumulator** (`append_step`/`read_stream`, the "boundary I/O" section
    below) — the one impure part: an append-only, `fsync`'d, schema-tagged,
    torn-tail-tolerant session log, byte-mirroring `intent_ledger`'s ARIES
    discipline so the stream survives across the many separate hook processes of
    one session.

Why it is byte-clean (the docs/138 invariant, inherited from `tool_stream`)
===========================================================================

Walk the provenance of the `StreamStep` this adapter mints (the §5a argument
`tool_stream` makes, restated for the live hook):

  * `tool_name` / `args_digest` — the **agent** authored these (it chose the call
    and its arguments — `tool_input` is the agent-authored arg packet).
  * `result_digest`            — the **env** authored these. In a PostToolUse event
    the result key (`tool_response`, or `tool_output` on some CC builds) is the
    bytes the TOOL/MCP server returned, not bytes the agent wrote. The judged agent
    did **not** author the *identity* of its own repeated tool results.

So the only question `tool_stream` asks of this stream — "is this env-authored
`result_digest` byte-identical to one the env already returned, N times in a row?"
— is a pure byte question about **env-authored** bytes, unforgeable in the agent's
favor. This sensor never asks the dangerous satisfaction predicate ("is the agent
making progress / has it done the right thing?") that the §5a trap forbids.

Why it is ADVISORY, and the honest hole (named, not buried)
===========================================================

PostToolUse fires AFTER the tool already ran, so a PostToolUse hook **cannot
block** — it is structurally incapable of cutting the turn, which is exactly the
docs/99 advisory-only doctrine made unavoidable by the host contract. The only
lever it has is `additionalContext`: it can ADD a re-surfaced fact to the model's
next turn, never remove one. So this sensor RE-SURFACES the env-authored value the
agent already holds (and points at waiting for a completion signal), and it does so
even on STALLED — never a command to stop.

That is the right shape because of the honest hole `tool_stream` names and this
sensor inherits: **eventual-consistency polling is a legitimate reason to re-read
with the same result.** A task correctly waiting for an async write to land
produces identical reads until it lands — a true REPEATING that is *not* a stall.
Re-surfacing the unchanged value is harmless if the agent was right to wait (it
ignores a value it does not yet need) and helpful if it was stuck (it gets the
value it kept failing to use). Quoting `tool_stream`'s own reasoning: "the
intervention a consumer attaches to REPEATING must be a WARN that re-surfaces the
value, never a cut." This module is that consumer, on the live hook seam.

The catch-of-record (the in-flight twin of `dos_solves_output_poll.py`)
=======================================================================

dos session ``2cd77e93`` polled an unchanged background-task ``.output`` file 5×
(identical 126-byte result ``deedb29c`` each read), STALLED at read 5;
``benchmark/toolathlon/dos_solves_output_poll.py`` proves the OFFLINE replay fired.
This module is the IN-FLIGHT version of that proof: the same five identical-result
events, fed one at a time through this sensor's accumulator, fire REPEATING by the
3rd and STALLED by the 5th — re-surfacing "the .output is unchanged; wait for the
completion notification" on the SAME budget, the moment the loop is established.

⚓ Kernel discipline (the litmus): this is a PURE verdict-adapter — it imports only
sibling kernel modules (`tool_stream`, `config`, `durable_schema`), names no host /
driver, resolves every path via `SubstrateConfig.paths` (never `__file__`), and
carries no policy of its own (the thresholds live in `StreamPolicy`, the CLI hands
in `cfg.stream_policy`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from dos import config as _config
from dos import durable_schema as _schema
from dos.tool_stream import (
    StreamPolicy,
    StreamState,
    StreamStep,
    StreamVerdict,
    ToolStream,
)

# The durable-schema family + version every stream record carries (§6). Bumped
# ONLY on a NON-additive shape change (a new optional field is additive and does
# NOT bump it — the `durable_schema` contract). This kernel UNDERSTANDS up to
# `TOOL_STREAM_SCHEMA`; a record tagged higher is REFUSED at read (`read_stream`'s
# schema gate), never best-effort-parsed into a forged repeat.
SCHEMA_FAMILY = "tool-stream"
TOOL_STREAM_SCHEMA = 1

# The directory the per-session stream logs live under, beneath `.dos/`. A sibling
# of `intent_ledger`'s `.dos/runs/<run_id>/` — keyed by the host-authored
# `session_id` rather than a kernel run-id, because the PostToolUse event carries a
# `session_id`, not a DOS run-id (the join to a run-id is a later phase; the stream
# only needs a stable per-session key to accumulate under).
STREAMS_DIRNAME = "streams"

# The PostToolUse result key. Current Claude Code docs name it `tool_response`; some
# versions/builds emit `tool_output`. We READ BOTH defensively (the dual-read is
# mandatory robustness, not optional) — the same fail-safe direction as reading a
# missing key as "no result" rather than crashing. (docs/173 §4.)
_RESULT_KEYS = ("tool_response", "tool_output")


def _now_iso() -> str:
    """Second-resolution UTC stamp for a stream record (the `intent_ledger` idiom)."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# The PURE adapter — a hook event in, a StreamStep out (no I/O).
# ---------------------------------------------------------------------------
def _digest(b: bytes) -> str:
    """The truncated SHA every digest uses. PURE.

    Truncated to 16 hex chars to MATCH `dos_solves_output_poll.py`'s `_digest`
    (`hexdigest()[:16]`) so the live sensor and the offline proof artifact compute
    byte-identical digests over the same bytes — the in-flight twin really is the
    same signal, not a look-alike.
    """
    return hashlib.sha256(b).hexdigest()[:16]


def _canonical_bytes(value) -> bytes:
    """Canonical UTF-8 bytes of a JSON-able value (sorted keys). PURE.

    A string is hashed as its own bytes (the env returned text — hash the text, the
    `dos_solves_output_poll` posture for a `.output` result block). Any other JSON
    value (a dict/list/number a structured-result tool returned) is hashed as its
    canonical `json.dumps` (sorted keys, no incidental whitespace), so two
    byte-equal results digest equally regardless of key order. `default=str` keeps a
    non-JSON-able scalar (a stray datetime) from raising — the fail-safe break.
    """
    if isinstance(value, str):
        return value.encode("utf-8", "replace")
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode(
        "utf-8", "replace"
    )


def _result_from_event(event: dict):
    """The result object the env returned, read from BOTH candidate keys. PURE.

    Reads `tool_response` first (the current-docs key), falling back to `tool_output`
    (the older/alternate build key) — the mandatory dual-read (docs/173 §4). Returns
    a 2-tuple `(present, value)`: `present=False` when NEITHER key is in the event
    (a call that errored / returned nothing), which the caller maps to
    `result_digest=None` — the fail-safe break (no result is never 'the same
    result'). A key present with value `None` is treated as ABSENT too (an explicit
    null result is no result), the same safe direction.
    """
    for k in _RESULT_KEYS:
        if k in event:
            v = event.get(k)
            if v is not None:
                return True, v
    return False, None


def step_from_event(event: dict, policy: "StreamPolicy | None" = None) -> Optional[StreamStep]:
    """Turn one PostToolUse hook event into a `StreamStep`. PURE — hashes event fields only.

    The boundary-coordinate adapter: it reads the event's agent-authored
    `tool_name`/`tool_input` and the env-authored result (dual-key
    `tool_response`/`tool_output`), and computes the two digests `tool_stream` keys
    on. Returns None when there is no `tool_name` — nothing to record (the event was
    not a tool call, or was malformed); the caller emits nothing.

      * `args_digest`   — sha256 of the NORMALIZED `tool_input` (sorted keys,
                          canonical JSON — the `dos_solves_output_poll` normalization
                          and the `StreamStep` docstring's "sorted keys, canonical
                          scalar repr"). AGENT-authored: the agent chose the call's
                          arguments. Prefixed with the tool name so two different
                          tools with byte-equal args never collide on a digest.
      * `result_digest` — sha256 of the ENV-returned result bytes
                          (`_canonical_bytes` of the result object). ENV-authored —
                          the load-bearing field. None when the event carried no
                          result (a call that errored / returned nothing) — None
                          never matches another step, so it BREAKS a run rather than
                          extending it (the fail-safe; the `StreamStep` contract).

    `policy` is accepted for signature-symmetry with the rest of the axis (a future
    boundary normalization a policy might tune); the v1 adapter does not branch on
    it. PURE: no disk, no clock, no network — only `hashlib`/`json` over the
    already-supplied event, the `tool_stream` "the kernel hashes nothing live inside
    the verdict; the boundary computes the digests" line.
    """
    if not isinstance(event, dict):
        return None
    tool_name = event.get("tool_name")
    if not (isinstance(tool_name, str) and tool_name):
        return None  # not a tool call (or malformed) — nothing to record

    tool_input = event.get("tool_input")
    if tool_input is None:
        tool_input = {}
    # The args digest is over the tool name + the normalized input, so two
    # different tools that happen to share an arg packet are never one repeat run.
    args_blob = _canonical_bytes({"tool": str(tool_name), "input": tool_input})
    args_digest = _digest(args_blob)

    present, result = _result_from_event(event)
    result_digest = _digest(_canonical_bytes(result)) if present else None

    return StreamStep(
        tool_name=str(tool_name),
        args_digest=args_digest,
        result_digest=result_digest,
    )


# ---------------------------------------------------------------------------
# The PURE warn renderer — a StreamVerdict in, the exact CC dialect out (no I/O).
# ---------------------------------------------------------------------------
def warn_payload(verdict: StreamVerdict) -> Optional[dict]:
    """Render a REPEATING/STALLED `StreamVerdict` as the EXACT Claude-Code WARN dialect. PURE.

    Returns the non-blocking PostToolUse `additionalContext` envelope — and NOTHING
    ELSE — or None for ADVANCING (emit nothing). The shape is the ONE dialect real
    Claude Code honors (verified against code.claude.com/docs); the field names are
    case-sensitive and exact:

        {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                "additionalContext": "<text>"}}

    This is the load-bearing correctness fact: the sibling `dos hook stop` is a
    SILENT NO-OP against real CC because it emits `{"ok": false}`, a dialect CC
    ignores. This sensor MUST emit `hookSpecificOutput`/`PostToolUse`/
    `additionalContext` exactly, or it is invisible the same way.

    The `additionalContext` re-surfaces the ENV-AUTHORED fact (never an agent
    judgment): it names the repeated tool, the `repeat_run` count, and that the env
    returned identical bytes N times so no new information is entering the loop —
    advising the agent to WAIT for a completion signal / use the value it already
    holds. It NEVER tells the agent to stop (PostToolUse cannot block; and a
    legitimate poll must not be cut — the docs/99 advisory line, the honest
    eventual-consistency hole made structural).
    """
    if verdict.state not in (StreamState.REPEATING, StreamState.STALLED):
        return None  # ADVANCING (or anything else) → emit nothing
    rs = verdict.repeated_step
    tool = rs.tool_name if rs is not None else "the same tool"
    digest = rs.result_digest if rs is not None else "(unknown)"
    text = (
        f"DOS tool_stream {verdict.state.value}: `{tool}` returned BYTE-IDENTICAL "
        f"results {verdict.repeat_run} times in a row (env-authored digest "
        f"{digest}) — no new information is entering the loop. The value you already "
        f"received has not changed; do NOT re-issue the same call expecting a "
        f"different answer. If you are polling a background task / an async write, "
        f"WAIT for its completion signal instead of re-reading; otherwise USE the "
        f"value you already hold and move on. ({verdict.reason})"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": text,
        }
    }


# ===========================================================================
# Boundary I/O — the ONE impure part: the session-scoped accumulator.
# Byte-mirrors `intent_ledger`'s ARIES discipline (fsync, O_APPEND, torn-tail
# tolerance, the §6 schema gate). A stream record is one append-only line; the
# whole session's lines REPLAY into a ToolStream the pure verdict folds.
# ===========================================================================
def _safe_session_name(session_id: str) -> Optional[str]:
    """The sanitized filename stem for a host-authored `session_id`, or None to skip.

    `session_id` is an agent/host-authored token (the PostToolUse event's
    `session_id`) — distrusted as a filename. We strip any path separators and
    drive/`..` components so it can never escape the streams dir (a path-traversal
    surface, the `_resolve_driver_config` dotted-name reflex), keeping only the safe
    characters of a normal session uuid. An empty/whitespace token (or one that
    sanitizes to empty) returns None — no identity, no accumulator (the caller emits
    nothing rather than writing to a junk path).
    """
    if not isinstance(session_id, str):
        return None
    # Keep only characters safe in a filename across OSes; drop separators + dots
    # used for traversal. A session uuid is `[0-9a-f-]`, so this is loss-free for the
    # real key and defensive for a hostile one.
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return safe or None


def streams_dir_for(cfg: "_config.SubstrateConfig | None" = None) -> Path:
    """The `.dos/streams/` directory under the active workspace. PURE path arithmetic.

    Rides `cfg.paths.dot_dos` (the per-project `.dos/` home), the sibling of
    `intent_ledger`'s `.dos/runs/`. Never creates the dir — `append_step` is the
    only creator (the read-only-path discipline)."""
    cfg = _config.ensure(cfg)
    return cfg.paths.dot_dos / STREAMS_DIRNAME


def stream_path_for(
    session_id: str, cfg: "_config.SubstrateConfig | None" = None
) -> Optional[Path]:
    """The `.dos/streams/<session_id>.jsonl` path for a session, or None if unusable.

    Pure path arithmetic (the `intent_ledger.ledger_path_for` idiom): never creates
    anything. Returns None when `session_id` sanitizes to empty (no safe filename) —
    the caller treats that as "no accumulator," emitting nothing.
    """
    safe = _safe_session_name(session_id)
    if safe is None:
        return None
    return streams_dir_for(cfg) / f"{safe}.jsonl"


def _step_entry(
    step: StreamStep,
    *,
    run_id: str | None = None,
    step_index: int | None = None,
    verdict_state: str | None = None,
) -> dict:
    """The durable record for one `StreamStep` — schema-tagged, canonical. PURE.

    Carries the §6 schema tag so a record written directly (not via `append_step`)
    is self-declaring, the `intent_ledger.*_entry` posture. A `None` `result_digest`
    is written as JSON `null` and reads back as None (the fail-safe break survives
    the round-trip).

    The three join-fields below are the docs/179 Phase-0 additions that turn a step
    record into a labelable *firing*. They are ADDITIVE optional fields — present
    only when known — so a record without them reads back identically to a v1 record
    and the schema version does NOT bump (the `durable_schema` additive contract:
    a new optional field is forward/backward compatible). They are written ONLY when
    non-None, so the common (no-spine) record is byte-for-byte the old one and the
    whole shipped `tool_stream` suite stays green:

      * `run_id`        — the DOS correlation-spine id for this step's run, the join
                          key the firing-label fold (docs/179) needs to reach the
                          run's git-minted ground truth (`trace.build_trace`). The
                          PostToolUse event carries only a host `session_id`; the
                          caller resolves the run_id from the active spine (env / a
                          run-dir) when present, else leaves it absent — and an
                          absent run_id is an honest `BROKEN_LINK`, never a guess.
      * `step_index`    — this step's 0-based ordinal WITHIN the session stream (the
                          count of prior records). Makes "the detector fired at
                          step N" a durable fact joinable to the stream position,
                          rather than something re-derived on every replay.
      * `verdict_state` — the `StreamState` value (REPEATING/STALLED) the detector
                          emitted AT this step, stamped only on a record that
                          actually fired. This is what makes the record a *firing*:
                          without it the fold would have to re-run the verdict over a
                          replay and guess which step it fired on. ADVANCING is never
                          stamped (no firing → no field), so the presence of
                          `verdict_state` IS the firing.
    """
    e = {
        **_schema.tag(SCHEMA_FAMILY, TOOL_STREAM_SCHEMA),
        "op": "STEP",
        "tool_name": step.tool_name,
        "args_digest": step.args_digest,
        "result_digest": step.result_digest,
    }
    if run_id:
        e["run_id"] = run_id
    if step_index is not None:
        e["step_index"] = step_index
    if verdict_state:
        e["verdict_state"] = verdict_state
    return e


def append_step(
    session_id: str,
    step: StreamStep,
    cfg: "_config.SubstrateConfig | None" = None,
    *,
    path: Path | None = None,
    run_id: str | None = None,
    step_index: int | None = None,
    verdict_state: str | None = None,
) -> None:
    """Append ONE `StreamStep` to the session's stream log and `fsync` it.

    Copies `intent_ledger.append`'s durability idiom EXACTLY: stamp the record (the
    §6 schema tag + a `ts`), write one canonical-JSON line + newline through
    `os.open(O_WRONLY|O_APPEND|O_CREAT)` + `os.write` + `os.fsync` + `os.close`, so
    the record is durable before this returns and the append is atomic w.r.t. any
    other appender at the OS level. `mkdir(parents=True)` the streams dir lazily (the
    only creator). `path` overrides the resolved location (tests).

    `run_id`/`step_index`/`verdict_state` are the docs/179 additive firing-join
    fields (see `_step_entry`): pass them to make this step a labelable firing. All
    optional — omitting them writes the byte-identical v1 record.

    Raises on an unusable `session_id` (no `path` and the session sanitizes to
    empty) — the CLI wraps this whole call in a fail-safe try/except (advisory: never
    block a real workflow on the sensor's own write failure), so a raise here degrades
    to "emit nothing," never a crashed turn.
    """
    p = path or stream_path_for(session_id, cfg)
    if p is None:
        raise ValueError("append_step needs a usable session_id or an explicit path")
    e = _step_entry(
        step, run_id=run_id, step_index=step_index, verdict_state=verdict_state
    )
    e.setdefault("ts", _now_iso())
    line = json.dumps(e, sort_keys=True, default=str, ensure_ascii=False) + "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_stream(
    session_id: str,
    cfg: "_config.SubstrateConfig | None" = None,
    *,
    path: Path | None = None,
    understands: int = TOOL_STREAM_SCHEMA,
) -> ToolStream:
    """Replay the session's stream log into a `ToolStream`. The accumulator read-side.

    Two distrust postures layered, byte-mirroring `intent_ledger.read_all`:

      * **Torn-tail tolerance** — an unparseable TRAILING line (a crash mid-append)
        is skipped: a half-written record is "didn't happen." A non-trailing
        unparseable line is dropped too (a stream is a best-effort fossil, not a
        ledger whose every gap must be flagged) — the safe direction is to under-count
        a repeat, never to over-count one.
      * **Schema gate** (§6) — a record whose `schema` tag is a NON-additively-newer
        version than `understands` is NOT parsed into a `StreamStep`; it is SKIPPED
        (treated as un-foldable), so a record this kernel is too old to read can never
        fabricate a repeat. An UNTAGGED (legacy) record is read permissively as v1
        (the `durable_schema.UNTAGGED` tolerant side); a WRONG_FAMILY record (a
        foreign line) is skipped.

    A record missing/`null` `result_digest` reads back as `result_digest=None` (the
    fail-safe break survives). Returns an EMPTY `ToolStream` when the file is absent —
    the too-young-to-judge floor `tool_stream` reads as ADVANCING. `understands` is
    injectable so a test can simulate an OLD reader meeting a NEW record.
    """
    p = path or stream_path_for(session_id, cfg)
    if p is None or not p.exists():
        return ToolStream(())
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ToolStream(())
    lines = raw.splitlines()
    steps: list[StreamStep] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            # Torn final line → "didn't happen"; a mid-file corrupt line → skip (a
            # stream under-counts a repeat rather than fabricating one).
            continue
        if not isinstance(obj, dict):
            continue
        # The §6 schema gate. READABLE/UNTAGGED proceed; UNREADABLE_NEWER and
        # WRONG_FAMILY are skipped (a too-new/foreign record never forges a repeat).
        v = _schema.classify(obj, family=SCHEMA_FAMILY, understands=understands)
        if v.readability not in (_schema.Readability.READABLE, _schema.Readability.UNTAGGED):
            continue
        tool_name = obj.get("tool_name")
        args_digest = obj.get("args_digest")
        if not (isinstance(tool_name, str) and isinstance(args_digest, str)):
            continue  # a record with no identity is not a comparable step
        rd = obj.get("result_digest")
        result_digest = rd if isinstance(rd, str) else None
        steps.append(
            StreamStep(
                tool_name=tool_name,
                args_digest=args_digest,
                result_digest=result_digest,
            )
        )
    return ToolStream(tuple(steps))


def read_stream_records(
    session_id: str,
    cfg: "_config.SubstrateConfig | None" = None,
    *,
    path: Path | None = None,
    understands: int = TOOL_STREAM_SCHEMA,
) -> list[dict]:
    """The RAW stream records for a session — the join-field-preserving reader (docs/361).

    `read_stream` reconstructs bare `StreamStep`s that DROP the docs/179 join
    fields (`run_id`/`step_index`/`verdict_state`); the `tool_trace` spine needs
    those fields, so this reader returns the raw record dicts UNTOUCHED instead.
    Same tolerance + §6 schema gate as `read_stream` (a torn/foreign/too-new line
    is skipped), but it keeps every field the writer wrote — the fields are
    already on disk, so this is a read-side helper, not a schema change.

    Returns `[]` when the session file is absent/unreadable (the read-only-shows-
    what-it-has floor). A record with no `tool_name` identity is dropped (it is
    not a comparable step), mirroring `read_stream`.
    """
    p = path or stream_path_for(session_id, cfg)
    if p is None or not p.exists():
        return []
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        v = _schema.classify(obj, family=SCHEMA_FAMILY, understands=understands)
        if v.readability not in (_schema.Readability.READABLE, _schema.Readability.UNTAGGED):
            continue
        if not isinstance(obj.get("tool_name"), str):
            continue
        out.append(obj)
    return out


def read_all_stream_records(
    cfg: "_config.SubstrateConfig | None" = None,
    *,
    understands: int = TOOL_STREAM_SCHEMA,
) -> list[dict]:
    """Every raw stream record across ALL session files under `.dos/streams/`.

    The fleet-wide spine source for `dos tool-trace` (which filters by `run_id`
    in the pure fold, not by session file). Walks every `*.jsonl` in the streams
    dir, concatenating `read_stream_records` per file. A missing dir degrades to
    `[]`. Boundary I/O — read-only, no lease.
    """
    d = streams_dir_for(cfg)
    if not d.exists():
        return []
    out: list[dict] = []
    try:
        files = sorted(d.glob("*.jsonl"))
    except OSError:
        return []
    for f in files:
        out.extend(read_stream_records("", cfg, path=f, understands=understands))
    return out


# FOLLOW-UP (not v1): a keep-last-N / size guard on the per-session stream log. The
# `.dos` reaper family already bounds growth elsewhere; a long session's stream is a
# small append-only file, and the verdict only needs the TRAILING run, so an
# unbounded read is acceptable for v1. A future trim (keep the last `stall_n + k`
# records) belongs with the reaper, not here.
