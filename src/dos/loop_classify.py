"""The /dispatch stamp grammar — pure text→verdict classification.

KIND: kernel (pure) · owner: job `docs/62b_dispatch-loop-driver-extraction-plan.md`
(MQ3Y P1) · lifted out of `scripts/dispatch_loop_iter_driver.py` 2026-08-07.

A `/dispatch` run stamps its outcome into ONE line — the Step 9 archive commit
subject (``docs/dispatch: archive <ts> — <lane> → <N/T> picks shipped
(verdict=LIVE)``) — plus, on the co-cause path, a ``reason_class=`` token its
chained-run README carries. Every dispatch-family consumer (the `/dispatch-loop`
per-iteration driver, the loop status renderer's ``--timeline``/``--scoreboard``)
has to read that grammar, and each carried its own regex copy. Two copies of one
grammar drift: the driver's ``verdict=`` pattern requires the archive prefix and
is uppercase-only, the status renderer's is bare and case-insensitive. This
module is the ONE versioned home for that grammar so a consumer imports it
instead of re-deriving it.

PURE — no I/O, no clock, no env, no subprocess, no filesystem. The two impure
facts the classifier needs are reduced to a scalar record at the caller's edge
and injected:

  * ``RateLimitFacts`` — the host's rate-limit classifier's reading of the
    terminal ``result`` envelope (a usage rejection is an infra signal that
    outranks any verdict the child self-reports).
  * ``SidecarFacts`` — the child's TYPED result-envelope
    (``result_envelopes/dispatch.verdict.json``), the data channel that beats the
    diagnostic channel when present.

One-way dependency arrow: this module imports only stdlib + sibling ``dos``
vocabulary (``dos.tokens``, ``dos.provider_limit``). It never imports back into a
host's ``scripts.*`` — pinned by ``tests/test_loop_classify.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from dos.provider_limit import from_rate_limit_kind, policy_for
from dos.tokens import GateVerdict, normalize_token

# The interim-exit threshold (minutes): a short run with NO archive line is a
# killed-mid-wait INTERIM (→ UNCLEAR); a short run WITH an archive line is a
# legitimate fast GATE. Mirrors the /dispatch-loop SKILL.md Step 3 interim guard.
# The classifier below now decides interim STRUCTURALLY (no concrete archive ts →
# INTERIM regardless of duration — see `classify_outcome_token` §3), so this is
# the documented threshold the guard was sized against, not a live branch.
INTERIM_MAX_MIN = 3.0

# The literal /dispatch Step 9 archive subject prefix.
ARCHIVE_PREFIX = "docs/dispatch: archive"
# The chained-run ts on the archive subject (`docs/dispatch: archive <ts> …`).
ARCHIVE_TS_RE = re.compile(r"docs/dispatch: archive (\d{8}T\d{4,6}Z)")
# The verdict=<X> token on the archive subject (QWB8).
VERDICT_RE = re.compile(r"docs/dispatch: archive .*?verdict=([A-Z][A-Z-]*)")
# The structured drain/wedge reason_class the child1 /next-up computed
# (reason_class=LANE_ALL_INFLIGHT_OR_DEFERRED, LANE_ALL_SHIPPED_..._STALE_STAMP, …).
# It distinguishes a DRAINED lane (route /replan) from a real wedge (route /unstick)
# — but the /dispatch archive subject historically dropped it, flattening the drain
# to "child2 skipped" prose so unstick_audit.classify_cause fell through to the
# generic gate_wedge_unspecified bucket and surfaced a fake recurring blocker
# (the 2026-06-05 iter-1 stall; job finding #441). Carry it into the Outcome cell so
# the cause survives to the next sweep. UNDERSCORE-spelled, UPPER-or-mixed case.
REASON_CLASS_RE = re.compile(r"reason_class=([A-Za-z][A-Za-z0-9_]*)")
# The chained-run README's human-rendered form of the same token
# (`- Reason class: LANE_ALL_INFLIGHT_OR_DEFERRED`). child1 /next-up writes its
# structured verdict into the chained README's body, NOT into the parent
# /dispatch archive subject the parent narrates — so on the co-cause path
# (in-flight soft-claim + packet schema-drift) the parent's terminal `result`
# text and run.log carry NO `reason_class=` token, but the chained README does
# (job finding #513). `REASON_CLASS_RE` matches the inline `reason_class=X` the
# README also carries; this matches the rendered `Reason class: X` bullet so the
# backfill works whichever form the chained child wrote.
README_REASON_CLASS_RE = re.compile(
    r"[Rr]eason[ _]class[:=]\s*([A-Za-z][A-Za-z0-9_]*)")
# A child2-skipped subject with no verdict= token is a pre-QWB8 GATE → DRAIN.
CHILD2_SKIPPED_RE = re.compile(r"docs/dispatch: archive .+child2 skipped")
# A child2-skipped subject where the PARENT explicitly recommended /replan
# ("child2 skipped (/replan recommended)" / "(replan recommended)") is the
# parked-child DRAIN signal (job finding #505): the parent /dispatch authored a
# /replan recommendation, so a BLOCKED that carries no other reason_class is a
# refill condition, NOT a structural wedge. Narrower than CHILD2_SKIPPED_RE on
# purpose — a bare "child2 skipped." with no /replan recommendation stays
# ambiguous and keeps the gate_wedge_unspecified self-label.
CHILD2_SKIPPED_REPLAN_RE = re.compile(
    r"child2 skipped[^)]*\(/?replan recommended", re.IGNORECASE)
# A "N/T picks shipped" subject with no verdict= token is a pre-QWB8 SHIPPED.
PICKS_SHIPPED_RE = re.compile(r"docs/dispatch: archive .+ \d+/\d+ picks shipped")
# The fanout run-ts /dispatch's child2 archived (parsed from the same subject).
FANOUT_TS_RE = re.compile(r"docs/_fanout_runs/(\d{8}T\d{4,6}Z)")
# The chained-run dir id mentioned anywhere in the parent log (`docs/_chained_runs/<ts>`)
# — used to disambiguate WHICH owned child2 was THIS iteration's when several exist.
RUNS_DIR_TS_RE = re.compile(r"docs/_chained_runs/(\d{8}T\d{4,6}Z)")

# The one rate-limit kind that is a TRANSIENT overload rather than a usage wall.
# Compared as the upstream classifier's enum VALUE (a plain str) so this kernel
# imports nothing from the host's `rate_limit_classify` — the same
# provider-invariance discipline `dos.provider_limit` follows.
KIND_OVERLOADED = "OVERLOADED"


@dataclass(frozen=True)
class RateLimitFacts:
    """The host rate-limit classifier's reading of a terminal `result` envelope.

    `kind` is the upstream classifier's enum VALUE (`RATE_LIMITED` /
    `OVERLOADED` / `CREDIT_LOW` / `NONE`) as a plain string. `reset_at` is the
    ISO stamp the window reopens at, already resolved by the adapter (prose
    regex first, structured `rate_limit_event.resetsAt` fallback second) — the
    kernel never re-parses a log for it.
    """

    hit: bool
    kind: str = ""
    reason: str = ""
    reset_at: Optional[str] = None


@dataclass(frozen=True)
class SidecarFacts:
    """The child's TYPED result envelope, reduced to the three fields the
    grammar reads. `is_gate` is True only when `verdict` is a complete canonical
    gate verdict — a sidecar that carries an `exit_reason` but no gate (a
    /replan) is NOT decisive and falls through to the prose path."""

    is_gate: bool
    verdict: Optional[str] = None
    ship_count: Optional[int] = None


def is_terminal_text(result_text: str) -> bool:
    """True iff a `result` envelope's text carries a TERMINAL outcome marker —
    the same marker the verdict grep keys on. A run.log accumulates interim and
    heartbeat `result` lines, so the terminal envelope is selected by CONTENT,
    never by position."""
    r = (result_text or "").lower()
    return "verdict=" in r or "docs/dispatch: archive" in r


def is_gate_verdict(token: str) -> bool:
    """True iff `token` is a complete GATE verdict the loop branches on
    (LIVE/DRAIN/STALE-STAMP/BLOCKED/RACE — the `GateVerdict` enum). A truncated
    streaming fragment like `BLO` fails `GateVerdict()` and is rejected."""
    try:
        GateVerdict(token)
        return True
    except Exception:
        return False


def classify_outcome_token(
    log_text: str, terminal: dict, dur_min: float, *,
    rate_limit: Optional[RateLimitFacts] = None,
    sidecar: Optional[SidecarFacts] = None,
) -> tuple[str, dict]:
    """Classify one iteration's exit into the Step-3 token string + a detail dict.

    Returns one of: ``SHIPPED verdict=LIVE``, ``GATE verdict=<X>``, ``INTERIM``,
    ``UNCLEAR``, ``RATE_LIMITED kind=<X>``, ``OVERLOADED kind=OVERLOADED`` — the
    same surface the SKILL.md Step 3 grep prints. Detection order matches the
    prose exactly: RATE_LIMITED/OVERLOADED is checked FIRST (a usage rejection is
    not a crash and not a drained backlog), then the child's typed result-envelope
    sidecar if it wrote one, then the structural verdict=<X> prose extract, then
    the INTERIM / pre-QWB8 fallbacks.

    `rate_limit` and `sidecar` are the two facts the adapter gathers at the I/O
    edge and injects. Omitting `sidecar` is exactly today's no-sidecar behavior:
    the prose logic owns the call. Omitting `rate_limit` means "no usage signal".
    The downstream reconcile/oracle cross-check in the host's `classify_iter`
    still runs and still overrides on `VERDICT-LIES`, so a child that
    mis-self-reports is caught by git ancestry exactly as before.
    """
    detail: dict = {}
    # 1. Rate-limit / overload FIRST (off the terminal envelope, is_error-agnostic).
    #    A usage rejection is an infra signal that OUTRANKS any verdict the child
    #    self-reports — the child may have written a stale sidecar before the
    #    rate-limit hit, so the typed channel is consulted only AFTER this.
    if rate_limit is not None and rate_limit.hit:
        detail["rate_limit_kind"] = rate_limit.kind
        detail["rate_limit_reason"] = rate_limit.reason
        detail["reset_at"] = rate_limit.reset_at
        # PLC1: stamp the canonical dos.provider_limit category + its retry
        # policy into the detail (additive — the returned TOKEN is unchanged, so
        # the proven OutcomeKind mapping downstream is untouched). This is the
        # shared-vocabulary tag every dispatch-family consumer can read instead
        # of re-deriving transient-vs-usage from the raw kind.
        cat = from_rate_limit_kind(rate_limit.kind)
        detail["provider_limit"] = cat.value
        detail["provider_limit_retryable"] = policy_for(cat).retryable_same_iter
        if rate_limit.kind == KIND_OVERLOADED:
            return f"OVERLOADED kind={rate_limit.kind}", detail
        # CREDIT_LOW maps to RATE_LIMITED (operator-action-required, same stop policy).
        return f"RATE_LIMITED kind={rate_limit.kind}", detail

    # 1.25. Structured reason_class (drain-vs-wedge discriminator) — extracted ONCE
    #     here, BEFORE the sidecar/prose verdict channels, off the same texts, so the
    #     GATE Outcome cell can name WHY it blocked regardless of which channel the
    #     verdict came from. LANE_ALL_INFLIGHT_OR_DEFERRED → a DRAINED lane (route
    #     /replan), distinct from a real wedge (route /unstick). Historically dropped
    #     from the /dispatch archive subject, which flattened the drain to "child2
    #     skipped" prose → unstick_audit.classify_cause fell through to the generic
    #     gate_wedge_unspecified bucket → fake recurring blocker (job finding #441, the
    #     2026-06-05 iter-1 stall). Best-effort: a cell with no token simply omits it.
    for scan in (terminal.get("result") or "", log_text):
        rc = REASON_CLASS_RE.search(scan)
        if rc:
            detail["reason_class"] = rc.group(1)
            break

    # 1.5. Typed result-envelope sidecar (PREFERRED over the prose grep). When the
    #      child wrote `result_envelopes/dispatch.verdict.json` with a complete
    #      canonical gate verdict, trust it — it is data the child emitted on
    #      purpose, not a token grepped out of a stream-json prose blob that may
    #      quote another run / carry a truncated fragment. Same verdict→token
    #      mapping as the prose path (LIVE→SHIPPED, else GATE). A sidecar present
    #      but WITHOUT a gate verdict (e.g. a /replan that wrote exit_reason only)
    #      is NOT decisive here — fall through so the existing replan/INTERIM
    #      handling owns those. `detail["verdict_source"]` records the channel.
    if sidecar is not None and sidecar.is_gate:
        verdict = sidecar.verdict
        detail["verdict"] = verdict
        detail["verdict_source"] = "sidecar"
        if sidecar.ship_count is not None:
            detail["ship_count"] = sidecar.ship_count
        if verdict == GateVerdict.LIVE:
            return "SHIPPED verdict=LIVE", detail
        return f"GATE verdict={verdict}", detail

    # 2. QWB8 structural verdict=<X> token off the archive subject. Scan ALL
    #    `verdict=` matches and pick the first that is a valid GateVerdict member —
    #    a stream-json `run.log` can carry a TRUNCATED partial-message fragment
    #    (e.g. `verdict=BLO` from a mid-stream `--include-partial-messages` chunk)
    #    alongside the complete `verdict=BLOCKED` subject. A naive `.search()`
    #    grabs whichever is first and feeds `GateVerdict('BLO')` → ValueError. The
    #    terminal envelope's own `result` text (complete) is preferred, with the
    #    raw log as fallback. (Found by the real-data smoke-test on run
    #    20260530T092719Z; see the job repo's dispatch-loop HISTORY.md "Step 3
    #    token grep".)
    #
    #    CONCRETE-TS GUARD (job FQ-617, 2026-06-13): a real `/dispatch` archive
    #    subject always names a CONCRETE chained-run ts (`docs/dispatch: archive
    #    20260612T194127Z …`). Two illegitimate sources carry a matchable
    #    `docs/dispatch: archive …verdict=X` substring with NO concrete ts:
    #      (a) the SKILL.md *template/example* lines the child echoes into its
    #          stream-json (`docs/dispatch: archive <UTC-ts> — <packet-tag> →
    #          <N/T> picks shipped (verdict=LIVE)`) — the literal `<UTC-ts>`
    #          placeholder, every example verdict including LIVE;
    #      (b) a parent's own narration that quotes child1 /next-up's GATE verdict
    #          (`… (1 BRX depth pick, verdict=LIVE)`) — a lane-pickable signal,
    #          NOT a ship.
    #    Both manufactured a false `SHIPPED verdict=LIVE` head off a parked-parent
    #    iteration (run 20260612T192217Z: terminal result was "Child2 running
    #    detached — waiting", child2 never archived), which then folded to
    #    PENDING_WITNESS → UNCLEAR/SHIP_CLAIM_UNWITNESSED and charged ~$6 to the
    #    UNCLEAR bucket — the #1 dispatch-loop UNCLEAR driver (5 loops, ~$30/day).
    #    ⚓ trustworthy-fanout-ships / structure-not-content: trust the verdict ONLY
    #    when the matched archive span names a concrete ts. The template/placeholder
    #    and the inline next-up quote both fail `ARCHIVE_TS_RE` on their span, so
    #    they are skipped; every genuine archive subject names a real ts and passes.
    #
    #    `any_archive` is the SAME concrete-ts gate, hoisted: the bare
    #    `ARCHIVE_PREFIX in log_text` test it used to be is ALSO poisoned by the
    #    template lines (the SKILL.md prose carries `docs/dispatch: archive
    #    <UTC-ts> …` literally), which made the pre-QWB8 `child2 skipped` /
    #    `picks shipped` fallbacks below fire on template text and the INTERIM
    #    fall-through unreachable. Require a CONCRETE archive ts so "did the parent
    #    archive a real outcome" is answered by structure, not by the presence of
    #    the prose template (run 20260612T192217Z: no concrete archive line at all,
    #    so this is correctly INTERIM, not a template-manufactured GATE).
    any_archive = bool(ARCHIVE_TS_RE.search(terminal.get("result") or "")) \
        or bool(ARCHIVE_TS_RE.search(log_text))
    verdict = None
    for scan_text in (terminal.get("result") or "", log_text):
        for m in VERDICT_RE.finditer(scan_text):
            # The matched span runs from `docs/dispatch: archive` to `verdict=X`;
            # a real archive subject carries a concrete `\d{8}T\d{4,6}Z` ts inside
            # it, the `<UTC-ts>` template + the inline next-up quote do not.
            if not ARCHIVE_TS_RE.search(m.group(0)):
                continue
            norm = normalize_token(m.group(1))
            # Validate against the GateVerdict enum the host will construct
            # (LIVE/DRAIN/STALE-STAMP/BLOCKED/RACE) — NOT the broader
            # archive-token set, which omits RACE and includes outcome-only
            # tokens. A truncated `BLO` fails GateVerdict() and is skipped.
            if norm and is_gate_verdict(norm):
                verdict = norm
                break
        if verdict:
            break
    if verdict is None and any_archive:
        # An archive line exists but no COMPLETE verdict token parsed — a
        # pre-QWB8 build or a truncated-only capture. Fall through to the
        # child2-skipped / picks-shipped pre-QWB8 fallbacks below.
        pass
    elif verdict is not None:
        detail["verdict"] = verdict
        detail["verdict_source"] = "prose"
        if verdict == GateVerdict.LIVE:
            return "SHIPPED verdict=LIVE", detail
        return f"GATE verdict={verdict}", detail

    # 3. No verdict= token. Interim (killed mid-wait) vs pre-QWB8 fallbacks.
    # FBR2 (parked-parent honesty): the pre-QWB8 fallbacks (`child2 skipped` /
    # `picks shipped` prose) are checked FIRST — they are authoritative
    # parent-authored signals and must win over the interim default below.
    # GATED ON `any_archive` (the concrete-ts gate, job FQ-617): both regexes carry
    # the `docs/dispatch: archive` prefix, which the SKILL.md template prose ALSO
    # carries with a `<UTC-ts>` placeholder — so without this gate a parked-parent
    # log (no real archive line) matched the template's `… child2 skipped …` text
    # and mis-classified GATE DRAIN instead of INTERIM. A real pre-QWB8 archive
    # names a concrete ts, so `any_archive` is True and these still fire for it.
    if any_archive and CHILD2_SKIPPED_RE.search(log_text):
        detail["verdict"] = "DRAIN"
        return "GATE verdict=DRAIN", detail  # pre-QWB8 conservative default
    if any_archive and PICKS_SHIPPED_RE.search(log_text):
        detail["verdict"] = "LIVE"
        return "SHIPPED verdict=LIVE", detail
    # No archive line + no verdict token + no typed sidecar (the sidecar branch 1.5
    # already returned if present) + no pre-QWB8 prose ⇒ the parent NEVER reached
    # its Step-9 Outcome: a PARKED parent with no envelope. Classify INTERIM
    # REGARDLESS of duration. The old `dur_min < INTERIM_MAX_MIN` (3-min) guard was
    # sized for the original short-death case, but keep-alive + the FBR1
    # grandchild-barrier re-arm now legitimately push a parked parent WELL past 3
    # min before its -p turn ends (observed: run 20260609T202551Z parked at 7.7 min
    # — child2 /fanout died at the grandchild barrier when its foreground hold hit
    # the 10-min Bash cap). Such a run carried no archive line and no verdict token,
    # so the >3-min fall-through mislabelled it `UNCLEAR` → (via the downstream
    # gate_wedge_unspecified self-label) a causeless GATE BLOCKED → a hard FQ-510
    # `blocked-redispatch-invariant` STOP over LIVE/recoverable work. INTERIM →
    # OutcomeKind.UNCLEAR → decide() adopt-waits / re-dispatches (bounded by
    # max_adopt_wait + the consecutive_unclear breaker), and the FQ-509 ancestry
    # lift still upgrades INTERIM→SHIPPED when the picks DID land. So this only
    # changes the no-evidence-either-way parked case from "false hard STOP" to
    # "recoverable, bounded re-dispatch". A short run WITH an archive line stays a
    # legitimate fast GATE (handled above — this branch requires `not any_archive`).
    if not any_archive:
        detail["interim_reason"] = "parked-no-envelope"
        return "INTERIM", detail
    return "UNCLEAR", detail
