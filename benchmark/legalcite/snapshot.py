"""Snapshot CourtListener responses into a frozen cache so the legalcite replay is
$0 and deterministic (docs/279 §2).

The unauthenticated `/search/` endpoint is a full-text relevance search, NOT a
citation index — querying a bare reporter string misses canonical cases (Roe,
Miranda, Plessy all MISS by exact-citation-array match on the top page), which would
breach the docs/277 false-fire floor if used as live ground truth. So we establish the
ground truth RELIABLY via the NAME-search path (which reliably surfaces the canonical
cluster), read each cluster's TRUE citation array + case name from the third-party
reporter, and FREEZE that. The frozen clusters are then what `classify()` runs over —
the verdict is a pure function of bytes Free Law Project authored, replayable forever
with no network.

This is a ONE-TIME assembly tool, not part of the scored run. Run it to (re)build
`frozen_corpus.json`; the benchmark harness reads only the frozen file. The frozen file
IS committed (it is the corpus), so a fresh checkout replays the exact same numbers.

    python benchmark/legalcite/snapshot.py            # rebuild frozen_corpus.json

Each real cite is recorded with the cluster CourtListener returned for it (name +
citation array + an opinion-text snippet for the quote rung); each fabricated cite is
recorded with the empty result the reporter returned (no carrying cluster). The
snapshot records the date + endpoint so the corpus is an auditable, dated fossil
([[feedback-date-observations-for-staleness]]).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = HERE / "frozen_corpus.json"
BASE = "https://www.courtlistener.com"
UA = {"User-Agent": "dos-citation-resolve/0.1 (https://github.com/anthony-chaudhary/dos-kernel)"}


def _get(params: dict, retries: int = 4) -> dict:
    url = f"{BASE}/api/rest/v4/search/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    last: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - snapshot tool, retry then raise
            last = e
            time.sleep(3)
    raise last  # type: ignore[misc]


def _norm(c: str) -> str:
    return " ".join(c.split()).lower()


def _snippet(res: dict) -> str:
    for k in ("snippet", "text", "plain_text"):
        v = res.get(k)
        if isinstance(v, str) and v.strip():
            return v[:2000]
    for op in res.get("opinions") or []:
        if isinstance(op, dict):
            for k in ("snippet", "text"):
                v = op.get(k)
                if isinstance(v, str) and v.strip():
                    return v[:2000]
    return ""


def find_cluster_by_name(name: str, want_cite: str) -> dict | None:
    """Find the canonical cluster for `name` whose citation array carries `want_cite`.
    The reliable ground-truth path (name search), not the noisy bare-cite search."""
    d = _get({"q": name, "type": "o"})
    last_token = name.split(" v")[0].split()[-1].lower()
    for res in (d.get("results") or [])[:25]:
        cits = [str(c) for c in (res.get("citation") or [])]
        if _norm(want_cite) in {_norm(c) for c in cits}:
            cn = (res.get("caseName") or "")
            if last_token in cn.lower():
                return {"name": cn, "citations": cits, "opinion_text": _snippet(res)}
    return None


def confirm_unresolved(cite: str) -> dict:
    """Confirm a fabricated cite returns NO carrying cluster (the empty reporter read)."""
    d = _get({"q": f'"{cite}"', "type": "o"})
    carrying = []
    for res in (d.get("results") or []):
        if _norm(cite) in {_norm(str(c)) for c in (res.get("citation") or [])}:
            carrying.append(res.get("caseName"))
    return {"count": d.get("count"), "carrying": carrying}


# The labeled set — see dataset.py for the provenance of each entry.
from dataset import REAL_CITES, FABRICATED_CITES  # noqa: E402


def main() -> int:
    snap: dict = {
        "_meta": {
            "captured": "2026-06-09",
            "source": "CourtListener (Free Law Project) /api/rest/v4/search/",
            "note": "Real clusters resolved via NAME-search ground-truth path; "
                    "fabrications confirmed by empty bare-cite reads. THIRD_PARTY bytes.",
        },
        "real": {},
        "fabricated": {},
    }
    for cite, name, quote in REAL_CITES:
        cl = find_cluster_by_name(name, cite)
        if cl is None:
            print(f"WARN real cite did NOT resolve via name path: {cite} ({name}) — SKIPPING")
            time.sleep(2)
            continue
        snap["real"][cite] = {"claimed_name": name, "quote": quote, "cluster": cl}
        print(f"OK   real {cite:18s} -> {cl['name']}")
        time.sleep(2)
    for cite, name, quote, note in FABRICATED_CITES:
        info = confirm_unresolved(cite)
        if info["carrying"]:
            print(f"NOTE fabricated {cite} resolves to a DIFFERENT case {info['carrying'][:1]} "
                  f"(collision — name guard will catch); keeping with its real cluster")
            # Snapshot the colliding cluster so the name-guard path is exercised offline.
            d = _get({"q": f'"{cite}"', "type": "o"})
            coll = None
            for res in (d.get("results") or []):
                if _norm(cite) in {_norm(str(c)) for c in (res.get("citation") or [])}:
                    coll = {"name": res.get("caseName"),
                            "citations": [str(c) for c in (res.get("citation") or [])],
                            "opinion_text": _snippet(res)}
                    break
            snap["fabricated"][cite] = {"claimed_name": name, "quote": quote,
                                        "note": note, "cluster": coll}
        else:
            snap["fabricated"][cite] = {"claimed_name": name, "quote": quote,
                                        "note": note, "cluster": None}
            print(f"OK   fab  {cite:18s} -> UNRESOLVED (count={info['count']})")
        time.sleep(2)
    FROZEN.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(f"\nwrote {FROZEN} — {len(snap['real'])} real, {len(snap['fabricated'])} fabricated")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(HERE))
    raise SystemExit(main())
