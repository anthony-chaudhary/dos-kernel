"""One-time generator for `recorded_transport.json` (the offline test fixture).

NOT part of the scored run — like `snapshot.py`, this is a build tool. It derives
the canned API verdict map from the committed `frozen_corpus.json` (the THIRD_PARTY
bytes Free Law Project authored, captured 2026-06-09) so the recorded transport
reproduces the live `gather()` bytes minus the network. Two synthetic demonstrator
keys (`__ABSTAIN_DEMO__`, `__MISMATCH_DEMO__`) let the offline test pin the ABSTAIN
and RESOLVED_MISMATCH columns by key.

    python benchmark/legalcite/_gen_recorded.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    frozen = json.loads((HERE / "frozen_corpus.json").read_text(encoding="utf-8"))
    records: dict[str, dict] = {}
    for cite, rec in frozen["real"].items():
        records[cite] = {"reachable": True, "cluster": rec["cluster"]}
    for cite, rec in frozen["fabricated"].items():
        records[cite] = {"reachable": True, "cluster": rec["cluster"]}
    # (a) an ABSTAIN demonstrator — the resolver could not reach the corpus.
    records["__ABSTAIN_DEMO__"] = {"reachable": False, "detail": "demo: rate-limited (ABSTAIN)"}
    # (b) a RESOLVED_MISMATCH demonstrator — resolves, but the claimed quote is NOT in
    #     the FULL opinion (text_is_full=True so the quote rung may refute).
    records["__MISMATCH_DEMO__"] = {
        "reachable": True,
        "cluster": {
            "name": "Obergefell v. Hodges",
            # the citation array MUST carry the queried key for the exact-match rung to
            # resolve it (then the quote rung refutes the absent quote → MISMATCH).
            "citations": ["__MISMATCH_DEMO__", "576 U.S. 644"],
            "opinion_text": "The Court holds that the Fourteenth Amendment requires "
                            "marriage equality nationwide.",
            "text_is_full": True,
        },
    }
    out = {
        "_meta": {
            "purpose": "Offline canned API verdicts for benchmark.legalcite.live_corpus "
                       "--transport recorded. Real-cite clusters + documented-fabrication "
                       "reads are the FROZEN bytes Free Law Project authored "
                       "(frozen_corpus.json, 2026-06-09); the two __*_DEMO__ keys are "
                       "synthetic demonstrators the test references by key.",
            "derived_from": "frozen_corpus.json (2026-06-09); regenerate via _gen_recorded.py",
        },
        "records": records,
    }
    (HERE / "recorded_transport.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote recorded_transport.json — {len(records)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
