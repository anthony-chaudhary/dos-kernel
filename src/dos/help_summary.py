"""`dos helped` — the operator-facing "what did DOS catch for me?" projection.

DOS fires a firehose of enforcement decisions every session — a SELF_MODIFY
block, a lane COLLISION refused, a tool-stream WARN re-surfaced — and each one is
durably banked as an `OP_ENFORCE` record on the lane WAL (`lane_journal`,
docs/189 §C4). But until this module **nobody ever told the operator it was
happening**: the hook emitted a `deny`/`additionalContext` to the *agent*, and the
record went to a JSONL file no human reads. So the substrate could be quietly
saving a fleet from a dozen self-overwrites a day and the person running it would
never know — the observability "ran out" one rung short of the human (the docs/204
§4 wall, applied to DOS's own value).

This module closes that last rung. It is a **read-only projection** over the
enforcement stream the WAL already carries (the `observe`/`decisions`/`trace`
contract): it reads the OP_ENFORCE records, folds them into a "DOS helped with N
things" rollup (by intervention rung, by typed reason class, by tool), and the
hook path uses its cadence helper to surface a one-line nudge in the operator's
normal flow every Nth fire. It mints no belief, takes no lease, adjudicates
*nothing new* — the verdicts it counts were minted by the sensors; this only
folds and renders them. Delete it and you lose the reader, not the data.

Design rules (inherited from `observe`/`verdict_journal` — the projection scope):

* **Pure where it can be.** `summarize()` / `should_nudge()` / `nudge_line()`
  take records / an index and return data — entries in, value out, no disk — so
  the suite folds them without touching a file. Only the CLI verb's single
  `read_all` at the boundary touches the journal.
* **Byte-clean by construction (docs/138).** Every field this counts —
  `intervention`, `reason_class`, `tool`, `withheld`, `ts` — is **env-authored**:
  the kernel wrote the OP_ENFORCE record downstream of an already-decided verdict.
  No agent narration enters the count; a run cannot self-report its way to a
  bigger "helped" number.
* **"Helped" is the rungs that changed behavior.** By default a help is a BLOCK
  (a refused/withheld call) or a WARN (a surfaced correction) — the two rungs that
  actually intervened. A passive OBSERVE log is recorded but is NOT a help, so the
  number stays honest and is never inflated by silent logging.

There is deliberately no writer here — this module only reads what the sensors
already banked, so it can never journal a "help" the kernel did not enforce.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# The intervention rungs that count as a "help" — the two that changed behavior.
# A BLOCK refused/withheld a call; a WARN surfaced a correction. A passive OBSERVE
# is recorded on the WAL but is NOT a help (counting it would inflate the number
# with silent logging that intervened in nothing). DEFER is the "ask a human" rung
# — also a real intervention, so it counts. Closed set, matched case-folded.
HELP_RUNGS: tuple[str, ...] = ("BLOCK", "WARN", "DEFER")

# Plain-English, one-line meaning of each refusal class an operator sees in the
# rollup — the answer to "what does `admission`/`SELF_MODIFY`/… actually mean?"
# Keyed by the actual tokens the kernel writes to a record's `reason_class`: the
# `BASE_REASONS` refusal tokens an ENFORCE record can carry (`reasons.py`) + the
# `CLASS_BUDGET_EXHAUSTED` named arbiter refuse (`arbiter.py`) + the env-authored
# handler-name fallbacks (`admission`/`provenance`, written when a record predates
# the typed-token lift). Keys are matched case-insensitively so an older `admission`
# record and a typed `SELF_MODIFY` token both resolve. An unknown key degrades to no
# gloss — we never invent an explanation, so a token added to the vocabulary later
# without a gloss here just shows bare, exactly as today. This is reference DATA, not
# a verdict: it explains an already-counted help, it never decides whether one IS a help.
REASON_GLOSSARY: dict[str, str] = {
    "SELF_MODIFY": "an agent tried to edit the kernel's own running code "
                   "while a loop was adjudicating it",
    "UNKNOWN_LANE": "an agent requested a lane this workspace doesn't declare",
    "SCHEMA_UNREADABLE": "a durable record was tagged at a schema version this "
                         "kernel predates — refused rather than mis-parsed",
    "CLASS_BUDGET_EXHAUSTED": "the concurrency budget for this class of work was "
                              "already full",
    # The env-authored handler-name fallbacks (written when a record predates the
    # typed-token lift) — explained as the rung that proposed the block.
    "admission": "the lane-admission rung refused the call (usually a SELF_MODIFY "
                 "edit or a held-lane collision)",
    "provenance": "the provenance rung refused the call (the claimed effect could "
                  "not be witnessed)",
    "UNCLASSIFIED": "the kernel refused the call but recorded no typed reason "
                    "(an older record, predating the reason-class lift)",
}


def explain_reason(reason_class: str) -> str:
    """The one-line plain-English meaning of a refusal class, or "" if unknown.

    Case-insensitive lookup into `REASON_GLOSSARY`. Returns "" for an unknown class
    so a renderer shows the bare token rather than an invented explanation — we never
    guess what a class means. Pure (a string in, a string out)."""
    if not reason_class:
        return ""
    return REASON_GLOSSARY.get(reason_class, REASON_GLOSSARY.get(reason_class.upper(), ""))

# The in-flow nudge cadence: surface once on the FIRST help of a session (so the
# operator learns the substrate is working), then every 5th after. `1` and every
# multiple of `_NUDGE_EVERY` from there (1, 5, 10, 15, …).
_NUDGE_EVERY = 5


# ---------------------------------------------------------------------------
# The fold — OP_ENFORCE records in, a typed rollup out. Pure (no disk).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Example:
    """One concrete help, for the `--explain` drill-down — env-authored, never narrated.

    `target` is the path(s) the refusal was about (from the kernel-written `reason`);
    `tool` is the tool call; `ts` is when; `reason` is the kernel's own one-line
    explanation. Every field is bytes the sensor authored downstream of the verdict
    (docs/138), so an example is a faithful record of what DOS caught, not a story.
    """

    target: str = ""
    tool: str = ""
    ts: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {"target": self.target, "tool": self.tool, "ts": self.ts,
                "reason": self.reason}


@dataclass(frozen=True)
class HelpSummary:
    """The "DOS helped with N things" rollup over a set of OP_ENFORCE records.

    `total` is the help count (BLOCK + WARN + DEFER records); `by_rung` /
    `by_reason` / `by_tool` are the breakdowns; `withheld` is how many of the helps
    were calls actually refused (the strictest, most defensible subset). `enforced`
    is the count of *all* enforcement records seen (helps + passive OBSERVE), so a
    renderer can be honest that some firings were observe-only. `examples` maps a
    reason class to a few concrete `Example`s (for the `--explain` drill-down); it is
    populated only when `with_examples=True` so the cheap rollup path stays cheap.
    `since` / `latest` echo the time window the count covers (the first/last `ts`).
    """

    total: int = 0
    enforced: int = 0
    withheld: int = 0
    by_rung: dict[str, int] = field(default_factory=dict)
    by_reason: dict[str, int] = field(default_factory=dict)
    by_tool: dict[str, int] = field(default_factory=dict)
    # reason class → {rung: count}. The per-bucket split `--explain` needs to label
    # a bucket honestly — `by_reason` alone cannot say whether its 25 records were
    # denies or advisory warns (issue #9: a mostly-WARN bucket rendered as "25 blocks").
    by_reason_rung: dict[str, dict[str, int]] = field(default_factory=dict)
    examples: dict[str, tuple[Example, ...]] = field(default_factory=dict)
    since: str = ""
    latest: str = ""

    @property
    def blocked(self) -> int:
        return self.by_rung.get("BLOCK", 0)

    @property
    def warned(self) -> int:
        return self.by_rung.get("WARN", 0)

    @property
    def deferred(self) -> int:
        return self.by_rung.get("DEFER", 0)

    def to_dict(self) -> dict:
        out = {
            "total": self.total,
            "enforced": self.enforced,
            "withheld": self.withheld,
            "blocked": self.blocked,
            "warned": self.warned,
            "deferred": self.deferred,
            "by_rung": dict(self.by_rung),
            "by_reason": dict(self.by_reason),
            "by_reason_rung": {cls: dict(rungs)
                               for cls, rungs in self.by_reason_rung.items()},
            "by_tool": dict(self.by_tool),
            "since": self.since,
            "latest": self.latest,
        }
        if self.examples:
            out["examples"] = {
                cls: [e.to_dict() for e in exs]
                for cls, exs in self.examples.items()
            }
            out["glossary"] = {
                cls: explain_reason(cls) for cls in self.by_reason
                if explain_reason(cls)
            }
        return out


def _rung_of(rec: dict) -> str:
    """The intervention rung of an OP_ENFORCE record, upper-cased.

    Reads the top-level `intervention` token `enforce_entry` lifts (the cheap
    forensic field), degrading an absent/blank token to "" — never guessed.
    """
    val = rec.get("intervention") or ""
    return str(val).strip().upper()


def is_help(rec: dict, *, help_rungs: tuple[str, ...] = HELP_RUNGS) -> bool:
    """True iff this OP_ENFORCE record is a behavior-changing help (BLOCK/WARN/DEFER).

    The single predicate the whole module turns on — keep the "what counts" rule in
    exactly one place. A record whose `op` is not ENFORCE, or whose rung is OBSERVE
    / blank / unknown, is not a help.
    """
    if rec.get("op") != "ENFORCE":
        return False
    return _rung_of(rec) in help_rungs


# The env-authored target path(s) live in the parenthesized list inside the
# kernel-written `reason` text: `… running code (src/dos/arbiter.py, …) — refusing …`.
# We extract that list (and only that — a path-shaped, slash-or-backslash token), so
# the operator sees WHICH file was blocked. This reads the kernel's OWN sentence, not
# agent narration — the `reason` was authored by the sensor downstream of the verdict
# (docs/138), so the example stays byte-clean: a run cannot inject a path here.
_PAREN_PATHS = re.compile(r"\(([^)]*)\)")
_PATH_TOKEN = re.compile(r"[\w./\\-]+\.[\w]+")


def _target_of(rec: dict) -> str:
    """The concrete path(s) a record's refusal was about, or "" — env-authored.

    Pulls the parenthesized path list out of the kernel-written `reason` (e.g.
    `(src/dos/arbiter.py)`), keeping only path-shaped tokens, joined back with ", ".
    Falls back to the record's `proposal.reason` then the `lane`. Reads only
    kernel-authored bytes; never the agent's. Pure (a record in, a string out)."""
    text = str(rec.get("reason") or "")
    if not text:
        body = rec.get("proposal")
        if isinstance(body, dict):
            text = str(body.get("reason") or "")
    for chunk in _PAREN_PATHS.findall(text):
        paths = _PATH_TOKEN.findall(chunk)
        if paths:
            return ", ".join(paths)
    return ""


def _recover_reason_class(rec: dict) -> str:
    """The TYPED refusal class for a record, recovered as far as the env allows.

    Prefers the top-level `reason_class`, then the SAME token nested in the
    env-authored `proposal` body (present on older records whose top-level token was
    never lifted — the 092ad29 gap), then the env-authored `handler` name, then
    "UNCLASSIFIED". Every source is kernel-written; the human-readable `reason` prose
    is never mined. This is the fix for the misleading "admission 597 / SELF_MODIFY 13"
    split — both are SELF_MODIFY, but the older 597 lost their top-level token, so we
    recover it from the proposal body and the two buckets collapse into the honest one."""
    cls = str(rec.get("reason_class") or "").strip()
    if cls:
        return cls
    body = rec.get("proposal")
    if isinstance(body, dict):
        cls = str(body.get("reason_class") or "").strip()
        if cls:
            return cls
    return str(rec.get("handler") or "").strip() or "UNCLASSIFIED"


# How many distinct examples to bank per reason class for the `--explain` view.
# A small cap: the drill-down shows the SHAPE of what was caught, not the firehose.
_EXAMPLES_PER_REASON = 3


def summarize(
    records,
    *,
    holder: str = "",
    since: str = "",
    help_rungs: tuple[str, ...] = HELP_RUNGS,
    with_examples: bool = False,
) -> HelpSummary:
    """Fold OP_ENFORCE records into a "DOS helped with N things" rollup. PURE.

    `records` is any iterable of journal entries (the `lane_journal.read_all`
    output, or a hand-built list in a test). `holder` filters to one session/owner
    (the OP_ENFORCE `holder` is the session id — see `_journal_pretool_outcome`),
    so the in-flow nudge and the stop digest can count *this* session's helps.
    `since` keeps only records with `ts >= since` (ISO-8601 sorts lexically, so a
    string compare is the window) — for `dos helped --since`. `help_rungs` is the
    "what counts" set (defaults to BLOCK/WARN/DEFER). `with_examples` additionally
    banks a few concrete `Example`s per reason class (for `dos helped --explain`) —
    off by default so the cheap rollup path stays cheap.

    Entries in, counts out, no disk — the unit-test surface, mirroring
    `verdict_journal.rollup`.
    """
    total = 0
    enforced = 0
    withheld = 0
    by_rung: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_reason_rung: dict[str, dict[str, int]] = {}
    by_tool: dict[str, int] = {}
    examples: dict[str, list[Example]] = {}
    seen_targets: dict[str, set[str]] = {}
    first_ts = ""
    last_ts = ""

    for rec in records:
        if rec.get("op") != "ENFORCE":
            continue
        if holder and str(rec.get("holder") or "") != holder:
            continue
        ts = str(rec.get("ts") or "")
        if since and ts and ts < since:
            continue
        enforced += 1
        if ts:
            if not first_ts or ts < first_ts:
                first_ts = ts
            if ts > last_ts:
                last_ts = ts
        rung = _rung_of(rec)
        if rung not in help_rungs:
            continue  # recorded (counted in `enforced`) but not a help
        total += 1
        by_rung[rung] = by_rung.get(rung, 0) + 1
        if rec.get("withheld") is True:
            withheld += 1
        # The TYPED reason class, recovered as far as the env allows (top-level →
        # the same token nested in the proposal body → the env-authored handler name
        # → UNCLASSIFIED). All kernel-written — the `reason` prose is never mined.
        # Recovering the nested token is what collapses the misleading
        # "admission 597 / SELF_MODIFY 13" split into the honest single bucket.
        reason_class = _recover_reason_class(rec)
        by_reason[reason_class] = by_reason.get(reason_class, 0) + 1
        rungs = by_reason_rung.setdefault(reason_class, {})
        rungs[rung] = rungs.get(rung, 0) + 1
        tool = str(rec.get("tool") or "").strip() or "-"
        by_tool[tool] = by_tool.get(tool, 0) + 1

        if with_examples:
            bank = examples.setdefault(reason_class, [])
            if len(bank) < _EXAMPLES_PER_REASON:
                target = _target_of(rec)
                # Prefer DISTINCT targets so the few examples shown are different
                # files, not the same path three times.
                seen = seen_targets.setdefault(reason_class, set())
                if not target or target not in seen:
                    if target:
                        seen.add(target)
                    reason_text = str(rec.get("reason") or "").strip()
                    body = rec.get("proposal")
                    if not reason_text and isinstance(body, dict):
                        reason_text = str(body.get("reason") or "").strip()
                    bank.append(Example(
                        target=target, tool=tool, ts=ts,
                        reason=_first_sentence(reason_text)))

    return HelpSummary(
        total=total,
        enforced=enforced,
        withheld=withheld,
        by_rung=dict(sorted(by_rung.items(), key=lambda kv: (-kv[1], kv[0]))),
        by_reason=dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        by_reason_rung={
            cls: dict(sorted(rungs.items(), key=lambda kv: (-kv[1], kv[0])))
            for cls, rungs in by_reason_rung.items()
        },
        by_tool=dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))),
        examples={cls: tuple(exs) for cls, exs in examples.items()},
        since=first_ts,
        latest=last_ts,
    )


def _first_sentence(text: str, *, limit: int = 160) -> str:
    """The first sentence of a kernel `reason`, trimmed for one-line display.

    The full `reason` carries the explanation plus a "Pass --force …" trailer; the
    operator-facing example wants just the first clause. Splits on the em-dash the
    SELF_MODIFY/collision sentences use, else the first period, else hard-truncates.
    Pure (a string in, a string out); reads kernel-authored bytes only."""
    if not text:
        return ""
    for sep in ("—", " - ", ". "):
        head = text.split(sep, 1)[0].strip()
        if head and head != text:
            return head[:limit].rstrip()
    return text[:limit].rstrip()


# ---------------------------------------------------------------------------
# The cadence — when does the in-flow nudge fire? PURE (an index in, a bool out).
# ---------------------------------------------------------------------------


def should_nudge(help_index: int, *, every: int = _NUDGE_EVERY) -> bool:
    """True iff the operator nudge should fire on this help (1-based count).

    "First + every 5th": fire on the 1st help of the session (so the operator sees
    the substrate is alive) and every `every`th after — indices 1, 5, 10, 15, ….
    `help_index` is the running BLOCK/WARN/DEFER count *including* this firing
    (1-based). A non-positive index never nudges.
    """
    if help_index <= 0:
        return False
    if help_index == 1:
        return True
    return help_index % every == 0


# ---------------------------------------------------------------------------
# Rendering — the one-line in-flow nudge + the full operator rollup.
# ---------------------------------------------------------------------------


def nudge_line(summary: HelpSummary) -> str:
    """The one-line in-flow nudge appended to the hook's additionalContext.

    Operator-facing, single sentence, no narration: "DOS has caught N things this
    session (X blocked, Y warned)." Surfaced on the 1st + every 5th help so the
    operator learns, in their normal flow, that the substrate is working — without
    a separate command and without nagging.
    """
    parts: list[str] = []
    if summary.blocked:
        parts.append(f"{summary.blocked} blocked")
    if summary.warned:
        parts.append(f"{summary.warned} warned")
    if summary.deferred:
        parts.append(f"{summary.deferred} deferred")
    detail = f" ({', '.join(parts)})" if parts else ""
    noun = "thing" if summary.total == 1 else "things"
    return (
        f"DOS has caught {summary.total} {noun} this session{detail}. "
        f"Run `dos helped` for the breakdown."
    )


def _rate_lines(rate) -> list[str]:
    """The "of N adjudicated calls" block for an `InterventionRate`, or []. Pure.

    The docs/297 denominator line. `rate` is a `dos.hook_observation
    .InterventionRate` (duck-typed: `adjudicated`/`passed`/`intervened` + the
    `*_pct` properties) — every number in it came from ONE observation log, by
    construction of that fold. None, or zero adjudicated calls, renders nothing
    at all, so a workspace without an observation log keeps today's bytes. The
    trailing note is the honesty caveat: the rate's log and the catch counts'
    journal have different windows and scopes, so the two never share a number.
    """
    if rate is None or rate.adjudicated <= 0:
        return []
    return [
        "",
        (f"  of {rate.adjudicated} tool calls adjudicated by the hooks, "
         f"{rate.passed} passed untouched ({rate.passed_pct:.1f}%) and "
         f"{rate.intervened} were intervened on ({rate.intervened_pct:.1f}%)"),
        ("    (from the per-call hook observation log — its window and scope "
         "differ from the catch counts above)"),
    ]


def render_summary_text(summary: HelpSummary, *, scope: str = "",
                        rate=None) -> str:
    """The full `dos helped` operator rollup — headline + breakdowns. Pure.

    Leads with the headline count, then the by-reason-class and by-tool tables (the
    "what kind of help, on which tool" an operator wants), and an honest footer
    noting how many firings were observe-only (recorded but not a behavior-change).
    `rate` (docs/297) is an optional `hook_observation.InterventionRate` — when
    present it adds the self-contained "of N adjudicated calls" block; when None
    (no observation log) the output is byte-identical to the rate-less form.
    """
    out: list[str] = []
    title = "# dos helped"
    if scope:
        title += f" · {scope}"
    out.append(title)
    noun = "thing" if summary.total == 1 else "things"
    out.append(f"  DOS has caught {summary.total} {noun}"
               + (f" since {summary.since}" if summary.since else ""))
    if summary.total:
        rung_parts = []
        if summary.blocked:
            rung_parts.append(f"{summary.blocked} blocked")
        if summary.warned:
            rung_parts.append(f"{summary.warned} warned")
        if summary.deferred:
            rung_parts.append(f"{summary.deferred} deferred")
        out.append(f"    {', '.join(rung_parts)}"
                   + (f"  ·  {summary.withheld} calls actually refused"
                      if summary.withheld else ""))
    if not summary.total:
        out.append("  (no behavior-changing interventions recorded yet — "
                   "DOS has been observing, not blocking)")
        if summary.enforced:
            out.append(f"  ({summary.enforced} enforcement record(s) seen, "
                       f"all observe-only)")
        out.extend(_rate_lines(rate))
        return "\n".join(out)
    out.extend(_rate_lines(rate))
    if summary.by_reason:
        out.append("")
        out.append("  by reason")
        for reason, n in summary.by_reason.items():
            gloss = explain_reason(reason)
            line = f"    {reason:<22} {n:>4}"
            if gloss:
                line += f"   {gloss}"   # the plain-English meaning, inline
            out.append(line)
    if summary.by_tool:
        out.append("")
        out.append("  by tool")
        for tool, n in summary.by_tool.items():
            out.append(f"    {tool:<22} {n:>4}")
    observe_only = summary.enforced - summary.total
    if observe_only > 0:
        out.append("")
        out.append(f"  ({observe_only} further firing(s) were observe-only — "
                   f"recorded, but changed nothing)")
    # Point the operator at the drill-down — the answer to "but WHICH ones?"
    out.append("")
    out.append("  Run `dos helped --explain` for concrete examples (which files, why).")
    return "\n".join(out)


# The per-bucket heading noun, rung-honest (issue #9): the old flat "(N blocks)"
# overstated enforcement — a bucket of 25 records could be 21 advisory warn-and-pass
# WARNs and only 4 denies. Canonical rungs get their own noun; any record outside
# them (a custom `help_rungs` set) falls back to the neutral "catch", the same verb
# the headline uses.
_RUNG_NOUNS = {"BLOCK": "block", "WARN": "warn", "DEFER": "defer"}


def _bucket_label(total: int, rungs: dict[str, int]) -> str:
    """The honest count label for one `--explain` reason bucket. PURE.

    "4 blocks, 21 warns" when the per-rung split is known; "N catches" when it is
    not (a summary built without `by_reason_rung`) or for records on a rung outside
    the canonical three. A WARN-only bucket therefore never reads "blocks"."""
    def plural(n: int, noun: str) -> str:
        return f"{n} {noun}" + ("" if n == 1 else ("es" if noun == "catch" else "s"))

    parts = [plural(rungs[r], _RUNG_NOUNS[r]) for r in ("BLOCK", "WARN", "DEFER")
             if rungs.get(r)]
    leftover = total - sum(rungs.get(r, 0) for r in _RUNG_NOUNS)
    if leftover > 0:
        parts.append(plural(leftover, "catch"))
    return ", ".join(parts) if parts else plural(total, "catch")


def render_explain_text(summary: HelpSummary, *, scope: str = "") -> str:
    """The `dos helped --explain` drill-down — per reason class: meaning + examples.

    The answer to "but WHICH ones, and what does `admission` mean?": for each reason
    class, the plain-English gloss, the count, and a few concrete examples (the file
    blocked, the tool, the kernel's own one-line reason). Every shown field is
    env-authored (docs/138) — the gloss is reference data, the examples are bytes the
    sensor wrote downstream of the verdict; no agent narration appears. Pure.
    """
    out: list[str] = []
    title = "# dos helped --explain"
    if scope:
        title += f" · {scope}"
    out.append(title)
    noun = "thing" if summary.total == 1 else "things"
    out.append(f"  DOS has caught {summary.total} {noun}"
               + (f" since {summary.since}" if summary.since else ""))
    if not summary.total:
        out.append("")
        out.append("  (no behavior-changing interventions recorded yet — "
                   "DOS has been observing, not blocking)")
        return "\n".join(out)
    for reason, n in summary.by_reason.items():
        out.append("")
        label = _bucket_label(n, summary.by_reason_rung.get(reason, {}))
        out.append(f"  {reason}  ({label})")
        gloss = explain_reason(reason)
        if gloss:
            out.append(f"    means: {gloss}")
        exs = summary.examples.get(reason, ())
        if exs:
            out.append("    e.g.")
            for e in exs:
                where = e.target or "(target not recorded)"
                tool = f" via {e.tool}" if e.tool and e.tool != "-" else ""
                out.append(f"      · {where}{tool}")
                if e.reason:
                    out.append(f"        {e.reason}")
    return "\n".join(out)
