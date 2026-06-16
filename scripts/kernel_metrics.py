#!/usr/bin/env python3
"""kernel_metrics — fold THIS repo's own DOS artifacts into headline rows.

The scoreboard charts (`scoreboard_charts.py`) draw what the kernel found in
OTHER people's repos. This module is the mirror: it folds the data the kernel
generated about ITSELF — the local `.dos/` journals every `dos` hook and
syscall writes as it runs — into a few small dicts the kernel-data charts
(`kernel_charts.py`) turn into pictures.

Three surfaces, three folds, all from files the kernel wrote with no human in
the loop:

  * **`fold_observations()`** — `.dos/metrics/observations.jsonl`, the per-call
    log the trust hook writes for EVERY tool call it adjudicates: the outcome
    (passthrough / warn / deny / block / …), the rung it had to check, the
    latency. This is the "disbelief funnel": tens of thousands of calls
    watched, almost all passed, a real few stopped.
  * **`fold_lane_journal()`** — `.dos/lane-journal.jsonl`, the admission/lease
    journal: every ENFORCE decision, the intervention (WARN / BLOCK), the
    refusal reason class, and — for the SELF_MODIFY refusals — which kernel
    module the refusal NAMED. This is the "self-modify wall": agents reaching
    for the very code adjudicating them, and being refused.
  * **`fold_verdict_journal()`** — `.dos/verdict-journal.jsonl`, the memory
    admit/recall trust verdicts: of the memories the kernel recalled, how many
    re-verified as still TRUE against git + the working tree, how many were
    unverifiable, how many had gone STALE.

Design contract (the same one the scoreboard generators keep):

  * **No number is ever authored in a chart.** Every count a chart draws comes
    from one of these folds, so a chart can never disagree with the data. The
    journals GROW as the kernel keeps running, so a regenerated chart simply
    shows the newer, larger truth — never a stale hardcoded figure.
  * **Stdlib only, dev tooling.** Lives under `scripts/`; nothing under
    `src/dos/` imports it — the one-way arrow the scoreboard generators follow.
  * **Fail-soft on a missing/partial file.** A surface with no journal yet
    folds to zeros (an empty workspace is a valid zero-activity fact, not an
    error), so the generator degrades to drawing nothing rather than crashing.

Run it directly to print every folded surface:

    python scripts/kernel_metrics.py
"""
from __future__ import annotations

import collections
import glob
import json
import re
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOS_DIR = REPO / ".dos"
SCOREBOARD = REPO / "docs" / "scoreboard"

# The outcomes the disbelief funnel draws as named stream segments. Anything the
# sensor emits that is NOT in this set is folded into one honest "other" bucket
# (so the bar claims only segments the sensor witnesses, never an invented one).
# "stopped" = the three INTERVENTION outcomes; override-admit lets a call
# THROUGH, so it is deliberately excluded from the stopped count.
STREAM_OUTCOMES = ("passthrough", "delegate", "warn", "deny", "block")
STOPPED_OUTCOMES = ("warn", "deny", "block")

# The src/dos/<name>.py token a SELF_MODIFY refusal names — the module the
# guard quotes as the one it is refusing to let the agent rewrite.
_MODULE_RE = re.compile(r"src/dos/([A-Za-z0-9_]+)\.py")


def _iter_jsonl(path: Path):
    """Yield each parseable JSON object from a .jsonl file; skip junk lines."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (ValueError, TypeError):
            continue


# ---------------------------------------------------------------------------
# surface A — the per-call adjudication log (the disbelief funnel).
# ---------------------------------------------------------------------------


def fold_observations(path: Path | None = None) -> dict:
    """Fold the hook's per-call observation log into outcome/verb/latency rows.

    Returns {total, outcomes, verbs, rungs, latency, stopped} where `outcomes`
    is the full Counter (every outcome the sensor emitted), `stopped` is the
    warn+deny+block subtotal, and `latency` maps each verb to {n, p50, p99}.
    A missing file folds to all-zeros (a valid no-activity fact).
    """
    p = path or (DOS_DIR / "metrics" / "observations.jsonl")
    oc: collections.Counter = collections.Counter()
    verb: collections.Counter = collections.Counter()
    rung: collections.Counter = collections.Counter()
    lat: dict[str, list] = collections.defaultdict(list)
    total = 0
    for o in _iter_jsonl(p):
        total += 1
        oc[o.get("outcome", "?")] += 1
        verb[o.get("verb", "?")] += 1
        rung[o.get("rung", "none")] += 1
        lv = o.get("latency_ms")
        if isinstance(lv, (int, float)):
            lat[o.get("verb", "?")].append(float(lv))
    latrows = {}
    for v, xs in lat.items():
        xs2 = sorted(xs)
        latrows[v] = dict(
            n=len(xs2),
            p50=round(statistics.median(xs2), 3),
            p99=round(xs2[max(int(len(xs2) * 0.99) - 1, 0)], 3),
        )
    stopped = sum(oc.get(k, 0) for k in STOPPED_OUTCOMES)
    return dict(total=total, outcomes=dict(oc), verbs=dict(verb),
                rungs=dict(rung), latency=latrows, stopped=stopped)


# ---------------------------------------------------------------------------
# surface B — the admission/lease journal (the self-modify wall).
# ---------------------------------------------------------------------------


def fold_lane_journal(path: Path | None = None) -> dict:
    """Fold the admission journal into the self-modify-refusal rows.

    Returns {n_entries, ops, interventions, withheld, reasons, self_modify}
    where `self_modify` is {total, block, warn, got_through, by_module} and
    `by_module` is a sorted (name, count) list of which kernel module each
    SELF_MODIFY refusal named. A missing file folds to zeros.
    """
    p = path or (DOS_DIR / "lane-journal.jsonl")
    op: collections.Counter = collections.Counter()
    iv: collections.Counter = collections.Counter()
    rc: collections.Counter = collections.Counter()
    by_module: collections.Counter = collections.Counter()
    n_entries = withheld = 0
    sm_total = sm_block = sm_warn = sm_through = 0
    for o in _iter_jsonl(p):
        n_entries += 1
        op[o.get("op", "?")] += 1
        i = o.get("intervention")
        if i:
            iv[i] += 1
        if o.get("withheld"):
            withheld += 1
        r = o.get("reason_class")
        if r:
            rc[r] += 1
        if r == "SELF_MODIFY":
            sm_total += 1
            if i == "BLOCK":
                sm_block += 1
            elif i == "WARN":
                sm_warn += 1
            else:
                sm_through += 1
            m = _MODULE_RE.search(o.get("reason", "") or "")
            if m:
                by_module[m.group(1) + ".py"] += 1
    return dict(
        n_entries=n_entries,
        ops=dict(op.most_common()),
        interventions=dict(iv.most_common()),
        withheld=withheld,
        reasons=dict(rc.most_common()),
        self_modify=dict(
            total=sm_total, block=sm_block, warn=sm_warn,
            got_through=sm_through,
            by_module=by_module.most_common(6),
        ),
        enforce_block=iv.get("BLOCK", 0),
        enforce_warn=iv.get("WARN", 0),
    )


# ---------------------------------------------------------------------------
# surface C — the memory trust journal (re-checking its own notes).
# ---------------------------------------------------------------------------


def fold_verdict_journal(path: Path | None = None) -> dict:
    """Fold the verdict journal into memory recall/admit verdict counts.

    Returns {total, recall, admit} where `recall` maps each RECALL_* verdict to
    its count (the denominator for "memories re-checked") and `admit` maps each
    ADMIT_* verdict. A missing file folds to empty dicts.
    """
    recall: collections.Counter = collections.Counter()
    admit: collections.Counter = collections.Counter()
    total = 0
    p = path or (DOS_DIR / "verdict-journal.jsonl")
    for o in _iter_jsonl(p):
        total += 1
        sc = o.get("syscall")
        vd = o.get("verdict", "?")
        if sc == "memory_recall":
            recall[vd] += 1
        elif sc == "memory_admit":
            admit[vd] += 1
    return dict(total=total, recall=dict(recall), admit=dict(admit))


# ---------------------------------------------------------------------------
# surface D — the scoreboard rollup (reused for the composite footer line).
# ---------------------------------------------------------------------------


def fold_scoreboard(root: Path | None = None) -> dict:
    """Fold the committed per-repo sweep.json files into set-wide totals.

    A thin, dependency-free fold of the same data `scoreboard_rollup.py` reads,
    used only for the one-line trust headline under the kernel charts. Returns
    {repos, commits, attributed, checkable, witnessed, unwitnessed}.
    """
    base = root or SCOREBOARD
    repos = commits = attributed = 0
    checkable = witnessed = unwitnessed = 0
    for p in sorted(glob.glob(str(base / "*" / "*" / "sweep.json"))):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        repos += 1
        commits += int(d.get("commits_scanned", 0) or 0)
        attributed += int(d.get("attributed_commits", 0) or 0)
        s = d.get("summary", {}) or {}
        checkable += int(s.get("checkable", 0) or 0)
        witnessed += int(s.get("witnessed", 0) or 0)
        unwitnessed += int(s.get("unwitnessed", 0) or 0)
    return dict(repos=repos, commits=commits, attributed=attributed,
                checkable=checkable, witnessed=witnessed,
                unwitnessed=unwitnessed)


def fold_all() -> dict:
    """All four surfaces in one call — what the generator hands the charts."""
    return dict(
        observations=fold_observations(),
        lane=fold_lane_journal(),
        verdict=fold_verdict_journal(),
        scoreboard=fold_scoreboard(),
    )


if __name__ == "__main__":
    import pprint
    pprint.pp(fold_all())
