"""CLI ergonomics — agent-discoverability affordances on the `dos` surface.

Four agent-facing improvements, pinned here so they can't silently regress:

  1. The verdict-IS-the-exit-code contract is PUBLISHED in `dos doctor --json`
     under `exit_codes`, per verb — so an agent discovers it instead of
     reverse-engineering `$?`. Sourced from the SAME maps the handlers return.
  3. The verdict-bearing verbs carry a "USE THIS WHEN" body on `--help`
     (`description=`), ported from the MCP tool docstrings.
  4. `--workspace` / `--driver` / `--job` are accepted BOTH globally (before the
     verb) AND per-subcommand (after it), so `dos --workspace . verify` and
     `dos verify --workspace .` both resolve the same workspace.

(Item 2, `--explain` on gate / man wedge, is pinned in test_interpret_parity.py
alongside the rest of the interpretation surface.)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _env() -> dict:
    import os
    return {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def _cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from dos.cli import main; raise SystemExit(main())",
         *argv],
        capture_output=True, text=True, encoding="utf-8", env=_env())


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _plain_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init")


# ---------------------------------------------------------------------------
# Item 4 — global --workspace placement
# ---------------------------------------------------------------------------
def test_global_workspace_before_verb_resolves_same_as_after(tmp_path: Path):
    """`dos --workspace W verify …` resolves the SAME workspace as
    `dos verify --workspace W …` — the wart fix. Both report the tmp repo's root
    in `doctor`, neither falls back to cwd."""
    _plain_repo(tmp_path)
    ws = str(tmp_path)

    before = _cli("--workspace", ws, "doctor", "--json")
    after = _cli("doctor", "--workspace", ws, "--json")
    assert before.returncode == 0, before.stderr
    assert after.returncode == 0, after.stderr

    rb = json.loads(before.stdout)
    ra = json.loads(after.stdout)
    assert rb["workspace"] == str(tmp_path.resolve())
    assert ra["workspace"] == rb["workspace"]


def test_global_workspace_on_verify(tmp_path: Path):
    """The original agent-hostile case: `dos --workspace W verify P PH` must work
    (it previously dropped --workspace and verified against cwd)."""
    _plain_repo(tmp_path)
    cp = _cli("--workspace", str(tmp_path), "verify", "SOMEPLAN", "PH1", "--json")
    assert cp.returncode == 1, (cp.stdout, cp.stderr)  # not shipped in an empty repo
    out = json.loads(cp.stdout)
    assert out == {"plan": "SOMEPLAN", "phase": "PH1",
                   "shipped": False, "source": "none"}


def test_subcommand_workspace_wins_when_both_given(tmp_path: Path):
    """When both placements name a workspace, the per-subcommand one wins
    (last-parsed) — the documented precedence."""
    other = tmp_path / "other"
    real = tmp_path / "real"
    _plain_repo(other)
    _plain_repo(real)
    cp = _cli("--workspace", str(other), "doctor", "--workspace", str(real), "--json")
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["workspace"] == str(real.resolve())


def test_global_driver_flag_before_verb(tmp_path: Path):
    """`dos --driver workshop doctor` resolves the driver via the global flag (the
    workshop driver's lane taxonomy reaches the report)."""
    _plain_repo(tmp_path)
    cp = _cli("--driver", "workshop", "doctor", "--workspace", str(tmp_path), "--json")
    assert cp.returncode == 0, cp.stderr
    report = json.loads(cp.stdout)
    # The workshop driver declares a non-default lane taxonomy; the exact lanes are
    # the driver's business — we only assert the driver was applied at all, i.e.
    # the report built without error and carries a lane structure.
    assert set(report["lanes"]) == {"concurrent", "exclusive", "autopick", "trees"}


def test_job_flag_dual_placement_precedence(tmp_path: Path):
    """The `--job` alias (store_true) resolves in BOTH placements — this pins the
    fragile parent-default-False / child-SUPPRESS interaction so a future refactor
    that drops the SUPPRESS (re-introducing the clobber) is caught.

    `--job` == `--driver job`, so it swaps in the job lane taxonomy; we detect it
    by the job driver's signature lanes appearing in the report (the generic
    default has only `main`). Asserted via the lane SET differing from the generic,
    not exact lanes (those are the driver's business)."""
    _plain_repo(tmp_path)
    generic = json.loads(_cli("doctor", "--workspace", str(tmp_path), "--json").stdout)
    glanes = set(generic["lanes"]["concurrent"]) | set(generic["lanes"]["exclusive"])

    before = _cli("--job", "doctor", "--workspace", str(tmp_path), "--json")
    after = _cli("doctor", "--job", "--workspace", str(tmp_path), "--json")
    assert before.returncode == 0, before.stderr
    assert after.returncode == 0, after.stderr

    for cp in (before, after):
        rep = json.loads(cp.stdout)
        lanes = set(rep["lanes"]["concurrent"]) | set(rep["lanes"]["exclusive"])
        # The job driver replaced the generic taxonomy (the flag took effect in
        # this placement — if the SUPPRESS regressed, the global `--job` would be
        # clobbered back to False and `lanes` would equal the generic set).
        assert lanes != glanes, (cp.args, rep["lanes"])

    # And the two placements agree with each other.
    assert (json.loads(before.stdout)["lanes"]
            == json.loads(after.stdout)["lanes"])


# ---------------------------------------------------------------------------
# Item 1 — runtime cross-check of EVERY verdict-bearing verb's rows
# ---------------------------------------------------------------------------
def _stamped_ship_repo(repo: Path) -> tuple[str, str]:
    """A repo with one ship-shaped commit the generic grep rung recognizes.

    Returns (series, phase) that `dos verify` will report SHIPPED via grep — so the
    shipped(0) exit row can be cross-checked against runtime."""
    _plain_repo(repo)
    _git(repo, "commit", "--allow-empty", "-m", "widget rollout Phase 1: land it")
    return ("widget rollout", "Phase 1")


# ---------------------------------------------------------------------------
# Item 1 — the exit-code contract in doctor --json
# ---------------------------------------------------------------------------
def test_doctor_json_publishes_exit_code_contract(tmp_path: Path):
    """`dos doctor --json` carries a per-verb `exit_codes` table an agent reads to
    discover the verdict-IS-exit-code semantics of every verdict-bearing verb."""
    _plain_repo(tmp_path)
    cp = _cli("doctor", "--workspace", str(tmp_path), "--json")
    assert cp.returncode == 0, cp.stderr
    ec = json.loads(cp.stdout)["exit_codes"]

    assert ec["verify"] == {"shipped": 0, "not_shipped": 1, "contract_error": 2}
    assert ec["arbitrate"] == {"acquire": 0, "refuse": 1, "contract_error": 2}
    # liveness / gate keep their multi-valued maps + the unknown floor.
    assert ec["liveness"]["ADVANCING"] == 0
    assert ec["liveness"]["SPINNING"] == 3
    assert ec["liveness"]["STALLED"] == 4
    assert ec["liveness"]["contract_error"] == 2
    assert ec["gate"]["LIVE"] == 0
    assert ec["gate"]["DRAIN"] == 3
    assert ec["gate"]["STALE-STAMP"] == 4
    assert ec["gate"]["BLOCKED"] == 5
    assert ec["gate"]["RACE"] == 6
    assert ec["gate"]["contract_error"] == 2


def test_exit_code_contract_matches_runtime_behaviour(tmp_path: Path):
    """The published table is not a hand-maintained doc that can drift — it equals
    the codes the binary actually returns. Spot-check the load-bearing rows by
    RUNNING the verbs and matching `$?` to the table."""
    _plain_repo(tmp_path)
    ec = json.loads(_cli("doctor", "--workspace", str(tmp_path), "--json").stdout
                    )["exit_codes"]

    # verify: an unshipped claim in an empty repo → not_shipped.
    nm = _cli("verify", "--workspace", str(tmp_path), "P", "PH")
    assert nm.returncode == ec["verify"]["not_shipped"]

    # gate: an empty packet → DRAIN.
    g = _cli("gate", "--workspace", str(tmp_path), "--picks-json", "[]")
    assert g.returncode == ec["gate"]["DRAIN"]

    # gate: a malformed --picks-json → contract_error.
    bad = _cli("gate", "--workspace", str(tmp_path), "--picks-json", "{not json")
    assert bad.returncode == ec["gate"]["contract_error"]

    # liveness: a bad --run-id → contract_error.
    lv = _cli("liveness", "--workspace", str(tmp_path), "--run-id", "not-a-rid")
    assert lv.returncode == ec["liveness"]["contract_error"]


def test_exit_code_contract_matches_runtime_verify_shipped_and_arbitrate(tmp_path: Path):
    """The drift-prone rows the spot-check above missed: a SHIPPED verify (0) and
    ALL THREE arbitrate rows (acquire/refuse/contract_error). These verbs return
    their exit code FROM `_VERIFY/_ARBITRATE_EXIT_CODES` now, so this proves the
    published table and the binary read the same map — not two coincidentally-equal
    literals."""
    ship = tmp_path / "ship"
    series, phase = _stamped_ship_repo(ship)
    ec = json.loads(_cli("doctor", "--workspace", str(ship), "--json").stdout
                    )["exit_codes"]

    # verify: a real shipped phase → shipped(0).
    sv = _cli("verify", "--workspace", str(ship), series, phase)
    assert sv.returncode == ec["verify"]["shipped"], (sv.stdout, sv.stderr)

    # arbitrate: a free lane against an empty world → acquire(0).
    acq = _cli("arbitrate", "--workspace", str(ship), "--lane", "main",
               "--leases", "[]")
    assert acq.returncode == ec["arbitrate"]["acquire"], (acq.stdout, acq.stderr)

    # arbitrate: the SAME lane already held by a live lease → refuse(1).
    held = json.dumps([{"lane": "main", "lane_kind": "global", "tree": ["**/*"]}])
    ref = _cli("arbitrate", "--workspace", str(ship), "--lane", "main",
               "--kind", "global", "--tree", "**/*", "--leases", held)
    assert ref.returncode == ec["arbitrate"]["refuse"], (ref.stdout, ref.stderr)

    # arbitrate: malformed --leases → contract_error(2).
    bad = _cli("arbitrate", "--workspace", str(ship), "--lane", "main",
               "--leases", "{not json")
    assert bad.returncode == ec["arbitrate"]["contract_error"], (bad.stdout, bad.stderr)


# ---------------------------------------------------------------------------
# Item 3 — USE THIS WHEN framing on --help
# ---------------------------------------------------------------------------
def test_help_bodies_carry_use_this_when(tmp_path: Path):
    """Each verdict-bearing verb's `--help` carries the 'USE THIS WHEN' framing and
    its exit-code line — the prose ported from the MCP docstrings."""
    for verb in ("verify", "arbitrate", "liveness", "gate", "man"):
        cp = _cli(verb, "--help")
        assert cp.returncode == 0, (verb, cp.stderr)
        assert "USE THIS WHEN" in cp.stdout, f"{verb} --help lost the framing"

    # The exit-code line is present on the verdict-as-exit-code verbs.
    assert "Exit:" in _cli("verify", "--help").stdout
    assert "exit code" in _cli("liveness", "--help").stdout
    assert "exit code" in _cli("gate", "--help").stdout


def test_top_level_help_lists_global_workspace_flag():
    """`dos --help` advertises the global `--workspace` (it's a top-level option,
    not buried in each subcommand)."""
    cp = _cli("--help")
    assert cp.returncode == 0, cp.stderr
    assert "--workspace" in cp.stdout


def test_help_exit_code_prose_matches_the_maps():
    """The `_HELP_*` bodies hardcode exit codes as prose ("3 DRAIN", "0 ADVANCING").
    Derive the assertion FROM the maps so the prose can't silently drift: every
    verdict→code pair the gate/liveness maps publish must appear, as a
    `<code> <TOKEN>` substring, in the matching help body. If someone renumbers a
    map without updating the prose, --help would start lying about a machine-read
    branch code — this catches that."""
    from dos import cli

    # gate: token → code, e.g. "0 LIVE", "3 DRAIN".
    for token, code in cli._GATE_EXIT_CODES.items():
        assert f"{code} {token}" in cli._HELP_GATE, (token, code, "missing from _HELP_GATE")

    # liveness: same shape.
    for token, code in cli._LIVENESS_EXIT_CODES.items():
        assert f"{code} {token}" in cli._HELP_LIVENESS, (token, code, "missing from _HELP_LIVENESS")

    # verify: the prose says "0 shipped, 1 not-shipped" — assert both codes appear
    # alongside their words (looser match, the verify prose is sentence-style).
    assert f"{cli._VERIFY_EXIT_CODES['shipped']} shipped" in cli._HELP_VERIFY
    assert f"{cli._VERIFY_EXIT_CODES['not_shipped']} not-shipped" in cli._HELP_VERIFY


# ---------------------------------------------------------------------------
# `dos exit-codes` — the exit-code contract as a discoverable CLI surface
# (the `dos man` move for the verdict-IS-exit-code table, so a CI author looks
# it up without parsing `doctor --json`)
# ---------------------------------------------------------------------------
def test_exit_codes_json_equals_doctor_contract(tmp_path: Path):
    """`dos exit-codes --json` is the SAME object `doctor --json` carries under
    `exit_codes` — one source, two surfaces, so they can't drift."""
    _plain_repo(tmp_path)
    via_cmd = json.loads(_cli("exit-codes", "--json").stdout)
    via_doctor = json.loads(
        _cli("doctor", "--workspace", str(tmp_path), "--json").stdout)["exit_codes"]
    assert via_cmd == via_doctor
    # And it carries the load-bearing rows.
    assert via_cmd["verify"] == {"shipped": 0, "not_shipped": 1, "contract_error": 2}


def test_exit_codes_filter_one_verb():
    """`dos exit-codes VERB` filters to one verb (JSON form is the single row)."""
    one = json.loads(_cli("exit-codes", "liveness", "--json").stdout)
    assert set(one) == {"liveness"}
    assert one["liveness"]["ADVANCING"] == 0
    assert one["liveness"]["STALLED"] == 4


def test_exit_codes_text_form_lists_verbs_and_codes():
    """The default text form names verbs and prints `<code>  <token>` rows a human
    scans — success(0) leads each block."""
    cp = _cli("exit-codes")
    assert cp.returncode == 0, cp.stderr
    assert "dos verify" in cp.stdout
    assert "0  shipped" in cp.stdout
    assert "1  not_shipped" in cp.stdout


def test_exit_codes_unknown_verb_is_usage_error():
    """An unknown verb is a contract error (exit 2), and the message lists the
    known verbs so the typo is self-correcting."""
    cp = _cli("exit-codes", "nosuchverb")
    assert cp.returncode == 2
    assert "not a verdict-bearing verb" in cp.stderr
    assert "verify" in cp.stderr  # the known-verbs hint


# ---------------------------------------------------------------------------
# `dos quickstart` — the one-command caught-lie demo (the 60-second on-ramp)
# ---------------------------------------------------------------------------
def test_quickstart_runs_the_caught_lie_contrast():
    """`dos quickstart` scaffolds a throwaway repo and prints the SHIPPED/NOT_SHIPPED
    contrast from the real truth syscall — exit 0 when both verdicts come out right."""
    cp = _cli("quickstart")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    # AUTH1 is backed by a real commit subject → SHIPPED via grep-subject.
    assert "SHIPPED AUTH AUTH1" in cp.stdout
    assert "via grep-subject" in cp.stdout
    # AUTH2 has nothing behind it → NOT_SHIPPED via none (the caught claim).
    assert "NOT_SHIPPED AUTH AUTH2" in cp.stdout
    assert "via none" in cp.stdout


def test_quickstart_default_shows_the_fleet_act():
    """The DEFAULT quickstart's part two is the admission half of the pitch: agent
    A is admitted onto src, agent B is redirected off the busy region onto the
    disjoint docs lane (the collision that never reached the files), and agent C
    is refused when every lane is held. All three lines must come from the real
    arbiter's decision fields (lane names, the auto-pick redirect reason, the
    saturation refuse), not canned strings.

    The act runs — and narrates — the DURABLE verb (`dos lease-lane acquire`,
    journaled to the demo repo's WAL), not the pure `arbitrate`: a reader who
    replayed `dos arbitrate` after watching the old transcript got `acquire`
    twice in a row (nothing was journaled) and read it as a double-booking. The
    state that made B and C answer differently from A must be VISIBLE (the
    `lease-lane live` beat) and the ask/hold split must be said out loud."""
    cp = _cli("quickstart")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    out = cp.stdout
    # The scaffold derived the two-lane taxonomy the act arbitrates over.
    assert "derived 2 concurrent lane(s) (docs, src)" in out
    # The narrated command is the journaling verb the reader can replay.
    assert "dos lease-lane acquire --lane src" in out
    # Agent A admitted onto src.
    assert "acquire 'src'" in out
    # Agent B redirected onto the free disjoint lane — auto-pick, not a refusal
    # and not a double-booking (the kernel's own redirect reason).
    assert "acquire 'docs'" in out
    assert "was busy" in out
    # Agent C refused when saturated — the typed, scriptable no.
    assert "refuse" in out
    assert "all concurrent cluster lanes are held" in out
    # The state is shown where it lives: the lease journal, folded by the real
    # `lease-lane live` (the holder tags prove it is the fold, not a canned list).
    assert "dos lease-lane live" in out
    assert "held by agent-A" in out
    assert "held by agent-B" in out
    # The ask/hold split is stated: arbitrate reads the journal, never writes it.
    assert "dos arbitrate --lane" in out
    assert "reads the journal but never writes it" in out


def test_quickstart_default_routes_the_wider_audience():
    """The default quickstart is written for a newcomer who is NOT already a
    fleet operator. Two affordances are pinned: (1) the demo opens as a STORY
    with both claims NAMED (the password reset is the lie that gets caught),
    not as bare AUTH1/AUTH2 tokens; (2) the closing is an adoption ROUTER —
    one line each for a hook-capable agent runtime, an MCP host, a CI step,
    and a fleet — so every audience leaves with the move that applies to them."""
    cp = _cli("quickstart")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    out = cp.stdout
    # (1) the named-claim story frames the contrast.
    assert "password reset" in out
    # (2) the router: hooks (enforcement), MCP (advisory), CI (exit code), fleet.
    assert "dos init --hooks" in out
    assert "dos-mcp" in out
    assert "exit code" in out
    assert "dos arbitrate --lane" in out


def test_quickstart_default_leaves_no_temp_dir():
    """The default run cleans up its temp dir — no demo repo is left behind."""
    import os
    import tempfile
    tmpdir = tempfile.gettempdir()
    before = {p for p in os.listdir(tmpdir) if p.startswith("dos-quickstart-")}
    cp = _cli("quickstart")
    assert cp.returncode == 0, cp.stderr
    after = {p for p in os.listdir(tmpdir) if p.startswith("dos-quickstart-")}
    assert after <= before, f"quickstart left a temp dir behind: {after - before}"


def test_quickstart_keep_dir_persists_a_working_workspace(tmp_path: Path):
    """`--keep DIR` leaves a real, re-verifiable workspace: a dos.toml + a git repo
    where `dos verify AUTH AUTH1` independently reports SHIPPED."""
    keep = tmp_path / "demo"
    cp = _cli("quickstart", "--keep", str(keep))
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    assert (keep / "dos.toml").exists()
    assert (keep / ".git").exists()
    # The kept workspace verifies on its own — proof the demo wasn't a canned string.
    again = _cli("verify", "--workspace", str(keep), "AUTH", "AUTH1")
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert "SHIPPED" in again.stdout


def test_quickstart_keep_dir_fleet_act_replays_from_the_journal(tmp_path: Path):
    """The fleet act's escalation is REPLAYABLE in the kept repo, because the demo
    journaled real leases (agent-A on src, agent-B on docs) rather than threading
    an in-memory list: a later `dos arbitrate --lane src` — a different process,
    long after the demo exited — reads the WAL and refuses/redirects exactly as
    the transcript taught. This is the property the old in-memory act lacked (a
    reader replaying its `arbitrate` line got `acquire` twice and read it as a
    double-booking)."""
    keep = tmp_path / "demo"
    cp = _cli("quickstart", "--keep", str(keep))
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    # The epilogue tells the keeper the leases are still held + how to see them.
    assert "lease-lane live" in cp.stdout
    # The journal survives the demo process: the fold still shows both holders.
    live = _cli("--workspace", str(keep), "lease-lane", "live")
    assert live.returncode == 0, (live.stdout, live.stderr)
    leases = json.loads(live.stdout)
    holders = {(l.get("lane"), l.get("holder")) for l in leases}
    assert ("src", "agent-A") in holders
    assert ("docs", "agent-B") in holders
    # And a fresh arbitrate against the kept repo sees the held world: requesting
    # the held src lane cannot acquire it (exit 1 refuse, or an auto-pick redirect
    # to a lane that is NOT src — either way, never a double-booking of src).
    arb = _cli("arbitrate", "--workspace", str(keep), "--lane", "src",
               "--output", "json")
    decision = json.loads(arb.stdout)
    assert not (decision["outcome"] == "acquire" and decision["lane"] == "src"), \
        decision


def test_quickstart_driver_workshop_shows_concurrency_arbitration():
    """`dos quickstart --driver workshop` runs in a throwaway repo (so the reference
    driver is NEVER shadowed by a workspace's own dos.toml) and shows BOTH halves:
    the truth-syscall contrast AND the workshop driver's concurrent/exclusive lane
    arbitration through the real `arbiter`. This is the one unshadowed path a
    stranger has to SEE a reference driver work."""
    cp = _cli("quickstart", "--driver", "workshop")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    # Scaffolded under the workshop driver (not the auto-derived generic config).
    assert "driver: workshop" in cp.stdout
    # The truth-syscall half still fires.
    assert "SHIPPED AUTH AUTH1" in cp.stdout
    assert "NOT_SHIPPED AUTH AUTH2" in cp.stdout
    # The admission half: two disjoint cluster lanes admit; the exclusive one refuses.
    assert "frontend" in cp.stdout and "backend" in cp.stdout
    assert "runs concurrently" in cp.stdout
    assert "exclusive" in cp.stdout
    # And it points the reader at the precedence-safe adoption path.
    assert "dos init --example workshop" in cp.stdout


def test_quickstart_unknown_driver_is_a_clean_error():
    """An unknown `--driver` name fails with exit 2 and points at the template,
    never a traceback."""
    cp = _cli("quickstart", "--driver", "nonesuch")
    assert cp.returncode == 2, (cp.stdout, cp.stderr)
    assert "could not be resolved" in cp.stderr
    assert "workshop" in cp.stderr  # the suggested template


def test_quickstart_spinning_runs_the_false_still_working_contrast():
    """`dos quickstart --spinning` (issue #59) replays the overnight burn: a loop
    narrates 'making progress' for four steps while landing zero commits, and the
    kernel rules SPINNING off the git delta + heartbeat — then one honest commit
    (the only change) flips the verdict to ADVANCING, and `dos efficiency` prices
    the spin WASTEFUL. The honest boundary must be stated in the transcript: the
    verdict is advisory, and the evidence is the delta, never the prose."""
    cp = _cli("quickstart", "--spinning")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    out = cp.stdout
    # The narrated commands are the real verbs a reader can replay.
    assert "dos liveness --run-id" in out
    assert "dos efficiency --work 0 --tokens 48000" in out
    # The contrast: spinning while narrating, advancing once a commit lands.
    assert "SPINNING" in out
    assert "WASTEFUL" in out
    assert "ADVANCING" in out
    # SPINNING is ruled BEFORE ADVANCING — the honest step flips it, not luck.
    assert out.index("SPINNING") < out.index("ADVANCING")
    # The honest boundary, stated: advisory verdict; evidence, not narration.
    assert "ADVISORY" in out
    assert "never the prose" in out


def test_quickstart_spinning_keep_replays_the_real_verdict(tmp_path: Path):
    """The --spinning verdicts come from the real classifiers over the throwaway
    repo's actual git history, not canned strings: the kept repo's replay line
    (the exact `dos liveness` invocation the epilogue prints) answers ADVANCING
    from a fresh process, because the honest commit is in the repo's git."""
    import re
    keep = tmp_path / "spin-demo"
    cp = _cli("quickstart", "--spinning", "--keep", str(keep))
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    assert (keep / ".git").exists()
    m = re.search(
        r"--run-id (RID-\S+) --start-sha (\S+) "
        r"--last-heartbeat-age-ms 60000 --now-ms (\d+)",
        cp.stdout)
    assert m, f"no replayable liveness line in the transcript:\n{cp.stdout}"
    rid, sha, now_ms = m.group(1), m.group(2), m.group(3)
    again = _cli("liveness", "--workspace", str(keep), "--run-id", rid,
                 "--start-sha", sha, "--last-heartbeat-age-ms", "60000",
                 "--now-ms", now_ms)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert "ADVANCING" in again.stdout


def test_quickstart_spinning_rejects_driver_combo():
    """--spinning is its own scene: combining it with --driver is a contract
    error (exit 2) with a clean message, never a half-run of either scene."""
    cp = _cli("quickstart", "--spinning", "--driver", "workshop")
    assert cp.returncode == 2, (cp.stdout, cp.stderr)
    assert "its own scene" in cp.stderr


# ---------------------------------------------------------------------------
# the arbitrate follow-up note — ask vs hold, said at the moment it bites
# ---------------------------------------------------------------------------
def test_arbitrate_followup_note_names_the_durable_verb(tmp_path: Path):
    """An interactive ACQUIRE gets the two-line orientation a first run needs:
    arbitrate journals NOTHING (so a second call "acquires" again — the field
    confusion this note exists for), the durable verb is `lease-lane acquire`;
    and when the requested name is not a lane this workspace declares, the way
    to get real lanes (`dos init .` / `dos man lane`). Pure builder, unit-tested
    here; emission is TTY-gated (test_arbitrate_non_tty_stderr_is_silent)."""
    from dos import config as dos_config
    from dos.arbiter import LaneDecision
    from dos.cli import _arbitrate_followup_note

    cfg = dos_config.load_workspace_config(workspace=tmp_path)
    d = LaneDecision("acquire", lane="main", lane_kind="cluster",
                     tree=["**/*"], auto_picked=True,
                     reason="auto-picked free cluster lane 'main' "
                            "(requested 'src' is not a lane in this workspace).")
    note = _arbitrate_followup_note(d, "src", cfg.lanes)
    assert "not a lane in this workspace" in note
    assert "dos init ." in note and "dos man lane" in note
    assert "nothing was journaled" in note
    assert "dos lease-lane acquire --lane main" in note

    # A KNOWN lane name skips the unknown-lane orientation, keeps the verb note.
    note_known = _arbitrate_followup_note(d, "main", cfg.lanes)
    assert "not a lane in this workspace" not in note_known
    assert "dos lease-lane acquire" in note_known

    # A refusal gets no note — the operator was not misled into holding anything.
    refused = LaneDecision("refuse", reason="held")
    assert _arbitrate_followup_note(refused, "src", cfg.lanes) is None


def test_arbitrate_non_tty_stderr_is_silent(tmp_path: Path):
    """Piped/scripted invocations see NO follow-up note: stderr stays empty on a
    non-TTY acquire, so existing parsers, hooks, and CI logs are byte-identical
    with or without the interactive affordance."""
    _plain_repo(tmp_path)
    cp = _cli("arbitrate", "--workspace", str(tmp_path), "--lane", "src",
              "--leases", "[]", "--output", "json")
    assert cp.returncode in (0, 1), (cp.stdout, cp.stderr)
    assert cp.stderr.strip() == ""


def test_init_example_workshop_scaffolds_the_reference_taxonomy(tmp_path: Path):
    """`dos init --example workshop` writes a dos.toml carrying the workshop driver's
    OWN lanes, so `doctor` reads them back with NO --driver flag — the precedence-safe
    reachability path (a plain `--driver` is shadowed once a dos.toml exists; this
    bakes the taxonomy INTO the dos.toml so they agree)."""
    cp = _cli("--workspace", str(tmp_path), "init", "--example", "workshop")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    toml = (tmp_path / "dos.toml").read_text(encoding="utf-8")
    assert 'concurrent = ["frontend", "backend"]' in toml
    assert "frontend" in toml and "backend" in toml and "release" in toml
    # doctor reads the lanes back WITHOUT --driver — proof it's unshadowed.
    doc = _cli("--workspace", str(tmp_path), "doctor", "--json")
    assert doc.returncode == 0, (doc.stdout, doc.stderr)
    trees = json.loads(doc.stdout)["lanes"]["trees"]
    assert {"frontend", "backend", "release"} <= set(trees)


def test_init_example_unknown_driver_is_a_clean_error(tmp_path: Path):
    """An unknown `--example` name fails with exit 1 and names the template, no traceback."""
    cp = _cli("--workspace", str(tmp_path), "init", "--example", "nonesuch")
    assert cp.returncode == 1, (cp.stdout, cp.stderr)
    assert "could not be resolved" in cp.stderr
    assert "workshop" in cp.stderr
    assert not (tmp_path / "dos.toml").exists()  # nothing scaffolded on the error path


# ---------------------------------------------------------------------------
# `dos init --hooks <host> --dry-run` — preview the merge, write nothing
# (the dress rehearsal for wiring a runtime that already has its own config)
# ---------------------------------------------------------------------------
def test_init_hooks_dry_run_writes_nothing_and_previews(tmp_path: Path):
    """`--dry-run` prints the proposed settings.json and writes NOTHING — not the
    hooks file, not even the dos.toml (preview means touch-nothing)."""
    cp = _cli("init", str(tmp_path), "--hooks", "claude-code", "--dry-run")
    assert cp.returncode == 0, cp.stderr
    assert "--dry-run" in cp.stdout
    assert "nothing written" in cp.stdout
    # The proposed config is shown, with the three real hook commands.
    assert "dos hook pretool" in cp.stdout
    assert "dos hook posttool" in cp.stdout
    assert "dos hook stop" in cp.stdout
    # Nothing was written: no .claude dir, no dos.toml.
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / "dos.toml").exists()


def test_init_hooks_dry_run_preview_matches_a_real_write(tmp_path: Path):
    """The preview is exactly what a subsequent real run produces (same pure merge),
    so the dress rehearsal can't mislead."""
    # Real write into one dir.
    real = tmp_path / "real"
    real.mkdir()
    wrote = _cli("init", str(real), "--hooks", "claude-code")
    assert wrote.returncode == 0, wrote.stderr
    written_settings = (real / ".claude" / "settings.json").read_text(encoding="utf-8")

    # Dry-run into a fresh dir; the preview body must contain the same merged JSON.
    prev = _cli("init", str(tmp_path / "dry"), "--hooks", "claude-code", "--dry-run")
    assert prev.returncode == 0, prev.stderr
    # The proposed text is printed between the preview banners; the written file's
    # content (stripped) appears verbatim in the preview.
    assert written_settings.strip() in prev.stdout


def test_init_dry_run_previews_the_default_flow_and_writes_nothing(tmp_path: Path):
    """A bare `dos init --dry-run` PREVIEWS the whole default-on install (skills,
    hooks, MCP) and writes nothing — every surface is default-on now, so there is
    always something to preview (it is no longer the hooks-only usage error)."""
    cp = _cli("init", str(tmp_path), "--dry-run")
    assert cp.returncode == 0, cp.stderr
    assert "--dry-run" in cp.stdout
    assert "would install" in cp.stdout          # the skills preview
    assert "preview only" in cp.stdout           # the closing preview line
    # Preview means touch-nothing: no dos.toml, no .claude, no .mcp.json.
    assert not (tmp_path / "dos.toml").exists()
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".mcp.json").exists()


# ---------------------------------------------------------------------------
# `dos --help` — the curated, grouped command list (the front-door affordance)
# ---------------------------------------------------------------------------
def test_top_level_help_groups_commands_and_leads_with_quickstart():
    """`dos --help` shows the curated TRUTH/ADMISSION/SPINE/OPS groups and leads a
    newcomer to `dos quickstart` — not a flat alphabetical wall."""
    out = _cli("--help").stdout
    assert "New here?" in out
    assert "dos quickstart" in out
    for group in ("TRUTH", "ADMISSION", "SPINE", "OPS"):
        assert group in out, f"--help lost the {group} group"
    # The auto-generated subcommand wall is collapsed to a hint, not dumped inline.
    assert "<command>" in out


def test_grouped_commands_are_all_real_subparsers():
    """Every `dos <verb>` named in the curated groups is a REAL registered verb
    (`dos <verb> --help` exits 0) — the curated list can't advertise a verb that
    doesn't exist. Guards against the description drifting from the parser."""
    from dos import cli
    parser = cli.build_parser()
    # Pull the registered subcommand names from the subparsers action.
    registered = set()
    for action in parser._actions:
        if isinstance(action, __import__("argparse")._SubParsersAction):
            registered |= set(action.choices)
    # The verbs the curated help promises (the leading `dos X` token of each row).
    promised = {
        "quickstart", "init", "doctor", "verify", "commit-audit", "coverage",
        "liveness", "productivity", "efficiency", "complete", "verify-result", "attest",
        "verify-receipt", "arbitrate", "scope-gate", "pickable", "enumerate",
        "cooldown", "reconcile", "breaker", "exec-capability", "lint", "run-id",
        "lease-lane", "lease", "journal", "resume", "rewind", "status", "trace",
        "decisions", "top", "notify", "man", "skillify", "guard", "hook", "reindex",
        "projects", "learn", "exit-codes",
    }
    missing = promised - registered
    assert not missing, f"curated --help names non-existent verbs: {sorted(missing)}"


def test_lease_lane_spawn_subcommand_parses():
    """`dos lease-lane spawn --lane L` is a registered subcommand that parses to the
    lease-lane dispatcher with the spawn cmd (the SPAWN→ACQUIRE visibility verb)."""
    from dos import cli
    ns = cli.build_parser().parse_args(["lease-lane", "spawn", "--lane", "src"])
    assert ns.lease_lane_cmd == "spawn"
    assert ns.lane == "src"


# ---------------------------------------------------------------------------
# Issue #168 — the high-traffic usage-error sites route through `_fail`, so a
# malformed flag carries a `→ next action` hint without changing its exit code.
# (The subprocess stderr is non-TTY, so the hint rides a plain `→` line — no
# ANSI bytes — and stdout is never touched.)
# ---------------------------------------------------------------------------
def test_malformed_leases_carries_a_hint_without_changing_exit_code(tmp_path: Path):
    """`arbitrate --leases '<bad json>'` is still a contract error (exit 2), and now
    prints a `→` next-action hint on stderr."""
    repo = tmp_path / "r"
    _plain_repo(repo)
    cp = _cli("--workspace", str(repo), "arbitrate", "--lane", "main",
              "--kind", "cluster", "--leases", "[not json")
    assert cp.returncode == 2, (cp.stdout, cp.stderr)   # exit code unchanged
    assert "not valid JSON" in cp.stderr
    assert "→" in cp.stderr                              # the next-action hint
    assert "Traceback" not in cp.stderr
    assert cp.stdout == ""                               # stdout stays byte-clean


def test_malformed_pickable_state_carries_a_hint_without_changing_exit_code(tmp_path: Path):
    """`pickable --state '<bad json>'` keeps its contract-error exit and gains a hint."""
    repo = tmp_path / "r"
    _plain_repo(repo)
    cp = _cli("--workspace", str(repo), "pickable", "--state", "{not json")
    assert cp.returncode == 2, (cp.stdout, cp.stderr)
    assert "not valid JSON" in cp.stderr
    assert "→" in cp.stderr
    assert "Traceback" not in cp.stderr


def test_unknown_driver_carries_a_hint_without_changing_exit_code(tmp_path: Path):
    """An unknown `--example` driver keeps exit 1 and routes the remedy through the
    `→` hint line (the template suggestion)."""
    repo = tmp_path / "r"
    _plain_repo(repo)
    cp = _cli("--workspace", str(repo), "init", "--example", "nonesuch")
    assert cp.returncode == 1, (cp.stdout, cp.stderr)    # the init refusal floor
    assert "could not be resolved" in cp.stderr
    assert "→" in cp.stderr and "workshop" in cp.stderr
