"""The legalcite LIVE arm — run the shipped `citation_resolve.resolve()` over a
SCALED labeled corpus against the live CourtListener API, and report a real-world
DETECT recall + FALSE-FIRE rate (docs/277 §6 #1, docs/279 §2 "live arm").

The frozen harness (`harness.py`) proved the *mechanism* over n=18 with no network.
This runner converts that into a *measured-at-scale* number: it iterates
`corpus.full_corpus()` (hundreds of labeled cites) and, per cite, calls the REAL
driver verdict — never a re-implementation — so the numbers are a property of the
shipped classifier, not of this harness.

What this runner adds that the frozen one did not need:

  * THREE-WAY accounting. Every cite lands in exactly one of DETECT / FALSE-FIRE /
    ABSTAIN. ABSTAIN (no corpus access — token missing, rate-limited, timeout) is its
    OWN column and is EXCLUDED from both rate denominators. An abstain is "could not
    tell," never a silent pass and never a fabricated flag — the driver's honest
    four-way split, carried into the score.
  * RATE-LIMIT pacing + bounded retries. CourtListener's token endpoint is
    rate-limited (≈5/min); a batch must pace itself so the run does not become one
    long string of rate-limit ABSTAINs. Pacing is a fixed inter-call delay
    (`--min-interval`) plus a bounded backoff on a transient failure.
  * CHECKPOINT / resume. `--out FILE` writes a JSON checkpoint after every cite;
    `--resume` reads it back and skips cites already scored, so a multi-hour run
    survives the daily quota or an interruption without re-querying.
  * A RECORDED transport (`--transport recorded`). Reads a committed map of canned
    API verdicts and runs the SAME scoring path with zero network and zero token —
    so the entire runner (accounting, checkpoint, exit gate) is unit-testable
    offline. This is how the runner is proven before any token run.

Usage:

    # Offline, $0, no token — proves the runner end-to-end:
    python -m benchmark.legalcite.live_corpus --transport recorded --json

    # The operator's real-world run (needs a free CourtListener token):
    COURTLISTENER_TOKEN=... python -m benchmark.legalcite.live_corpus \
        --out live_results.json --min-interval 13

See RUNBOOK.md for the operator steps and RESULTS.md for where the number lands.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

HERE = Path(__file__).resolve().parent
RECORDED = HERE / "recorded_transport.json"

# The REAL driver — score the shipped classifier, never re-implement a verdict.
from dos.drivers.citation_resolve import (  # noqa: E402
    Citation,
    CitationEvidence,
    CitationVerdict,
    ResolvedCluster,
    classify,
    resolve,
)

# Verdicts that count as "flagged" — the witness refused to vouch for the cite.
_FLAGGED = {Citation.UNRESOLVED, Citation.RESOLVED_MISMATCH}
# Verdicts where there was no corpus access — excluded from both rate denominators.
_ABSTAINED = {Citation.ABSTAIN}


# ---------------------------------------------------------------------------
# Transports — both return the REAL `CitationVerdict`. The live one hits the
# network via the shipped `resolve()`; the recorded one builds the evidence from a
# canned map and runs the real `classify()`. Scoring is identical for both.
# ---------------------------------------------------------------------------

Transport = Callable[[str, str, str, str], CitationVerdict]  # (cite, name, quote, label)


def live_transport(token: str, base: str = "") -> Transport:
    """A transport that calls the shipped resolver against the live API. The `label`
    is ignored — the live API decides existence; the corpus label is only ground
    truth for SCORING, never an input to the verdict (that would forge the rung)."""
    kw = {"token": token}
    if base:
        kw["base"] = base

    def _run(cite: str, claimed_name: str, quote: str, label: str) -> CitationVerdict:
        return resolve(cite, claimed_name=claimed_name, quote=quote, **kw)

    return _run


def _evidence_from_record(cite: str, rec: dict) -> CitationEvidence:
    """Build a CitationEvidence from a recorded record — exactly the object the live
    `gather()` would have produced, minus the network. A record with
    `reachable: false` (or no `cluster` and an explicit `unreachable`) becomes the
    ABSTAIN floor; otherwise the clusters drive UNRESOLVED / MATCH / MISMATCH."""
    reachable = bool(rec.get("reachable", True))
    cl = rec.get("cluster")
    clusters: tuple[ResolvedCluster, ...] = ()
    if cl:
        clusters = (ResolvedCluster(
            name=cl.get("name") or "",
            citations=tuple(cl.get("citations") or ()),
            opinion_text=cl.get("opinion_text") or "",
            text_is_full=bool(cl.get("text_is_full", False)),
        ),)
    return CitationEvidence(
        cite=cite,
        claimed_name=rec.get("claimed_name") or "",
        quote=rec.get("quote") or "",
        clusters=clusters,
        reachable=reachable,
        detail=rec.get("detail") or "recorded transport",
    )


def recorded_transport(path: Path = RECORDED) -> Transport:
    """A transport that resolves each cite from a committed recorded map and runs the
    REAL classify(). Zero network, zero token — the offline proof path.

    Default for a cite with NO explicit record (so the fixture stays small while the
    synth corpus scales): a `synth`/generated FABRICATION resolves reachable-but-
    no-cluster → UNRESOLVED (a real case's number, moved — the reporter returns
    nothing, by construction); an unrecorded REAL cite is unreachable → ABSTAIN (we
    have no third-party bytes to vouch for it — the honest floor, never a free pass).
    Explicit records always win — that is how the collision / mis-quote / abstain
    cases are pinned."""
    data = json.loads(path.read_text(encoding="utf-8"))
    records: dict[str, dict] = data.get("records", {})

    def _run(cite: str, claimed_name: str, quote: str, label: str) -> CitationVerdict:
        rec = records.get(cite)
        if rec is None:
            if label == "fabricated":
                # No reporter carries a perturbed (synth) cite — reachable, empty.
                rec = {"reachable": True, "cluster": None,
                       "detail": "synth perturbation: no reporter carries it (default)"}
            else:
                rec = {"reachable": False,
                       "detail": "no recorded response for a real cite (ABSTAIN floor)"}
        rec = {**rec, "claimed_name": claimed_name, "quote": quote}
        return classify(_evidence_from_record(cite, rec))

    return _run


# ---------------------------------------------------------------------------
# The run — iterate the corpus, score each cite, accumulate the three-way result.
# ---------------------------------------------------------------------------

@dataclass
class _Row:
    cite: str
    name: str
    label: str  # "real" | "fabricated"
    verdict: str
    flagged: bool
    abstained: bool
    why: str

    def to_dict(self) -> dict:
        return {"cite": self.cite, "name": self.name, "label": self.label,
                "verdict": self.verdict, "flagged": self.flagged,
                "abstained": self.abstained, "why": self.why}


def _pace(min_interval: float, last_call: list[float]) -> None:
    """Block until at least `min_interval` seconds have passed since the last call.
    `last_call` is a 1-element mutable carrying the monotonic timestamp; injectable
    sleep/clock would over-engineer this — the live arm uses real time, the recorded
    arm passes min_interval=0 so tests never sleep."""
    if min_interval <= 0:
        return
    now = time.monotonic()
    wait = min_interval - (now - last_call[0])
    if wait > 0:
        time.sleep(wait)
    last_call[0] = time.monotonic()


def run(
    corpus: Iterable,
    transport: Transport,
    *,
    min_interval: float = 0.0,
    checkpoint: Path | None = None,
    resume: bool = False,
    progress: bool = False,
) -> dict:
    """Score every LabeledCite in `corpus` through `transport`. Returns the result
    dict (three-way accounting + per-cite rows). Writes a JSON checkpoint after each
    cite when `checkpoint` is set; with `resume`, skips cites already in it."""
    rows: dict[str, _Row] = {}
    if resume and checkpoint and checkpoint.exists():
        prior = json.loads(checkpoint.read_text(encoding="utf-8"))
        for rd in prior.get("rows", []):
            rows[rd["cite"]] = _Row(**rd)

    last_call = [0.0]
    items = list(corpus)
    for i, lc in enumerate(items):
        if lc.cite in rows:  # resumed — already scored
            continue
        _pace(min_interval, last_call)
        v: CitationVerdict = transport(lc.cite, lc.claimed_name, lc.quote, lc.label)
        flagged = v.verdict in _FLAGGED
        abstained = v.verdict in _ABSTAINED
        rows[lc.cite] = _Row(
            cite=lc.cite, name=lc.claimed_name, label=lc.label,
            verdict=v.verdict.value, flagged=flagged, abstained=abstained,
            why=v.reason,
        )
        if progress:
            print(f"  [{i + 1}/{len(items)}] {lc.cite:18s} {v.verdict.value}",
                  file=sys.stderr)
        if checkpoint is not None:
            _write_checkpoint(checkpoint, rows)
    return _score(list(rows.values()))


def _write_checkpoint(path: Path, rows: dict[str, _Row]) -> None:
    path.write_text(
        json.dumps({"rows": [r.to_dict() for r in rows.values()]}, indent=2),
        encoding="utf-8",
    )


def _score(rows: list[_Row]) -> dict:
    """Fold the per-cite rows into the three-way headline. Rates are over the
    REACHABLE (non-abstain) cites only — an abstain is excluded from both
    denominators, never counted as a pass or a flag."""
    real = [r for r in rows if r.label == "real"]
    fab = [r for r in rows if r.label == "fabricated"]

    real_reachable = [r for r in real if not r.abstained]
    fab_reachable = [r for r in fab if not r.abstained]

    false_fires = sum(1 for r in real_reachable if r.flagged)
    detected = sum(1 for r in fab_reachable if r.flagged)

    real_abstain = sum(1 for r in real if r.abstained)
    fab_abstain = sum(1 for r in fab if r.abstained)

    return {
        "n_real": len(real),
        "n_fabricated": len(fab),
        "n_real_reachable": len(real_reachable),
        "n_fabricated_reachable": len(fab_reachable),
        "real_abstained": real_abstain,
        "fabricated_abstained": fab_abstain,
        "detected": detected,
        "false_fires": false_fires,
        "detect_recall": (detected / len(fab_reachable)) if fab_reachable else None,
        "false_fire_rate": (false_fires / len(real_reachable)) if real_reachable else None,
        "rows": [r.to_dict() for r in rows],
    }


def _fmt(r: dict, *, transport_name: str) -> str:
    out: list[str] = []
    out.append("=" * 78)
    out.append("legalcite LIVE — DOS over a scaled corpus, real CourtListener reporter")
    out.append(f"  transport: {transport_name}")
    out.append("=" * 78)
    out.append("")
    out.append(f"  REAL cites:        {r['n_real']:4d}  "
               f"({r['n_real_reachable']} reachable, {r['real_abstained']} abstained)")
    out.append(f"  FABRICATED cites:  {r['n_fabricated']:4d}  "
               f"({r['n_fabricated_reachable']} reachable, {r['fabricated_abstained']} abstained)")
    out.append("")
    dr = r["detect_recall"]
    ff = r["false_fire_rate"]
    dr_s = f"{dr * 100:.1f}%" if dr is not None else "n/a (all abstained)"
    ff_s = f"{ff * 100:.1f}%" if ff is not None else "n/a (all abstained)"
    out.append(f"  DETECT recall    = {r['detected']}/{r['n_fabricated_reachable']} = {dr_s}"
               "   (fabrications flagged, over REACHABLE)")
    out.append(f"  FALSE-FIRE rate  = {r['false_fires']}/{r['n_real_reachable']} = {ff_s}"
               "   (real cites wrongly flagged, over REACHABLE)")
    out.append(f"  ABSTAIN          = {r['real_abstained'] + r['fabricated_abstained']}"
               "   (no corpus access — excluded from BOTH rates, never a pass/flag)")
    out.append("")
    if ff == 0.0:
        out.append("  ✓ FALSE-FIRE FLOOR HELD (0% over reachable) — the docs/277 §6 prediction.")
    elif ff is None:
        out.append("  · no reachable real cites — the run was all-abstain (token? rate-limit?).")
    else:
        out.append("  ✗ FALSE-FIRE FLOOR BREACHED — a reachable real cite was flagged; report it.")
    out.append("=" * 78)
    return "\n".join(out)


def _floor_breached(r: dict) -> bool:
    """The exit gate: the false-fire floor is breached only when a REACHABLE real
    cite was wrongly flagged. An all-abstain run (ff is None) is not a breach — it is
    'could not tell', surfaced in the text, exit 0 (the honest floor, never fail-open
    into a false green nor a false red)."""
    return r["false_fire_rate"] not in (0.0, None)


def main(argv: list[str] | None = None) -> int:
    import os

    ap = argparse.ArgumentParser(prog="benchmark.legalcite.live_corpus",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--transport", choices=("live", "recorded"), default="live",
                    help="live = real CourtListener (needs token); recorded = offline canned map")
    ap.add_argument("--token", default="", help="CourtListener token (or $COURTLISTENER_TOKEN)")
    ap.add_argument("--base", default="", help="override the API base (testing)")
    ap.add_argument("--n-synth", type=int, default=None,
                    help="cap the generated synth fabrications (default: all)")
    ap.add_argument("--min-interval", type=float, default=0.0,
                    help="min seconds between live calls (rate-limit pacing; ~13 for 5/min)")
    ap.add_argument("--out", default="", help="checkpoint file (written after each cite)")
    ap.add_argument("--resume", action="store_true", help="resume from --out, skipping done cites")
    ap.add_argument("--progress", action="store_true", help="per-cite progress to stderr")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(HERE))  # so `import corpus` works under `-m`
    import corpus  # noqa: E402

    the_corpus = corpus.full_corpus(n_synth=args.n_synth)

    if args.transport == "recorded":
        transport = recorded_transport()
        transport_name = "recorded (offline, $0)"
        min_interval = 0.0
    else:
        token = args.token or os.environ.get("COURTLISTENER_TOKEN", "")
        if not token:
            print("live transport needs a token: set $COURTLISTENER_TOKEN or pass --token "
                  "(without it every cite ABSTAINs — see RUNBOOK.md).", file=sys.stderr)
            # Not a hard error: a no-token live run is a legitimate all-abstain result;
            # but we warn loudly so it is never mistaken for a real measurement.
        transport = live_transport(token, base=args.base)
        transport_name = f"live CourtListener{' (NO TOKEN — all-abstain)' if not token else ''}"
        min_interval = args.min_interval

    checkpoint = Path(args.out) if args.out else None
    r = run(the_corpus, transport, min_interval=min_interval,
            checkpoint=checkpoint, resume=args.resume, progress=args.progress)
    r["_transport"] = transport_name

    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(_fmt(r, transport_name=transport_name))
    return 1 if _floor_breached(r) else 0


if __name__ == "__main__":
    raise SystemExit(main())
