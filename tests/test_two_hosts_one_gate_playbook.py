"""Pin playbook 10 (two hosts, one gate, one WAL) against the real kernel — the
docs/342 M5 (P-SPOKEN) existence proof.

`examples/playbooks/10_two-hosts-one-gate.md` and its runnable
`examples/playbooks/two_hosts_one_gate.sh` claim a specific, falsifiable fact:
TWO independent agent hosts coordinate one concurrent run through DOS's verbs,
and the second host's colliding write is refused **by the same `dos apply` gate
reading the same lease WAL**, with **no host-specific collision check
re-implemented on the B side**. Pasted transcripts rot silently; this test
executes the exact two-host interleave through the real `dos` CLI and asserts the
witnesses a self-report cannot produce:

  * host A's lease is durably recorded in the shared WAL (`lease-lane acquire`
    then `lease-lane live` reads it back);
  * host B, naming the SAME lane A holds, is REFUSED by `dos apply` on the
    sibling-collision floor — `allowed:false`, exit 1, typed `SCOPE_ESCAPE`,
    while the inner scope verdict is `IN_SCOPE` (so the refusal is the WAL
    collision, NOT B's own scope — the cross-host property);
  * host B writing OUTSIDE its own lane is REFUSED on the scope-escape rung;
  * the file is UNCHANGED on disk after the refusal (apply is pre-effect: it
    decides, it does not write);
  * host B's in-lane, non-colliding write PASSES the SAME gate (exit 0).

The B-side calls re-implement nothing: they are the identical `dos apply` verb A
would run. The proof is two hosts, one gate, one WAL. See docs/340 §3.1 (own the
shared effect-language) and docs/342 M5.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import dos


def _cli(repo: Path, *argv: str, host_id: str = "") -> subprocess.CompletedProcess:
    """Run `dos <argv> --workspace <repo>` as a real subprocess (a host call).

    `host_id` stamps DISPATCH_HOST_ID so each call carries a distinct host
    identity — the two hosts are genuinely different processes with different
    ids, sharing only the repo + WAL. Loop-context env is stripped so the
    boundary self-lease resolver uses the explicit `--lane` we pass (hermetic).
    """
    env = {**os.environ, "PYTHONPATH": str(Path(dos.__file__).parents[1])}
    for k in ("CID_RUN_ID", "DISPATCH_RUN_ID", "DISPATCH_LOOP_TS"):
        env.pop(k, None)
    if host_id:
        env["DISPATCH_HOST_ID"] = host_id
    return subprocess.run(
        [sys.executable, "-m", "dos.cli", "--workspace", str(repo), *argv],
        capture_output=True, text=True, env=env, cwd=str(repo),
    )


def _shared_repo(repo: Path) -> Path:
    """One repo, two DISJOINT lanes (src/**, ui/**), the SCOPE_ESCAPE reason.

    Mirrors examples/playbooks/two_hosts_one_gate.sh step 0. A real git repo so
    `dos apply`'s self-lease resolution + WAL live under a normal `.dos/`.
    """
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "dos.toml").write_text(
        'workspace = "."\n\n'
        "[lanes]\n"
        'concurrent = ["src", "ui"]\n'
        'exclusive = ["global"]\n'
        'autopick = ["src", "ui"]\n\n'
        "[lanes.trees]\n"
        'src = ["src/**"]\n'
        'ui = ["ui/**"]\n'
        'global = ["**/*"]\n\n'
        "[reasons.SCOPE_ESCAPE]\n"
        'category = "MISROUTE"\n'
        'summary = "a staged write escapes the held lane or collides with a live lease"\n'
        'fix = "narrow the write, or take the lane it targets"\n',
        encoding="utf-8",
    )
    (repo / "src" / "auth").mkdir(parents=True, exist_ok=True)
    (repo / "ui").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "auth" / "login.py").write_text("def login(): ...\n", encoding="utf-8")
    (repo / "ui" / "page.tsx").write_text("export const Page = () => null\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q", "."],
        ["git", "config", "user.email", "demo@example.com"],
        ["git", "config", "user.name", "demo"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "src/SETUP: seed src + ui lanes"],
    ):
        subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, check=True)
    return repo


def _host_a_takes_src(repo: Path) -> None:
    """HOST A books lane src in the shared WAL; assert it reads back."""
    acq = _cli(repo, "lease-lane", "acquire", "--lane", "src", "--kind", "cluster",
               "--owner", "host-A-claude-code", host_id="host-A-claude-code")
    assert acq.returncode == 0, (acq.stdout, acq.stderr)
    live = _cli(repo, "lease-lane", "live", host_id="host-A-claude-code")
    assert live.returncode == 0, (live.stdout, live.stderr)
    leases = json.loads(live.stdout)
    assert any(l.get("lane") == "src" and l.get("holder") == "host-A-claude-code"
               for l in leases), leases


def test_host_b_colliding_write_refused_by_same_gate_over_shared_wal(tmp_path: Path):
    """THE M5 WITNESS: B (claiming A's lane) is refused by the WAL collision floor.

    `scope.allowed` is True (B's write is inside lane src's tree) yet the apply
    verdict is False — the refusal came from A's live lease in the shared WAL,
    not from B's own scope. B re-implemented no collision check; it ran the same
    `dos apply`. Two hosts, one gate, one WAL.
    """
    repo = _shared_repo(tmp_path)
    _host_a_takes_src(repo)

    before = (repo / "src" / "auth" / "login.py").read_text(encoding="utf-8")
    proc = _cli(repo, "apply", "--lane", "src", "--file", "src/auth/login.py", "--json",
                host_id="host-B-cursor")
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    out = json.loads(proc.stdout)
    assert out["allowed"] is False, out
    assert out["reason_class"] == "SCOPE_ESCAPE", out
    assert "collides with a live lease" in out["reason"], out
    # The cross-host signature: B's write is IN_SCOPE for the lane it named; the
    # refusal is the WAL collision, not B's scope.
    assert out["scope"]["allowed"] is True, out
    # The filesystem witness — a refused pre-effect write touches nothing.
    after = (repo / "src" / "auth" / "login.py").read_text(encoding="utf-8")
    assert before == after, "apply is pre-effect: a refusal must not write the file"


def test_host_b_out_of_lane_write_refused_scope_escape(tmp_path: Path):
    """B holding lane ui, writing into src/, is refused on the scope-escape rung."""
    repo = _shared_repo(tmp_path)
    _host_a_takes_src(repo)
    proc = _cli(repo, "apply", "--lane", "ui", "--file", "src/auth/login.py", "--json",
                host_id="host-B-cursor")
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    out = json.loads(proc.stdout)
    assert out["allowed"] is False, out
    assert out["reason_class"] == "SCOPE_ESCAPE", out
    assert out["scope"]["verdict"] == "WRONG_TARGET", out


def test_host_b_in_lane_write_passes_same_gate(tmp_path: Path):
    """The control: B's in-lane, non-colliding write PASSES the SAME gate (exit 0).

    Proves the gate refuses the collision, not every write — same binary, same WAL.
    """
    repo = _shared_repo(tmp_path)
    _host_a_takes_src(repo)
    proc = _cli(repo, "apply", "--lane", "ui", "--file", "ui/page.tsx", "--json",
                host_id="host-B-cursor")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    out = json.loads(proc.stdout)
    assert out["allowed"] is True, out
    assert out["reason_class"] == "", out


def test_playbook_and_script_name_the_verb_contract(tmp_path: Path):
    """The walkthrough + script exist and NAME the versioned verb contract.

    A doc that drops the verb-contract naming would silently weaken the P-SPOKEN
    deliverable (docs/342 M5: "the versioned verb contract it exercises"); pin it.
    """
    here = Path(__file__).resolve().parents[1] / "examples" / "playbooks"
    md = (here / "10_two-hosts-one-gate.md").read_text(encoding="utf-8")
    sh = (here / "two_hosts_one_gate.sh").read_text(encoding="utf-8")
    for token in ("dos lease-lane acquire", "dos apply", "SCOPE_ESCAPE", "one WAL"):
        assert token in md, f"playbook 10 must name {token!r}"
    for token in ("lease-lane acquire", "dos --workspace . apply", "SCOPE_ESCAPE"):
        assert token in sh, f"the runnable script must use {token!r}"
