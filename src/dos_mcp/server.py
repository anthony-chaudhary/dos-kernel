"""The FastMCP server — DOS syscalls as MCP tools.

Run it as ``dos-mcp`` (the console script) or ``python -m dos_mcp.server``. It
serves over stdio by default, which is what an MCP host (Claude Desktop, Cursor,
Cline, …) launches and talks to. See the package docstring for the design fence:
this consumes `dos`, the kernel never imports it.
"""

from __future__ import annotations

import functools
import os
import sys
import threading
from typing import Any
from urllib.parse import unquote

# Force UTF-8 on the streams, matching the spine modules' discipline — a verdict
# summary / man line may carry an em-dash or middot, and a host on a cp1252
# console must not crash the server on it. (The MCP transport is JSON, but be
# defensive about any stray stderr logging too.)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as e:  # pragma: no cover - install-hint path
    raise SystemExit(
        "dos-mcp requires the MCP server framework, which is an optional extra.\n"
        "Install it with:  pip install 'dos-kernel[mcp]'   (or:  pip install mcp)\n"
        f"(original import error: {e})"
    )

import dos  # noqa: E402 — intentionally after the MCP-framework import guard above
from dos import config as _config  # noqa: E402 — (so a missing [mcp] extra fails with a hint)
from dos import interpret as _interpret  # noqa: E402 — shared with the CLI's --explain

from dos_mcp import answers as _answers  # noqa: E402 — the answer-corpus retrieval surface


# ---------------------------------------------------------------------------
# Workspace config — the `dos` CLI's four-table dos.toml readback, shared.
#
# The readback (generic base + [reasons]/[stamp]/[lanes]/[paths]) lives in ONE
# place, `config.load_workspace_config`, which the CLI also calls — so the two
# surfaces can't drift. The server's only divergence from the CLI is what it
# does with the result: the CLI `set_active`s it (correct for a one-shot
# process); the server passes it EXPLICITLY into each syscall
# (`oracle.is_shipped(cfg=...)`, `arbiter.arbitrate(config=...)`) — the
# "explicit SubstrateConfig in code" rung — because a long-lived server fields
# concurrent calls against different workspaces and must never mutate a
# process-global. A malformed table is routed to stderr as a server log line
# (MCP hosts capture stderr), never crashing a tool that doesn't touch that axis.
# ---------------------------------------------------------------------------
_UNKNOWN_WORLD_LEASE = {
    "lane": "<unreadable-lane-journal>",
    "lane_kind": "",
    "tree": ["**/*"],
    "mode": "exclusive",
    "holder": "<unknown>",
}


def _live_lease_set(
    cfg: "_config.SubstrateConfig", supplied: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], str]:
    """Return the arbitration world and its provenance; journal faults fail closed."""
    if supplied is not None:
        return list(supplied), "caller"
    from dos import lane_lease
    try:
        return list(lane_lease.live_leases(cfg)), "lane-journal"
    except Exception as exc:
        print(
            f"[dos-mcp] lane journal unreadable ({exc!r}); arbitrating "
            "fail-closed against an unknown world",
            file=sys.stderr,
        )
        return [dict(_UNKNOWN_WORLD_LEASE)], "unreadable"


def _load_workspace_config(workspace: str | None) -> "_config.SubstrateConfig":
    """Build the config for ``workspace`` (None/"." → cwd), folding in dos.toml.

    Thin adapter over `config.load_workspace_config`; see that function for the
    layering + asymmetry contract. A workspace with no ``dos.toml`` is
    byte-identical to the generic built-in default.
    """
    def _warn(label: str, message: str) -> None:
        print(f"[dos-mcp] ignoring malformed [{label}] in "
              f"{workspace or '.'}/dos.toml: {message}", file=sys.stderr)

    # `gather_env=False`: NONE of this server's tools read `cfg.env` (the runtime
    # EnvPrint — kernel version/SHA/platform/tools), so probing it on every tool
    # call wasted a `git rev-parse` subprocess + (first call) a WMI platform query
    # — ~tens of ms per call for a field thrown away. Skipping it leaves `env=None`
    # (the documented "not recorded" state every consumer already handles). If a
    # future tool needs the print, build that one call's config with the default
    # (gather_env=True) — the gatherer memoizes per process, so the cost is paid
    # at most once for the server's lifetime.
    return _config.load_workspace_config(workspace, gather_env=False, warn=_warn)


def _review_subjects(rev_range: str, root: str, limit: int = 500) -> dict[str, str]:
    """sha -> subject labels for `dos_review`, gathered at the MCP boundary."""
    from dos.vcs import active_vcs

    lines = active_vcs(root=root).log_lines(
        (f"-{int(limit)}", "--pretty=format:%H\x1f%s", rev_range))
    if not lines:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        if "\x1f" in line:
            sha, subj = line.split("\x1f", 1)
            out[sha.strip()] = subj
    return out


# ---------------------------------------------------------------------------
# Agent-facing interpretation — turn a kernel verdict into one line of "what
# this means for your NEXT action."
#
# The functions themselves live in `dos.interpret` (the kernel-side presentation
# seam, beside `dos.render`), NOT here — so the `dos` CLI's `--explain` flag and
# these MCP tools call the SAME code and can never drift (the parity is
# structural, pinned by tests/test_interpret_parity.py). They are PURE
# PRESENTATION, added to a tool's return as an `interpretation` field ALONGSIDE
# the kernel's own verbatim verdict fields — never replacing them. This honors
# the renderer invariant (HACKING.md Axis 4 / docs/76): the hint is strictly
# downstream of an already-decided verdict, so it can never leak policy back into
# the adjudication. The worst a wrong hint can do is read awkwardly; it cannot
# mis-verify a ship or mis-admit a lease. The point is Claude-friendliness: a
# model acts better on "treat as NOT done; do not rely on a worker's claim" than
# on a bare `{"shipped": false}`.
# ---------------------------------------------------------------------------
# The tool-call deadline — the kernel's STALLED verdict, applied to this server.
#
# A tool body that never returns is the server narrating "I'm working" while
# making NO forward progress — exactly `liveness.Verdict.STALLED` ("no fresh
# heartbeat, no commits — dead/hung"). The kernel preaches that distrust for a
# WORKER run; we apply it to our own MCP surface: a tool call is a mini-run, so
# bound it with a wall-clock deadline and, on expiry, return a TYPED STALLED
# envelope from the closed verdict vocabulary instead of hanging the host.
#
# This matters most on a hot, multi-session tree: a peer's `git commit` holds
# `.git/index.lock`, and a syscall that shells `git show HEAD` / `git diff`
# blocks on it. The CLI computing the SAME verdict in ~300 ms is the
# ground-truth witness that the kernel logic is healthy — the stall is the
# TRANSPORT, so the envelope says "fall back to the CLI," and (docs/99) it is
# advisory: surface, do NOT auto-retry (a retry on a held lock just stalls
# again — the poll-loop antipattern). See docs/282.
#
# The budget is POLICY: env `DOS_MCP_TOOL_DEADLINE_MS` (default 5000); 0/blank
# disables the wrapper entirely (byte-identical to the pre-deadline server, so
# a host that wants the old unbounded behavior opts out). Mechanism only — no
# `src/dos/` leaf is touched; the one-way arrow (dos_mcp imports dos) holds.
# ---------------------------------------------------------------------------
def _tool_deadline_ms() -> int:
    """The per-tool wall-clock budget in ms (env-driven policy; 0 disables)."""
    raw = os.environ.get("DOS_MCP_TOOL_DEADLINE_MS", "5000").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 5000


def _with_deadline(fn: Any, budget_ms: int) -> Any:
    """Race ``fn`` against ``budget_ms``; on expiry return a typed STALLED dict.

    The tool bodies are synchronous (they shell git / read files), so a blocked
    call would hang the event loop. We run the body in a daemon thread and join
    with the budget: if it is still alive past the deadline, the call returns a
    STALLED verdict promptly and the zombie thread is left to drain when the OS
    resource frees (a daemon thread can't be force-killed in CPython — acceptable
    for a stall escape hatch; the point is the *call* returns, not that the work
    is reaped). The fast path (body returns in time) passes through byte-identical.
    """
    if budget_ms <= 0:
        return fn

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        box: dict[str, Any] = {}

        def _run() -> None:
            try:
                box["result"] = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
                box["error"] = exc

        worker = threading.Thread(target=_run, name=f"dos-mcp:{fn.__name__}",
                                  daemon=True)
        worker.start()
        worker.join(budget_ms / 1000.0)
        if worker.is_alive():
            return {
                "verdict": "STALLED",
                # The ONLY witnessed fact is the missed deadline. The kernel's own
                # rule -- never state what you cannot witness -- applies to its stall
                # envelope too: the prior text asserted "the git index lock is
                # blocked" as the cause, but a stall on a hot multi-session box is
                # observed with NO `.git/index.lock` present and the CLI shelling
                # the same git never blocking. So the reason names only the missed
                # budget; the differential goes in `candidate_causes`, unranked.
                "reason": (
                    f"tool {fn.__name__!r} did not return within its {budget_ms} ms "
                    "deadline. The only witnessed fact is the missed budget -- the "
                    "root cause is NOT established here (see candidate_causes)."
                ),
                "candidate_causes": [
                    "a peer process holding a git write lock (e.g. "
                    "`.git/index.lock` during a concurrent commit)",
                    "MCP-server / stdio-transport contention when many servers "
                    "share one host (a fleet of agent sessions)",
                    "a slow or contended filesystem under the workspace",
                ],
                "fallback": (
                    "The kernel verdict is reachable on the CLI, which stays healthy "
                    "under the same load that stalls this transport (verified: the "
                    "CLI returns the same verdict in ~1-2 s while this call timed "
                    "out): run the matching `dos` verb. This stall is the TRANSPORT, "
                    "not the syscall."
                ),
                "advice": (
                    "Advisory (do not auto-retry on a timeout -- if a lock IS held, a "
                    "retry just stalls again; the poll-loop antipattern). Surface "
                    "this and either use the CLI or wait and retry once the host "
                    "quiesces."
                ),
            }
        if "error" in box:
            raise box["error"]
        return box.get("result")

    return wrapper


# ---------------------------------------------------------------------------
# Per-verdict deep-answer link — one hop from a verdict to its canonical page.
#
# A verdict tool answers "is this claim true?"; the answer corpus
# (docs/answers/*.md) explains the WHY and the HOW in depth. An agent that hits a
# `dos_verify` result should be one fetch from the page that teaches the move —
# so each tool's return carries a `learn_more` URL to its canonical answer page.
#
# ONE mapping (tool name → answer slug), ONE helper that stamps the blob URL,
# applied UNIFORMLY by wrapping every tool body at registration (the same seam
# `_with_deadline` uses) so every return path — including a tool's early-return
# error envelope — is covered without editing nine separate return sites. Like
# `interpretation`, this is PURE PRESENTATION added ALONGSIDE the verbatim verdict
# fields, strictly downstream of an already-decided verdict (the renderer
# invariant): a wrong link can only read awkwardly, never mis-adjudicate.
# tests/test_mcp_answer.py pins every slug here to a page that exists on disk, so
# a renamed/deleted page fails loudly (like test_answers.py's link resolver).
# ---------------------------------------------------------------------------
_ANSWER_BLOB_URL = "https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/{slug}.md"

_ANSWER_FOR_TOOL = {
    "dos_verify": "how-to-verify-an-ai-agent-actually-did-the-work",
    "dos_commit_audit": "does-the-commit-message-match-what-changed",
    "dos_review": "stop-re-reviewing-code-the-machine-already-verified",
    "dos_arbitrate": "how-to-stop-two-ai-agents-overwriting-each-other",
    "dos_refuse_reasons": "refuse-an-agent-action-with-a-structured-reason",
    "dos_check_reason": "refuse-an-agent-action-with-a-structured-reason",
    "dos_recall": "recalled-agent-memory-is-stale-how-to-reverify",
    "dos_doctor": "make-an-agent-prove-the-work-not-self-certify",
    "dos_status": "verify-what-a-subagent-claims-before-folding",
    "dos_citation_resolve": "how-to-verify-a-cited-legal-case-exists",
}


def _with_learn_more(fn: Any) -> Any:
    """Stamp the tool's `learn_more` answer-page URL onto its dict return.

    A no-op for a tool with no mapping or a non-dict return. Never overwrites a
    `learn_more` the body set itself (a tool may point somewhere more specific).
    """
    slug = _ANSWER_FOR_TOOL.get(fn.__name__)
    if slug is None:
        return fn

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        out = fn(*args, **kwargs)
        if isinstance(out, dict) and "learn_more" not in out:
            out["learn_more"] = _ANSWER_BLOB_URL.format(slug=slug)
        return out

    return wrapper


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------
def build_server() -> FastMCP:
    """Construct the FastMCP server with the DOS syscall tools registered.

    Factored out of `main()` so a test can build the server and introspect /
    call the tools without starting the stdio transport.
    """
    mcp = FastMCP(
        "dos",
        instructions=(
            "DOS — the domain-free trust substrate for fleets of autonomous "
            "agents. The kernel is the part that doesn't believe the agents. "
            "Use `dos_verify` to confirm a claim landed from git evidence rather "
            "than a worker's self-report; `dos_arbitrate` to decide whether two "
            "workers may run concurrently without colliding on the same files; "
            "`dos_refuse_reasons` / `dos_check_reason` to refuse with a "
            "structured, verifiable reason from a closed vocabulary instead of "
            "free-text; and `dos_citation_resolve` to check that a cited legal "
            "case actually exists in a third-party reporter before relying on "
            "it. Workspace-scoped tools take an optional `workspace` (a repo "
            "path); it defaults to the server's working directory."
        ),
    )

    # Install the tool-call deadline transparently: wrap `mcp.tool` so every
    # `@mcp.tool()` registration below races its body against the wall-clock
    # budget and returns a typed STALLED verdict on expiry instead of hanging
    # (docs/282). `functools.wraps` in `_with_deadline` preserves the function
    # name, docstring, and signature, so FastMCP's schema introspection of each
    # tool is unchanged — the deadline is invisible to the wire contract. When
    # the budget is 0 (env opt-out) `_with_deadline` returns the body untouched,
    # so this is byte-identical to the pre-deadline server.
    _budget_ms = _tool_deadline_ms()
    _raw_tool = mcp.tool

    def _tool(*d_args: Any, **d_kwargs: Any):
        _register = _raw_tool(*d_args, **d_kwargs)

        def _decorate(fn: Any) -> Any:
            # learn_more wraps the body (inner) so a real dict return gets the
            # deep-answer link; the deadline wraps that (outer) so a STALL still
            # short-circuits with its typed envelope. functools.wraps in both
            # preserves the name/signature FastMCP introspects.
            return _register(_with_deadline(_with_learn_more(fn), _budget_ms))

        return _decorate

    mcp.tool = _tool  # type: ignore[method-assign]

    @mcp.tool()
    def dos_verify(plan: str, phase: str, workspace: str = ".") -> dict[str, Any]:
        """Did (plan, phase) actually ship? — the truth syscall.

        USE THIS WHEN: another agent (or the user) *claims* a task/phase/feature
        is done, and you want to confirm it from real evidence before relying on
        it or building on top of it. This is the antidote to a self-narrating
        worker: it answers from artifacts, never from anyone's word.

        It checks, in order: a run-registry row (status=done), then a git-log
        grep over the workspace's ship-commit grammar, then an honest
        `source="none"` when there is no positive evidence. Works against a plain
        git repo with **no plan and no registry** — point it at any repo.

        Args:
            plan: the plan / series id (e.g. "AUTH", "RS").
            phase: the phase id within that plan (e.g. "AUTH2", "RS1").
            workspace: the repo root to verify against (default: cwd). Its
                `dos.toml [stamp]` grammar is honored if present.

        Returns {plan, phase, shipped, source, sha?, summary?, interpretation}.
        `shipped` is the closed binary judgment; `source` names which authority
        answered ("registry" | "grep" | "none") — a thin answer can never
        masquerade as a strong one. `interpretation` (added by this server) tells
        you in one line what the verdict means for your next action.
        """
        from dos import oracle
        cfg = _load_workspace_config(workspace)
        verdict = oracle.is_shipped(plan, phase, cfg=cfg).to_dict()
        verdict["interpretation"] = _interpret.verify(verdict)
        return verdict

    @mcp.tool()
    def dos_commit_audit(ref: str = "HEAD", workspace: str = ".") -> dict[str, Any]:
        """Does a commit's CLAIM match what its DIFF actually did? — author-neutral.

        USE THIS WHEN: you (or a worker) are about to report a commit as "done," OR
        you are reviewing someone's commit and want to know whether its message can
        be trusted. It is the plan-free form of the truth syscall: a commit subject
        is forgeable (whoever wrote the message authored it), the files it touched
        are not (git did). So it catches a `fix: ...` that touched only a README, an
        `--allow-empty "shipped"`, or a "tests pass" that deleted the assertions —
        the SAME way whether a human or an agent wrote the commit.

        Needs no plan, no phase, no config — point it at any commit in any git repo.
        It grades did-the-diff-do-the-KIND-of-thing-claimed, NEVER whether the code
        is correct (run the tests for that).

        Args:
            ref: a commit ref (default "HEAD"). A `A..B` range is NOT supported by
                this tool — call it per-commit so each verdict is its own object.
            workspace: the repo root the commit lives in (default: cwd).

        Returns {sha, verdict ("OK"|"CLAIM_UNWITNESSED"|"ABSTAIN"), claim_kind,
        witness ("diff-witnessed"|"subject-only"|"abstain"), reason, source_files,
        test_files, interpretation}. `witness` is the forgeability rung:
        `diff-witnessed` is non-forgeable evidence; `subject-only` means the claim
        rests on the message text alone. `interpretation` (added by this server)
        tells you in one line what to do next.
        """
        from dos import commit_audit as _ca
        cfg = _load_workspace_config(workspace)
        v = _ca.audit_commit(ref, root=cfg.paths.root)
        if v is None:
            out = {
                "sha": "", "verdict": "ABSTAIN", "claim_kind": "none",
                "witness": "abstain",
                "reason": f"cannot read commit '{ref}' (not a git repo, or bad ref)",
                "source_files": [], "test_files": [],
            }
            out["interpretation"] = (
                "UNREADABLE — the ref could not be read; there is no commit to audit "
                "here. Check the ref and the workspace path.")
            return out
        out = v.to_dict()
        out["interpretation"] = _interpret.commit_audit(out)
        return out

    @mcp.tool()
    def dos_review(rev_range: str = "HEAD~20..HEAD", workspace: str = ".") -> dict[str, Any]:
        """Review the RESIDUAL, not the diff — where is a human's attention actually owed?

        USE THIS WHEN: you (or a host's CI gate) are about to review a RANGE of
        commits and want to spend attention only where the kernel could NOT already
        confirm the change. It re-projects `dos_commit_audit`'s per-commit verdict
        into three attention bands so a reviewer reads the part that matters first:

          * CLEARED — the claim is `diff-witnessed` / `data-witnessed`: the kernel
            corroborated the SHAPE of the change against the file set git itself
            recorded. ~0 attention for "did this do what it said".
          * RESIDUAL — a claim the diff could NOT witness (`subject-only` /
            CLAIM_UNWITNESSED). This is the 100% — the only place review attention
            buys something the machine couldn't get. The CI gate fires on this band.
          * UNVERIFIABLE — the commit made no checkable claim (ABSTAIN). Still
            reviewable, but lower priority than an unwitnessed CLAIM.

        Plus an advisory SEMANTIC lens that re-flags ALREADY-cleared commits touching
        a risk surface (concurrency, auth, money, crypto, deletion). It can only ask
        for MORE eyes, never fewer, so it can never hide a residual. Carries ZERO new
        trust over `dos_commit_audit`: the bands are a pure re-projection of the same
        rung, sorted by commit instead of folded into a drift rate. Read-only — it
        adjudicates nothing and writes nothing.

        Args:
            rev_range: the git range to review, e.g. "origin/master..HEAD" or
                "HEAD~20..HEAD" (the default). A single ref reviews just that commit.
            workspace: the repo root the commits live in (default: cwd).

        Returns {rev_range, n_commits, checkable, cleared_rate, residual[], cleared[],
        unverifiable[], semantic[], residual_count, has_residual, interpretation}.
        `has_residual` is the one-bit CI gate (the `dos review` exit-1 condition);
        `interpretation` (added by this server) tells you in one line what to do next.
        """
        from dos import commit_audit as _ca
        from dos import residual_review as _rr

        cfg = _load_workspace_config(workspace)
        root = str(cfg.paths.root)
        verdicts = _ca.audit_range(rev_range, root=root)
        plan = _rr.plan_review(
            verdicts, rev_range, subjects=_review_subjects(rev_range, root))
        out = _rr.plan_to_dict(plan)
        n_resid = len(plan.residual)
        out["residual_count"] = n_resid
        out["has_residual"] = n_resid > 0
        if n_resid:
            pct = round(plan.cleared_rate * 100)
            out["interpretation"] = (
                f"RESIDUAL — {n_resid} commit(s) make a claim git could not witness; "
                f"that is the {100 - pct if plan.checkable else 100}% a human must read. "
                f"The kernel already cleared {pct}% of {plan.checkable} checkable "
                f"claim(s) — skip those. `dos review` would exit non-zero here.")
        else:
            out["interpretation"] = (
                "CLEAN — every checkable claim in the range was witnessed by its own "
                "diff; there is no residual a human must read. `dos review` exits 0.")
        return out

    @mcp.tool()
    def dos_arbitrate(
        lane: str = "",
        kind: str = "",
        mode: str = "",
        tree: list[str] | None = None,
        live_leases: list[dict[str, Any]] | None = None,
        force: bool = False,
        class_budgets: dict[str, int] | None = None,
        workspace: str = ".",
    ) -> dict[str, Any]:
        """May a worker take this lane right now? — the pure admission kernel.

        USE THIS WHEN: you are about to start work that touches a set of files
        (or dispatch a sub-agent to), and other agents may be working in the same
        repo concurrently. Call this FIRST to find out whether your file-tree
        collides with work already in flight. It is the mechanism that stops two
        agents editing the same files at once.

        State in, decision out. By default the MCP boundary reconstructs live
        leases from the workspace lane journal, matching the CLI; callers may
        inject `live_leases` explicitly for a snapshot/pure test. The arbiter then
        uses the workspace taxonomy and lock-mode tree rule (shared/shared may
        overlap; anything with an exclusive holder must be tree-disjoint).

        Args:
            lane: the requested lane ("" = a bare auto-pick request — the arbiter
                walks the workspace's autopick ladder for a free, disjoint lane).
            kind: "cluster" | "keyword" | "global" | "" (bare → auto-pick).
            mode: "exclusive" | "shared" | "" (bare → exclusive). Shared leases
                may overlap other shared leases; anything involving an exclusive
                holder conflicts on intersecting trees.
            tree: the requested file tree as repo-relative globs. If omitted and
                a `lane` is named, the lane's canonical tree from `dos.toml` is
                used.
            live_leases: optional explicit lease snapshot, a list of dicts each
                with at least {lane, lane_kind, tree}. Omitted reads the workspace
                lane journal; explicit [] means an intentionally empty world.
            force: operator override — honor an explicit `lane` literally, skip
                the disjointness refuse (still respects a live exclusive lane).
            class_budgets: OPTIONAL per-kind concurrency-class budgets, a pure-data
                `{lane_kind: max_concurrent}` dict — how many live leases of a
                given KIND may be held at once. This OVERLAYS the workspace's
                declared `[[concurrency_class]]` defaults (`dos.toml`), with the
                explicit argument winning per kind — the SAME flag-over-config
                precedence the CLI's `--class-budget KIND=N` has. `None` (default)
                ⇒ just the declared defaults (none, in the generic workspace) ⇒
                byte-identical to the pre-budget walk. REACHABILITY SCOPE: a budget
                BITES only on the BARE auto-pick walk and only where a host supplies
                an `auto_pick_order` pool of a budgeted kind (so the arbiter can
                count live holders of that kind and skip an (N+1)-th) — the SAME
                documented limitation the CLI flag has, hence true parity. A
                directly-named or `force`d lane is never budget-gated. Per the
                scope note on the issue, only this pure DATA dict is exposed here;
                the callable pool / tree-deriver are NOT MCP-passable.
            workspace: the repo root whose lane taxonomy to arbitrate over
                (default: cwd). Its `dos.toml [lanes]` is honored if present.

        Returns {outcome ("acquire"|"refuse"), lane, lane_kind, tree,
        auto_picked, reason, free_clusters, pick_count, interpretation}. On a
        refuse, `reason` explains why and `free_clusters` lists lanes you could
        take instead. `interpretation` (added by this server) is a one-line
        GO/STOP verdict for your next action.

        Note: unlike `dos arbitrate --force` on the CLI, this tool never persists
        a decision — it is a pure adjudication. An MCP tool decides; it does not
        write to the workspace.
        """
        from dos import arbiter
        from dos.admission import built_in_predicates
        cfg = _load_workspace_config(workspace)
        req_tree = list(tree or [])
        if not req_tree and lane:
            req_tree = cfg.lanes.tree_for(lane)
        # Class budgets: the config-declared [[concurrency_class]] set, OVERLAID by
        # the explicit `class_budgets` argument (the explicit value wins per kind) —
        # the SAME flag-over-config precedence the CLI's --class-budget gives, just
        # fed as a ready-made `{kind: N}` dict instead of `KIND=N` strings (an MCP
        # client can't pass the CLI's flag form, but it can pass pure data). The
        # `or None` collapses an empty merge back to the no-budget sentinel, so the
        # arbiter sees a byte-identical request to the pre-budget walk when neither
        # the config nor the argument supplies a budget (the regression guard).
        budgets = dict(cfg.class_budgets.as_arbiter_budgets())
        if class_budgets:
            budgets.update(class_budgets)
        # Scope the SELF_MODIFY guard to the kernel-source files that actually
        # exist under the SERVED workspace: a foreign repo's `**/*` lane cannot
        # edit a `src/dos/` file that isn't there, so it must not trip the guard.
        # We pass `config=cfg` so the guard reads the CACHED `cfg.workspace` facts
        # `_load_workspace_config` already gathered — no second disk probe per
        # tool call, which matters for a long-lived server fielding concurrent
        # workspaces (the explicit-config / no-global-mutation discipline). These
        # are the workspace-scoped BUILT-INS only (no `dos.predicates` plugin
        # discovery — this tool stays plugin-free, matching its prior behavior).
        # Match `dos arbitrate` / `lease-lane acquire`: omission means the
        # workspace's real live set. Explicit [] remains the pure/testing override.
        live, lease_source = _live_lease_set(cfg, live_leases)
        decision = arbiter.arbitrate(
            requested_lane=lane or "",
            requested_kind=kind or "",
            requested_tree=req_tree,
            requested_mode=mode or "",
            live_leases=live,
            config=cfg,
            force=force,
            predicates=built_in_predicates(config=cfg),
            class_budgets=budgets or None,
        ).to_dict()
        decision["interpretation"] = _interpret.arbitrate(decision)
        decision["lease_source"] = lease_source
        return decision

    @mcp.tool()
    def dos_refuse_reasons(workspace: str = ".") -> dict[str, Any]:
        """The closed structured-refusal vocabulary for this workspace.

        USE THIS WHEN: you need to decline / refuse / report-blocked and want to
        do it with a *structured* reason the system can verify, instead of
        free-text prose. Browse this list, pick the token that fits, and emit
        THAT (verify it first with `dos_check_reason`).

        DOS refuses with a *reason from a closed set* — every reason is
        simultaneously **emittable** (a producer may stamp it), **verifiable** (an
        oracle can check the condition it names), and **refusable** (the loop
        knows to route it to a replan). That is what makes "no" a first-class,
        auditable value rather than a dead end.

        Args:
            workspace: the repo root (default: cwd). Reasons declared in its
                `dos.toml [reasons]` table are included alongside the built-ins.

        Returns {workspace, count, reasons: [{token, category, refusal, summary,
        fix, see_also}, ...]}. `category` is the coarse class the reason rolls up
        to (TRUE_DRAIN | OPERATOR_GATE | STALE_CLAIM | MISROUTE | UNCLASSIFIED);
        `refusal` is whether carrying it blocks (vs advisory-only).
        """
        cfg = _load_workspace_config(workspace)
        reg = cfg.reasons
        return {
            "workspace": str(cfg.paths.root),
            "count": len(reg.specs),
            "reasons": [
                {
                    "token": s.key,
                    "category": s.category,
                    "refusal": s.refusal,
                    "summary": s.summary,
                    "fix": s.fix,
                    "see_also": list(s.see_also),
                }
                for s in reg.specs
            ],
        }

    @mcp.tool()
    def dos_check_reason(reason_class: str, workspace: str = ".") -> dict[str, Any]:
        """Is `reason_class` a member of the closed refusal vocabulary?

        USE THIS WHEN: you have a reason token in mind for a refusal and want to
        confirm it is real BEFORE emitting it. The companion to
        `dos_refuse_reasons`: emit only a reason this returns `known=true` for.
        An unknown token is the `UNCLASSIFIED` prose-drift the kernel exists to
        kill — this tool surfaces it as a bug to declare, not tolerate.

        Args:
            reason_class: the reason token to check (case-insensitive, e.g.
                "LANE_DRAINED").
            workspace: the repo root (default: cwd); its declared reasons count.

        Returns {reason_class, known, category, refusal, summary?, fix?,
        interpretation}. When `known` is false, `category` is "UNCLASSIFIED" and
        `refusal` is true (an unrecognised refusal is refused conservatively);
        `interpretation` tells you whether it is safe to emit.
        """
        cfg = _load_workspace_config(workspace)
        reg = cfg.reasons
        spec = reg.get(reason_class)
        out: dict[str, Any] = {
            "reason_class": reason_class,
            "known": spec is not None,
            "category": reg.category_for(reason_class),
            "refusal": reg.is_refusal(reason_class),
        }
        if spec is not None:
            out["summary"] = spec.summary
            out["fix"] = spec.fix
            out["see_also"] = list(spec.see_also)
        out["interpretation"] = _interpret.check_reason(out)
        return out

    @mcp.tool()
    def dos_recall(name: str, workspace: str = ".", store: str = "") -> dict[str, Any]:
        """Is this recalled memory still TRUE? — re-verify a memory at read time.

        USE THIS WHEN: a saved memory / note is about to be injected as context and
        it NAMES a concrete artifact (a commit SHA, an import/flag, a file path).
        A memory is a frozen self-report from a past session — the least
        trustworthy signal in the stack, yet recall hands it to you wearing the
        authority of a fact. Call this to re-check its claims against git + the
        working tree NOW, instead of trusting the body. This is `dos_verify`'s
        discipline (evidence, not self-report) aimed at the agent's own memory
        (docs/103).

        It parses the memory's frontmatter (trusted structure), extracts the
        checkable claims in its body + the polarity each asserts (is this code
        claimed PRESENT? this commit SHIPPED?), and re-probes each against ground
        truth: a comment-aware working-tree grep for a code token, git
        merge-base ancestry for a SHA, git history for a path. Returns a closed
        recall verdict; on anything but RECALL_FRESH, present the memory hedged or
        withhold it — never inject its raw content as confirmed.

        Args:
            name: the memory's frontmatter `name` / slug (resolved against the
                store) or a direct path to the `.md` file.
            workspace: the repo root whose git/working-tree is ground truth
                (default: cwd).
            store: the agent-memory directory (default: the documented
                `~/.claude/projects/<workspace>/memory` layout). Pass it explicitly
                when the memory store is elsewhere.

        Returns {memory, verdict, type, culprit, claims, interpretation}.
        `verdict` is one of RECALL_FRESH / RECALL_STALE / RECALL_UNVERIFIABLE;
        `culprit` (on STALE) is the deciding claim + the git evidence behind it;
        `interpretation` (added here) tells you in one line what to do next. The
        driver is resolved by name — the kernel never imports it.
        """
        import importlib
        cfg = _load_workspace_config(workspace)
        mr = importlib.import_module("dos.drivers.memory_recall")
        verdict = mr.recall_one(name, cfg=cfg, store=store or None).to_dict()
        verdict["interpretation"] = mr.interpret(verdict)  # gloss single-sourced in the DRIVER
        return verdict

    @mcp.tool()
    def dos_doctor(workspace: str = ".") -> dict[str, Any]:
        """The machine-readable workspace report — paths, lanes, stamp grammar.

        What an agent reads once to discover a workspace's layout instead of
        hardcoding it: where plans live, the lane taxonomy `dos_arbitrate` will
        use, the ship-stamp grammar `dos_verify` recognizes, and whether the root
        is a git repo. Read-only — resolves everything without creating `.dos/`.

        Args:
            workspace: the repo root to report on (default: cwd).

        Returns {dos_version, workspace, git, paths, lanes, stamp}.
        """
        cfg = _load_workspace_config(workspace)
        return {
            "dos_version": dos.__version__,
            "workspace": str(cfg.paths.root),
            "git": (cfg.paths.root / ".git").exists(),
            "paths": {
                "root": str(cfg.paths.root),
                "execution_state": str(cfg.paths.execution_state),
                "plans_glob": cfg.paths.plans_glob,
                "style": cfg.paths.style,
            },
            "lanes": {
                "concurrent": list(cfg.lanes.concurrent),
                "exclusive": list(cfg.lanes.exclusive),
                "autopick": list(cfg.lanes.autopick),
                "trees": {k: list(v) for k, v in cfg.lanes.trees.items()},
            },
            "stamp": cfg.stamp.to_dict(),
        }

    @mcp.tool()
    def dos_status(
        run_id: str,
        start_sha: str = "",
        lane: str = "",
        loop_ts: str = "",
        stopped: bool = False,
        live: bool = False,
        now_ms: int | None = None,
        workspace: str = ".",
    ) -> dict[str, Any]:
        """One folded, fail-closed status fact for a run — liveness · progress · region · resume.

        USE THIS WHEN: you want a single A2A-shaped answer to "what is the state of
        run X right now?" without trusting any worker's self-report. It folds FOUR
        adjudicated kernel verdicts into one record — liveness (is it moving?),
        ledger-VERIFIED progress (never the agent's claim), the run's held-lease
        region, and the resume plan (only once the run has stopped). It is the
        legible, peer-readable form of `dos_verify`'s distrust discipline aimed at a
        whole run instead of one phase.

        The load-bearing property (docs/120 §3): the digest has **no `claimed`
        field** by construction. A peer reading this result structurally cannot pick
        up a self-report it is never handed — `progress` is built from the kernel's
        VERIFIED rung only. Fail-closed everywhere: a run with no intent ledger is a
        valid zero-progress fact (not an error); a run holding no lease has an empty
        `region`; the resume verdict is null while the run is live.

        Args:
            run_id: the run-id (RID-…) the digest is keyed on.
            start_sha: the run's start commit (commits since = the liveness forward
                delta). Default: the run's declared start_sha off its intent ledger,
                else empty (a conservative 0-commit floor).
            lane / loop_ts: this run's lease identity; together they scope the
                liveness journal rungs to THIS lease. Omit ⇒ the commit rung decides.
            stopped / live: override the automatic stopped-predicate (which is
                `ledger SUSPENDed OR liveness STALLED`). `stopped` forces the resume
                read; `live` skips it. The resume read runs the expensive ancestry
                re-adjudication, so it is gated — never run on a live run.
            now_ms: wall-clock epoch-ms (default: now). Injectable for determinism.
            workspace: the repo root the run lives under (default: cwd).

        Returns the digest dict {schema, run_id, liveness, progress, region, resume}
        — and deliberately NO `claimed` key (the fail-closed A2A contract). On a bad
        run-id, returns an {error, run_id} dict rather than raising.
        """
        import time
        from dos import (git_delta, intent_ledger, journal_delta, lane_journal,
                         liveness as _lvn, resume as _resume, resume_evidence,
                         run_id as _rid, status as _status)

        cfg = _load_workspace_config(workspace)
        started_ms = _rid.ts_ms_of(run_id)
        if started_ms is None:
            return {"error": f"{run_id!r} is not a valid run-id token "
                             f"(expected an RID-… minted by `dos run-id mint`)",
                    "run_id": run_id}
        now = now_ms if now_ms is not None else int(time.time() * 1000)

        # Read A — the intent ledger (first: it sources start_sha + the stopped
        # predicate). Fail-closed: no ledger → a zero LedgerState, never a raise.
        entries = intent_ledger.read_all(run_id, cfg=cfg)
        ledger_state = (intent_ledger.replay(entries) if entries
                        else intent_ledger.LedgerState(run_id=run_id))
        resolved_start = (start_sha or "").strip() or ledger_state.start_sha

        # Read B — liveness. The clock/git/journal reads happen HERE at the boundary
        # (the explicit-cfg discipline: every read takes cfg / root=cfg.paths.root),
        # then the PURE classifier folds them.
        commits = git_delta.count_commits_since(resolved_start, root=cfg.paths.root)
        lease_key = (str(loop_ts), str(lane)) if lane and loop_ts else None
        try:
            j_entries = lane_journal.read_all(path=cfg.paths.lane_journal)
        except Exception:  # noqa: BLE001 — a bad journal must not crash the verdict
            j_entries = []
        jd = journal_delta.fold_since(j_entries, run_started_ms=started_ms,
                                      now_ms=now, lease_key=lease_key)
        liveness_verdict = _lvn.classify(_lvn.ProgressEvidence(
            run_started_ms=started_ms, now_ms=now,
            commits_since_start=commits,
            journal_events_since=jd.events_since_start,
            last_heartbeat_age_ms=jd.newest_heartbeat_age_ms,
        ))

        # Read C — the held-lease region (the spine join: lease.run_id == run_id),
        # reusing the already-read journal entries. `.get()` so an old un-stamped
        # ACQUIRE simply doesn't match (region () — backward-compat, never a raise).
        live_region: tuple[str, ...] = ()
        for lease in lane_journal.replay(j_entries):
            if str(lease.get("run_id") or "") == run_id:
                tree = lease.get("tree")
                live_region = (tuple(str(g) for g in tree)
                               if isinstance(tree, (list, tuple)) else ())
                break

        # Read D — resume, CONDITIONAL on the stopped predicate (skips the expensive
        # ancestry re-adjudication on a live run). stopped/live override the auto rule.
        is_stopped = bool(ledger_state.suspended
                          or liveness_verdict.verdict is _lvn.Liveness.STALLED)
        if stopped:
            is_stopped = True
        if live:
            is_stopped = False
        resume_plan = None
        if is_stopped and ledger_state.has_intent:
            anc = resume_evidence.gather_ancestry(ledger_state, cfg=cfg)
            resume_plan = _resume.resume_plan(ledger_state, anc)

        digest = _status.status_digest(
            run_id=run_id, ledger_state=ledger_state,
            liveness_verdict=liveness_verdict,
            live_region=live_region, resume_plan=resume_plan,
        )
        return digest.to_dict()      # the same no-`claimed` shape as the CLI --json

    @mcp.tool()
    def dos_citation_resolve(
        cite: str,
        claimed_name: str = "",
        quote: str = "",
        base: str = "",
        token: str = "",
    ) -> dict[str, Any]:
        """Does this cited case EXIST — and does the quote MATCH? — the legal-citation witness.

        USE THIS WHEN: a legal citation (e.g. "925 F.3d 1339") is about to be
        relied on — in a brief, a memo, a worker's summary — and you want to know
        whether it resolves in a third-party reporter BEFORE trusting it. This is
        the witness for the *Mata v. Avianca* failure class: fabricated cases
        cited as real. The verdict comes from the Free Law Project's reporter
        index (CourtListener) — bytes the citing agent authored zero of — never
        from how plausible the citation looks.

        Two operands are checked: the citation STRING must resolve to a reporter
        cluster, AND that cluster's case NAME must agree with the claimed party
        names — a real reporter slot carrying a DIFFERENT case than claimed is a
        documented fabrication pattern, and returns UNRESOLVED. An optional
        quoted holding is checked against the resolved opinion text where the
        full text is available. It witnesses EXISTENCE + quote-fidelity only; it
        does NOT judge whether the legal argument is correct.

        Args:
            cite: the reporter citation as claimed, e.g. "925 F.3d 1339".
            claimed_name: the case name as claimed (e.g. "Varghese v. China
                Southern Airlines"); arms the name-collision guard. "" checks
                the bare citation string only.
            quote: the quoted holding to check against the resolved opinion
                ("" skips the quote rung).
            base: the CourtListener-compatible API base URL (default: the
                public Free Law Project instance). Point it at a mirror if you
                run one.
            token: a CourtListener API token (default: the COURTLISTENER_TOKEN
                env var). With a token the purpose-built citation-lookup
                endpoint answers; without one, the noisier unauthenticated
                search.

        Returns the typed CitationVerdict dict {verdict, reason, matched_name,
        evidence: {cite, claimed_name, quote, reachable, detail, clusters}}.
        `verdict` is one of RESOLVED_MATCH (exists; quote matched or not
        applicable) / RESOLVED_MISMATCH (exists, but the quoted holding is NOT
        in the opinion — a mis-quote) / UNRESOLVED (no reporter carries it as
        claimed — treat as fabricated) / ABSTAIN (no corpus access: no token
        and the network read failed — never a fabricated verdict). The network
        call happens here at the tool boundary; a slow corpus read past the
        server's per-tool deadline returns the typed STALLED envelope, and the
        driver CLI (`python -m dos.drivers.citation_resolve`) is the fallback.
        """
        # dos_mcp sits outside the kernel, so importing a driver is allowed
        # (the kernel itself never imports either — the one-way arrow holds).
        # No workspace config: the witness adjudicates against a third-party
        # corpus, not a repo, so there is nothing in dos.toml to honor.
        from dos.drivers import citation_resolve as _cr
        evidence = _cr.gather(cite, claimed_name=claimed_name, quote=quote,
                              base=base or _cr.DEFAULT_BASE, token=token)
        return _cr.classify(evidence).to_dict()

    @mcp.tool()
    def dos_answer(query: str, k: int = 3) -> dict[str, Any]:
        """Ask DOS "how do I X?" and get the canonical, sourced answer page.

        USE THIS WHEN: you (the agent) need to know HOW to do something with DOS —
        verify a claim, stop two agents colliding, catch a fabricated citation,
        keep a self-improvement loop honest — and want the canonical, evidence-
        backed answer instead of guessing. Every OTHER tool here lets an agent
        CHECK a claim; this one lets it ASK. It scores your question against the
        DOS answer corpus (the `docs/answers/*.md` pages, each one a high-intent
        query answered with an evidence table whose every number links to the file
        that proves it) and returns the best matches.

        Matching is deterministic lexical scoring over each page's question, its
        registered query phrasings (the searcher's own words), and its one-line
        answer — no embedding, no network, same query → same ranking. Read-only:
        it reads a shipped index, takes no lease, writes nothing.

        Args:
            query: the question in your own words, e.g. "how do I prove an agent
                actually committed the code" or "stop agents clobbering each other".
            k: how many ranked answers to return (default 3, min 1).

        Returns {query, results: [{slug, question, answer, commands, url, path,
        queries, score}], count, note?}. `results` is best-first; each row's `url`
        is the fetchable page (fetch it for the full evidence table and the one
        command). `score` is a 0-1 relevance number. An empty `results` with a
        `note` means the corpus index is not available in this deployment (an
        installed wheel ships no docs tree) — fetch llms.txt instead. This tool
        never fabricates an answer: it points you at a sourced page, or says it
        cannot reach the corpus here.
        """
        results = _answers.search(query, k=k)
        out: dict[str, Any] = {
            "query": query,
            "results": results,
            "count": len(results),
        }
        if not results:
            out["note"] = (
                "the answer corpus index is not available here (an installed "
                "wheel ships no docs/ tree) — fetch the corpus at "
                "https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/README.md"
                if not _answers.load_rows()
                else "no answer page scored above zero for this query; try "
                "rephrasing, or browse the corpus at docs/answers/README.md"
            )
        return out

    # -----------------------------------------------------------------------
    # Resources — browsable context, not just callable tools. A host (and the
    # user) can READ these to load the workspace's refusal vocabulary and lane
    # taxonomy as context, e.g. before deciding how to refuse or which lane to
    # take. URIs are addressable; the `{workspace}`-templated variants let a host
    # browse a specific repo. Read-only, like the tools they mirror.
    # -----------------------------------------------------------------------
    def _reasons_markdown(workspace: str) -> str:
        cfg = _load_workspace_config(workspace)
        lines = [f"# DOS refusal vocabulary — {cfg.paths.root}",
                 "",
                 "The closed set of reasons a blocked/no-pick verdict may carry. "
                 "Emit only a token listed here (it is simultaneously emittable, "
                 "verifiable, and refusable).", ""]
        for s in cfg.reasons.specs:
            lines.append(f"## `{s.key}`  ({s.category}"
                         + ("" if s.refusal else ", advisory-only") + ")")
            if s.summary:
                lines.append(s.summary)
            if s.fix:
                lines.append(f"- **fix:** {s.fix}")
            lines.append("")
        return "\n".join(lines)

    def _lanes_markdown(workspace: str) -> str:
        cfg = _load_workspace_config(workspace)
        lanes = cfg.lanes
        lines = [f"# DOS lane taxonomy — {cfg.paths.root}",
                 "",
                 "Concurrent lanes run in parallel iff their file trees are "
                 "disjoint; exclusive lanes run alone. `dos_arbitrate` decides "
                 "admission over these.", "",
                 f"- **concurrent:** {', '.join(lanes.concurrent) or '(none)'}",
                 f"- **exclusive:** {', '.join(lanes.exclusive) or '(none)'}",
                 f"- **autopick order:** {', '.join(lanes.autopick) or '(none)'}",
                 "", "## Trees", ""]
        for name in sorted(set(lanes.concurrent) | set(lanes.exclusive)
                           | set(lanes.trees)):
            tree = ", ".join(lanes.tree_for(name)) or "(no tree declared)"
            lines.append(f"- `{name}`: {tree}")
        return "\n".join(lines)

    @mcp.resource("dos://reasons", mime_type="text/markdown")
    def reasons_resource() -> str:
        """The refusal vocabulary for the server's default workspace (cwd)."""
        return _reasons_markdown(".")

    @mcp.resource("dos://reasons/{workspace}", mime_type="text/markdown")
    def reasons_resource_ws(workspace: str) -> str:
        """The refusal vocabulary for a specific workspace path.

        The `{workspace}` URI segment is a single path segment (FastMCP matches
        it as `[^/]+`), so a workspace path is carried percent-encoded — an
        absolute POSIX root like `/srv/ws` would otherwise inject a bare slash
        and make the URI unroutable (the Windows path `C:\\ws` has none, which is
        why this only bit Linux). Decode it back to the real path here.
        """
        return _reasons_markdown(unquote(workspace))

    @mcp.resource("dos://lanes", mime_type="text/markdown")
    def lanes_resource() -> str:
        """The lane taxonomy for the server's default workspace (cwd)."""
        return _lanes_markdown(".")

    @mcp.resource("dos://lanes/{workspace}", mime_type="text/markdown")
    def lanes_resource_ws(workspace: str) -> str:
        """The lane taxonomy for a specific workspace path.

        See `reasons_resource_ws` — the `{workspace}` segment is percent-encoded
        so an absolute path survives FastMCP's `[^/]+` segment match; decode it.
        """
        return _lanes_markdown(unquote(workspace))

    def _answers_markdown() -> str:
        """The browsable answer-corpus index — every page's question → fetch URL.

        Lets a host (and the user) BROWSE the corpus, the read-only complement to
        the `dos_answer` search tool. Reads the shipped index; degrades to a one-
        line note when it is absent (an installed wheel ships no docs tree).
        """
        rows = _answers.load_rows()
        lines = ["# DOS answer corpus — how do I X?", ""]
        if not rows:
            lines += [
                "The answer-corpus index is not available in this deployment "
                "(an installed wheel ships no `docs/` tree). Fetch it from the "
                "repository: "
                "https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/answers/README.md",
            ]
            return "\n".join(lines)
        lines += [
            f"{len(rows)} sourced, self-contained answer pages — one per high-intent "
            "question, each with an evidence table whose every number links to the "
            "file that proves it. Search them with the `dos_answer` tool, or fetch "
            "any page below.", "",
        ]
        for r in sorted(rows, key=lambda r: r.get("question", "")):
            cmds = ", ".join(f"`{c}`" for c in r.get("commands", [])) or "—"
            lines.append(f"- [{r['question']}]({r['url']}) — {cmds}")
        return "\n".join(lines)

    @mcp.resource("dos://answers", mime_type="text/markdown")
    def answers_resource() -> str:
        """The answer corpus as a browsable question → URL index.

        The read-only complement to the `dos_answer` search tool: a host can load
        the whole corpus map as context, then fetch any page for its full evidence.
        """
        return _answers_markdown()

    # -----------------------------------------------------------------------
    # Prompts — user-invokable entry points. These surface in the host UI (e.g.
    # as slash-commands in Claude Desktop) so a USER can drive DOS directly,
    # without knowing the tool names. Each returns a short instruction that
    # teaches the agent the right tool + sequence — the "use it directly with
    # Claude" path the README describes.
    # -----------------------------------------------------------------------
    @mcp.prompt(title="Verify a claim actually shipped")
    def verify_a_claim(plan: str, phase: str, workspace: str = ".") -> str:
        """Confirm a (plan, phase) really shipped, from evidence not self-report."""
        return (
            f"Use the `dos_verify` tool with plan={plan!r}, phase={phase!r}, "
            f"workspace={workspace!r}. Then tell me plainly whether it shipped, "
            f"citing the `source` (registry / git commit / no evidence) and the "
            f"sha if there is one. Do NOT take anyone's word that it shipped — "
            f"rely only on what `dos_verify` returns."
        )

    @mcp.prompt(title="Can I safely take this lane?")
    def can_i_take_this_lane(lane: str, tree: str = "",
                             workspace: str = ".") -> str:
        """Check whether starting work on a lane/file-tree collides with live work."""
        tree_note = (f" Its file tree is: {tree} (pass as the `tree` argument, "
                     f"split into a list of globs).") if tree else ""
        return (
            f"Use the `dos_arbitrate` tool to decide whether I may take lane "
            f"{lane!r} in workspace {workspace!r} right now.{tree_note} If you "
            f"know what leases are currently live, pass them as `live_leases`. "
            f"Then give me a clear GO or STOP, and if STOP, list any free lanes I "
            f"could take instead."
        )

    @mcp.prompt(title="Refuse with a structured reason")
    def refuse_with_a_reason(situation: str, workspace: str = ".") -> str:
        """Pick a verifiable refusal reason for a situation, instead of free text."""
        return (
            f"I need to refuse / report-blocked for this situation: {situation}\n\n"
            f"First call `dos_refuse_reasons` (workspace={workspace!r}) to see the "
            f"closed vocabulary. Pick the single token that best fits, confirm it "
            f"with `dos_check_reason`, then refuse using THAT token (not free-text "
            f"prose). If nothing fits, say so and suggest a new reason to declare "
            f"in dos.toml [reasons]."
        )

    # ---------------------------------------------------------------------
    # Third-party MCP tools — the `dos.mcp_tools` entry-point seam.
    #
    # The built-in syscall tools above are the curated ABI. This seam lets a third
    # party ADD a tool/verb to the `dos` server from their OWN pip package, with no
    # fork — the MCP-surface analogue of `dos.judges` / `dos.drivers`. A plugin
    # registers `name = "pkg.module:register"` under
    # `[project.entry-points."dos.mcp_tools"]`; `register(mcp)` is handed THIS server
    # and calls `mcp.tool()` itself, so its tools get the SAME deadline + deep-answer
    # wrapping the built-ins do (the patched `mcp.tool` above). A bare tool callable
    # is also accepted and registered directly. The seam is ADDITIVE — a plugin adds a
    # verb, never replaces a built-in (FastMCP keeps the first registration of a name).
    # A broken plugin is SKIPPED with a stderr note (MCP hosts capture stderr), never
    # crashing the server — the same fail-soft discovery posture the kernel seams take.
    _register_entry_point_tools(mcp)

    return mcp


def _register_entry_point_tools(mcp: "FastMCP", *, _stderr=None) -> list[str]:
    """Discover + register `dos.mcp_tools` plugins on `mcp`. Returns the names registered.

    Factored out so a test can build a server and assert a fake entry point was wired
    without starting the stdio transport. Discovery I/O at build time only; a plugin that
    fails to load or register is skipped with a one-line stderr note."""
    stderr = _stderr if _stderr is not None else sys.stderr
    registered: list[str] = []
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return registered
    try:
        eps = entry_points(group="dos.mcp_tools")
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get("dos.mcp_tools", [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash the server
        return registered
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            obj = ep.load()
            # A `register(mcp)` callable wires its own tools (preferred — it can register
            # several and pick titles); a bare tool function is registered directly.
            if _looks_like_registrar(obj):
                obj(mcp)
            else:
                mcp.tool()(obj)
            registered.append(ep.name)
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(
                f"warning: mcp_tools plugin {ep.name!r} failed to load ({e}); skipping",
                file=stderr,
            )
            continue
    return registered


def _looks_like_registrar(obj: Any) -> bool:
    """Is `obj` a `register(mcp)`-style callable (vs a bare tool function)?

    A registrar takes exactly the server handle — its single required parameter is the
    `mcp`. A bare tool function has the tool's own parameters (text/plan/…). We treat a
    callable named `register`, or one whose only required parameter is named `mcp`/`server`,
    as a registrar; anything else is a bare tool. Pure introspection; no I/O."""
    if not callable(obj):
        return False
    if getattr(obj, "__name__", "") == "register":
        return True
    try:
        import inspect

        params = list(inspect.signature(obj).parameters.values())
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return False
    required = [p for p in params
                if p.default is p.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(required) == 1 and required[0].name in ("mcp", "server")


def _quiet_windows_console() -> None:
    """Hide the stray empty console window on Windows; no-op elsewhere.

    The `console_scripts` launcher (`dos-mcp`) is a CONSOLE-subsystem .exe, so when
    an MCP host (Claude Code/Desktop, Cursor, …) spawns the server detached, Windows
    allocates a fresh, EMPTY console window that lingers for the whole session — one
    per launch, which operators see as a random blank terminal box popping up. The
    server only ever talks over the inherited stdio PIPES, never the console. We
    can't just FreeConsole(): the LAUNCHER process owns that console and outlives
    this call, so detaching only ourselves leaves the window up. Instead hide the
    shared console WINDOW (any attached process may), which drops the visible box
    regardless of who holds the console; then FreeConsole() so this process is
    cleanly detached too. The JSON-RPC transport is untouched — the std handles are
    already redirected to pipes, independent of the console (verified: the
    initialize/list_tools handshake still completes after both calls).

    Called from main() only — NOT at import — so importing the module (the test
    suite, tooling) never touches a console. Skipped when stdin/stdout is an
    interactive TTY (a human running `dos-mcp` in a terminal to poke it), so we only
    ever hide the unused auto-allocated console, never a real one.
    """
    if os.name != "nt":
        return
    try:
        stdin_tty = sys.stdin is not None and sys.stdin.isatty()
        stdout_tty = sys.stdout is not None and sys.stdout.isatty()
        if stdin_tty or stdout_tty:
            return
        import ctypes

        k32 = ctypes.windll.kernel32
        hwnd = k32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        k32.FreeConsole()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    """Console-script entrypoint — build the server and serve over stdio.

    `argv` is accepted for symmetry with the `dos` CLI and to keep the signature
    test-friendly; the stdio transport takes no arguments today.
    """
    _quiet_windows_console()
    server = build_server()
    server.run()  # stdio transport — what an MCP host launches and speaks to
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
