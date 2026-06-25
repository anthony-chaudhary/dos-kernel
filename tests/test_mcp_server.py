"""The dos-mcp server exposes the syscalls faithfully — and stays read-only.

This is the MCP analogue of `test_verify_no_plan.py`: it proves the syscall tools
build, list, and return the kernel's own verdict shapes when driven against a
plain git repo with no phased plan — and that a foreign workspace's `dos.toml`
(`[stamp]` / `[lanes]`) reads back through the server's config layering exactly
as it does through the `dos` CLI. If the `mcp` extra isn't installed, the whole
module skips (the server can't be built without it).

The tests call each tool's REGISTERED callable (`Tool.fn`) — the real function
FastMCP holds, not a re-imported copy — so a registration regression (a tool
dropped, renamed, or mis-wired) fails here.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("mcp", reason="dos-mcp needs the optional `mcp` extra")

from dos_mcp.server import build_server


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _plain_repo(repo: Path) -> None:
    """A git repo with zero phased-plan surface (mirrors test_verify_no_plan)."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init: empty repo, no phased plan")


def _tools(server) -> dict:
    """Map {tool_name: registered_callable} from the built server."""
    return {t.name: t.fn for t in server._tool_manager.list_tools()}


# ---------------------------------------------------------------------------
# the server builds and registers exactly the syscall surface
# ---------------------------------------------------------------------------
def test_server_registers_the_syscall_tools():
    server = build_server()
    listed = asyncio.run(server.list_tools())
    # The built-in syscall ABI is the `dos_*` surface; any non-`dos_` tool came
    # from the `dos.mcp_tools` third-party seam (docs/378) — a host installing an
    # extension must not perturb the curated ABI this test pins.
    names = {t.name for t in listed if t.name.startswith("dos_")}
    assert names == {
        "dos_verify", "dos_commit_audit", "dos_review", "dos_arbitrate",
        "dos_refuse_reasons", "dos_check_reason", "dos_doctor", "dos_recall",
        "dos_status", "dos_citation_resolve", "dos_answer",
    }
    # Every tool carries a docstring-derived description (the agent-facing prose).
    assert all(t.description for t in listed)


# ---------------------------------------------------------------------------
# dos_verify — the truth syscall, through the tool, with no plan
# ---------------------------------------------------------------------------
def test_verify_tool_honest_negative_with_no_plan(tmp_path: Path):
    _plain_repo(tmp_path)
    verify = _tools(build_server())["dos_verify"]
    out = verify(plan="SOMEPLAN", phase="PH1", workspace=str(tmp_path))
    assert out["shipped"] is False
    assert out["source"] == "none"  # no registry, no matching commit — honest no
    assert out["plan"] == "SOMEPLAN" and out["phase"] == "PH1"


def test_commit_audit_tool_flags_unwitnessed_claim(tmp_path: Path):
    _plain_repo(tmp_path)
    # an EMPTY commit that claims an implementation — the claim its diff can't witness
    _git(tmp_path, "commit", "--allow-empty", "-m", "implement the caching layer")
    audit = _tools(build_server())["dos_commit_audit"]
    out = audit(ref="HEAD", workspace=str(tmp_path))
    assert out["verdict"] == "CLAIM_UNWITNESSED"
    assert out["witness"] == "subject-only"
    assert "interpretation" in out and out["interpretation"]


def test_commit_audit_tool_witnesses_a_real_change(tmp_path: Path):
    _plain_repo(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "add the app entrypoint")
    audit = _tools(build_server())["dos_commit_audit"]
    out = audit(ref="HEAD", workspace=str(tmp_path))
    assert out["verdict"] == "OK"
    assert out["witness"] == "diff-witnessed"
    assert "app.py" in out["source_files"]


def test_commit_audit_tool_unreadable_ref_is_safe(tmp_path: Path):
    _plain_repo(tmp_path)
    audit = _tools(build_server())["dos_commit_audit"]
    out = audit(ref="nope-not-a-ref", workspace=str(tmp_path))
    assert out["verdict"] == "ABSTAIN"  # safe degrade, never a crash
    assert "UNREADABLE" in out["interpretation"]


def test_verify_tool_finds_a_git_only_ship(tmp_path: Path):
    _plain_repo(tmp_path)
    # A real ship recorded only in git history, in the job direct-ship grammar.
    _git(tmp_path, "commit", "--allow-empty", "-m", "docs/RS: RS1 — ship the surfacer")
    verify = _tools(build_server())["dos_verify"]
    out = verify(plan="RS", phase="RS1", workspace=str(tmp_path))
    assert out["shipped"] is True
    # `grep-subject` (docs/118): the ship matched the commit SUBJECT of an
    # `--allow-empty` commit — the forgeable rung, surfaced through MCP too.
    assert out["source"] == "grep-subject"


def test_verify_tool_reads_back_foreign_stamp_grammar(tmp_path: Path):
    """A foreign repo's `[stamp]` grammar in dos.toml is honored by the tool.

    The dir-free generic shape (`subject_dirs = []`) recognises a bare
    `<SERIES><PHASE>:` ship — the external-repo convention. This pins that the
    server's config layering reads `[stamp]` back, the SCV seam through MCP.
    """
    _plain_repo(tmp_path)
    _git(tmp_path, "commit", "--allow-empty", "-m", "AUTH2: ship token refresh")
    (tmp_path / "dos.toml").write_text(
        '[stamp]\nstyle = "grep"\nsubject_dirs = []\n', encoding="utf-8")
    verify = _tools(build_server())["dos_verify"]
    out = verify(plan="AUTH", phase="2", workspace=str(tmp_path))
    assert out["shipped"] is True
    # `grep-subject` (docs/118): the bare `<SERIES><PHASE>:` subject match under
    # the generic grammar is still the forgeable subject rung.
    assert out["source"] == "grep-subject"
    # And the honest-negative still holds under the looser grammar.
    out2 = verify(plan="AUTH", phase="9", workspace=str(tmp_path))
    assert out2["shipped"] is False


# ---------------------------------------------------------------------------
# dos_arbitrate — the pure admission kernel, through the tool
# ---------------------------------------------------------------------------
def test_arbitrate_tool_admits_a_free_lane(tmp_path: Path):
    _plain_repo(tmp_path)
    arb = _tools(build_server())["dos_arbitrate"]
    # The generic default taxonomy has a concurrent `main` lane (tree **/*).
    out = arb(lane="main", kind="cluster", live_leases=[], workspace=str(tmp_path))
    assert out["outcome"] == "acquire"
    assert out["lane"] == "main"


def test_arbitrate_tool_refuses_a_colliding_lease(tmp_path: Path):
    """Two workers wanting the same file tree collide → refuse (no I/O)."""
    _plain_repo(tmp_path)
    # Declare two concurrent lanes whose trees would overlap when both want src/**.
    (tmp_path / "dos.toml").write_text(
        "[lanes]\n"
        'concurrent = ["api", "worker"]\n'
        'autopick = ["api", "worker"]\n'
        "[lanes.trees]\n"
        'api = ["src/**"]\n'
        'worker = ["src/**"]\n',
        encoding="utf-8",
    )
    arb = _tools(build_server())["dos_arbitrate"]
    live = [{"lane": "api", "lane_kind": "cluster", "tree": ["src/**"]}]
    # A keyword request for the same tree, with `api` already live, must refuse.
    out = arb(lane="worker", kind="keyword", tree=["src/**"],
              live_leases=live, workspace=str(tmp_path))
    assert out["outcome"] == "refuse"
    assert out["reason"]  # carries an explanation


def test_arbitrate_tool_reads_back_foreign_lane_tree(tmp_path: Path):
    """A lane's canonical tree from `dos.toml [lanes.trees]` fills an omitted tree."""
    _plain_repo(tmp_path)
    (tmp_path / "dos.toml").write_text(
        "[lanes]\n"
        'concurrent = ["web"]\n'
        'autopick = ["web"]\n'
        "[lanes.trees]\n"
        'web = ["frontend/**"]\n',
        encoding="utf-8",
    )
    arb = _tools(build_server())["dos_arbitrate"]
    out = arb(lane="web", kind="cluster", live_leases=[], workspace=str(tmp_path))
    assert out["outcome"] == "acquire"
    assert out["tree"] == ["frontend/**"]  # filled from the declared taxonomy


def test_arbitrate_tool_accepts_class_budgets_arg(tmp_path: Path):
    """The `class_budgets` pure-data dict is an accepted argument (#175).

    The concurrency-class budget the CLI threads via `--class-budget KIND=N` is
    now reachable over MCP as a `{kind: max_concurrent}` dict — pinning that the
    argument exists, is accepted, and threads through without raising.
    """
    _plain_repo(tmp_path)
    arb = _tools(build_server())["dos_arbitrate"]
    out = arb(lane="main", kind="cluster", live_leases=[],
              class_budgets={"cluster": 3}, workspace=str(tmp_path))
    assert out["outcome"] == "acquire"
    assert out["lane"] == "main"


def test_arbitrate_tool_non_binding_budget_is_back_compat(tmp_path: Path):
    """A non-binding budget yields the SAME decision as no budget (back-compat).

    On a directly-named lane the budget never bites (the kernel only consults it
    on the bare auto-pick walk), so a `class_budgets` overlay must be byte-for-byte
    identical to omitting it. This is the regression guard the issue requires.
    """
    _plain_repo(tmp_path)
    arb = _tools(build_server())["dos_arbitrate"]
    base = arb(lane="main", kind="cluster", live_leases=[], workspace=str(tmp_path))
    with_budget = arb(lane="main", kind="cluster", live_leases=[],
                      class_budgets={"cluster": 99}, workspace=str(tmp_path))
    # The interpretation gloss is downstream of the verdict; compare the whole
    # decision dict — every field, not just the outcome — to prove nothing drifted.
    assert with_budget == base
    # And explicit None is identical to omitting the argument entirely.
    none_budget = arb(lane="main", kind="cluster", live_leases=[],
                      class_budgets=None, workspace=str(tmp_path))
    assert none_budget == base


def test_arbitrate_tool_overlay_does_not_corrupt_decision_path(tmp_path: Path):
    """An explicit overlay OVERRIDES the declared `[[concurrency_class]]` default,
    and the merged budget threads into the kernel without corrupting the decision.

    The explicit argument wins per kind (flag-over-config, the CLI's precedence):
    the workspace declares `cluster=1`, the argument overlays `cluster=5`, and a
    second disjoint cluster lane is still admitted — which a binding `cluster=1`
    budget on the bare walk would have refused. So the overlay both takes effect
    (the explicit value won) and leaves the disjointness decision path intact.
    """
    _plain_repo(tmp_path)
    (tmp_path / "dos.toml").write_text(
        "[lanes]\n"
        'concurrent = ["a", "b"]\n'
        'autopick = ["a", "b"]\n'
        "[lanes.trees]\n"
        'a = ["a/**"]\n'
        'b = ["b/**"]\n'
        "[[concurrency_class]]\n"
        'name = "cluster"\n'
        "max_concurrent = 1\n",
        encoding="utf-8",
    )
    arb = _tools(build_server())["dos_arbitrate"]
    # One cluster lease already live; a second DISJOINT cluster lane is requested
    # by name (not the bare walk), so the budget never gates it — it must acquire
    # regardless of the budget value, proving the merged dict does not corrupt the
    # per-lease disjointness path.
    live = [{"lane": "a", "lane_kind": "cluster", "tree": ["a/**"]}]
    out = arb(lane="b", kind="cluster", tree=["b/**"], live_leases=live,
              class_budgets={"cluster": 5}, workspace=str(tmp_path))
    assert out["outcome"] == "acquire"
    assert out["lane"] == "b"
    assert out["tree"] == ["b/**"]


# ---------------------------------------------------------------------------
# dos_refuse_reasons / dos_check_reason — the structured-refusal vocabulary
# ---------------------------------------------------------------------------
def test_refuse_reasons_lists_the_closed_vocabulary(tmp_path: Path):
    _plain_repo(tmp_path)
    reasons = _tools(build_server())["dos_refuse_reasons"]
    out = reasons(workspace=str(tmp_path))
    tokens = {r["token"] for r in out["reasons"]}
    # The built-in base reasons are present.
    assert "LANE_DRAINED" in tokens
    assert "SELF_MODIFY" in tokens
    assert out["count"] == len(out["reasons"])
    # Each carries its verifiable category.
    drained = next(r for r in out["reasons"] if r["token"] == "LANE_DRAINED")
    assert drained["category"] == "TRUE_DRAIN"
    assert drained["refusal"] is True


def test_refuse_reasons_includes_a_declared_reason(tmp_path: Path):
    """A reason declared in dos.toml [reasons] is additive onto the base set."""
    _plain_repo(tmp_path)
    (tmp_path / "dos.toml").write_text(
        "[reasons.LANE_PARKED_FOR_BUDGET]\n"
        'category = "OPERATOR_GATE"\n'
        'summary = "lane parked: monthly token budget hit"\n',
        encoding="utf-8",
    )
    reasons = _tools(build_server())["dos_refuse_reasons"]
    tokens = {r["token"] for r in reasons(workspace=str(tmp_path))["reasons"]}
    assert "LANE_PARKED_FOR_BUDGET" in tokens
    assert "LANE_DRAINED" in tokens  # base still present (additive)


def test_check_reason_known_and_unknown(tmp_path: Path):
    _plain_repo(tmp_path)
    check = _tools(build_server())["dos_check_reason"]
    known = check(reason_class="LANE_DRAINED", workspace=str(tmp_path))
    assert known["known"] is True
    assert known["category"] == "TRUE_DRAIN"

    unknown = check(reason_class="MADE_UP_REASON", workspace=str(tmp_path))
    assert unknown["known"] is False
    assert unknown["category"] == "UNCLASSIFIED"  # the drift signal
    assert unknown["refusal"] is True  # an unknown refusal is refused conservatively


# ---------------------------------------------------------------------------
# dos_doctor — the machine-readable workspace report
# ---------------------------------------------------------------------------
def test_doctor_tool_reports_layout(tmp_path: Path):
    _plain_repo(tmp_path)
    doctor = _tools(build_server())["dos_doctor"]
    out = doctor(workspace=str(tmp_path))
    assert out["git"] is True
    assert out["workspace"] == str(tmp_path.resolve())
    assert "concurrent" in out["lanes"]
    assert "subject_dirs" in out["stamp"]
    assert out["dos_version"]


def test_doctor_tool_writes_no_dos_dir(tmp_path: Path):
    """Read-only discipline: doctor (like verify) never creates `.dos/`."""
    _plain_repo(tmp_path)
    doctor = _tools(build_server())["dos_doctor"]
    doctor(workspace=str(tmp_path))
    assert not (tmp_path / ".dos").exists()


def test_verify_tool_writes_no_dos_dir(tmp_path: Path):
    _plain_repo(tmp_path)
    verify = _tools(build_server())["dos_verify"]
    verify(plan="X", phase="X1", workspace=str(tmp_path))
    assert not (tmp_path / ".dos").exists()


# ---------------------------------------------------------------------------
# Performance contract (docs/275) — the server stays cheap PER TOOL CALL: it
# builds the config WITHOUT the EnvPrint probe (no tool reads it), and the truth
# syscall greps git IN-PROCESS instead of spawning a python interpreter. These
# pin the mechanisms, deterministically (no millisecond thresholds).
# ---------------------------------------------------------------------------
def test_server_config_skips_the_env_probe(tmp_path: Path):
    """`_load_workspace_config` passes `gather_env=False` — no tool reads `cfg.env`,
    so the per-call `git rev-parse` + platform probe is skipped (env stays None)."""
    from dos_mcp.server import _load_workspace_config

    _plain_repo(tmp_path)
    cfg = _load_workspace_config(str(tmp_path))
    assert cfg.env is None, (
        "the MCP server's config build probed the EnvPrint — the docs/275 "
        "per-call cost-skip regressed")
    # Sanity: the config is otherwise fully built (lanes/stamp/paths present), so
    # skipping env did not break the parts the tools DO read.
    assert cfg.lanes.concurrent and cfg.stamp is not None


def test_verify_tool_greps_git_in_process(tmp_path: Path, monkeypatch):
    """A `dos_verify` tool call spawns NO `python -m dos.phase_shipped` child.

    End-to-end through the real registered tool: the grep rung runs in-process, so a
    spy over the oracle's `subprocess.run` records no python-interpreter spawn (only
    git, in phase_shipped's own namespace, is allowed). Proves the docs/275 win holds
    through the server, not just in a unit test of the rung."""
    import subprocess as _sp

    from dos import oracle

    _plain_repo(tmp_path)
    _git(tmp_path, "commit", "--allow-empty", "-m", "docs/RS: RS1 — ship the surfacer")

    spawned: list[list[str]] = []
    real_run = _sp.run

    def _spy(cmd, *a, **kw):
        spawned.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(oracle.subprocess, "run", _spy)
    monkeypatch.delenv("DOS_ORACLE_GREP_SUBPROCESS", raising=False)

    verify = _tools(build_server())["dos_verify"]
    out = verify(plan="RS", phase="RS1", workspace=str(tmp_path))

    assert out["shipped"] is True  # the in-process rung still finds the ship
    python_children = [c for c in spawned
                       if any("dos.phase_shipped" in str(part) for part in c)]
    assert python_children == [], (
        f"dos_verify spawned a python subprocess for the grep rung — the docs/275 "
        f"in-process win regressed: {python_children}")


# ---------------------------------------------------------------------------
# dos_status — the folded fact, through the MCP tool (docs/120 Phase 3)
# ---------------------------------------------------------------------------
def _mint(workspace: Path) -> str:
    from dos import run_id
    return run_id.mint("dispatch").run_id


def test_status_tool_folds_the_digest_no_claimed_key(tmp_path: Path):
    """The MCP digest folds verified progress + region, and exposes NO `claimed` key.

    The fail-closed invariant (docs/120 §3) re-pinned at the MCP boundary: a peer
    reading this tool's result structurally cannot pick up a self-report.
    """
    from dos import config as _config, intent_ledger as il, lane_journal as lj
    _plain_repo(tmp_path)
    cfg = _config.default_config(tmp_path)
    rid = _mint(tmp_path)
    il.append(rid, il.intent_entry(goal="g", declared_steps=["s1", "s2"]), cfg=cfg)
    il.append(rid, il.step_claimed_entry("s1", "deadbeef" * 5), cfg=cfg)  # self-report
    # A held lane lease stamped with this run's id (the spine join → region).
    lease = {"lane": "src", "lane_kind": "cluster", "tree": ["src/dos/**"],
             "loop_ts": "L1", "host_id": "h", "pid": 1, "ttl_minutes": 30,
             "run_id": rid}
    lj.append(lj.acquire_entry(lease, run_id=rid), path=cfg.paths.lane_journal)

    status = _tools(build_server())["dos_status"]
    out = status(run_id=rid, workspace=str(tmp_path))

    assert out["run_id"] == rid
    assert out["progress"]["verified_count"] == 0     # the claim did not count
    assert out["progress"]["declared_count"] == 2
    assert out["region"] == ["src/dos/**"]            # the run's held lease
    assert "liveness" in out
    # The load-bearing invariant: no `claimed` key anywhere in the A2A shape.
    assert "claimed" not in out
    assert "claimed" not in out["progress"]
    assert "deadbeef" not in str(out)


def test_status_tool_no_intent_is_fail_closed(tmp_path: Path):
    """A run with no intent ledger → a valid zero-progress fact, not an error."""
    _plain_repo(tmp_path)
    rid = _mint(tmp_path)
    status = _tools(build_server())["dos_status"]
    out = status(run_id=rid, workspace=str(tmp_path))
    assert out["progress"]["verified_count"] == 0
    assert out["progress"]["declared_count"] == 0
    assert out["region"] == []
    assert out["resume"] is None


def test_status_tool_bad_run_id_returns_error_not_raise(tmp_path: Path):
    """A bad run-id returns an {error, run_id} dict — a FastMCP tool must not raise out."""
    _plain_repo(tmp_path)
    status = _tools(build_server())["dos_status"]
    out = status(run_id="not-a-real-rid", workspace=str(tmp_path))
    assert "error" in out
    assert out["run_id"] == "not-a-real-rid"


def test_status_tool_writes_no_dos_dir(tmp_path: Path):
    """Read-only discipline: status (like verify/doctor) never creates `.dos/`."""
    _plain_repo(tmp_path)
    rid = _mint(tmp_path)
    status = _tools(build_server())["dos_status"]
    status(run_id=rid, workspace=str(tmp_path))
    assert not (tmp_path / ".dos").exists()


# ---------------------------------------------------------------------------
# agent-friendliness: every actionable verdict carries an `interpretation`, and
# the kernel fields are left verbatim (the hint is additive, never a rewrite).
# ---------------------------------------------------------------------------
def test_verify_carries_actionable_interpretation(tmp_path: Path):
    _plain_repo(tmp_path)
    verify = _tools(build_server())["dos_verify"]
    neg = verify(plan="X", phase="X1", workspace=str(tmp_path))
    assert "interpretation" in neg
    assert neg["shipped"] is False  # kernel field untouched
    assert "not done" in neg["interpretation"].lower()

    _git(tmp_path, "commit", "--allow-empty", "-m", "docs/RS: RS1 — ship it")
    pos = verify(plan="RS", phase="RS1", workspace=str(tmp_path))
    assert pos["shipped"] is True
    assert "shipped" in pos["interpretation"].lower()


def test_arbitrate_interpretation_is_go_or_stop(tmp_path: Path):
    _plain_repo(tmp_path)
    arb = _tools(build_server())["dos_arbitrate"]
    go = arb(lane="main", kind="cluster", live_leases=[], workspace=str(tmp_path))
    assert go["outcome"] == "acquire"
    assert go["interpretation"].startswith("GO")

    live = [{"lane": "main", "lane_kind": "cluster", "tree": ["**/*"]}]
    stop = arb(lane="main", kind="keyword", tree=["**/*"],
               live_leases=live, workspace=str(tmp_path))
    assert stop["outcome"] == "refuse"
    assert stop["interpretation"].startswith("STOP")


def test_check_reason_interpretation_flags_drift(tmp_path: Path):
    _plain_repo(tmp_path)
    check = _tools(build_server())["dos_check_reason"]
    good = check(reason_class="LANE_DRAINED", workspace=str(tmp_path))
    assert "valid" in good["interpretation"].lower()
    bad = check(reason_class="NOPE", workspace=str(tmp_path))
    assert bad["known"] is False
    assert "do not emit" in bad["interpretation"].lower()


# ---------------------------------------------------------------------------
# resources — browsable vocabulary + taxonomy
# ---------------------------------------------------------------------------
def test_resources_are_registered():
    server = build_server()
    rm = server._resource_manager
    static = {str(r.uri) for r in rm.list_resources()}
    templates = {t.uri_template for t in rm.list_templates()}
    assert "dos://reasons" in static
    assert "dos://lanes" in static
    assert "dos://reasons/{workspace}" in templates
    assert "dos://lanes/{workspace}" in templates


def test_reasons_resource_renders_the_vocabulary(tmp_path: Path):
    _plain_repo(tmp_path)
    server = build_server()
    # The workspace is one URI path segment (FastMCP matches `[^/]+`), so a
    # client percent-encodes the path — without this, an absolute POSIX
    # `tmp_path` injects a bare slash and the URI is unroutable on Linux.
    ws = quote(str(tmp_path), safe="")
    contents = asyncio.run(server.read_resource(f"dos://reasons/{ws}"))
    body = contents[0].content
    assert "LANE_DRAINED" in body
    assert "refusal vocabulary" in body.lower()


def test_lanes_resource_renders_the_taxonomy(tmp_path: Path):
    _plain_repo(tmp_path)
    server = build_server()
    ws = quote(str(tmp_path), safe="")
    contents = asyncio.run(server.read_resource(f"dos://lanes/{ws}"))
    body = contents[0].content
    assert "lane taxonomy" in body.lower()
    assert "concurrent" in body.lower()


# ---------------------------------------------------------------------------
# prompts — user-invokable entry points
# ---------------------------------------------------------------------------
def test_prompts_are_registered():
    server = build_server()
    names = {p.name for p in server._prompt_manager.list_prompts()}
    assert {"verify_a_claim", "can_i_take_this_lane",
            "refuse_with_a_reason"} <= names


def test_verify_prompt_renders_an_instruction():
    server = build_server()
    res = asyncio.run(server.get_prompt(
        "verify_a_claim", {"plan": "AUTH", "phase": "AUTH2"}))
    text = res.messages[0].content.text
    assert "dos_verify" in text
    assert "AUTH" in text and "AUTH2" in text


# ---------------------------------------------------------------------------
# anti-drift: the server and the CLI build the SAME config from a workspace.
# This is the guarantee the shared `config.load_workspace_config` exists to give
# — the two surfaces used to carry byte-identical readback loops that could drift.
# ---------------------------------------------------------------------------
def test_server_and_cli_resolve_identical_config(tmp_path: Path):
    from dos import config as _config
    from dos_mcp.server import _load_workspace_config

    _plain_repo(tmp_path)
    (tmp_path / "dos.toml").write_text(
        "[lanes]\n"
        'concurrent = ["api", "web"]\n'
        'autopick = ["api"]\n'
        "[lanes.trees]\n"
        'api = ["src/api/**"]\n'
        'web = ["web/**"]\n'
        "[stamp]\n"
        'style = "grep"\n'
        'subject_dirs = ["src", "lib"]\n'
        "[reasons.LANE_PARKED_FOR_BUDGET]\n"
        'category = "OPERATOR_GATE"\n',
        encoding="utf-8",
    )
    # What the server builds (explicit-cfg path) ...
    server_cfg = _load_workspace_config(str(tmp_path))
    # ... and what the CLI's shared helper builds (it then set_active()s this).
    cli_cfg = _config.load_workspace_config(str(tmp_path))

    assert server_cfg.lanes.concurrent == cli_cfg.lanes.concurrent
    assert server_cfg.lanes.trees == cli_cfg.lanes.trees
    assert server_cfg.stamp.to_dict() == cli_cfg.stamp.to_dict()
    assert server_cfg.reasons.tokens() == cli_cfg.reasons.tokens()
    assert "LANE_PARKED_FOR_BUDGET" in server_cfg.reasons.tokens()
    assert str(server_cfg.paths.root) == str(cli_cfg.paths.root)


# ---------------------------------------------------------------------------
# dos_citation_resolve — the legal-citation witness, through the tool (issue #42).
# The corpus transport is FAKED from the frozen Mata v. Avianca sample
# (benchmark/legalcite/frozen_corpus.json), so the suite stays deterministic and
# never touches the network; the no-network case poisons the transport entirely.
# ---------------------------------------------------------------------------
_FROZEN_CORPUS = (Path(__file__).resolve().parents[1]
                  / "benchmark" / "legalcite" / "frozen_corpus.json")


def _corpus_entry(section: str, cite: str) -> dict:
    import json
    return json.loads(_FROZEN_CORPUS.read_text(encoding="utf-8"))[section][cite]


def _fake_search_transport(monkeypatch, cluster: dict | None) -> None:
    """Serve a frozen-corpus cluster through the driver's /search/ rung.

    `cluster=None` (the corpus's record of a fabrication) serves an EMPTY result
    set — exactly what the live reporter index returns for a cite it does not
    carry. Clearing COURTLISTENER_TOKEN forces the unauthenticated search rung,
    so the fake payload shape matches the rung the server will parse.
    """
    import io
    import json

    from dos.drivers import citation_resolve as cr

    results = []
    if cluster:
        results.append({"caseName": cluster["name"],
                        "citation": list(cluster["citations"]),
                        "snippet": cluster.get("opinion_text", "")})

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(cr.urllib.request, "urlopen",
                        lambda req, *a, **k: _Resp(
                            json.dumps({"results": results}).encode()))
    monkeypatch.delenv("COURTLISTENER_TOKEN", raising=False)


def test_citation_tool_flags_a_mata_fabrication(monkeypatch):
    """The issue #42 done-condition: the documented lead *Mata v. Avianca*
    fabrication returns UNRESOLVED through the MCP tool."""
    entry = _corpus_entry("fabricated", "925 F.3d 1339")
    assert entry["cluster"] is None  # the frozen ground truth: no reporter carries it
    _fake_search_transport(monkeypatch, entry["cluster"])
    tool = _tools(build_server())["dos_citation_resolve"]
    out = tool(cite="925 F.3d 1339", claimed_name=entry["claimed_name"])
    assert out["verdict"] == "UNRESOLVED"
    assert "does not resolve" in out["reason"]


def test_citation_tool_flags_the_collision_slot(monkeypatch):
    """A REAL reporter slot carrying a DIFFERENT case than claimed is UNRESOLVED
    (the docs/279 §3 collision — resolution alone would rubber-stamp it)."""
    entry = _corpus_entry("fabricated", "92 F.3d 1074")  # claimed Hyatt, really Grilli
    _fake_search_transport(monkeypatch, entry["cluster"])
    tool = _tools(build_server())["dos_citation_resolve"]
    out = tool(cite="92 F.3d 1074", claimed_name=entry["claimed_name"])
    assert out["verdict"] == "UNRESOLVED"
    assert "DIFFERENT case" in out["reason"]
    assert "Grilli" in out["matched_name"]


def test_citation_tool_resolves_a_real_case(monkeypatch):
    entry = _corpus_entry("real", "576 U.S. 644")
    _fake_search_transport(monkeypatch, entry["cluster"])
    tool = _tools(build_server())["dos_citation_resolve"]
    out = tool(cite="576 U.S. 644", claimed_name=entry["claimed_name"])
    assert out["verdict"] == "RESOLVED_MATCH"
    assert "Obergefell" in out["matched_name"]


def test_citation_tool_abstains_with_no_network_no_token(monkeypatch):
    """The other issue #42 done-condition half: no token + no network → ABSTAIN,
    never a fabricated verdict (the fail-safe floor, through the MCP surface)."""
    import urllib.error

    from dos.drivers import citation_resolve as cr

    def boom(*a, **k):
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr(cr.urllib.request, "urlopen", boom)
    monkeypatch.delenv("COURTLISTENER_TOKEN", raising=False)
    tool = _tools(build_server())["dos_citation_resolve"]
    out = tool(cite="925 F.3d 1339",
               claimed_name="Varghese v. China Southern Airlines")
    assert out["verdict"] == "ABSTAIN"
    assert out["evidence"]["reachable"] is False


# ---------------------------------------------------------------------------
# The tool-call deadline — the server applies the kernel's STALLED verdict to
# its own MCP surface (docs/282): a tool body that never returns yields a typed
# STALLED envelope within the budget instead of hanging the host. The only
# witnessed fact is the missed deadline; the cause (a peer git lock, transport
# contention, a slow FS) is offered as an UNRANKED differential, never asserted
# as fact. Here we block deterministically on an Event that is never set.
# ---------------------------------------------------------------------------
def test_deadline_fast_body_passes_through_byte_identical():
    """A body that returns in time is unchanged — the deadline is invisible."""
    import time

    from dos_mcp.server import _with_deadline

    def body(x: int) -> dict:
        return {"verdict": "OK", "echo": x}

    wrapped = _with_deadline(body, 5000)
    t0 = time.perf_counter()
    out = wrapped(7)
    assert out == {"verdict": "OK", "echo": 7}
    assert (time.perf_counter() - t0) < 1.0  # nowhere near the 5 s budget


def test_deadline_blocked_body_returns_typed_stalled_within_budget():
    """A body that never returns yields a STALLED verdict ~at the budget, no hang."""
    import threading
    import time

    from dos_mcp.server import _with_deadline

    never = threading.Event()  # never set → body blocks forever

    def stuck() -> dict:
        never.wait()
        return {"verdict": "OK"}  # unreachable

    wrapped = _with_deadline(stuck, 100)  # 100 ms budget
    t0 = time.perf_counter()
    out = wrapped()
    elapsed = time.perf_counter() - t0
    # Returned promptly (budget + thread-join slack), NOT hung:
    assert elapsed < 2.0, f"deadline did not fire promptly ({elapsed:.2f}s)"
    assert out["verdict"] == "STALLED"
    # The envelope names the transport stall + the CLI fallback (the witness):
    assert "deadline" in out["reason"].lower()
    assert "cli" in out["fallback"].lower()
    assert "advis" in out["advice"].lower()  # advisory: do not auto-retry
    # Honesty property (the kernel's own rule applied to its stall envelope):
    # the reason states ONLY the witnessed missed-budget fact and explicitly
    # disclaims a root cause; the differential is an unranked list, not a guess
    # dressed as a finding.
    reason = out["reason"].lower()
    assert "not established" in reason or "witnessed fact" in reason
    assert "is blocked" not in reason  # no unwitnessed cause asserted as fact
    causes = out["candidate_causes"]
    assert isinstance(causes, list) and len(causes) >= 2  # a differential, not one cause
    blob = " ".join(causes).lower()
    assert "git" in blob and ("transport" in blob or "contention" in blob)


def test_deadline_zero_budget_is_identity_passthrough(monkeypatch):
    """Budget 0 (env opt-out) returns the body untouched — pre-deadline behavior."""
    from dos_mcp import server as srv

    def body() -> dict:
        return {"verdict": "OK"}

    assert srv._with_deadline(body, 0) is body

    # And the env policy resolves 0/blank/garbage safely.
    monkeypatch.setenv("DOS_MCP_TOOL_DEADLINE_MS", "0")
    assert srv._tool_deadline_ms() == 0
    monkeypatch.setenv("DOS_MCP_TOOL_DEADLINE_MS", "not-a-number")
    assert srv._tool_deadline_ms() == 5000  # falls back to the default
    monkeypatch.delenv("DOS_MCP_TOOL_DEADLINE_MS", raising=False)
    assert srv._tool_deadline_ms() == 5000  # default when unset


def test_deadline_reraises_a_body_error():
    """An exception in the body is re-raised on the caller, not swallowed."""
    from dos_mcp.server import _with_deadline

    def boom() -> dict:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        _with_deadline(boom, 5000)()


def test_deadline_preserves_tool_schema_for_fastmcp():
    """Wrapping must not break FastMCP introspection — names/params survive."""

    async def _list():
        return await build_server().list_tools()

    tools = asyncio.run(_list())
    by_name = {t.name: t for t in tools}
    # The tool that hung in the field keeps its real name + params:
    assert "dos_commit_audit" in by_name
    props = (by_name["dos_commit_audit"].inputSchema or {}).get("properties", {})
    assert "ref" in props and "workspace" in props
    # No wrapper internals leaked as a tool name:
    assert not ({"wrapper", "_decorate", "fn"} & set(by_name))
