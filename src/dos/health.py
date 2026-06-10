"""Pre-dispatch lane-health gate — query a lane's *startability* BEFORE a
child launch, and route to `/unstick` / `/replan` instead of burning a child
to rediscover a knowable-at-t0 blocker.

Motivation (the 2026-06-01 incident this module exists for): a `/dispatch-loop`
auto-picked a lane, spent ~$9 and ~40 min launching a full `/dispatch` child,
and only THEN discovered two blockers that were both knowable at second zero —
(1) the lane's last 8 dispatch runs had all failed on the *same* renderer
sidecar-drop (a recurring structural blocker that only `/unstick` resolves), and
(2) the auto-picked lane structurally overlapped a live sibling lease. The loop's
existing breakers (drained-twice, packet-judge, recurring-wedge) all fire
*after* a child has run. This gate fires *before*.

Design — mirrors `dos.gate_classify`:

  * `lane_health(...)` is a **pure function**: facts in (live leases, the lane's
    recent verdict history, the lane tree), a typed `HealthVerdict` out. No I/O,
    so it is replay-tested in isolation.
  * `collect_lane_history(...)` is the thin I/O wrapper: it shells `git log` over
    recent dispatch/dispatch-loop archive commits and parses each into a
    `RunRecord`. The caller (the loop's Step 0, or `dos health` CLI) composes the
    two.

The gate is **advisory-but-actionable**: it never blocks acquisition itself
(that is the arbiter's job); it returns a *route* the loop acts on. A
`route_unstick` means "this lane has been failing the same way — run /unstick
first"; a `route_replan` means "this lane is soak/data-gated — /replan, not
/unstick"; `proceed` means "nothing in the history says don't start."
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum

from dos.lane_overlap import overlap_verdict

# How many recent dispatch archive commits to scan for the lane. 12 covers
# ~a day of an active fleet without walking deep history; tune via the CLI arg.
DEFAULT_HISTORY_WINDOW = 12

# A lane is "recurring-blocked" when at least this many of its recent runs are
# non-shipping failures on the SAME cause key. 3 matches the recurrence floor
# the post-hoc recurring-wedge router uses, so the pre-gate and the post-gate
# agree on what "recurring" means.
RECURRING_THRESHOLD = 3


class HealthAction(str, Enum):
    """What the loop should do with this lane, right now."""
    PROCEED       = "proceed"        # history is clean (or shipping) — launch
    ROUTE_UNSTICK = "route_unstick"  # recurring structural blocker — /unstick first
    ROUTE_REPLAN  = "route_replan"   # soak/data-gated — /replan, not /unstick
    OVERLAP_BLOCK = "overlap_block"  # a live lease's tree collides — pick elsewhere


# Verdict tokens a dispatch/dispatch-loop archive commit can carry. Kept in
# sync with dos.verdicts; duplicated as a frozenset here only for cheap parsing
# (the gate must not depend on the full verdict module to scan a log line).
_SHIPPING = frozenset({"LIVE", "SHIPPED", "SHIPPED-CLEAN"})
_NONSHIP_BLOCKER = frozenset({"ERROR", "WEDGE", "BLOCKED", "BLOCKED-OUTCOME", "STALLED"})
_DRAIN = frozenset({"DRAIN"})

# Causes that route to /replan rather than /unstick (soak/data-gated — no
# structural defect for /unstick to fix). Substring match against the cause
# text the archive commit carries.
_REPLAN_CAUSE_CUES = (
    "soak", "soak-gated", "data-gated", "data gated", "awaiting a live run",
    "drain", "drained",
)

_VERDICT_RE = re.compile(r"verdict=([A-Z][A-Z-]*)")
# A recurrence-count phrase the dispatch archives carry verbatim, e.g.
# "6th-consecutive", "8th recurrence", "5th consecutive". The ordinal is a
# strong recurring signal even within a short window.
_RECURRENCE_RE = re.compile(r"(\d+)(?:st|nd|rd|th)[ -](?:consecutive|recurrence)")

# A /dispatch-loop STOP archive halts the loop and (almost always) hands the
# operator/next-sweep a `/unstick` directive — e.g.
#   "… 0 picks; STOP recurring BLOCKED (APPLY_LANE_BLOCKED_MESH) → /unstick"
#   "… STOP override on recurring APPLY_LANE_POST_UNSTICK_STOP_RESPAWN … → /unstick"
# The STOP is an *operator-visible directive*: it says "do not re-iterate this
# lane until something lands." A loop that respawns the same lane anyway —
# before any operator action or structural commit clears the directive — is the
# POST-STOP-respawn doom-loop (`APPLY_LANE_POST_UNSTICK_STOP_RESPAWN`, logged 9×
# in 24h across the apply/tailor/CD lanes; cost-anchor ~$43 per /unstick cycle).
# `_STOP_RE` detects the STOP token; `_UNSTICK_ROUTE_RE` confirms it routed to
# /unstick (vs a self-healing /replan stamp-drift STOP, which the existing
# recurring-blocker rule already handles).
_STOP_RE = re.compile(r"\bSTOP\b")
_UNSTICK_ROUTE_RE = re.compile(r"/unstick\b")

# The cause_key the POST-STOP-respawn rule emits — kept verbatim equal to the
# reason_class the hand-rolled PRE-SCREEN WEDGE path has been writing into
# archive subjects, so /unstick clusters the pre-gate STOP and the in-the-wild
# respawns under one cause. (Lower-cased to match `BlockedReason` value style.)
POST_STOP_RESPAWN_CAUSE_KEY = "post_stop_respawn_no_operator_action"


@dataclass(frozen=True)
class RunRecord:
    """One recent dispatch/dispatch-loop archive commit, parsed."""
    run_ts: str
    verdict: str            # normalized token (ERROR/WEDGE/DRAIN/SHIPPED/…), "" if none
    cause: str              # free-text cause tail of the commit subject
    recurrence_ordinal: int  # parsed "Nth-consecutive/recurrence", 0 if absent
    subject: str            # the full commit subject (for evidence)

    @property
    def is_shipping(self) -> bool:
        return self.verdict in _SHIPPING

    @property
    def is_blocker(self) -> bool:
        return self.verdict in _NONSHIP_BLOCKER

    @property
    def is_drain(self) -> bool:
        return self.verdict in _DRAIN

    @property
    def is_stop(self) -> bool:
        """True iff this archive is a /dispatch-loop STOP (the loop halted
        itself). Detected on the full subject, not the cause tail, since the
        STOP token sits before the em-dash on loop archives."""
        return bool(_STOP_RE.search(self.subject))

    @property
    def is_stop_with_unstick(self) -> bool:
        """A STOP that routed to /unstick — the operator-visible "do not
        re-iterate this lane until something lands" directive. This is the
        signal the POST-STOP-respawn guard keys on; a STOP that routed only to
        /replan is a stamp-drift halt the recurring-blocker rule already covers,
        so it is deliberately excluded here."""
        return self.is_stop and bool(_UNSTICK_ROUTE_RE.search(self.subject))

    @property
    def is_operator_action(self) -> bool:
        """True iff this archive subject marks a deliberate operator action that
        clears a prior STOP directive — a commit whose subject carries an
        explicit `operator-action:` token. Such a record, newer than a STOP,
        means the directive was answered and the lane may respawn."""
        return "operator-action:" in self.subject.lower()


@dataclass(frozen=True)
class HealthVerdict:
    action: HealthAction
    reason: str
    cause_key: str = ""          # the recurring cause, when action==ROUTE_*
    runs_considered: int = 0
    blocker_runs: int = 0        # how many of those were same-cause blockers
    overlap_lane: str = ""       # the colliding live lease's lane, when OVERLAP_BLOCK
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def should_proceed(self) -> bool:
        return self.action == HealthAction.PROCEED


def _normalize_cause(cause: str) -> str:
    """Collapse a free-text cause tail to a stable key for same-cause counting.

    Deliberately coarse: lower-case, strip the recurrence ordinal and digits,
    squeeze whitespace, keep the first ~8 salient words. Two archive subjects
    describing the same defect ("renderer .prompts.json sidecar drop 6th
    consecutive" vs "renderer-sidecar-drop preflight refuse 8th recurrence")
    must map to the same key, so we key on the stable noun phrase, not the
    ordinal or the exact wording.
    """
    c = cause.lower()
    c = _RECURRENCE_RE.sub("", c)
    c = re.sub(r"\d+", "", c)
    c = re.sub(r"[^a-z._/ -]", " ", c)
    # canonical synonyms — collapse the many phrasings of one recurring cause
    # to a single key so same-cause runs count together (the threshold is
    # per-key). Order matters: most-specific defect first.
    if "sidecar" in c or ".prompts" in c or "prompts.json" in c:
        return "renderer_sidecar_drop"
    if "ship-oracle" in c or "ship_oracle" in c or "false-positive" in c:
        return "ship_oracle_false_positive"
    if "stale" in c and "claim" in c:
        return "stale_claim_false_block"
    if "soak" in c or "data-gated" in c or "data gated" in c:
        return "lane_soak_or_data_gated"
    if "overlap" in c or "collision" in c:
        return "lane_overlap_collision"
    toks = [t for t in re.split(r"[ /._-]+", c) if len(t) > 2][:8]
    return "_".join(toks) if toks else "uncategorized"


def _cause_routes_replan(cause: str) -> bool:
    lc = cause.lower()
    return any(cue in lc for cue in _REPLAN_CAUSE_CUES)


def lane_health(
    lane: str,
    *,
    lane_tree: list[str],
    live_leases: list[dict],
    history: list[RunRecord],
    own_lease_ts: str = "",
    recurring_threshold: int = RECURRING_THRESHOLD,
) -> HealthVerdict:
    """Pure pre-dispatch health decision for `lane`.

    Args:
      lane          — the lane about to be dispatched.
      lane_tree     — that lane's file-glob tree (for the overlap check).
      live_leases   — dicts with at least {lane, lane_kind, tree, loop_ts};
                      the loop's OWN lease (own_lease_ts) is excluded.
      history       — recent RunRecords for this lane, newest first (from
                      `collect_lane_history`).
      own_lease_ts  — this loop's own lease ts, so its own lease never
                      self-blocks the overlap check.
      recurring_threshold — same-cause blocker count that trips ROUTE_*.

    Decision order (first match wins):
      1. OVERLAP_BLOCK — a *foreign* live lease's tree collides with lane_tree
         (via the fixed `overlap_verdict`). Highest priority: starting into a
         real overlap guarantees a mutual wedge.
      2. ROUTE_UNSTICK (post-STOP respawn) — the most recent meaningful lane
         event is a STOP→/unstick directive with no shipping run or explicit
         operator action newer than it. The loop is respawning a lane the
         previous loop halted; a STOP is an operator-visible "do not re-iterate
         until something lands" directive, not a mesh-state the next iteration
         can clear. Trips on the FIRST such respawn (not the threshold) because
         one ignored STOP is already the doom-loop. See POST_STOP_RESPAWN_*.
      3. ROUTE_UNSTICK / ROUTE_REPLAN — the recent history is dominated by the
         SAME-cause non-shipping blocker at/over the threshold. Route by cause:
         soak/data-gated → /replan; structural → /unstick.
      4. PROCEED — anything else (a shipping run in the window, a clean drain,
         mixed causes below threshold, or no history at all).
    """
    # 1. foreign-lease overlap (uses the fixed exact-glob-aware overlap_verdict)
    for lease in live_leases:
        lts = str(lease.get("loop_ts", ""))
        if own_lease_ts and lts == own_lease_ts:
            continue
        llane = str(lease.get("lane", ""))
        if llane == lane:
            continue  # same-lane is the arbiter's concern, not an overlap signal
        ltree = list(lease.get("tree", []) or [])
        if not ltree or not lane_tree:
            continue  # unknown blast radius handled by the arbiter
        ov = overlap_verdict(list(lane_tree), ltree)
        if not ov.admissible:
            return HealthVerdict(
                action=HealthAction.OVERLAP_BLOCK,
                reason=(f"lane {lane!r} tree collides with live lease "
                        f"{llane!r} (loop {lts}): {ov.reason}"),
                overlap_lane=llane,
                runs_considered=len(history),
                evidence=(f"overlap:{llane}:{ov.verdict.value}",),
            )

    # 2. post-STOP respawn — the previous loop halted this lane with a /unstick
    # directive and nothing has cleared it since. Walk newest-first: the first
    # record that is a shipping run OR an explicit operator action means the
    # directive was answered (lane recovered) → fall through. The first record
    # that is a STOP→/unstick, reached before any such clearing event, means the
    # respawn is re-entering an unanswered STOP → route /unstick on this first
    # respawn rather than burning a child to rediscover the same wedge.
    for rec in history:  # newest-first
        if rec.is_shipping or rec.is_operator_action:
            break  # the STOP (if any) was cleared — not a doom-loop respawn
        if rec.is_stop_with_unstick:
            return HealthVerdict(
                action=HealthAction.ROUTE_UNSTICK,
                reason=(
                    f"lane {lane!r} was STOPped with a /unstick directive at "
                    f"{rec.run_ts or 'a recent archive'} and no shipping run or "
                    f"operator action has landed since — respawning re-enters an "
                    f"unanswered STOP. Route /unstick (or take an operator action) "
                    f"before launching a child. STOP subject: {rec.subject[:140]}"
                ),
                cause_key=POST_STOP_RESPAWN_CAUSE_KEY,
                runs_considered=len(history),
                blocker_runs=1,
                evidence=(f"stop:{rec.run_ts}:/unstick",),
            )

    # 3. recurring same-cause blocker in the recent window
    if history:
        # group blocker runs by normalized cause key
        by_cause: dict[str, list[RunRecord]] = {}
        for rec in history:
            if rec.is_blocker:
                by_cause.setdefault(_normalize_cause(rec.cause), []).append(rec)
        if by_cause:
            # dominant cause = the one with the most blocker runs
            cause_key, recs = max(by_cause.items(), key=lambda kv: len(kv[1]))
            # an explicit "Nth-consecutive" ordinal in the window is itself a
            # recurrence signal even if the window only captured a few of them
            max_ordinal = max((r.recurrence_ordinal for r in recs), default=0)
            tripped = len(recs) >= recurring_threshold or max_ordinal >= recurring_threshold
            # a shipping run more recent than every blocker means the lane
            # recovered — do NOT route (the blocker is stale history)
            newest_ship = next((i for i, r in enumerate(history) if r.is_shipping), None)
            newest_blocker = next((i for i, r in enumerate(history) if r.is_blocker), None)
            recovered = (
                newest_ship is not None
                and newest_blocker is not None
                and newest_ship < newest_blocker  # ship is newer (lower index)
            )
            if tripped and not recovered:
                sample_cause = recs[0].cause.strip()
                action = (
                    HealthAction.ROUTE_REPLAN
                    if _cause_routes_replan(sample_cause)
                    else HealthAction.ROUTE_UNSTICK
                )
                route = "replan" if action == HealthAction.ROUTE_REPLAN else "unstick"
                n = max(len(recs), max_ordinal)
                return HealthVerdict(
                    action=action,
                    reason=(f"lane {lane!r} has {n} recent dispatch run(s) "
                            f"blocked on the same cause "
                            f"({cause_key}) — route to /{route} before "
                            f"spending another child launch. Sample: "
                            f"{sample_cause[:120]}"),
                    cause_key=cause_key,
                    runs_considered=len(history),
                    blocker_runs=len(recs),
                    evidence=tuple(f"{r.run_ts}:{r.verdict}" for r in recs[:5]),
                )

    # 3. nothing says don't start
    return HealthVerdict(
        action=HealthAction.PROCEED,
        reason=(f"lane {lane!r} health OK — "
                + (f"{len(history)} recent run(s), no recurring same-cause "
                   "blocker" if history else "no recent dispatch history")),
        runs_considered=len(history),
    )


# ── I/O wrapper: parse recent dispatch archive commits into RunRecords ───────

def parse_archive_subject(subject: str, lane: str) -> RunRecord | None:
    """Parse one `git log --oneline` subject into a RunRecord, or None if it is
    not a dispatch/dispatch-loop archive for `lane`.

    Recognized shapes (both carry `verdict=` or a bracketed outcome):
      `docs/dispatch: archive <ts> — <tag> → verdict=ERROR, child2 …`
      `docs/dispatch-loop: archive <ts> — N iters …, 0 picks shipped (<LANE> lane; … verdict=ERROR …)`

    The lane match is a substring test against the subject (the dispatch-loop
    archives name the lane as `<LANE> lane`; the per-`/dispatch` archives do
    not always carry the lane, so those are matched only when `lane` is the
    empty string — i.e. "all lanes" — see `collect_lane_history`).
    """
    if "archive" not in subject:
        return None
    if "docs/dispatch" not in subject and "docs/dispatch-loop" not in subject:
        return None
    # lane filter: when a specific lane is requested, require it to appear in
    # one of the conventions dispatch archives actually use for the lane name:
    #   - "<lane> lane"           the dispatch-loop archive convention
    #   - "scope <lane>"          a --scope <lane> hand-run / inherited child
    #   - "(<lane>;"              the parenthetical lane tag on some loop archives
    #   - "<LANE>_LANE_..."       the reason_class convention (APPLY_LANE_BLOCKED_MESH,
    #                             TAILOR_LANE_FOCUS_..., CD_LANE_OPERATOR_...). STOP
    #                             archives frequently name the lane ONLY here, so
    #                             without this clause the post-STOP-respawn guard
    #                             would fail to attribute the very respawns it
    #                             exists to catch (the test_real_archive_subject
    #                             regression that pinned this).
    if lane:
        lane_l = lane.lower()
        subj_l = subject.lower()
        reason_class_tag = f"{lane_l}_lane_"
        if (f"{lane_l} lane" not in subj_l
                and f"scope {lane_l}" not in subj_l
                and f"({lane_l};" not in subj_l
                and reason_class_tag not in subj_l):
            return None
    m_ts = re.search(r"archive\s+(\d{8}T\d{6}Z|\d{8}T\d{4}Z)", subject)
    run_ts = m_ts.group(1) if m_ts else ""
    m_v = _VERDICT_RE.search(subject)
    verdict = m_v.group(1) if m_v else ""
    # cause = the tail after the verdict token (or after the em-dash)
    cause = subject
    if m_v:
        cause = subject[m_v.end():].lstrip(" ,—-")
    elif "—" in subject:
        cause = subject.split("—", 1)[1].strip()
    m_r = _RECURRENCE_RE.search(subject)
    ordinal = int(m_r.group(1)) if m_r else 0
    return RunRecord(
        run_ts=run_ts, verdict=verdict, cause=cause,
        recurrence_ordinal=ordinal, subject=subject,
    )


def collect_lane_history(
    lane: str,
    *,
    git_log_lines: list[str],
    window: int = DEFAULT_HISTORY_WINDOW,
) -> list[RunRecord]:
    """Parse `git log --oneline` output into recent RunRecords for `lane`.

    `git_log_lines` is the raw `git log --oneline -<N> -- docs/_dispatch_loops/
    docs/_chained_runs/` output (one subject per line, newest first). Pass an
    empty `lane` to collect across ALL lanes (the per-`/dispatch` archives that
    do not name a lane are then included). Newest-first order is preserved.
    """
    out: list[RunRecord] = []
    for line in git_log_lines:
        line = line.strip()
        if not line:
            continue
        # drop the leading short-sha from `--oneline`
        subject = line.split(" ", 1)[1] if " " in line else line
        rec = parse_archive_subject(subject, lane)
        if rec is not None:
            out.append(rec)
        if len(out) >= window:
            break
    return out


# ── CLI layer (the I/O composition: git log + leases → health JSON) ──────────

# Archive commits live under these dirs; the git-log pathspec scopes the scan.
_ARCHIVE_PATHSPEC = ("docs/_dispatch_loops/", "docs/_chained_runs/")


def _git_log_subjects(scan_depth: int) -> list[str]:
    """`git log --oneline -<scan_depth> -- <archive dirs>` → subject lines.

    Best-effort: a git failure (no repo, detached, etc.) yields [] so the gate
    degrades to "no history → proceed" rather than crashing the loop's Step 0.
    """
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", f"-{scan_depth}", "--", *_ARCHIVE_PATHSPEC],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def check(
    lane: str,
    *,
    lane_tree: list[str],
    live_leases: list[dict],
    own_lease_ts: str = "",
    window: int = DEFAULT_HISTORY_WINDOW,
    scan_depth: int | None = None,
    git_log_lines: list[str] | None = None,
) -> HealthVerdict:
    """One-call composition: gather the lane's recent history (via git log
    unless `git_log_lines` is supplied for testing) and run `lane_health`.

    `scan_depth` is how many commits to walk (defaults to ~4× the window so a
    lane-filtered scan still finds `window` matches); `window` caps how many
    matched records feed the decision.
    """
    if git_log_lines is None:
        git_log_lines = _git_log_subjects(scan_depth or max(40, window * 4))
    history = collect_lane_history(lane, git_log_lines=git_log_lines, window=window)
    return lane_health(
        lane, lane_tree=lane_tree, live_leases=live_leases,
        history=history, own_lease_ts=own_lease_ts,
    )


def verdict_to_dict(v: HealthVerdict) -> dict:
    return {
        "action": v.action.value,
        "should_proceed": v.should_proceed,
        "reason": v.reason,
        "cause_key": v.cause_key,
        "runs_considered": v.runs_considered,
        "blocker_runs": v.blocker_runs,
        "overlap_lane": v.overlap_lane,
        "evidence": list(v.evidence),
    }


def cmd_check(args: argparse.Namespace) -> int:
    """`dos health --lane TM --tree '...' --leases-json '...'` → health JSON.

    Leases + the lane tree are passed IN (the live-lease registry and the
    lane→tree resolver are host-app concerns — the job side supplies them); the
    history is gathered here via git log. Exit code mirrors the action so a
    shell caller can branch without parsing JSON: 0 PROCEED, 3 ROUTE_UNSTICK,
    4 ROUTE_REPLAN, 6 OVERLAP_BLOCK.
    """
    lane_tree = [t for t in (args.tree or "").split(",") if t.strip()]
    live_leases: list[dict] = []
    if args.leases_json:
        try:
            live_leases = json.loads(args.leases_json)
        except (ValueError, TypeError):
            live_leases = []
    git_lines = None
    if args.git_log_file:
        with open(args.git_log_file, encoding="utf-8") as fh:
            git_lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    v = check(
        args.lane, lane_tree=lane_tree, live_leases=live_leases,
        own_lease_ts=args.own_lease_ts or "", window=args.window,
        git_log_lines=git_lines,
    )
    print(json.dumps(verdict_to_dict(v), indent=2, sort_keys=True))
    return {
        HealthAction.PROCEED: 0,
        HealthAction.ROUTE_UNSTICK: 3,
        HealthAction.ROUTE_REPLAN: 4,
        HealthAction.OVERLAP_BLOCK: 6,
    }[v.action]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dos-health",
        description="Pre-dispatch lane-health gate — query a lane's startability.",
    )
    p.add_argument("--lane", required=True, help="the lane about to be dispatched")
    p.add_argument("--tree", default="",
                   help="comma-separated file-glob tree for the lane (overlap check)")
    p.add_argument("--leases-json", default="",
                   help="JSON array of live leases [{lane,lane_kind,tree,loop_ts}]")
    p.add_argument("--own-lease-ts", default="",
                   help="this loop's own lease ts (never self-blocks)")
    p.add_argument("--window", type=int, default=DEFAULT_HISTORY_WINDOW,
                   help=f"matched records to consider (default {DEFAULT_HISTORY_WINDOW})")
    p.add_argument("--git-log-file", default="",
                   help="read git-log subjects from a file instead of running git (testing)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return cmd_check(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
