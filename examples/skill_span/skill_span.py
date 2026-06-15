#!/usr/bin/env python3
"""skill_span — fence a skill's span the harness won't fence for us.

The wall the host won't build (docs/352). A real Claude-Code user asked for the
exact start/end time and token usage of every individual skill run, and hit two
host facts that make the obvious approach lie:

  1. **PostToolUse fires on the DECISION to load a skill (~30-40ms), not on its
     execution.** A skill's real work happens across the *subsequent* tool calls
     and model turns of the same session, each firing its OWN PostToolUse. So the
     PostToolUse duration measures the wrong event — the load, not the work.
  2. **Token usage is only emitted at SessionEnd, aggregated** over the whole
     session — so it cannot be attributed to one skill.

The DOS reflex is to NOT trust the convenient point number and instead stand on
what the *environment* authored. The kernel is the part that doesn't believe the
agents; here it is the part that doesn't believe the *harness* either. We have two
env-authored fossils, and they are enough to build the wall the host won't:

  * the per-session stream log `.dos/streams/<session_id>.jsonl`
    (`dos.posttool_sensor`) — one fsync'd, **timestamped** record per tool fire,
    in order. A skill run is the SPAN between two of these records, not a point.
  * the provider usage record (`dos.spend.SpendBreakdown` / `parse_usage`) — the
    BILLED token aggregate, the one number an agent can no more shrink than inflate.

The wall, precisely
===================

A skill run is a SPAN, not a tool event::

    start fence  = the stream record where tool_name == "Skill"   (env-authored ts)
    end fence    = the NEXT "Skill" record in the session, OR the final record
    duration_ms  = end.ts - start.ts                  # from timestamps we HAVE
    tokens       = usage_at(end) - usage_at(start)    # delta of the BILLED total
                   ^ only when a usage snapshot exists at BOTH fences; else refused

The load-bearing design choice — **we refuse a token number we cannot ground**.
A span with no usage snapshot at a fence does not get a guessed token count
(a proportional split of the session total would be a number no environment
authored — exactly the self-report DOS exists to reject). It gets DURATION_ONLY:
the duration is real, the tokens are honestly absent. The refusal IS the wall.

This mirrors `dos.efficiency`'s "withhold the accusation until there is enough
spend to judge" and `dos.posttool_sensor`'s "no result is never the same result"
— the distrust ladder, applied to attribution instead of repeat-detection.

The three rungs (`SpanVerdict`)
===============================

    ATTRIBUTED     both fences present AND a usage snapshot at each — duration +
                   a grounded token delta (a real SpendBreakdown diff, so it also
                   says input/output/cache/reasoning share).
    DURATION_ONLY  both fences present, but a usage snapshot is missing at one or
                   both — duration reported, tokens REFUSED.
    UNATTRIBUTED   the span cannot even be fenced (an open final span with no end,
                   a single dangling record) — neither number asserted. The
                   honest nothing.

A backwards / non-monotonic usage snapshot (the end total below the start total)
is a contract error — refused LOUDLY by `SpendBreakdown.of`'s own non-negativity
validation, never clamped to zero (the `dos.spend` double-count discipline).

The one honest seam (named, not hidden)
=======================================

`dos.posttool_sensor.read_stream` replays the same fossil, but it projects each
record to a `StreamStep`, which carries NO timestamp (it only needs the digests
for repeat-detection). The span wall needs `ts`, so `load_stream` here reads the
raw `.jsonl` for `ts` + `tool_name` directly. Same fossil, a wider projection —
called out so the reuse boundary is legible, not papered over.

And the deeper blind spot the host has not yet closed: the PostToolUse event does
not today carry a per-call usage snapshot, so in a live session DURATION_ONLY is
the realistic default until a usage-bearing event (or a SessionEnd→fence join)
lands. This script proves the attribution MATH and the REFUSAL given snapshots;
capturing the snapshots live is the host-integration follow-up docs/352 names.

Run:  python examples/skill_span/skill_span.py
      python examples/skill_span/skill_span.py --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import enum
import json
from dataclasses import dataclass
from typing import Optional

# Stand on the REAL kernel spend primitive — do not reimplement token math. The
# proof is only worth anything if the token delta is a genuine SpendBreakdown.
from dos.spend import SpendBreakdown, parse_usage


class SpanVerdict(str, enum.Enum):
    """The typed attribution rung — three states, mutually exclusive.

    `str`-valued so it round-trips through a CLI token / JSON without a lookup
    table (the `dos.efficiency.Efficiency` / `dos.liveness.Liveness` idiom).
    Ordered most-grounded first.
    """

    ATTRIBUTED = "ATTRIBUTED"        # duration + a grounded token delta
    DURATION_ONLY = "DURATION_ONLY"  # duration real, tokens honestly refused
    UNATTRIBUTED = "UNATTRIBUTED"    # cannot even be fenced — the honest nothing

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class StreamRecord:
    """One env-authored stream-fossil record — the fence material.

    `ts` is the ISO-8601 stamp `dos.posttool_sensor` wrote (`_now_iso`).
    `tool_name` is the agent-authored call name (we fence on `"Skill"`).
    `skill` is the skill's name when the record carried it (`tool_input.skill`),
    else None — a bare Skill fire still fences a span, just unnamed.
    `usage` is the OPTIONAL provider usage record captured at this fire (a
    cumulative-since-session-start mapping) — present only when the host snapshotted
    it. Its ABSENCE is what forces DURATION_ONLY; we never fabricate one.
    """

    ts: str
    tool_name: str
    skill: Optional[str] = None
    usage: Optional[dict] = None


def _parse_ts(ts: str) -> Optional[dt.datetime]:
    """Parse an ISO-8601 stream stamp to an aware datetime, or None if unparseable.

    `posttool_sensor._now_iso` writes `...Z`; `fromisoformat` wants `+00:00`, so we
    swap the trailing Z. An unparseable stamp returns None — the caller treats a
    span with an unreadable fence as un-measurable (UNATTRIBUTED), never guesses 0.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(start_ts: str, end_ts: str) -> Optional[int]:
    """Wall-clock ms between two fence stamps, or None if either is unparseable.

    None (not 0) on an unreadable stamp — a missing duration is honestly absent,
    not a zero-length span. A negative delta (clock skew / out-of-order fossil) is
    also rejected to None: time did not run backwards, so we decline to assert it.
    """
    a, b = _parse_ts(start_ts), _parse_ts(end_ts)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds() * 1000.0
    if delta < 0:
        return None
    return int(round(delta))


@dataclass(frozen=True)
class SkillSpan:
    """One skill run, attributed by the wall — the result of fencing + diffing.

    `tokens` is a `SpendBreakdown` ONLY on ATTRIBUTED; None otherwise (the refusal
    made structural — a consumer that reads `.tokens` gets None, never a guess).
    `reason` is the one-line operator-facing why (legible distrust: the operator
    sees not just DURATION_ONLY but *which fence had no usage snapshot*).
    """

    name: str
    start_ts: str
    end_ts: Optional[str]
    duration_ms: Optional[int]
    verdict: SpanVerdict
    tokens: Optional[SpendBreakdown]
    reason: str

    def to_dict(self) -> dict:
        out: dict = {
            "name": self.name,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "duration_ms": self.duration_ms,
            "verdict": self.verdict.value,
            "reason": self.reason,
        }
        out["tokens"] = self.tokens.to_dict() if self.tokens is not None else None
        return out


def _usage_breakdown(usage: Optional[dict]) -> Optional[SpendBreakdown]:
    """Normalize a fence's usage snapshot to a canonical SpendBreakdown, or None.

    Stands on `dos.spend.parse_usage`, so the additive/inclusive wire-shape
    ambiguity — "the industry's double-count bug class" — is resolved ONCE, here at
    the boundary, exactly as the kernel requires. A snapshot already in disjoint
    canonical form (the five `SpendBreakdown` field names) is read via `.of`.
    Returns None when there is no snapshot — the absence the verdict reads.
    """
    if not isinstance(usage, dict) or not usage:
        return None
    # A snapshot pre-split into canonical fields (a host that already normalized).
    canonical = {"input", "output", "cache_read", "cache_creation", "reasoning"}
    if canonical & set(usage):
        return SpendBreakdown.of(**{k: usage[k] for k in canonical if k in usage})
    # Otherwise it is a raw provider usage record — let parse_usage detect the shape.
    return parse_usage(usage)


def _diff(start: SpendBreakdown, end: SpendBreakdown) -> SpendBreakdown:
    """The token delta end - start, per disjoint field, rebuilt via SpendBreakdown.of.

    Rebuilding through `.of` re-runs the non-negativity validation, so a
    non-monotonic snapshot (a fence whose end total is BELOW its start) raises a
    ValueError rather than producing a negative or clamped count — the wall refuses
    a backwards delta loudly (the `dos.spend.parse_usage` double-count discipline),
    never invents a plausible-looking wrong number.
    """
    return SpendBreakdown.of(
        input=end.input - start.input,
        output=end.output - start.output,
        cache_read=end.cache_read - start.cache_read,
        cache_creation=end.cache_creation - start.cache_creation,
        reasoning=end.reasoning - start.reasoning,
    )


def fence_spans(records: list[StreamRecord]) -> list[SkillSpan]:
    """Fence skill spans from an ordered stream fossil. PURE — no I/O.

    Walk the records in order. Each `tool_name == "Skill"` record OPENS a span; the
    span CLOSES at the next Skill record (or at the final record of the session).
    Between the two fences lies everything the skill actually did — the tool calls
    and turns the host scatters across many PostToolUse fires.

    Each closed span is attributed by the 3-rung ladder (this is the answer to "what
    can we honestly say about this skill run?"):

      * UNATTRIBUTED — the span cannot be fenced: no readable end stamp, or a
        duration that came back None (an unparseable / backwards fence). Neither
        number asserted. (An open FINAL span — a Skill fired but the session has no
        later record — is the canonical UNATTRIBUTED: we will not claim a duration
        for a span that has not closed.)
      * DURATION_ONLY — fenced and timed, but a usage snapshot is missing at one or
        both fences. Duration is real; tokens are refused (no env-authored number to
        difference). The honest default in a host that does not yet snapshot usage.
      * ATTRIBUTED — fenced, timed, AND a usage snapshot at each fence: duration +
        the grounded `end - start` token delta.

    Returns spans in start order. A session with no Skill record returns [].
    """
    skill_idx = [i for i, r in enumerate(records) if r.tool_name == "Skill"]
    spans: list[SkillSpan] = []
    for k, i in enumerate(skill_idx):
        start = records[i]
        name = start.skill or "(unnamed skill)"
        # The end fence: the next Skill record if there is one, else the session's
        # final record. An open final span (the Skill IS the last record) has no end.
        if k + 1 < len(skill_idx):
            end = records[skill_idx[k + 1]]
        elif i + 1 < len(records):
            end = records[-1]
        else:
            end = None  # open final span — nothing after the Skill fire

        if end is None:
            spans.append(SkillSpan(
                name=name, start_ts=start.ts, end_ts=None, duration_ms=None,
                verdict=SpanVerdict.UNATTRIBUTED,
                tokens=None,
                reason="open final span — the skill fired but the session has no "
                       "later record to close it; refusing to assert a duration",
            ))
            continue

        dur = _duration_ms(start.ts, end.ts)
        if dur is None:
            spans.append(SkillSpan(
                name=name, start_ts=start.ts, end_ts=end.ts, duration_ms=None,
                verdict=SpanVerdict.UNATTRIBUTED,
                tokens=None,
                reason="fence stamps unparseable or out of order — cannot time the "
                       "span; refusing to assert a duration",
            ))
            continue

        start_usage = _usage_breakdown(start.usage)
        end_usage = _usage_breakdown(end.usage)
        if start_usage is None or end_usage is None:
            which = (
                "neither fence carried a usage snapshot"
                if start_usage is None and end_usage is None
                else "the start fence carried no usage snapshot"
                if start_usage is None
                else "the end fence carried no usage snapshot"
            )
            spans.append(SkillSpan(
                name=name, start_ts=start.ts, end_ts=end.ts, duration_ms=dur,
                verdict=SpanVerdict.DURATION_ONLY,
                tokens=None,
                reason=f"duration {dur} ms is grounded, but {which} — refusing to "
                       f"guess a token count no environment authored",
            ))
            continue

        delta = _diff(start_usage, end_usage)  # raises loudly on a backwards snapshot
        spans.append(SkillSpan(
            name=name, start_ts=start.ts, end_ts=end.ts, duration_ms=dur,
            verdict=SpanVerdict.ATTRIBUTED,
            tokens=delta,
            reason=f"duration {dur} ms · {delta.total} tokens "
                   f"(grounded delta of the billed aggregate across both fences)",
        ))
    return spans


# ---------------------------------------------------------------------------
# Boundary read-side — the raw .jsonl replay (carries `ts`, which StreamStep drops).
# ---------------------------------------------------------------------------
def load_stream(path) -> list[StreamRecord]:
    """Replay a `.dos/streams/<sid>.jsonl` fossil into ordered StreamRecords.

    The one honest reuse seam: `dos.posttool_sensor.read_stream` replays the SAME
    file but projects to `StreamStep` (digests only, no `ts`). The span wall needs
    `ts`, so this reads the raw lines for `ts` + `tool_name` (+ an optional
    `tool_input.skill` and an optional `usage` snapshot a host may have stamped on
    the record). Torn-tail tolerant, like the kernel read-side: an unparseable line
    is skipped (a fossil under-counts rather than fabricating a fence).
    """
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return []
    out: list[StreamRecord] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        tool_name = obj.get("tool_name")
        ts = obj.get("ts")
        if not (isinstance(tool_name, str) and isinstance(ts, str)):
            continue
        tool_input = obj.get("tool_input")
        skill = tool_input.get("skill") if isinstance(tool_input, dict) else None
        usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else None
        out.append(StreamRecord(ts=ts, tool_name=tool_name, skill=skill, usage=usage))
    return out


# ---------------------------------------------------------------------------
# In-memory scenarios — the three rungs, the `plan_price.SCENARIOS` idiom.
# ---------------------------------------------------------------------------
def _rec(ts: str, tool: str, skill: Optional[str] = None, usage: Optional[dict] = None):
    return StreamRecord(ts=ts, tool_name=tool, skill=skill, usage=usage)


# A clean session: two skills, with a cumulative usage snapshot at every fire.
# The deltas come out grounded — both spans ATTRIBUTED.
_CLEAN = [
    _rec("2026-06-15T10:00:00Z", "Skill", "deep-research",
         usage={"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0}),
    _rec("2026-06-15T10:00:05Z", "WebSearch"),
    _rec("2026-06-15T10:00:40Z", "Skill", "code-review",
         usage={"input_tokens": 9000, "output_tokens": 1800, "cache_read_input_tokens": 4000}),
    _rec("2026-06-15T10:01:30Z", "Edit",
         usage={"input_tokens": 12000, "output_tokens": 2600, "cache_read_input_tokens": 7000}),
]

# Same shape, but NO usage snapshots — the realistic host today. Durations are real;
# tokens are refused. Both spans DURATION_ONLY.
_NO_USAGE = [
    _rec("2026-06-15T11:00:00Z", "Skill", "deep-research"),
    _rec("2026-06-15T11:00:05Z", "WebSearch"),
    _rec("2026-06-15T11:00:40Z", "Skill", "code-review"),
    _rec("2026-06-15T11:01:30Z", "Edit"),
]

# A skill fired as the very last record — no end fence. The open final span is
# UNATTRIBUTED: we refuse to claim a duration for a span that has not closed.
_OPEN = [
    _rec("2026-06-15T12:00:00Z", "Skill", "release"),
]

SCENARIOS: dict[str, list[StreamRecord]] = {
    "clean_with_snapshots": _CLEAN,
    "no_usage_snapshots": _NO_USAGE,
    "open_final_span": _OPEN,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _render(name: str, spans: list[SkillSpan]) -> str:
    lines = [f"## {name}"]
    if not spans:
        lines.append("  (no skill spans in this session)")
        return "\n".join(lines)
    for sp in spans:
        dur = f"{sp.duration_ms} ms" if sp.duration_ms is not None else "—"
        tok = f"{sp.tokens.total} tok" if sp.tokens is not None else "—"
        lines.append(f"  [{sp.verdict.value:<13}] {sp.name:<18} {dur:>9}  {tok:>9}")
        lines.append(f"      {sp.reason}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument(
        "--stream", metavar="PATH",
        help="a .dos/streams/<sid>.jsonl fossil to attribute (default: the built-in scenarios)",
    )
    args = ap.parse_args(argv)

    if args.stream:
        runs = {"stream": fence_spans(load_stream(args.stream))}
    else:
        runs = {name: fence_spans(recs) for name, recs in SCENARIOS.items()}

    if args.json:
        print(json.dumps(
            {name: [sp.to_dict() for sp in spans] for name, spans in runs.items()},
            indent=2,
        ))
    else:
        print("skill_span — per-skill duration + grounded tokens, "
              "refusing what no environment authored\n")
        for name, spans in runs.items():
            print(_render(name, spans))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
