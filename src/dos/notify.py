"""notify — push a DOS projection (decisions / fleet status) to a transport.

> **The verdict is the kernel; *where it lands* is a driver (docs/225).** DOS
> already has two read-only projections — `dos decisions` ("what needs a human")
> and `dos top` ("what is running now"). This seam turns either into ONE
> transport-agnostic `Notification` and hands it to a by-name `Notifier`. Slack is
> the first driver (`dos.drivers.notify_slack`); PagerDuty / email / a webhook are
> later drivers. **No transport name appears in this module.**

Why this module exists
======================

A fleet runs unattended; the operator is in Slack, not watching a terminal. The
two projections that answer "what needs me" / "what's running" render only to the
local screen today. The notification spine is the seam that pushes them out.

This is the kernel's pure-protocol + by-name-resolver pattern, for the FOURTH
time, now on the DELIVERY side — after `dos.judges` (the JUDGE rung),
`dos.overlap_policy` (the disjointness scorer), and `dos.hook_dialect` (the
host-hook renderer). The shape is identical: a pure Protocol + two frozen value
types + an unshadowable built-in + a by-name resolver; every real transport (which
names a vendor as code — a `SlackNotifier` is inherently Slack-specific) lives in a
driver and registers through the `dos.notifiers` entry-point group.

The neutral payload — `Notification`
=====================================

A `Notification` is the DOS-shaped fact, NOT a transport's wire format (it is not
Slack Block Kit — that is the Slack driver's shape). A renderer (this module) turns
the typed projection data into a `Notification`; a driver turns the `Notification`
into its transport's bytes. `Notification.key` is the load-bearing field for the
edit-in-place surface (`dos top` streamed into ONE re-edited message): a notifier
that can edit keys its single message on `key`; one that cannot ignores it and
posts.

The two adapters are PURE over the *already-built* projection data
==================================================================

The hard part is already done: `decisions.collect_decisions()` and
`dispatch_top.snapshot()` return typed, ranked, `to_dict()`-able data, and their
`render_*` functions produce the plain-text body. So the adapters here take that
data as ARGUMENTS (duck-typed — they read only the fields they need) and are pure:
data in, `Notification` out, no I/O. The CLI verb (layer 3) is what calls the
readers and hands the result in — that keeps `notify.py` a true layer-1 leaf that
imports no layer-3 module (the dependency-arrow rule; the same reason
`liveness.classify` takes evidence rather than reading git itself).

Failure direction = fail-SOFT
=============================

Unlike `hook_dialect` (fail-LOUD: a wrong dialect is a silent no-op against the
host, so it raises), a notification is advisory telemetry. A transport that raises
or is mis-wired must NEVER crash the fleet loop that emitted it. So `send_safely`
converts any `send` raise into a non-delivered `NotifyResult`. A *resolve* of an
unknown notifier name still raises (operator error, surfaced at config time, like
`resolve_judge`); a *send* never does. This is `LiveMessage._warn`'s philosophy —
"a streaming UI never crashes its producer" — lifted to the seam.

The advisory floor (docs/99)
============================

The notifier REPORTS; it never acts on the fleet. It cannot take a lease, stop a
run, or mutate state — it is a pure read-of-a-projection → push. A LIVENESS-halt
notification *describes* a proposed stop and carries the paste-to-stop command in a
field; enacting it stays the operator's call. `decisions.py`'s locked
read-only-router model, extended across the network boundary.

Pure-stdlib. The renderers + resolver are the unit-test surface; the only I/O in
the whole spine is inside a driver's `send`.
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only; never imported at runtime
    from dos.decisions import Decision
    from dos.dispatch_top import Frame


# ---------------------------------------------------------------------------
# The closed severity vocabulary. `str`-valued so it round-trips through `--json`
# without a lookup table (the `gate_classify.Verdict` / `DecisionKind` posture).
# ---------------------------------------------------------------------------


class Severity(str, enum.Enum):
    """How loud a notification is — the transport maps this to colour/emoji/route."""

    INFO = "INFO"      # a status digest, nothing wrong
    WARN = "WARN"      # a refusal / wedge / spinning lane is pending
    URGENT = "URGENT"  # a LIVENESS halt / stalled lane — a run is hung NOW

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ---------------------------------------------------------------------------
# The neutral payload — the DOS-shaped fact a driver renders to its wire format.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Notification:
    """One transport-agnostic notification — pure data, no wire format.

    `summary` is the plain-text body (reuses the projection's own renderer, so a
    notifier with no rich surface still says everything). `fields` are the TOP rows
    as `(label, value)` pairs — what a Block-Kit/section transport shows at a
    glance. `key` is the stable identity for edit-in-place (a notifier that can
    edit re-uses one message keyed on it); `source` names which projection this is.
    """

    severity: Severity
    title: str
    summary: str
    fields: tuple[tuple[str, str], ...] = ()
    key: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "title": self.title,
            "summary": self.summary,
            "fields": [list(f) for f in self.fields],
            "key": self.key,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# The result a driver returns — fail-soft: delivered=False is normal, never a raise.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotifyResult:
    """The outcome of a `send` — always returned, never raised (fail-soft).

    `delivered` is True iff the transport accepted it. `detail` is a one-line human
    reason (`"posted ts=…"` / `"edited"` / `"dry-run"` / `"no token — skipped"` /
    `"error: …"`). `ref` is the transport's message id (a Slack `ts`) when one
    exists, so a later edit-in-place can target the same message.
    """

    delivered: bool
    detail: str = ""
    ref: str = ""

    def to_dict(self) -> dict:
        return {"delivered": self.delivered, "detail": self.detail, "ref": self.ref}


# ---------------------------------------------------------------------------
# The Notifier protocol + the unshadowable built-in null sink.
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """A transport that delivers a `Notification`. The driver-side seam.

    `name` is the registered name (`"slack"`); `send` delivers and returns a
    `NotifyResult`. An implementation MAY post a fresh message or edit one in place
    (keyed on `note.key`) — that choice is the driver's, not the kernel's.
    """

    name: str

    def send(self, note: Notification) -> NotifyResult: ...


class NullNotifier:
    """The honest zero — delivers nothing, the unshadowable built-in baseline.

    The default notifier, so a bare `dos notify` is a safe no-op that renders the
    payload and sends it nowhere (the `AbstainJudge` / `prefix`-floor analogue: the
    built-in that can never loosen anything and is always resolvable). A host opts
    IN to a real transport by naming one (`--notifier slack`).
    """

    name = "null"

    def send(self, note: Notification) -> NotifyResult:
        return NotifyResult(delivered=False, detail="null sink (no transport configured)")


_BUILT_IN_NOTIFIERS: dict[str, type] = {"null": NullNotifier}

NOTIFIER_ENTRY_POINT_GROUP = "dos.notifiers"


# ---------------------------------------------------------------------------
# The pure adapters — typed projection data → a Notification. No I/O; duck-typed
# over the fields they read (so this module imports no layer-3 module at runtime).
# ---------------------------------------------------------------------------


def _decision_field(d: "Decision") -> tuple[str, str]:
    """One decision → a `(label, value)` field. `label` is kind@lane, `value` the why.

    Pure. Reads only `.kind`/`.lane`/`.reason_token`/`.reason_text`/`.dup_count`
    (duck-typed). A LIVENESS row's proposed stop command, when present, is appended
    to the value so the page CARRIES the paste-to-stop (advisory — the operator
    enacts it).
    """
    kind = getattr(getattr(d, "kind", ""), "value", "") or str(getattr(d, "kind", ""))
    lane = getattr(d, "lane", "") or "-"
    label = f"{kind} @ {lane}"
    value = getattr(d, "reason_token", "") or getattr(d, "reason_text", "") or "?"
    dup = getattr(d, "dup_count", 1) or 1
    if dup > 1:
        value = f"{value}  (×{dup})"
    cmd = getattr(d, "proposed_command", "") or ""
    if cmd:
        value = f"{value}  ⟶ stop: {cmd}"
    return (label, value[:300])


def notification_for_decisions(
    rows: list["Decision"], *, summary: str = "", top: int = 5
) -> Notification:
    """Pure: the ranked decision rows → one `Notification` (the digest surface).

    `rows` is the output of `decisions.collect_decisions(...)` — ALREADY ranked
    (LIVENESS halt first, then refusals, then wedges, then soak gates), so the first
    `top` rows ARE the TOP decisions to surface. `summary` is the prebuilt
    plain-text body (`decisions.render_list_plain(rows)`); the caller passes it so
    this module needs no `decisions` import. Severity rises with the worst row: a
    LIVENESS halt → URGENT, any pending row → WARN, an empty queue → INFO.

    `key` is workspace-stable so a notifier that edits in place keeps one digest
    message current rather than spamming; the digest surface posts by default
    (see the driver), but the key is available either way.
    """
    n = len(rows)
    has_liveness = any(
        (getattr(getattr(r, "kind", None), "value", "") == "LIVENESS") for r in rows
    )
    if has_liveness:
        severity = Severity.URGENT
    elif n:
        severity = Severity.WARN
    else:
        severity = Severity.INFO

    if n == 0:
        title = "fleet clear — no pending decisions"
    else:
        title = f"{n} decision{'s' if n != 1 else ''} need you"
    fields = tuple(_decision_field(r) for r in rows[: max(0, top)])
    return Notification(
        severity=severity,
        title=title,
        summary=summary,
        fields=fields,
        key="dos-decisions",
        source="decisions",
    )


def notification_for_top(frame: "Frame", *, summary: str = "") -> Notification:
    """Pure: a `dos top` `Frame` → one `Notification` (the live-status surface).

    `frame` is the output of `dispatch_top.snapshot(...)`; `summary` is the prebuilt
    screen (`dispatch_top.render_frame_text(frame)`). Severity rises with the worst
    lane: any STALLED lane → URGENT, any SPINNING → WARN, else INFO. `fields` are the
    non-FREE lanes (`lane → chip + holder`) plus a recent-verdict tally — the
    glance view. `key` is workspace-stable so the driver streams the status into ONE
    edited message (the `LiveMessage` use-case) instead of a post per tick.

    Duck-typed over `frame.lanes` (each with `.lane`/`.chip`/`.holder`) and
    `frame.verdicts`/`frame.workspace` — no `dispatch_top` import at runtime.
    """
    lanes = tuple(getattr(frame, "lanes", ()) or ())
    # The chip strings carry their state word ("🟢 ADVANCING" / "🟡 SPINNING" /
    # "🔴 STALLED" / "⚪ FREE"); we read the WORD, not the glyph, so a chip-format
    # tweak in dispatch_top does not silently break the severity here.
    def _word(chip: str) -> str:
        parts = str(chip or "").split()
        return parts[-1] if parts else ""

    words = [_word(getattr(s, "chip", "")) for s in lanes]
    advancing = sum(1 for w in words if w == "ADVANCING")
    spinning = sum(1 for w in words if w == "SPINNING")
    stalled = sum(1 for w in words if w == "STALLED")
    free = sum(1 for w in words if w == "FREE")

    if stalled:
        severity = Severity.URGENT
    elif spinning:
        severity = Severity.WARN
    else:
        severity = Severity.INFO

    title = (
        f"fleet: {advancing} advancing · {spinning} spinning · "
        f"{stalled} stalled · {free} free"
    )

    fields: list[tuple[str, str]] = []
    for s in lanes:
        if _word(getattr(s, "chip", "")) == "FREE":
            continue
        holder = getattr(s, "holder", "") or ""
        val = getattr(s, "chip", "")
        if holder:
            val = f"{val}  {holder}"
        fields.append((getattr(s, "lane", "") or "-", str(val)[:300]))
    nverd = len(tuple(getattr(frame, "verdicts", ()) or ()))
    if nverd:
        fields.append(("recent verdicts", str(nverd)))

    return Notification(
        severity=severity,
        title=title,
        summary=summary,
        fields=tuple(fields),
        key=f"dos-top:{getattr(frame, 'workspace', '')}",
        source="top",
    )


def notification_for_pulse(digest: "PulseDigest", *, summary: str = "") -> Notification:
    """Pure: a `pulse.PulseDigest` → one `Notification` (the standing-pulse surface).

    `digest` is the output of `pulse.fold_pulse(...)` — the folded fleet state a cron
    pulse pushes. `summary` is the prebuilt body (`pulse.render_pulse_text(digest)`);
    the caller passes it so this module needs no `pulse` import (the dependency-arrow
    rule, like the decisions/top adapters). The digest already carries the worst-signal
    severity as a string matching `Severity`, so this maps it straight across: a
    STALLED run → URGENT, a pending HUMAN queue / OPEN breaker → WARN, all-clear → INFO.

    `fields` are the folded counts as `(label, value)` glance rows — what a
    Block-Kit/section transport shows at a glance. `key` is workspace-stable so a
    notifier that edits in place keeps ONE pulse message current rather than spamming a
    post per cron tick (the `LiveMessage` use-case the decisions/top adapters share).
    Duck-typed over the digest's fields so a digest stand-in folds too.
    """
    sev_word = str(getattr(digest, "severity", "") or "INFO").upper()
    try:
        severity = Severity(sev_word)
    except ValueError:  # an unexpected word degrades to INFO, never crashes the push
        severity = Severity.INFO

    empty = bool(getattr(digest, "empty", True))
    stalled = int(getattr(digest, "stalled", 0) or 0)
    pending = int(getattr(digest, "pending_human", 0) or 0)
    breaker_open = bool(getattr(digest, "breaker_open", False))

    if empty:
        title = "fleet clear — pulse quiet"
    else:
        bits = []
        if stalled:
            bits.append(f"{stalled} stalled")
        if pending:
            bits.append(f"{pending} need a human")
        if breaker_open:
            bits.append("breaker OPEN")
        title = "pulse: " + (" · ".join(bits) if bits else "something to surface")

    fields: list[tuple[str, str]] = []
    if stalled:
        fields.append(("stalled runs", str(stalled)))
    if pending:
        fields.append(("decisions need a human", str(pending)))
    if breaker_open:
        fields.append(("breaker", "OPEN"))

    return Notification(
        severity=severity,
        title=title,
        summary=summary,
        fields=tuple(fields),
        key="dos-pulse",
        source="pulse",
    )


# ---------------------------------------------------------------------------
# Resolver + discovery — built-ins first, then the `dos.notifiers` plugins. The
# `resolve_judge` / `resolve_dialect` shape; discovery I/O at the call boundary.
# ---------------------------------------------------------------------------


def _discover_entry_point_notifiers(*, _stderr=None) -> list[tuple[str, "Notifier"]]:
    """Every `dos.notifiers` plugin as `(name, notifier)`, sorted by name.

    A plugin that fails to load is SKIPPED with a one-line stderr note rather than
    crashing — the `_discover_entry_point_judges` posture (a broken third-party
    plugin is the operator's to fix, not a kernel fault). Does entry-point I/O, so
    it is a call-boundary helper, never called inside an adapter.
    """
    stderr = _stderr if _stderr is not None else sys.stderr
    out: list[tuple[str, Notifier]] = []
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    try:
        eps = entry_points(group=NOTIFIER_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(NOTIFIER_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash a call
        return out
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            obj = ep.load()
            notifier = obj() if isinstance(obj, type) else obj
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(
                f"warning: notifier plugin {ep.name!r} failed to load ({e}); skipping",
                file=stderr,
            )
            continue
        out.append((ep.name, notifier))
    return out


def _accepted_kwargs(ctor: type, kwargs: dict) -> dict:
    """Filter `kwargs` to the parameters `ctor.__init__` actually accepts.

    The notification CLI builds ONE superset bag (channel/url/token/dry_run/root)
    and hands it to whichever transport was named, so it need not branch per driver.
    But a transport's `__init__` is keyword-only and would raise on an unexpected
    kwarg (`slack` does not take `url`; `webhook` does not take `channel` unless it
    opts in). So we pass only the parameters the constructor declares — unless it
    declares `**kwargs` (a `VAR_KEYWORD` param), in which case it absorbs the rest and
    we forward everything. Pure introspection; no I/O.
    """
    try:
        import inspect

        params = inspect.signature(ctor).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return kwargs
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def resolve_notifier(name: str, *, _stderr=None, **kwargs) -> "Notifier":
    """Resolve a notifier by name: built-ins first, then `dos.notifiers` plugins.

    Built-ins (`null`) resolve FIRST and cannot be shadowed (the trusted-fallback
    guarantee, identical to `resolve_judge`). An unknown name fails LOUD with the
    known list — a typo'd `--notifier` is an operator error, never a silent degrade
    to `null` (which would drop every notification quietly). `kwargs` (e.g.
    `channel=…`, `url=…`, `token=…`, `dry_run=…`) are forwarded to a CONSTRUCTOR-style
    occupant (a `type`), FILTERED to the parameters that constructor accepts (so the
    CLI can hand the same superset to any transport); a pre-built instance ignores
    them.
    """
    if name in _BUILT_IN_NOTIFIERS:
        cls = _BUILT_IN_NOTIFIERS[name]
        accepted = _accepted_kwargs(cls, kwargs)
        return cls(**accepted) if accepted else cls()
    # For discovered plugins we resolve the ENTRY POINT and, if it is a class,
    # construct it with kwargs (so the CLI can pass channel/token). A plugin that
    # exposes a pre-built instance is used as-is.
    stderr = _stderr if _stderr is not None else sys.stderr
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover
        entry_points = None  # type: ignore[assignment]
    found: object | None = None
    if entry_points is not None:
        try:
            eps = entry_points(group=NOTIFIER_ENTRY_POINT_GROUP)
        except TypeError:  # pragma: no cover - py<3.10
            eps = entry_points().get(NOTIFIER_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            eps = []
        for ep in eps:
            if ep.name == name:
                try:
                    found = ep.load()
                except Exception as e:  # pragma: no cover - third-party
                    raise ValueError(
                        f"notifier {name!r} failed to load: {e}"
                    ) from e
                break
    if found is not None:
        if isinstance(found, type):
            accepted = _accepted_kwargs(found, kwargs)
            return found(**accepted)  # type: ignore[return-value]
        return found  # a pre-built instance ignores kwargs
    discovered = [n for n, _ in _discover_entry_point_notifiers(_stderr=stderr)]
    known = sorted(set(_BUILT_IN_NOTIFIERS) | set(discovered))
    raise ValueError(f"unknown notifier {name!r}; known: {', '.join(known)}")


def active_notifiers(*, _stderr=None) -> list[tuple[str, "Notifier"]]:
    """Every resolvable notifier as `(name, notifier)` — built-ins THEN discovered.

    The order `dos doctor` would list. Does entry-point discovery (I/O), so it is a
    call-boundary helper, never called inside an adapter (the `active_judges` rule).
    """
    built: list[tuple[str, Notifier]] = [(n, cls()) for n, cls in _BUILT_IN_NOTIFIERS.items()]
    return built + _discover_entry_point_notifiers(_stderr=_stderr)


def active_notifier_names(*, _stderr=None) -> list[str]:
    """The names of every active notifier (built-in + discovered)."""
    return [name for name, _ in active_notifiers(_stderr=_stderr)]


# ---------------------------------------------------------------------------
# send_safely — the fail-soft wrapper. A send NEVER crashes the producer.
# ---------------------------------------------------------------------------


def send_safely(notifier: "Notifier", note: Notification) -> NotifyResult:
    """Deliver `note` via `notifier`, converting ANY raise to a non-delivered result.

    The fail-soft floor (`LiveMessage._warn`, lifted): a notification is advisory
    telemetry, so a transport that raises (network down, bad token, a buggy plugin)
    must never propagate into the fleet loop that emitted it. A clean `send` returns
    its own `NotifyResult`; a raise becomes `NotifyResult(delivered=False,
    detail="error: …")`. (Contrast `resolve_notifier`, which DOES raise on an
    unknown name — that is a config-time operator error, surfaced before any send.)
    """
    try:
        result = notifier.send(note)
    except Exception as e:  # noqa: BLE001 - advisory telemetry must not crash the producer
        return NotifyResult(delivered=False, detail=f"error: {e}")
    if isinstance(result, NotifyResult):
        return result
    # A misbehaving occupant returned a non-NotifyResult; treat as a soft failure
    # rather than trusting an unknown shape downstream.
    return NotifyResult(delivered=False, detail="notifier returned a non-NotifyResult")
