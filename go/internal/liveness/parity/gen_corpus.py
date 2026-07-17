#!/usr/bin/env python3
"""Generate the liveness differential parity corpus (docs/124 §3 Phase 1, docs/385 TP2).

Each line is a self-contained parity case for the PURE liveness classifier ported to
Go in ``go/internal/liveness/liveness.go``:

    {
      "name": "...",                 # human label
      "grace_ms": 1800000,           # LivenessPolicy.grace_ms (min run-age before SPINNING)
      "spin_ms": 900000,             # LivenessPolicy.spin_ms (heartbeat-freshness / alive bound)
      "expect": { ...to_dict()... }  # what classify() returned: verdict + reason + echoed evidence
    }

The ORACLE side: this script drives the REAL ``dos.liveness.classify`` — it builds a
``ProgressEvidence`` + ``LivenessPolicy`` from the case spec and records the verdict's
full ``to_dict()``. The Go ``parity_test.go`` rebuilds the SAME evidence from
``expect.evidence`` and asserts the native classifier reproduces ``verdict`` and
``reason`` byte-for-byte. Unlike the arbiter (whose reason prose is excluded from the
byte gate, docs/124 §2), liveness's reason interpolates INTEGER counts only — no float,
no set iteration, no lookbehind (docs/124 §1.4 / A.4) — so the WHOLE verdict + reason is
gated, the strictly-stronger pin that makes liveness the right first TP2 port.

This is the liveness twin of ``go/internal/account/parity/gen_corpus.py``. The reasons
carry three non-ASCII glyphs (— ≤ ≥), so the corpus is written UTF-8 with
``ensure_ascii=False`` and stdout is reconfigured to UTF-8 (the docs/124 A.1 cp1252
hazard, handled at the source).

Run:  python go/internal/liveness/parity/gen_corpus.py > go/internal/liveness/parity/corpus.jsonl
"""
from __future__ import annotations

import json
import sys

from dos.liveness import LivenessPolicy, ProgressEvidence, classify

# The default windows (the twin of dos.liveness.DEFAULT_POLICY): 30 min grace, 15 min spin.
GRACE = 30 * 60 * 1000   # 1_800_000
SPIN = 15 * 60 * 1000    # 900_000


def _emit_case(name: str, grace_ms: int, spin_ms: int, ev: dict) -> dict:
    """Drive the real classifier on one case and record its full verdict.

    ``ev`` carries only the fields the case sets; ``ProgressEvidence`` defaults the
    rest (journal_events_since=0, the optional rungs None). ``expect`` is the verdict's
    ``to_dict()`` — verdict + reason + the echoed evidence the Go side reads back as
    its input.
    """
    verdict = classify(ProgressEvidence(**ev),
                       LivenessPolicy(grace_ms=grace_ms, spin_ms=spin_ms))
    return {
        "name": name,
        "grace_ms": grace_ms,
        "spin_ms": spin_ms,
        "expect": verdict.to_dict(),
    }


# The scenario set — every rung of the ladder and every reason branch, plus the
# alive/dead and young/old boundaries, custom policy, and the docs/124 A.4 examples.
# (name, grace_ms, spin_ms, evidence-kwargs)
CASES: list[tuple] = [
    # --- ADVANCING: the forward-delta rungs (reason 1 commits, reason 2 journal) ---
    # 1. commit rung — the no-plan floor: a commit alone answers ADVANCING.
    ("advancing-commits", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 3}),
    # 2. commits win over journal events (commits checked first → reason 1, not 2).
    ("advancing-commits-over-journal", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 1,
      "journal_events_since": 5}),
    # 3. journal rung only (0 commits) → reason 2.
    ("advancing-journal-only", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 0,
      "journal_events_since": 4}),
    # 4. journal rung wins over a fresh heartbeat (events checked before alive).
    ("advancing-journal-over-heartbeat", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 0,
      "journal_events_since": 1, "last_heartbeat_age_ms": 5000}),
    # 5. tokens are echoed but NEVER enter an ADVANCING reason (only SPINNING's).
    ("advancing-commits-tokens-not-in-reason", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 2,
      "tokens_spent_since": 5000}),

    # --- ADVANCING: the young-and-alive benign guard (reason 4) ---
    # 6. fresh heartbeat, run younger than grace → withhold SPINNING, report benign.
    ("young-and-alive", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000}),
    # 7. process_alive=True does NOT demote a young run (True/None never demote).
    ("young-and-alive-proc-true", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000, "process_alive": True}),
    # 8. boundary: run_age == grace_ms - 1 is still young (strict <).
    ("young-exactly-grace-minus-one", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": GRACE - 1, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000}),

    # --- SPINNING (reason 5), with/without tokens, proc rungs, boundaries ---
    # 9. alive, past grace, not moving, no token signal.
    ("spinning-no-tokens", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000}),
    # 10. same, with the optional waste signal appended to the reason.
    ("spinning-with-tokens", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000, "tokens_spent_since": 1200}),
    # 11. process_alive=True confirms life but does not change SPINNING.
    ("spinning-proc-true", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000, "process_alive": True}),
    # 12. boundary: heartbeat age == spin_ms exactly is alive (≤) → SPINNING.
    ("spinning-age-exactly-spin", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": SPIN}),
    # 13. boundary: run_age == grace_ms exactly is old enough (not < grace) → SPINNING.
    ("spinning-age-exactly-grace", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": GRACE, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000}),
    # 14. large epoch-ms values (64-bit) — no overflow/format drift.
    ("spinning-large-epoch", GRACE, SPIN,
     {"run_started_ms": 1_000_000_000_000, "now_ms": 1_000_000_002_000_000,
      "commits_since_start": 0, "last_heartbeat_age_ms": 8000,
      "tokens_spent_since": 999999}),

    # --- STALLED: proc-demote (reason 3) and the not-alive bottom (reason 6a/6b) ---
    # 15. fresh heartbeat says alive, OS says the pid is gone → demote to STALLED.
    ("proc-demote-stalled", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000, "process_alive": False}),
    # 16. proc-demote fires even on a YOUNG alive run (checked before the grace guard).
    ("proc-demote-young", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 8000, "process_alive": False}),
    # 17. never beat (age None) → STALLED reason 6a.
    ("stalled-never-beat", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0}),
    # 18. stale heartbeat (age > spin_ms) → STALLED reason 6b.
    ("stalled-stale-heartbeat", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 1_000_000}),
    # 19. boundary: age == spin_ms + 1 is NOT alive → STALLED reason 6b.
    ("stalled-age-one-over-spin", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": SPIN + 1}),
    # 20. proc demote needs ALIVE: a stale beat + process_alive=False stays on the
    #     not-alive bottom branch (reason 6b), proving the demote is gated on alive.
    ("stalled-stale-proc-false", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 2_000_000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 1_000_000, "process_alive": False}),
    # 21. zero everything (now == started, no beat) → STALLED reason 6a.
    ("stalled-zero-everything", GRACE, SPIN,
     {"run_started_ms": 0, "now_ms": 0, "commits_since_start": 0}),

    # --- custom policy: the windows thread through the reason numbers ---
    # 22. tight windows, alive + past grace → SPINNING with the custom numbers.
    ("custom-policy-spinning", 10000, 5000,
     {"run_started_ms": 0, "now_ms": 20000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 3000}),
    # 23. tight windows, alive but young → young-and-alive ADVANCING with custom numbers.
    ("custom-policy-young", 10000, 5000,
     {"run_started_ms": 0, "now_ms": 8000, "commits_since_start": 0,
      "last_heartbeat_age_ms": 3000}),

    # --- the docs/124 A.4 worked examples (verbatim) ---
    # 24. the rich ADVANCING example (commits=3 wins over everything below).
    ("docs-a4-advancing", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 3,
      "journal_events_since": 5, "last_heartbeat_age_ms": 8000,
      "tokens_spent_since": 1200, "process_alive": True}),
    # 25. the zero-commits-no-heartbeat STALLED example.
    ("docs-a4-stalled", GRACE, SPIN,
     {"run_started_ms": 1000, "now_ms": 600000, "commits_since_start": 0,
      "last_heartbeat_age_ms": None}),
]


def main() -> int:
    # The reasons carry — / ≤ / ≥; reconfigure stdout so `> corpus.jsonl` is UTF-8 on
    # any host (the docs/124 A.1 cp1252 crash, prevented at the source).
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - very old Python
        pass
    lines = [json.dumps(_emit_case(*c), sort_keys=True, ensure_ascii=False) for c in CASES]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
