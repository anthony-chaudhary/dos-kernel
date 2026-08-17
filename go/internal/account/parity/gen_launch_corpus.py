#!/usr/bin/env python3
"""Generate launch-resolver parity cases for the Go account slice.

This corpus drives the live Python ``dos.drivers.account_switcher`` through:

  load_roster -> pick_account_spread -> env_for

The committed JSON normalizes the temp config root to ``$ROOT``. The Go test
re-materializes that root, runs ``ResolveLaunchEnv``, normalizes its output the
same way, and compares the chosen account, reason, wait fields, and launch env.

Run:
  python go/internal/account/parity/gen_launch_corpus.py > go/internal/account/parity/corpus_launch.jsonl
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from dos.drivers import account_switcher as sw

ROOT = "$ROOT"
NOW = 1_000_000.0


class _Probe:
    def __init__(self, allowed: bool, utilization: float, reset_at_epoch=None, status="ok"):
        self._allowed = allowed
        self.utilization = utilization
        self.reset_at_epoch = reset_at_epoch
        self.status = status

    @property
    def allowed(self):
        return self._allowed


def _probe(allowed, util, reset=None, status="ok"):
    return {"allowed": allowed, "utilization": util, "reset_at_epoch": reset, "status": status}


def _creds(*, expired: bool = False, token: str = "at-x") -> str:
    exp_ms = int((NOW - 100) * 1000) if expired else 9_999_999_999_000
    return json.dumps({"claudeAiOauth": {"accessToken": token, "expiresAt": exp_ms}})


def _roster_text(specs: list[dict], *, marker: str) -> str:
    lines = ["accounts:"]
    for spec in specs:
        lines.append(f"  - name: {spec['name']}")
        lines.append(f"    config_dir: '{marker}/{spec['name']}'")
        if not spec.get("enabled", True):
            lines.append("    enabled: false")
    lines.extend(["rotation:", "  order: by_reset", "  near_cap_util: 0.9"])
    return "\n".join(lines) + "\n"


def _materialize(root: Path, specs: list[dict]) -> list[sw.Account]:
    accounts = []
    for spec in specs:
        cfg = root / spec["name"]
        cfg.mkdir(parents=True, exist_ok=True)
        for name, content in spec.get("files", {}).items():
            (cfg / name).write_text(content, encoding="utf-8")
        accounts.append(
            sw.Account(
                name=spec["name"],
                config_dir=str(cfg),
                enabled=spec.get("enabled", True),
            )
        )
    return accounts


def _norm(value, root: Path):
    if isinstance(value, str):
        s = value.replace(str(root), ROOT)
        return s.replace("\\", "/")
    if isinstance(value, dict):
        return {k: _norm(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v, root) for v in value]
    return value


def _emit_case(name: str, specs: list[dict], *, seat_index: int = 0, environ=None) -> dict:
    environ = environ or {}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _materialize(root, specs)
        roster = root / "accounts.yaml"
        roster.write_text(_roster_text(specs, marker=str(root)), encoding="utf-8")
        accounts, policy = sw.load_roster(roster)
        probes = {
            spec["name"]: spec["probe"]
            for spec in specs
            if spec.get("probe") is not None
        }

        def probe_fn(creds_path, now):
            name = Path(creds_path).parent.name
            raw = probes.get(name)
            if raw is None:
                return None
            return _Probe(
                raw["allowed"],
                float(raw["utilization"]),
                raw.get("reset_at_epoch"),
                raw.get("status", ""),
            )

        pick = sw.pick_account_spread(
            accounts,
            seat_index=seat_index,
            probe_fn=probe_fn,
            now_epoch=NOW,
            policy=policy,
        )
        env = sw.env_for(pick.account, environ=environ) if pick.ok else None
        files = []
        for spec in specs:
            for file_name, content in spec.get("files", {}).items():
                files.append({"account": spec["name"], "name": file_name, "content": content})
        return {
            "name": name,
            "now": NOW,
            "seat_index": seat_index,
            "roster": _roster_text(specs, marker=ROOT),
            "files": files,
            "probes": probes,
            "environ": environ,
            "expected": {
                "account": pick.account.name if pick.account is not None else None,
                "reason": pick.reason,
                "ok": pick.ok,
                "wait_seconds": pick.wait_seconds,
                "soonest_reset_epoch": pick.soonest_reset_epoch,
                "env": _norm(env, root) if env is not None else None,
            },
        }


CASES = [
    (
        "token-only-failopen-env",
        [
            {"name": "A", "files": {".oauth-token": "sk-ant-oat01-a\n"}},
            {"name": "B", "files": {".oauth-token": "sk-ant-oat01-b\n"}},
        ],
    ),
    (
        "fresh-creds-defers-token",
        [
            {
                "name": "A",
                "files": {
                    ".credentials.json": _creds(token="at-a"),
                    ".oauth-token": "sk-ant-oat01-a\n",
                },
            },
            {"name": "B", "files": {".oauth-token": "sk-ant-oat01-b\n"}},
        ],
    ),
    (
        "spread-picks-high-headroom-login",
        [
            {
                "name": "A",
                "files": {".oauth-token": "sk-ant-oat01-a\n"},
                "probe": _probe(True, 0.80),
            },
            {
                "name": "B",
                "files": {".credentials.json": _creds(token="at-b")},
                "probe": _probe(True, 0.10),
            },
        ],
    ),
    (
        "near-cap-fallback-token",
        [
            {
                "name": "A",
                "files": {".oauth-token": "sk-ant-oat01-a\n"},
                "probe": _probe(True, 0.95),
            },
            {
                "name": "B",
                "files": {".oauth-token": "sk-ant-oat01-b\n"},
                "probe": _probe(True, 0.92),
            },
        ],
    ),
    (
        "all-walled-no-launch-env",
        [
            {
                "name": "A",
                "files": {".oauth-token": "sk-ant-oat01-a\n"},
                "probe": _probe(False, 0.99, reset=int(NOW + 50)),
            },
            {
                "name": "B",
                "files": {".oauth-token": "sk-ant-oat01-b\n"},
                "probe": _probe(False, 0.99, reset=int(NOW + 100)),
            },
        ],
    ),
    (
        "dedupe-same-setup-token",
        [
            {"name": "A", "files": {".oauth-token": "sk-ant-oat01-same\n"}},
            {"name": "B", "files": {".oauth-token": "sk-ant-oat01-same\n"}},
        ],
    ),
    (
        "disabled-head-skipped",
        [
            {"name": "A", "enabled": False, "files": {".oauth-token": "sk-ant-oat01-a\n"}},
            {"name": "B", "files": {".oauth-token": "sk-ant-oat01-b\n"}},
        ],
    ),
    (
        "env-for-splices-disk-token-after-parent-token",
        [
            {"name": "A", "files": {".oauth-token": "sk-ant-oat01-disk\n"}},
        ],
        {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-parent"},
    ),
]


def main() -> int:
    lines = []
    for item in CASES:
        if len(item) == 2:
            name, specs = item
            environ = None
        else:
            name, specs, environ = item
        lines.append(json.dumps(_emit_case(name, specs, environ=environ), sort_keys=True))
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
