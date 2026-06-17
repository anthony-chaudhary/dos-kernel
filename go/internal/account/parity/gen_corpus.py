#!/usr/bin/env python3
"""Generate the account-ranking differential parity corpus (docs/386 §6, docs/385 ratchet).

Each line is a self-contained parity case for the PURE ranking core ported to Go in
``go/internal/account/account.go``:

    {
      "name": "...",                 # human label
      "near_cap_util": 0.9,          # the policy threshold
      "now": 1000000.0,              # the injected clock (only the walled wait uses it)
      "accounts": [ {folded facts}, ... ],   # disk + live-probe facts, pre-folded
      "states":   [ {AccountState}, ... ],   # what account_state returned (per account)
      "pick":      {Pick},                    # pick_account's decision
      "serving_pool": ["name", ...],          # serving_pool's order
      "allocate":  {"3": ["name", ...], ...}, # allocate_seats(n) per n
      "spread":    {"0": {"account","reason"}, ...}  # pick_account_spread(seat) per seat
    }

The ORACLE side: this script drives the REAL ``dos.drivers.account_switcher`` end-to-end
— it materializes a temp config dir per account so the switcher's own disk reads
(``_token_expired`` / ``read_account_token``) fold to the intended facts, and injects a
synthetic ``probe_fn`` for the live signal. It records BOTH the folded facts it created
AND the decisions the switcher returned. The Go ``parity_test.go`` replays each line
through the native ranking core (which takes the folded facts — it never touches disk)
and asserts it reproduces every recorded decision + detail string. So the vendored
Python switcher is untouched (no re-vendor), and the Go port is pinned to its real output.

Run:  python go/internal/account/parity/gen_corpus.py > go/internal/account/parity/corpus.jsonl
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from dos.drivers import account_switcher as sw

# A fixed clock — only the all-walled wait reads it; everything else is folded.
NOW = 1_000_000.0
N_VALUES = (1, 2, 3, 5, 7)
SEAT_VALUES = (0, 1, 2, 3, 4)


class _Probe:
    """A synthetic ProbeLike — the injected live rate-limit signal for one account."""

    def __init__(self, allowed, utilization, reset_at_epoch, status):
        self._allowed = allowed
        self.utilization = utilization
        self.reset_at_epoch = reset_at_epoch
        self.status = status

    @property
    def allowed(self):
        return self._allowed


def _materialize(root: Path, fact: dict) -> sw.Account:
    """Build a temp config dir realizing ``fact`` so the real switcher folds to it."""
    d = root / fact["name"]
    d.mkdir(parents=True, exist_ok=True)
    if fact.get("creds_present"):
        exp_ms = int((NOW - 10_000) * 1000) if fact.get("token_expired") else int((NOW + 100_000) * 1000)
        (d / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "at-x", "expiresAt": exp_ms}}),
            encoding="utf-8")
    if fact.get("has_token"):
        (d / ".oauth-token").write_text("sk-ant-oat01-fake\n", encoding="utf-8")
    return sw.Account(name=fact["name"], config_dir=str(d), enabled=fact.get("enabled", True))


def _facts_json(fact: dict) -> dict:
    p = fact.get("probe")
    if p is not None:
        p = {
            "allowed": bool(p["allowed"]),
            "utilization": float(p["utilization"]),
            "reset_at_epoch": p.get("reset_at_epoch"),
            "status": p.get("status", ""),
        }
    return {
        "name": fact["name"],
        "enabled": bool(fact.get("enabled", True)),
        "creds_present": bool(fact.get("creds_present")),
        "token_expired": bool(fact.get("token_expired")),
        "has_token": bool(fact.get("has_token")),
        "probe": p,
    }


def _state_json(s) -> dict:
    return {
        "name": s.account.name,
        "kind": s.kind,
        "creds_present": s.creds_present,
        "token_expired": s.token_expired,
        "probe_status": s.probe_status or "",
        "utilization": s.utilization,
        "reset_at_epoch": s.reset_at_epoch,
        "detail": s.detail,
    }


def _pick_json(p) -> dict:
    return {
        "account": (p.account.name if p.account is not None else None),
        "reason": p.reason,
        "wait_seconds": p.wait_seconds,
        "soonest_reset_epoch": p.soonest_reset_epoch,
        "ok": p.ok,
    }


def _emit_case(name: str, ncu: float, facts: list[dict]) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        accounts = [_materialize(root, f) for f in facts]
        probe_map: dict[str, _Probe] = {}
        for acct, f in zip(accounts, facts):
            p = f.get("probe")
            if p is not None:
                probe_map[str(sw.account_creds_path(acct))] = _Probe(
                    p["allowed"], float(p["utilization"]), p.get("reset_at_epoch"), p.get("status", ""))

        def probe_fn(creds_path, now):
            return probe_map.get(creds_path)

        policy = sw.RotationPolicy(near_cap_util=ncu)
        states = [sw.account_state(a, probe_fn=probe_fn, now_epoch=NOW, near_cap_util=ncu)
                  for a in accounts]
        pick = sw.pick_account(accounts, probe_fn=probe_fn, now_epoch=NOW, policy=policy)
        pool = sw.serving_pool(accounts, probe_fn=probe_fn, now_epoch=NOW, policy=policy)
        allocate = {
            str(n): [a.name for a in sw.allocate_seats(
                accounts, n, probe_fn=probe_fn, now_epoch=NOW, policy=policy)]
            for n in N_VALUES
        }
        spread = {}
        for i in SEAT_VALUES:
            ps = sw.pick_account_spread(
                accounts, seat_index=i, probe_fn=probe_fn, now_epoch=NOW, policy=policy)
            spread[str(i)] = {
                "account": (ps.account.name if ps.account is not None else None),
                "reason": ps.reason,
            }
        return {
            "name": name,
            "near_cap_util": ncu,
            "now": NOW,
            "accounts": [_facts_json(f) for f in facts],
            "states": [_state_json(s) for s in states],
            "pick": _pick_json(pick),
            "serving_pool": [a.name for a in pool],
            "allocate": allocate,
            "spread": spread,
        }


def _probe(allowed, util, reset=None, status="ok"):
    return {"allowed": allowed, "utilization": util, "reset_at_epoch": reset, "status": status}


# The scenario set — covers every branch of the pure ranking core.
CASES: list[tuple] = [
    # 1. two fail-open (no probe) setup-token seats → both serving, spread round-robins.
    ("two-serving-failopen", 0.9, [
        {"name": "A", "has_token": True},
        {"name": "B", "has_token": True},
    ]),
    # 2. serving + near-cap + walled mix → pick the clean serving head.
    ("serving-nearcap-walled", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(True, 0.10)},
        {"name": "B", "has_token": True, "probe": _probe(True, 0.95)},
        {"name": "C", "has_token": True, "probe": _probe(False, 0.99, reset=int(NOW + 200))},
    ]),
    # 3. all walled with resets → pick the soonest, compute the wait.
    ("all-walled-with-reset", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(False, 0.99, reset=int(NOW + 100))},
        {"name": "B", "has_token": True, "probe": _probe(False, 0.99, reset=int(NOW + 50))},
    ]),
    # 4. all walled, no reset hint → the no-hint wait floor (1800).
    ("all-walled-no-reset", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(False, 0.99)},
        {"name": "B", "has_token": True, "probe": _probe(False, 0.99)},
    ]),
    # 5. walled with an ELAPSED reset → wait clamps to 0.
    ("walled-reset-elapsed", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(False, 0.99, reset=int(NOW - 10))},
    ]),
    # 6. nothing enrolled (one needs-enroll, one disabled) → no pick, enroll gap named.
    ("nothing-enrolled", 0.9, [
        {"name": "A"},  # no creds, no token → needs_enroll
        {"name": "B", "enabled": False, "has_token": True},  # disabled
    ]),
    # 7. all disabled → "no accounts in roster" (n_enroll == 0).
    ("all-disabled", 0.9, [
        {"name": "A", "enabled": False, "has_token": True},
        {"name": "B", "enabled": False, "has_token": True},
    ]),
    # 8. login vs setup-token enrollment → the `via` detail strings differ.
    ("login-vs-setuptoken", 0.9, [
        {"name": "A", "creds_present": True},          # login (fresh creds, no token)
        {"name": "B", "has_token": True},              # setup-token
    ]),
    # 9. expired creds (no token) → needs_enroll(expired); the other serves.
    ("expired-creds", 0.9, [
        {"name": "A", "creds_present": True, "token_expired": True},
        {"name": "B", "has_token": True},
    ]),
    # 10. all near-cap → fallback to the lowest-utilization one.
    ("all-near-cap", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(True, 0.92)},
        {"name": "B", "has_token": True, "probe": _probe(True, 0.95)},
    ]),
    # 11. three serving with distinct headroom → Hamilton apportionment + spread.
    ("headroom-allocate", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(True, 0.0)},
        {"name": "B", "has_token": True, "probe": _probe(True, 0.4)},
        {"name": "C", "has_token": True, "probe": _probe(True, 0.8)},
    ]),
    # 12. utilization values that pin the `:.0%` rounding cross-engine.
    ("probe-rounding", 0.95, [
        {"name": "A", "has_token": True, "probe": _probe(True, 0.125)},
        {"name": "B", "has_token": True, "probe": _probe(True, 0.155)},
        {"name": "C", "has_token": True, "probe": _probe(True, 0.145)},
    ]),
    # 13. a single serving seat → spread defers to pick_account (byte-identical).
    ("single-serving", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(True, 0.2)},
    ]),
    # 14. serving head + near-cap tail (mixed pool order for serving_pool/allocate).
    ("serving-plus-nearcap-tail", 0.9, [
        {"name": "A", "has_token": True, "probe": _probe(True, 0.1)},
        {"name": "B", "has_token": True, "probe": _probe(True, 0.93)},
        {"name": "C", "has_token": True, "probe": _probe(True, 0.5)},
        {"name": "D", "has_token": True, "probe": _probe(True, 0.91)},
    ]),
    # 15. empty roster → no pick, empty pool.
    ("empty-roster", 0.9, []),
]


def main() -> int:
    out = []
    for name, ncu, facts in CASES:
        out.append(json.dumps(_emit_case(name, ncu, facts), sort_keys=True))
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
