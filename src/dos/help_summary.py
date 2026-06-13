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
    "admission": "the lane-admission rung acted on the call — a held-lane collision "
                 "(refused) or a contention caution on an unknown/empty footprint "
                 "(advised, the call proceeded)",
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


# A SHORT (2-3 word) label per reason class for the refused-headline sub-line —
# the long `REASON_GLOSSARY` sentence is too wide to inline next to a count. Keyed
# by the same tokens. An unknown class falls back to no parenthetical (just the
# token + count), never an invented label — the same never-guess rule as the gloss.
REASON_SHORT_LABEL: dict[str, str] = {
    "SELF_MODIFY": "kernel-self-edit",
    "UNKNOWN_LANE": "undeclared lane",
    "SCHEMA_UNREADABLE": "unreadable schema",
    "CLASS_BUDGET_EXHAUSTED": "class budget full",
    "admission": "lane collision",
    "provenance": "unwitnessed effect",
    "UNCLASSIFIED": "untyped refusal",
}


def short_label(reason_class: str) -> str:
    """A 2-3 word label for a reason class, or "" if unknown. Pure. Never invents."""
    if not reason_class:
        return ""
    return REASON_SHORT_LABEL.get(
        reason_class, REASON_SHORT_LABEL.get(reason_class.upper(), ""))


def _refused_breakdown(summary: "HelpSummary") -> str:
    """The headline sub-line: "168 SELF_MODIFY (kernel-self-edit), 8 admission …".

    DERIVED from `by_refused_reason` (the withheld-only reason counts), so it is
    correct for ANY reason class the kernel emits — never a hardcoded two-category
    sentence. Each part is `<n> <class> (<short label>)`, the label dropped when
    unknown (we never invent one). Empty string when nothing was refused."""
    parts: list[str] = []
    for reason, n in summary.by_refused_reason.items():
        label = short_label(reason)
        parts.append(f"{n} {reason} ({label})" if label else f"{n} {reason}")
    return ", ".join(parts)

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
    # The WITHHELD subset, split out so the headline sub-line is DERIVED from the
    # data, never a hardcoded two-category sentence. `by_refused_reason` answers
    # "of the calls actually stopped, what KIND were they?" (SELF_MODIFY, admission,
    # provenance, …) — correct for any reason class, not just the two this repo
    # happens to show. `by_advisory_tool` is the advisory complement keyed by tool
    # (the `dos helped --advisory` breakdown — advisory cautions cluster by tool).
    by_refused_reason: dict[str, int] = field(default_factory=dict)
    by_advisory_tool: dict[str, int] = field(default_factory=dict)
    examples: dict[str, tuple[Example, ...]] = field(default_factory=dict)
    # A few concrete ADVISORY examples (non-withheld helps), distinct by their
    # first-clause reason — for the `dos helped --advisory` view. A flat tuple (the
    # advisory cautions are one conceptual bucket, unlike the per-reason `examples`).
    # Populated only when `with_examples=True`, like `examples`.
    advisory_examples: tuple[Example, ...] = ()
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

    @property
    def advisory(self) -> int:
        """Helps that were NOT withheld — DOS surfaced a caution but let the call
        proceed. The complement of `withheld` within `total`: a `withheld` help
        actually stopped the call (the headline number), an `advisory` one only
        warned (the secondary line). Derived, not stored — `total - withheld`."""
        return self.total - self.withheld

    def to_dict(self) -> dict:
        out = {
            "total": self.total,
            "enforced": self.enforced,
            "withheld": self.withheld,
            "advisory": self.advisory,
            "blocked": self.blocked,
            "warned": self.warned,
            "deferred": self.deferred,
            "by_rung": dict(self.by_rung),
            "by_reason": dict(self.by_reason),
            "by_reason_rung": {cls: dict(rungs)
                               for cls, rungs in self.by_reason_rung.items()},
            "by_tool": dict(self.by_tool),
            "by_refused_reason": dict(self.by_refused_reason),
            "by_advisory_tool": dict(self.by_advisory_tool),
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
        if self.advisory_examples:
            out["advisory_examples"] = [e.to_dict() for e in self.advisory_examples]
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
    by_refused_reason: dict[str, int] = {}
    by_advisory_tool: dict[str, int] = {}
    examples: dict[str, list[Example]] = {}
    seen_targets: dict[str, set[str]] = {}
    advisory_examples: list[Example] = []
    seen_advisory: set[str] = set()
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
        is_withheld = rec.get("withheld") is True
        if is_withheld:
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
        # The refused-vs-advisory split, keyed off the env-authored `withheld` flag
        # ONLY (no prose mined). A withheld help is one DOS actually stopped — break
        # it down by reason class for the headline. A non-withheld help is advisory —
        # break it down by tool for `--advisory` (cautions cluster by tool).
        if is_withheld:
            by_refused_reason[reason_class] = by_refused_reason.get(reason_class, 0) + 1
        else:
            by_advisory_tool[tool] = by_advisory_tool.get(tool, 0) + 1

        if with_examples:
            reason_text = str(rec.get("reason") or "").strip()
            body = rec.get("proposal")
            if not reason_text and isinstance(body, dict):
                reason_text = str(body.get("reason") or "").strip()
            first_clause = _first_sentence(reason_text)
            bank = examples.setdefault(reason_class, [])
            if len(bank) < _EXAMPLES_PER_REASON:
                target = _target_of(rec)
                # Prefer DISTINCT targets so the few examples shown are different
                # files, not the same path three times.
                seen = seen_targets.setdefault(reason_class, set())
                if not target or target not in seen:
                    if target:
                        seen.add(target)
                    bank.append(Example(
                        target=target, tool=tool, ts=ts, reason=first_clause))
            # Advisory examples (non-withheld helps) — distinct by first-clause, so
            # the `--advisory` view shows the SHAPE of the cautions (e.g. the
            # empty-tree warn) rather than the same sentence repeated per tool.
            if not is_withheld and len(advisory_examples) < _EXAMPLES_PER_REASON:
                key = first_clause or f"<{tool}>"
                if key not in seen_advisory:
                    seen_advisory.add(key)
                    advisory_examples.append(Example(
                        target=_target_of(rec), tool=tool, ts=ts, reason=first_clause))

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
        by_refused_reason=dict(sorted(by_refused_reason.items(),
                                      key=lambda kv: (-kv[1], kv[0]))),
        by_advisory_tool=dict(sorted(by_advisory_tool.items(),
                                     key=lambda kv: (-kv[1], kv[0]))),
        examples={cls: tuple(exs) for cls, exs in examples.items()},
        advisory_examples=tuple(advisory_examples),
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

    Operator-facing, single sentence, no narration. Leads with what DOS actually
    DID — the refused count — with the advisory cautions in a parenthetical, so
    the one-liner matches the refused-first rollup headline rather than the old
    lumped "caught N" total. Surfaced on the 1st + every 5th help so the operator
    learns, in their normal flow, that the substrate is working — without a
    separate command and without nagging.
    """
    advisory = summary.advisory
    if summary.withheld:
        call_noun = "call" if summary.withheld == 1 else "calls"
        detail = f" (+{advisory} advisory)" if advisory else ""
        return (
            f"DOS has refused {summary.withheld} {call_noun} this session{detail}. "
            f"Run `dos helped` for the breakdown."
        )
    # Nothing refused — be honest it was advisory-only, never imply a refusal.
    cau = "caution" if advisory == 1 else "cautions"
    return (
        f"DOS surfaced {advisory} advisory {cau} this session (no calls refused). "
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
    # The intervened share splits the same way the headline does: refused (a call
    # actually stopped) vs advised (warned, but let proceed). Render both so the rate
    # tells the SAME story as the headline — never a lumped "5% intervened" that hides
    # how little of it was a real refusal. Duck-typed: an older InterventionRate
    # without the split degrades to the lumped line (getattr fallback).
    refused = getattr(rate, "refused", None)
    advised = getattr(rate, "advised", None)
    lines = [
        "",
        (f"  of {rate.adjudicated} tool calls adjudicated by the hooks, "
         f"{rate.passed} passed untouched ({rate.passed_pct:.1f}%) and "
         f"{rate.intervened} were intervened on ({rate.intervened_pct:.1f}%)"),
    ]
    if refused is not None and advised is not None and rate.intervened:
        lines.append(
            f"    of those, {refused} were refused ({rate.refused_pct:.1f}%) and "
            f"{advised} were advised-but-allowed ({rate.advised_pct:.1f}%)")
    lines.append(
        "    (from the per-call hook observation log — its window and scope "
        "differ from the catch counts above)")
    return lines


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
    since_tail = f" since {summary.since}" if summary.since else ""
    if not summary.total:
        out.append(f"  DOS has refused 0 calls{since_tail}")
        out.append("  (no behavior-changing interventions recorded yet — "
                   "DOS has been observing, not blocking)")
        if summary.enforced:
            out.append(f"  ({summary.enforced} enforcement record(s) seen, "
                       f"all observe-only)")
        out.extend(_rate_lines(rate))
        return "\n".join(out)
    # The honest headline: lead with what DOS ACTUALLY DID (the withheld refusals),
    # with the advisories on their own clearly-labeled line — never lumped into one
    # inflated "caught N" total. The refused sub-line is derived from the data.
    advisory = summary.advisory
    if summary.withheld:
        call_noun = "call" if summary.withheld == 1 else "calls"
        out.append(f"  DOS has refused {summary.withheld} {call_noun} for you{since_tail}")
        breakdown = _refused_breakdown(summary)
        if breakdown:
            out.append(f"    {breakdown}")
        if advisory:
            cau = "caution" if advisory == 1 else "cautions"
            out.append(f"    + {advisory} advisory {cau} surfaced "
                       f"(the call was allowed to proceed)")
    else:
        # Nothing was withheld — be honest that DOS only advised, never refused.
        cau = "caution" if advisory == 1 else "cautions"
        out.append(f"  DOS surfaced {advisory} advisory {cau}{since_tail}")
        out.append("    (no calls were refused — every help here was advisory, "
                   "the call was allowed to proceed)")
    out.extend(_rate_lines(rate))
    if summary.by_reason:
        out.append("")
        # "refused + advisory" so the table can't be misread as all-refusals — the
        # headline already broke out the refused-only counts by reason class.
        out.append("  by reason (refused + advisory)")
        for reason, n in summary.by_reason.items():
            gloss = explain_reason(reason)
            line = f"    {reason:<22} {n:>4}"
            if gloss:
                line += f"   {gloss}"   # the plain-English meaning, inline
            out.append(line)
    if summary.by_tool:
        out.append("")
        out.append("  by tool (refused + advisory)")
        for tool, n in summary.by_tool.items():
            out.append(f"    {tool:<22} {n:>4}")
    observe_only = summary.enforced - summary.total
    if observe_only > 0:
        out.append("")
        out.append(f"  ({observe_only} further firing(s) were observe-only — "
                   f"recorded, but changed nothing)")
    # Point the operator at the two drill-downs — the refusals and the cautions.
    out.append("")
    out.append("  Run `dos helped --explain` for concrete examples (which files, why)"
               + (", `dos helped --advisory` for the cautions."
                  if summary.advisory else "."))
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
    since_tail = f" since {summary.since}" if summary.since else ""
    # Refused-first headline, matching the rollup. The per-bucket tables below
    # already split each reason class by rung (`_bucket_label`), so the drill-down
    # stays honest about which of a bucket's records were refused vs advised.
    call_noun = "call" if summary.withheld == 1 else "calls"
    out.append(f"  DOS has refused {summary.withheld} {call_noun}{since_tail}"
               + (f"  ·  +{summary.advisory} advisory" if summary.advisory else ""))
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


def render_advisory_text(summary: HelpSummary, *, scope: str = "") -> str:
    """The `dos helped --advisory` view — the cautions DOS surfaced but did NOT act on.

    The advisory complement of the refused-first headline: these helps WARNed and
    let the call proceed (`withheld=false`). They are off the default rollup so the
    headline stays about what DOS actually did — but one keystroke away here, broken
    down by tool (advisory cautions cluster by tool: a read-only Grep/Read/Bash got
    flagged against a held lease and ran anyway) with a few concrete example reasons.
    Every shown field is env-authored (docs/138). Pure."""
    out: list[str] = []
    title = "# dos helped --advisory"
    if scope:
        title += f" · {scope}"
    out.append(title)
    advisory = summary.advisory
    if not advisory:
        out.append("  DOS surfaced 0 advisory cautions"
                   + (f" since {summary.since}" if summary.since else ""))
        out.append("")
        out.append("  (every help recorded was a call DOS actually refused — "
                   "see `dos helped`)")
        return "\n".join(out)
    cau = "caution" if advisory == 1 else "cautions"
    out.append(f"  DOS surfaced {advisory} advisory {cau} "
               f"(the call was allowed to proceed)"
               + (f" since {summary.since}" if summary.since else ""))
    if summary.by_advisory_tool:
        out.append("")
        out.append("  by tool")
        for tool, n in summary.by_advisory_tool.items():
            out.append(f"    {tool:<22} {n:>4}")
    # A few concrete example cautions — the advisory-only bank (non-withheld helps),
    # distinct by first-clause. Shown only when the summary was built with examples
    # (`--advisory` implies it, like `--explain`).
    adv_exs = [e for e in summary.advisory_examples if e.reason]
    if adv_exs:
        out.append("")
        out.append("  e.g.")
        for e in adv_exs:
            tool = f" via {e.tool}" if e.tool and e.tool != "-" else ""
            out.append(f"    · {e.reason}{tool}")
    out.append("")
    out.append("  These changed nothing — DOS warned and let the call run. "
               "The refusals are in `dos helped`.")
    return "\n".join(out)
