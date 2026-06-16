r"""transcript_view — a dedicated *browser* for the agent OUTPUT STREAM (docs/137 sibling).

> **DOS could read an agent's verdicts but not its narration.** `dos trace`
> walks what the KERNEL adjudicated about a run (lineage, lanes, verified steps,
> commits — joined by `run_id`). `dos timeline` walks what a `/dispatch` HANDOFF
> did. But nothing let an operator walk the agent's own OUTPUT STREAM — the
> assistant text, the tool calls it made, the results it got back, the thinking,
> the harness-synthesized deaths — turn by turn, with the parts it doesn't care
> about hidden. So a headless `claude -p` run was *blind*: the same JSONL a live
> session renders to a terminal sat unread on disk. This module is that reader —
> the missing **browser** over the stream both run modes write identically.

The posture is `trace.py`'s, verbatim: a **read-only projection**, never a store.
It folds a transcript JSONL — bytes the agent and the harness authored, never the
kernel — into an ordered list of typed `StreamRecord`s, and renders them with
**modular show/hide** of each record kind. Delete this module and you lose the
reader, not any data. It:

  * stores nothing of its own, takes no lease, mints no belief, adjudicates
    *nothing new* (the `decisions.py` / `trace.py` contract);
  * does its file I/O at the boundary (`load_records` / the CLI), data to a pure
    core (`parse_records`) — the `liveness` / `result_state` rule. The read is
    STREAMING (a lazy line generator, never `readlines()`), so a multi-hundred-MB
    headless transcript parses without materializing the whole file; stdin is a
    first-class source (`dos transcript -`), for hook events + piped `claude -p
    --output-format stream-json`;
  * matches the kernel byte-mode (utf-8, errors=replace) every other transcript
    reader uses, and reuses `result_state`'s harness-death marker
    (`SYNTHETIC_MODEL`) so the readers **cannot drift** — one grammar, shared;
  * names no host or vendor in a branch — **not even in a path-literal**. The
    record SHAPE (a JSONL of `{type, message:{role, content:[blocks]}}`) is the
    generic agent-transcript shape, and the discovery helper takes a *directory
    to scan* (`discover_transcripts(projects_dir, …)`), never the host layout.
    The one place that knows `~/.claude/projects/<encoded>/` is Claude-Code's
    session store is a DRIVER (`drivers.witnessed_leak_test.projects_dir_for` —
    already documented "HOST KNOWLEDGE — exactly why this is a driver"); the CLI
    composition layer resolves it and hands this module the resulting path. So
    there is exactly ONE encoder of the host slug in the tree, reused.

Why a NEW reader, not a `claim_extract` / `result_state` extension
==================================================================

`claim_extract.assistant_text_from_transcript` reads the *tail* assistant text
for the Stop-hook claim; `result_state.verify_transcript` classifies the
*terminal* record for the fold-site death catch; `model_health` tallies per
model. Each reads the transcript for a single ADJUDICATION and discards the rest.
A *browser* is the inverse: it keeps the whole ordered stream and lets the
operator choose what to see. It is the read side an inspector wants, not a gate.

The honesty discipline (docs/103): every field shown is the byte the agent or the
harness wrote, surfaced as-is. The one DOS-authored judgement — labelling an
assistant record `synthetic_death` — is the *unforgeable* `model == "<synthetic>"`
harness-authorship fact (`result_state`'s rung), decided ONLY from `model` /
`isApiErrorMessage`, never from the record's CONTENT — so the projection stays a
re-surfacing of an authored fact, never a new adjudication.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterable, Iterator

from dos import result_state as _rs

# ---------------------------------------------------------------------------
# The record kinds — the modular show/hide knobs. Each StreamRecord is exactly
# one kind; --show/--hide select over this closed set (a closed vocabulary, the
# refusal-reasons discipline applied to a projection).
# ---------------------------------------------------------------------------
KIND_TEXT = "text"                  # an assistant text block (the narration)
KIND_TOOL_CALL = "tool_call"        # a tool_use block (name + input)
KIND_TOOL_RESULT = "tool_result"    # a tool_result block (output, maybe error)
KIND_THINKING = "thinking"          # an assistant thinking/reasoning block
KIND_USER = "user"                  # a user-role text turn (the human/prompt side)
KIND_SYNTHETIC = "synthetic_death"  # a harness-synthesized abnormal-termination record
KIND_META = "meta"                  # a non-message record / unmodelled block (bookkeeping)

ALL_KINDS = (
    KIND_TEXT,
    KIND_THINKING,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    KIND_USER,
    KIND_SYNTHETIC,
    KIND_META,
)

# What a bare `dos transcript` shows by default: the agent's narration + what it
# did, minus the noise. `meta` (harness bookkeeping) and `thinking` (often large)
# are OFF by default — `--show meta,thinking` opts them in. `user` is ON so the
# stream reads as a conversation; `--hide user` collapses to the agent side only.
DEFAULT_SHOWN = (
    KIND_TEXT,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    KIND_USER,
    KIND_SYNTHETIC,
)


@dataclass(frozen=True)
class StreamRecord:
    """One item in the agent's output stream — the unit the browser shows/hides.

    Every field is a byte the agent or the harness authored, surfaced as-is. The
    sole kernel-authored field is `kind == synthetic_death`, and only when the
    unforgeable `model == "<synthetic>"` marker is present.
    """

    index: int                       # 0-based position in the stream (stable handle)
    kind: str                        # one of ALL_KINDS
    role: str = ""                   # message.role for message records ("" otherwise)
    text: str = ""                   # the human-readable body (narration / result text)
    tool_name: str = ""              # tool_call: the tool invoked
    tool_input: dict = field(default_factory=dict)   # tool_call: the (possibly nested) input
    tool_use_id: str = ""            # tool_call id / tool_result's tool_use_id (the join key)
    caller: str = ""                 # tool_call: who invoked it (main agent vs a spawned child)
    is_error: bool = False           # tool_result: did the tool error? / synthetic: always True
    model: str = ""                  # assistant message.model (e.g. a model id, or "<synthetic>")
    timestamp: str = ""              # top-level timestamp, if present
    line_no: int = 0                 # 1-based source line (for "go look at the file")

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "kind": self.kind,
            "role": self.role,
            "text": self.text,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_use_id": self.tool_use_id,
            "caller": self.caller,
            "is_error": self.is_error,
            "model": self.model,
            "timestamp": self.timestamp,
            "line_no": self.line_no,
        }


# ---------------------------------------------------------------------------
# Tool-name humanization — a pure, deterministic render projection. An MCP tool
# id `mcp__<server>__<tool>` is noise in a scan; render it `<server> · <tool>`.
# The RAW name is always preserved on the record (and in --json); this only
# touches the human view. No host/vendor name is branched on — it is a pure
# string transform over the `mcp__a__b` SHAPE.
# ---------------------------------------------------------------------------
def humanize_tool(name: str) -> str:
    """`mcp__server__tool` → `server · tool`; any other name returned unchanged."""
    if not name.startswith("mcp__"):
        return name
    parts = name.split("__")
    # mcp__<server>__<tool>  (server may itself contain a single segment; tool is last)
    if len(parts) >= 3:
        server = parts[1]
        tool = "__".join(parts[2:])
        return f"{server} · {tool}"
    return name


def _caller_label(caller: object) -> str:
    """A short label for a tool_use `caller` block, if present. Pure, defensive.

    The schema carries `tool_use.caller` as a dict; its useful field varies by
    host, so we surface a compact best-effort (a `type`/`name`/`id`-ish value),
    or "" when there's nothing legible. Never raises.
    """
    if isinstance(caller, str):
        return caller
    if not isinstance(caller, dict) or not caller:
        return ""
    for k in ("type", "name", "agent", "id"):
        v = caller.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


# ---------------------------------------------------------------------------
# The pure core — transcript bytes (lines) -> ordered StreamRecords. No I/O.
# ---------------------------------------------------------------------------
def _tool_result_text(content: object, *, error_hint: bool = False) -> tuple[str, bool]:
    """Flatten a tool_result `content` into (text, is_error).

    A tool_result's content is, in real records, EITHER a bare string OR a list of
    blocks (each typically `{type:"text", text:…}`, sometimes carrying an
    `is_error` flag, occasionally an image/other block we summarise). `error_hint`
    seeds is_error from the block-level flag so the BARE-STRING case (which carries
    no inner flag) still reports the error the block declared.
    """
    is_error = bool(error_hint)
    if isinstance(content, str):
        return content, is_error
    if isinstance(content, dict):
        # A single block handed un-wrapped (defensive — some emitters do this).
        content = [content]
    if not isinstance(content, list):
        return "", is_error
    parts: list[str] = []
    for b in content:
        if isinstance(b, str):
            parts.append(b)
            continue
        if not isinstance(b, dict):
            continue
        if b.get("is_error"):
            is_error = True
        t = b.get("type")
        if t == "text":
            txt = b.get("text", "")
            if isinstance(txt, str) and txt:
                parts.append(txt)
        elif t in ("image", "image_url"):
            parts.append("[image]")
        elif "text" in b and isinstance(b["text"], str):
            parts.append(b["text"])
    return "\n".join(parts), is_error


def _is_synthetic(obj: dict, msg: dict) -> bool:
    """A harness-synthesized abnormal-termination record? The unforgeable rung.

    The PRIMARY signal is `message.model == SYNTHETIC_MODEL` (harness-authored —
    `result_state`'s law). `isApiErrorMessage` (a top-level sibling of `message`,
    per result_state's corrected placement) corroborates it. Decided ONLY from
    these authored markers — never from the record's content.
    """
    if msg.get("model") == _rs.SYNTHETIC_MODEL:
        return True
    return bool(obj.get("isApiErrorMessage"))


def parse_records(lines: Iterable[str]) -> list[StreamRecord]:
    """Fold transcript JSONL lines into an ordered list of StreamRecords. PURE.

    No-crash floor (the `claim_extract` rule): a blank/garbled/non-dict line is
    skipped, never raised on — a torn last line or a non-message harness record
    costs that one record, not the whole browse. Nothing is silently dropped: an
    unrecognised top-level `type` OR an unmodelled content block becomes a `meta`
    record (hidden by default, surfaced with `--show meta`), so the counts footer
    never understates what the file held.
    """
    out: list[StreamRecord] = []
    idx = 0

    def emit(**kw) -> None:
        nonlocal idx
        out.append(StreamRecord(index=idx, **kw))
        idx += 1

    for line_no, raw in enumerate(lines, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue

        ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else ""
        msg = obj.get("message")

        # Non-message harness records (attachment, ai-title, queue-operation, …):
        # one meta record carrying the top-level type, never dropped.
        if not isinstance(msg, dict):
            top = obj.get("type")
            emit(kind=KIND_META,
                 text=str(top) if top is not None else "(record)",
                 timestamp=ts, line_no=line_no)
            continue

        role = msg.get("role") or ""
        model = msg.get("model") or ""

        # A model=="<synthetic>" record is wholly harness-authored — collapse it
        # to one synthetic record (the death stands in for the turn). An
        # isApiErrorMessage record whose content still carries real blocks
        # (tool_use etc.) is NOT collapsed wholesale; it flows through the
        # per-block path below and the synthetic marking rides on the model field.
        if role == "assistant" and msg.get("model") == _rs.SYNTHETIC_MODEL:
            txt, _ = _tool_result_text(msg.get("content"))
            if not txt:
                txt = "(synthetic termination — harness-authored)"
            emit(kind=KIND_SYNTHETIC, role=role, text=txt,
                 is_error=True, model=model, timestamp=ts, line_no=line_no)
            continue

        synthetic_turn = role == "assistant" and _is_synthetic(obj, msg)

        content = msg.get("content")
        # Content may be a bare string (a plain user/assistant turn) or a list of
        # typed blocks (the rich case). Normalise the bare-string case.
        if isinstance(content, str):
            kind = KIND_SYNTHETIC if synthetic_turn else (
                KIND_USER if role == "user" else KIND_TEXT)
            emit(kind=kind, role=role, text=content,
                 is_error=synthetic_turn, model=model, timestamp=ts, line_no=line_no)
            continue
        if not isinstance(content, list):
            continue

        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = b.get("text", "")
                if not (isinstance(txt, str) and txt):
                    continue
                kind = KIND_SYNTHETIC if synthetic_turn else (
                    KIND_USER if role == "user" else KIND_TEXT)
                emit(kind=kind, role=role, text=txt, is_error=synthetic_turn,
                     model=model, timestamp=ts, line_no=line_no)
            elif bt == "thinking":
                txt = b.get("thinking") or b.get("text") or ""
                emit(kind=KIND_THINKING, role=role,
                     text=txt if isinstance(txt, str) else "",
                     model=model, timestamp=ts, line_no=line_no)
            elif bt == "redacted_thinking":
                emit(kind=KIND_THINKING, role=role, text="[redacted thinking]",
                     model=model, timestamp=ts, line_no=line_no)
            elif bt == "tool_use":
                inp = b.get("input")
                emit(kind=KIND_TOOL_CALL, role=role,
                     tool_name=str(b.get("name") or ""),
                     tool_input=inp if isinstance(inp, dict) else {},
                     tool_use_id=str(b.get("id") or ""),
                     caller=_caller_label(b.get("caller")),
                     model=model, timestamp=ts, line_no=line_no)
            elif bt == "tool_result":
                txt, is_err = _tool_result_text(
                    b.get("content"), error_hint=bool(b.get("is_error")))
                emit(kind=KIND_TOOL_RESULT, role=role, text=txt,
                     tool_use_id=str(b.get("tool_use_id") or ""),
                     is_error=is_err, model=model, timestamp=ts, line_no=line_no)
            else:
                # An unmodelled block type (server_tool_use, …): a countable meta
                # record carrying its type, so the footer never understates.
                emit(kind=KIND_META, role=role,
                     text=str(bt) if bt is not None else "(block)",
                     model=model, timestamp=ts, line_no=line_no)

    return out


# ---------------------------------------------------------------------------
# Filtering — the modular show/hide + the navigation knobs. PURE.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StreamFilter:
    """The browse knobs — all pure data, applied by `apply_filter`."""

    shown: tuple[str, ...] = DEFAULT_SHOWN  # which kinds survive
    last: int = 0                           # keep only the last N (0 = all)
    grep: str = ""                          # substring filter over text/tool_name (case-insens)
    tools: tuple[str, ...] = ()             # keep only tool_call/result for these tool names
    errors_only: bool = False               # keep only error tool_results + synthetic deaths


def resolve_shown(
    show: Iterable[str] | None,
    hide: Iterable[str] | None,
) -> tuple[str, ...]:
    """Turn --show / --hide into the concrete shown-kind set (over ALL_KINDS).

    --show with no --hide is ADDITIVE to the default (so `--show thinking` means
    "the usual view plus thinking"); a bare `--show all` shows everything; --hide
    subtracts from whatever --show produced. Unknown kind tokens are ignored (the
    no-crash floor) — a typo narrows nothing rather than crashing.
    """
    show = [s.strip() for s in (show or []) if s.strip()]
    hide = [h.strip() for h in (hide or []) if h.strip()]
    if "all" in show:
        shown = set(ALL_KINDS)
    elif show:
        shown = set(DEFAULT_SHOWN) | {s for s in show if s in ALL_KINDS}
    else:
        shown = set(DEFAULT_SHOWN)
    if "all" in hide:
        shown = set()
    else:
        shown -= {h for h in hide if h in ALL_KINDS}
    # Preserve the canonical ALL_KINDS ordering for stable output.
    return tuple(k for k in ALL_KINDS if k in shown)


def apply_filter(records: list[StreamRecord], flt: StreamFilter) -> list[StreamRecord]:
    """Apply the browse knobs to the parsed stream. PURE; order preserved.

    Order of operations: kind/grep/tools/errors narrow first (content filters),
    THEN `last` tails — so `--last 5 --tools Bash` means "the last 5 Bash calls,"
    the intuitive reading, not "Bash calls among the last 5 records."
    """
    shown = set(flt.shown)
    tools = {t.lower() for t in flt.tools}
    needle = flt.grep.lower()
    kept: list[StreamRecord] = []
    for r in records:
        if r.kind not in shown:
            continue
        if flt.errors_only and not (r.is_error or r.kind == KIND_SYNTHETIC):
            continue
        if tools:
            # a --tools filter is about tool records; drop everything else
            if r.kind not in (KIND_TOOL_CALL, KIND_TOOL_RESULT):
                continue
            if r.tool_name.lower() not in tools:
                continue
        if needle:
            hay = (r.text + " " + r.tool_name + " "
                   + json.dumps(r.tool_input, default=str)).lower()
            if needle not in hay:
                continue
        kept.append(r)
    if flt.last and flt.last > 0:
        kept = kept[-flt.last:]
    return kept


# ---------------------------------------------------------------------------
# The folded view — records + a counts footer. The to_dict()/render_text() pair.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TranscriptView:
    """A browsed transcript: the filtered records + a counts summary + provenance."""

    source: str                       # the transcript path browsed (provenance)
    records: list[StreamRecord]       # the filtered, ordered stream
    total: int                        # total records parsed (pre-filter)
    counts: dict                      # per-kind counts (pre-filter) + errors + models
    note: str = ""                    # a best-effort/discovery note, if any

    def to_dict(self) -> dict:
        # `schema` marks this an UNSTABLE derived projection, not an ABI — the raw
        # transcript JSONL remains the stable source; consumers pin this at risk.
        return {
            "schema": "dos.transcript_view/v1 (unstable projection)",
            "source": self.source,
            "total": self.total,
            "shown": len(self.records),
            "counts": self.counts,
            "note": self.note,
            "records": [r.to_dict() for r in self.records],
        }


def _counts(records: list[StreamRecord]) -> dict:
    per: dict = {}
    errors = 0
    models: set[str] = set()
    for r in records:
        per[r.kind] = per.get(r.kind, 0) + 1
        if r.is_error or r.kind == KIND_SYNTHETIC:
            errors += 1
        if r.model and r.model != _rs.SYNTHETIC_MODEL:
            models.add(r.model)
    per["errors"] = errors
    per["models"] = sorted(models)
    return per


def build_view(
    records: list[StreamRecord],
    flt: StreamFilter,
    *,
    source: str = "",
    note: str = "",
) -> TranscriptView:
    """Fold parsed records + a filter into the renderable view. PURE."""
    return TranscriptView(
        source=source,
        records=apply_filter(records, flt),
        total=len(records),
        counts=_counts(records),
        note=note,
    )


# ---------------------------------------------------------------------------
# Rendering — the human (column-aligned) view. PURE (string in/out).
# ---------------------------------------------------------------------------
_GLYPH = {
    KIND_TEXT: "  ",          # assistant narration
    KIND_USER: "> ",          # the human/prompt side
    KIND_THINKING: "..",      # thinking
    KIND_TOOL_CALL: "->",     # a tool call
    KIND_TOOL_RESULT: "<-",   # a tool result
    KIND_SYNTHETIC: "XX",     # a harness death
    KIND_META: "..",          # bookkeeping / unmodelled
}


def _one_line(s: str, width: int) -> str:
    """First line of `s`, truncated to `width`, control chars stripped."""
    s = (s or "").replace("\r", "").split("\n", 1)[0]
    s = "".join(ch if (ch == "\t" or ch.isprintable()) else " " for ch in s)
    s = s.replace("\t", " ").strip()
    if width and len(s) > width:
        return s[: max(0, width - 1)] + "…"
    return s


def render_record(r: StreamRecord, *, full: bool = False, width: int = 100) -> str:
    """One record as a left-glyph + body line (or expanded block when `full`)."""
    glyph = _GLYPH.get(r.kind, "  ")
    head = f"{r.index:>4} {glyph} "
    if r.kind == KIND_TOOL_CALL:
        body = humanize_tool(r.tool_name) or "(tool)"
        if r.caller:
            body = f"{body} [{r.caller}]"
        if r.tool_input:
            arg = _one_line(
                json.dumps(r.tool_input, default=str, ensure_ascii=False),
                0 if full else width)
            body = f"{body}  {arg}"
    elif r.kind == KIND_TOOL_RESULT:
        tag = "ERROR " if r.is_error else ""
        body = tag + (r.text or "(empty result)")
    elif r.kind == KIND_SYNTHETIC:
        body = "SYNTHETIC DEATH — " + (r.text or "")
    elif r.kind == KIND_META:
        body = f"[{r.text}]"
    else:
        body = r.text or ""

    if full:
        # Expanded: glyph header then the full body, indented.
        indent = " " * len(head)
        lines = (body or "").split("\n")
        first = head + (lines[0] if lines else "")
        rest = "\n".join(indent + ln for ln in lines[1:])
        return first + ("\n" + rest if rest else "")
    return head + _one_line(body, width)


def render_text(view: TranscriptView, *, full: bool = False, width: int = 100) -> str:
    """The human browser view: header + records + counts footer."""
    lines: list[str] = []
    src = view.source or "(stdin)"
    lines.append(f"transcript  {src}")
    if view.note:
        lines.append(f"  note: {view.note}")
    lines.append(f"  {view.total} records · showing {len(view.records)}")
    lines.append("")
    if not view.records:
        lines.append("  (nothing matched the current filter — try --show all, "
                     "widen --grep, or drop --errors)")
    for r in view.records:
        lines.append(render_record(r, full=full, width=width))
    # Counts footer.
    c = view.counts
    seg = []
    for k in ALL_KINDS:
        if c.get(k):
            seg.append(f"{c[k]} {k}")
    foot = "  ·  ".join(seg) if seg else "(empty)"
    models = ", ".join(c.get("models", []) or []) or "—"
    lines.append("")
    lines.append(f"  {foot}")
    lines.append(f"  errors: {c.get('errors', 0)}  ·  models: {models}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Boundary I/O — load a transcript, and a HOST-AGNOSTIC discovery helper.
# The helper takes a *directory to scan*; it knows nothing of the host layout.
# The CLI composition layer resolves the host projects dir (via the driver) and
# hands it here. There is NO host path-literal in this module.
#
# The read is STREAMING — we iterate the file line-by-line (a generator), never
# `readlines()` — so a multi-hundred-MB headless transcript does not materialize
# in memory before parsing. The byte mode (utf-8, errors=replace) matches every
# other transcript reader in the kernel (`claim_extract._read_lines`), so a
# garbled byte costs a replacement char, never a crash.
# ---------------------------------------------------------------------------
def _iter_file_lines(path: str) -> Iterator[str]:
    """Yield a file's lines lazily (utf-8, errors=replace). No full read."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        yield from fh


def load_records(path: str) -> list[StreamRecord]:
    """Read a transcript JSONL at `path` and parse it, STREAMING. NOT pure.

    Iterates the file lazily (no `readlines()` materialization), so a huge
    headless transcript is parsed without ~2× peak RSS over file size. Returns
    ``[]`` on a read error (the no-crash floor: a missing transcript browses to
    empty). The parsed `StreamRecord` list is the only thing held in memory.
    """
    try:
        return parse_records(_iter_file_lines(path))
    except OSError:
        return []


def load_records_from_stream(stream: IO[str]) -> list[StreamRecord]:
    """Parse a transcript JSONL from an open text stream (e.g. stdin). NOT pure.

    The headless-composability path: a hook event's `transcript_path` resolved by
    the caller, OR a live `claude -p … --output-format stream-json` piped straight
    in. Reads lazily; never raises on a torn/garbled line (the no-crash floor).
    """
    return parse_records(stream)


def load_records_from_stdin() -> list[StreamRecord]:
    """Parse a transcript JSONL from stdin (the `dos transcript -` path)."""
    return load_records_from_stream(sys.stdin)


def discover_transcripts(
    projects_dir: Path | str,
    *,
    session: str = "",
) -> list[Path]:
    """Best-effort: list transcript JSONLs under a project's session dir, newest first.

    HOST-AGNOSTIC: the caller passes the directory to scan (the CLI resolves the
    Claude-Code `~/.claude/projects/<encoded>/` location via the driver and hands
    it in) — this module never encodes a host layout. A miss returns ``[]`` (the
    caller falls back to asking for an explicit ``--transcript PATH``). It NEVER
    guesses a `run_id` onto a file: it returns candidate FILES, ordered by
    recency, for the operator to pick. With `session`, returns only the file whose
    stem matches that session id.
    """
    base = Path(projects_dir)
    if not base.is_dir():
        return []
    try:
        files = [p for p in base.iterdir() if p.suffix == ".jsonl" and p.is_file()]
    except OSError:
        return []
    if session:
        files = [p for p in files if p.stem == session]

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    files.sort(key=_mtime, reverse=True)
    return files
