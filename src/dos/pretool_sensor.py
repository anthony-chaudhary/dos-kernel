"""pretool-sensor — the boundary I/O for the PRE moment of a tool call (docs/191).

> **The PRE moment is the unique cell where a DOS verdict is both SOUND and backed
> by real DENY-power.** `dos hook posttool` (the BOUNDARY moment) can only ADD
> context — the call already fired. `dos hook stop` (the STOP moment) can only emit
> a host-honored `{ok:false}`. Only a `PreToolUse` hook can return a
> `permissionDecision: deny` the runtime honors BEFORE the tool runs (the docs/126
> mediated-write moment). This module is the boundary adapter that reads a CC
> `PreToolUse` event, runs the already-shipped PURE kernel verdicts that are sound at
> PRE, and emits the exact CC dialect — the `posttool_sensor` sibling, one moment
> earlier on the tool-call timeline.**

The evidence-locus asymmetry (docs/191 §0) is the whole reason this module exists and
is constrained the way it is. At PRE the only bytes that exist for THIS call are
agent-authored (`tool_name` / `tool_input`); this call's env-authored `result_digest`
does NOT exist yet (that is what BOUNDARY adds). So a PRE verdict may use ONLY:

  * the proposed call's own path/tree (`SelfModifyPredicate`, `DisjointnessPredicate` —
    request-absolute / lease-relative admission, sound on the proposed tree alone);
  * the agent's args checked against the corpus of PRIOR env results
    (`arg_provenance.classify_call` — the cross-moment join: prior RESULTS are
    env-authored, available at PRE even though THIS result is not).

`tool_stream` REPEATING and `terminal_error` are UNSOUND here — they need this call's
env `result_digest`, which does not exist until BOUNDARY. They bind at POST only. This
module never computes them.

Two rungs, two safe-failure directions (docs/191 §3 — keep them rigorously apart)
=================================================================================

  * **Rung A — structural admission** (auto-deny-safe). `admission.run_predicates`
    over the built-in conjunction (`DisjointnessPredicate`, `SelfModifyPredicate` — the
    ONLY two built-ins; there is no "dangerous-exec" class) is conjunctive-only +
    fail-CLOSED-to-REFUSE. A buggy predicate can only OVER-refuse, and an admission
    over-refusal is operator-visible + `--force`-overridable — an admission gate, NOT a
    mid-plan derail, so it carries no docs/143 −9 pp exposure. A Rung-A refusal with a
    structural reason becomes a `permissionDecision: deny` directly.

  * **Rung B — behavioral provenance** (confidence-gated, fail-to-OBSERVE). The
    provenance verdict is routed `classify_call → intervention.choose_intervention →
    enforce.run_handler`. `choose_intervention` clamps into `[floor, ceiling]` with the
    DEFAULT `ceiling=BLOCK`, so DEFER (the turn-spending rung) is structurally
    unreachable. `run_handler` is fail-to-OBSERVE + no-escalation: a handler that raises
    / returns a non-`EffectProposal` → OBSERVE (no deny), and a handler can never
    propose harder than the kernel's confidence-gated rung. So a handler bug CANNOT
    manufacture a deny.

These coexist in one hook with NO contradiction (the docs/191 §5 correction): admission
fails CLOSED (cheap, visible over-refusal) while the behavioral path fails toward
WARN/OBSERVE (the expensive −9 pp direction is the one avoided). The two are selected by
which seam produced the verdict.

Why it stays a PDP, not a PEP (docs/191 §4)
===========================================

A PRE deny is an `EffectProposal{dispatch_call=False}` — a PROPOSAL the kernel computes.
The CC runtime is the PEP that consumes `permissionDecision: deny` and actually withholds
the call. The default handler is `ObserveHandler`, which proposes OBSERVE on everything →
ZERO deny until a driver wires a ruling handler. So a default install emits no deny: DOS
is PDP-only by construction. The CC `PreToolUse` schema also offers `updatedInput` (rewrite
the agent's args) — this module DELIBERATELY never emits it: minting corrective bytes FOR
the agent would violate the byte-author invariant (docs/138). PRE stays
deny / passthrough / additionalContext only.

⚓ Kernel discipline (the litmus): a PURE verdict-adapter — imports only sibling kernel
modules (`admission`, `self_modify`, `arg_provenance`, `intervention`, `enforce`,
`lane_journal`, `config`), names no host beyond the `PreToolUse` JSON shape, resolves
every path via `SubstrateConfig`, takes no lease, carries no policy of its own (the
thresholds live in `ProvenancePolicy` / the `InterventionLadder` / `StreamPolicy`).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from dos import config as _config

# The CC PreToolUse result keys we must NOT see — their ABSENCE is the structural marker
# that distinguishes a PreToolUse event from a PostToolUse one (docs/191 §6). A PRE event
# carries no tool RESULT; if one of these is present the event is mis-routed (a PostToolUse
# event sent to the pre hook), and we decline to treat agent-unseen result bytes as PRE
# evidence. The same dual-key the posttool sensor reads on the other side.
_RESULT_KEYS = ("tool_response", "tool_output")

# The tools whose `tool_input` names a filesystem path we can turn into an admission tree.
# Conservative + host-shaped: a host with different tool names declares its own mapping in a
# driver. The kernel knows only the generic CC edit/write tools. A Bash command is parsed
# best-effort (see `_tree_from_event`); an unrecognized mutating tool yields an UNKNOWN tree
# (empty), which the SELF_MODIFY rung treats as unknown blast radius (the safe direction).
_PATH_ARG_KEYS = ("file_path", "path", "notebook_path")

# Read-only tools never take an admission tree (reads are how provenance ENTERS the corpus,
# docs/191 §2) — an empty tree admits. A tool not in either set is treated as potentially
# mutating with an unknown tree (conservative).
_READ_ONLY_TOOLS = frozenset(
    {"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch"}
)
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# The opt-in switch for the apply-gate binding turnstile (docs/126 Phase 1.5). The
# apply-gate generalizes the always-on SELF_MODIFY deny from the kernel's own T1
# files to ANY held lease's tree — a stronger gate the operator OPTS INTO (docs/126
# §3 rule 3: an additional binding surface, not a mutation of the default verdicts).
# An env switch (not a built-in predicate) keeps it host-policy / opt-in: a default
# install keeps exactly today's SELF_MODIFY + disjointness behavior. Any non-empty,
# non-"0"/"false" value enables it; the kernel names no host (an env name, not a
# vendor identifier).
_APPLY_GATE_ENV = "DOS_APPLY_GATE"
_APPLY_GATE_OFF = frozenset({"", "0", "false", "no", "off"})


def _apply_gate_enabled() -> bool:
    """True iff the operator opted the apply-gate binding surface in. Boundary read."""
    return os.environ.get(_APPLY_GATE_ENV, "").strip().lower() not in _APPLY_GATE_OFF


# ---------------------------------------------------------------------------
# PURE adapters — a PreToolUse event in, the pure kernel inputs out (no I/O).
# ---------------------------------------------------------------------------
def is_pre_event(event: dict) -> bool:
    """True iff this looks like a PreToolUse event we should act on. PURE.

    The structural PRE marker (docs/191 §6): a `tool_name` present AND no tool RESULT key
    (`tool_response`/`tool_output`). A PostToolUse event mis-routed to the pre hook carries
    a result key — we decline it (return False) so we never treat agent-unseen result bytes
    as PRE evidence, and the caller emits nothing (passthrough). A `hook_event_name` of
    `PreToolUse`, when present, is honored too — but its absence is not disqualifying (older
    builds omit it); the result-key absence is the load-bearing test.
    """
    if not isinstance(event, dict):
        return False
    tool_name = event.get("tool_name")
    if not (isinstance(tool_name, str) and tool_name):
        return False
    name = event.get("hook_event_name")
    if isinstance(name, str) and name and name != "PreToolUse":
        return False
    for k in _RESULT_KEYS:
        if event.get(k) is not None:
            return False  # a result is present → this is a BOUNDARY event, not PRE
    return True


def _tree_from_event(event: dict) -> tuple[tuple[str, ...], bool]:
    """The admission tree for the proposed call + whether the tree is KNOWN. PURE.

    Returns `(tree, known)`:
      * a read-only tool → `((), True)` (empty tree, known-empty → admits; reads are how
        provenance enters the corpus, never a self-modify hazard).
      * a write/edit tool with a path arg → `((path,), True)`.
      * a write/edit tool with NO usable path, or an unrecognized (potentially mutating)
        tool → `((), False)` — an UNKNOWN tree. The caller treats unknown-blast-radius
        conservatively at the SELF_MODIFY rung (docs/191 §6: a missed self-modify is the
        dangerous direction; an un-parseable mutating tree must not silently admit).

    Tree extraction from `tool_input` is intentionally lossy and host-shaped — the lane
    arbiter historically got trees from the dispatch layer, not from a tool-arg parse. The
    kernel handles only the generic CC edit/write tools + a best-effort Bash path scrape; a
    host with other tools declares its own mapping in a driver.
    """
    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str):
        return (), False
    if tool_name in _READ_ONLY_TOOLS:
        return (), True  # known-empty: a read takes no tree, admits
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    # A direct path arg (Write/Edit/NotebookEdit and the like).
    for k in _PATH_ARG_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return (_repo_relative(v.strip(), event),), True
    # Bash: FIRST ask whether the invoked program can write at all (issue #12 — a mention
    # is not a mutation). A command whose every segment invokes a known no-write-footprint
    # program (`gh issue create`, `git log`, `grep`, …) and carries no shell write
    # metacharacter gets the read-only posture: a kernel path in its ARGUMENTS is prose,
    # not a write footprint. Only then the best-effort scrape of path-shaped tokens.
    # Conservative both ways — an unrecognized program keeps today's scrape, and if we
    # find nothing path-shaped we return UNKNOWN (not empty-known), so a mutating command
    # we cannot parse is treated as unknown blast radius, never silently admitted.
    if tool_name == "Bash":
        cmd = tool_input.get("command")
        if isinstance(cmd, str) and cmd.strip():
            if _command_has_no_write_footprint(cmd):
                return (), True  # known-empty: the invoked programs cannot write a path
            paths = _paths_from_command(cmd)
            if paths:
                return tuple(_repo_relative(p, event) for p in paths), True
        return (), False  # unknown command footprint → unknown tree
    if tool_name in _WRITE_TOOLS:
        return (), False  # a write tool with no resolvable path → unknown, conservative
    # An unrecognized tool: could be a mutating MCP tool. Unknown tree (conservative) — the
    # SELF_MODIFY rung sees unknown blast radius; but since the tree is empty AND we cannot
    # name a runtime-file collision, this degrades to admit at Rung A (no false deny) while
    # Rung B (provenance) still applies to its args. The honest middle: we never invent a
    # collision we cannot prove, and we never claim a read is safe when we cannot tell.
    return (), False


def _repo_relative(path: str, event: dict) -> str:
    """Best-effort repo-relative POSIX form of a path (the shape a lane tree carries). PURE.

    A lane tree is repo-relative POSIX (e.g. `src/dos/arbiter.py`). We normalize separators
    and, when the path is under the event's `cwd`, strip that prefix. This is best-effort:
    when we cannot relativize (an absolute path outside cwd) we return the POSIX-normalized
    absolute form, which the SELF_MODIFY runtime-file compare will simply not match (the
    safe direction — an unrelatable path is not claimed to be a kernel file).
    """
    p = path.replace("\\", "/")
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        c = cwd.replace("\\", "/").rstrip("/")
        if p.startswith(c + "/"):
            return p[len(c) + 1 :]
    return p.lstrip("/")


# The closed set of command invocations that cannot WRITE a filesystem path named in
# their arguments — the docs/224 SHAPE move (`exec_capability.classify_command`'s
# closed-set, invoked-program-token discipline) re-aimed from the EXEC capability onto
# the WRITE capability (issue #12). Each entry is a prefix of program tokens: a one-token
# entry admits the program with any arguments (`grep` has no write-to-file flag); a
# longer entry admits only that subcommand (`git log` reads; bare `git` is NOT here).
# Membership means "this program, so invoked, writes no file path it is handed" — NOT
# "this command is safe" (a `gh issue close` mutates GitHub state; it just cannot touch
# the kernel's files). Deliberately small and certain; notable EXCLUSIONS, each
# write-capable despite looking read-only: `sort`/`uniq`/`tee`/`file`/`tree` (an output
# file via flag or positional), `sed`/`awk`/`find` (in-place / -exec), `gh issue develop`
# / `gh pr checkout` / `gh run download` / `gh release download` (write the local tree),
# and every wrapper (`sudo`/`env`/`xargs`/`time` — the wrapped program decides, so a
# wrapped command stays conservative). An absent program falls back to today's scrape:
# under-matching is the safe direction.
_NO_WRITE_FOOTPRINT_PREFIXES: frozenset[tuple[str, ...]] = frozenset(
    [
        # stdout-only filters/reporters — no flag or positional writes a file.
        ("cat",), ("grep",), ("rg",), ("head",), ("tail",), ("wc",), ("ls",),
        ("stat",), ("du",), ("df",), ("pwd",), ("echo",), ("printf",), ("which",),
        ("diff",), ("cmp",), ("cut",), ("tr",), ("nl",), ("basename",), ("dirname",),
        ("realpath",), ("readlink",), ("md5sum",), ("sha1sum",), ("sha256sum",),
    ]
    # git's read-only plumbing/porcelain — answers from the object store, writes nothing.
    + [
        ("git", sub) for sub in (
            "log", "diff", "status", "show", "blame", "grep", "rev-parse", "rev-list",
            "ls-files", "ls-tree", "ls-remote", "cat-file", "describe", "shortlog",
            "name-rev", "merge-base", "check-ignore",
        )
    ]
    # gh's network-only verbs — they mutate GitHub, never the local tree. (`develop`,
    # `checkout`, `download`, `clone` are the local-write exceptions, kept OUT.)
    + [
        ("gh", "issue", verb) for verb in (
            "create", "list", "view", "comment", "close", "reopen", "edit", "status",
            "lock", "unlock", "pin", "unpin", "transfer",
        )
    ]
    + [
        ("gh", "pr", verb) for verb in (
            "create", "list", "view", "diff", "checks", "status", "comment",
            "close", "reopen", "edit",
        )
    ]
    + [
        ("gh", "label"), ("gh", "search"), ("gh", "api"),
        ("gh", "run", "list"), ("gh", "run", "view"),
        ("gh", "release", "list"), ("gh", "release", "view"),
        ("gh", "repo", "view"),
    ]
)

# Shell metacharacters that can route bytes into a file (or run a hidden command) AROUND
# the invoked program: any redirection (`>` covers `>>`/`2>`/`&>`), a backtick or `$(`
# command substitution, a `<(` process substitution. Their PRESENCE anywhere in the
# command defeats the no-write-footprint classification — `git log > src/dos/arbiter.py`
# writes even though `git log` cannot. Plain `<` (input) and `<<` (heredoc) only FEED a
# program that already cannot write, so they are not vetoed.
_SHELL_WRITE_METACHARS: tuple[str, ...] = (">", "`", "$(", "<(")

# The shell operators that join command segments — each segment invokes its own program,
# so each must independently classify as no-write-footprint. Order matters: the two-char
# operators are replaced before their one-char prefixes.
_SEGMENT_SEPARATORS: tuple[str, ...] = ("&&", "||", ";", "|", "&", "\n")


def _segment_lead_tokens(segment: str, limit: int = 3) -> list[str]:
    """The invoked program token + up to two non-flag subcommand tokens. PURE.

    The same extraction discipline as `exec_capability._program_token`: skip leading
    `VAR=value` assignments, take the program's basename lower-cased, then collect
    following non-flag tokens (a `--no-pager` between `git` and `log` is skipped). A
    flag that CONSUMES a value (`git -C dir log`) mis-reads the value as a subcommand —
    which simply fails the closed-set lookup and falls back to the scrape (under-match,
    the safe direction). Wrappers are NOT skipped here: a `sudo grep` reports `sudo`,
    which is not in the no-write set, so a wrapped command stays conservative.
    """
    toks: list[str] = []
    for raw in segment.split():
        tok = raw.strip()
        if not tok:
            continue
        if not toks:
            if "=" in tok and not tok.startswith("="):
                head = tok.split("=", 1)[0]
                if head and all(c.isalnum() or c == "_" for c in head):
                    continue  # a leading VAR=value assignment — skip
            toks.append(tok.replace("\\", "/").rsplit("/", 1)[-1].lower())
        else:
            if tok.startswith("-"):
                continue  # a flag between program and subcommand
            toks.append(tok.lower())
        if len(toks) >= limit:
            break
    return toks


def _command_has_no_write_footprint(cmd: str) -> bool:
    """True iff EVERY segment of `cmd` invokes a known no-write-footprint program and no
    shell metacharacter can write around them. PURE — the issue-#12 classifier.

    The conjunctive shape: one metacharacter anywhere, or one segment whose invocation
    prefix is not in the closed set, and the whole command falls back to the
    conservative scrape. So this can only ever ADMIT-MORE for commands provably unable
    to write — it can never hide a write the old scrape would have caught (`echo x >
    src/dos/arbiter.py` is vetoed by the `>`; `git log && rm f.py` fails on the `rm`
    segment). The FQ-532 line, finally honored for prose: never invent a collision we
    cannot prove — a path INSIDE an argument to a program that cannot write it is a
    mention, not a mutation.
    """
    for meta in _SHELL_WRITE_METACHARS:
        if meta in cmd:
            return False
    work = cmd
    for sep in _SEGMENT_SEPARATORS:
        work = work.replace(sep, "\x00")
    segments = [s for s in (seg.strip() for seg in work.split("\x00")) if s]
    if not segments:
        return False
    for segment in segments:
        toks = _segment_lead_tokens(segment)
        if not toks:
            return False
        if not any(tuple(toks[:depth]) in _NO_WRITE_FOOTPRINT_PREFIXES
                   for depth in range(1, len(toks) + 1)):
            return False
    return True


def _paths_from_command(cmd: str) -> tuple[str, ...]:
    """Best-effort path-shaped tokens from a Bash command string. PURE.

    NOT a shell parser — a heuristic scrape for tokens that look like file paths (contain a
    `/` and a recognizable suffix, or name a known runtime file). Used only to give the
    SELF_MODIFY rung a chance to fire on `echo x > src/dos/arbiter.py`. Returns `()` when
    nothing path-shaped is found, which `_tree_from_event` maps to an UNKNOWN tree (the
    conservative branch). Deliberately under-extracts: a missed path → unknown tree →
    conservative, never a fabricated collision.
    """
    out: list[str] = []
    for raw in cmd.replace(";", " ").replace("|", " ").replace("&", " ").split():
        tok = raw.strip("\"'()<>")
        if "/" in tok and not tok.startswith("-") and "." in tok.rsplit("/", 1)[-1]:
            out.append(tok)
    return tuple(dict.fromkeys(out))  # de-dup, preserve order


def is_mutating_tool(event: dict) -> bool:
    """Whether the proposed call mutates state — the `ToolCall.is_mutating` flag. PURE.

    FAIL-OPEN (docs/191 §3, the `arg_provenance` posture): when unsure, treat as a READ
    (`is_mutating=False`), which short-circuits the provenance fold to ABSTAIN-all. Under-
    gating is the feasible-task-safe direction — a false gate risks a real regression while
    a missed gate just degrades to baseline. A tool explicitly in the read-only set is a
    read; a write tool / Bash is mutating; anything else is conservatively mutating ONLY for
    the provenance check (Rung B fails to OBSERVE, so a wrong guess there cannot deny).
    """
    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str):
        return False
    if tool_name in _READ_ONLY_TOOLS:
        return False
    return True


# ---------------------------------------------------------------------------
# PURE dialect renderers — a verdict in, the exact CC PreToolUse dialect out (no I/O).
# ---------------------------------------------------------------------------
def deny_payload(reason: str, *, additional_context: str = "") -> dict:
    """The CC PreToolUse DENY dialect — `permissionDecision: deny`. PURE.

    The one envelope real Claude Code honors to block a tool BEFORE it runs (verified
    against the CC v2.1.88 source: `permissionBehaviorSchema = z.enum(['allow','deny',
    'ask'])`; `deny` sets `result.permissionBehavior='deny'` and skips the tool). Field
    names are case-sensitive and exact, the same load-bearing dialect-exactness the
    posttool sensor's `additionalContext` envelope depends on (emit the wrong shape and the
    hook is a SILENT no-op, the old `dos hook stop` lesson). NEVER emits `updatedInput`
    (that would mint corrective bytes for the agent — a byte-author violation, docs/191 §4).
    """
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    if additional_context:
        out["hookSpecificOutput"]["additionalContext"] = additional_context
    return out


def warn_payload(text: str) -> dict:
    """The CC PreToolUse WARN dialect — `additionalContext` ONLY, no `permissionDecision`. PURE.

    A WARN does NOT deny: it omits `permissionDecision` entirely (so CC's normal permission
    flow proceeds — passthrough) and only ADDS a re-surfaced fact to the next turn. This is
    the turn-preserving soft rung: the agent gets the corrective OBSERVATION without losing
    its turn (docs/191 §3, the WARN-and-pass resolution for a LOW-confidence / composite
    mint).
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
        }
    }


# ---------------------------------------------------------------------------
# The impure half — gather PRE evidence at the boundary (the cross-moment join +
# the live-lease read), then run the pure kernel verdicts. All I/O is HERE, never
# inside a verdict (the `liveness`/`posttool_sensor` boundary discipline).
# ---------------------------------------------------------------------------
def live_leases_for(cfg: "_config.SubstrateConfig") -> list[dict]:
    """Replay the workspace lane journal into the live leases a PRE admission check sees.

    Boundary I/O (the `lane_journal` WAL read), handed to the pure `run_predicates`. Any
    failure (no journal, unreadable, replay error) → `[]` (no leases), which makes
    DisjointnessPredicate admit and leaves SelfModifyPredicate (request-absolute) still
    firing — the same idle-repo behavior `run_predicates` documents. Fail-safe: a journal
    read fault never denies a real call (it degrades to "no leases", the safe direction for
    the COLLISION rung; SELF_MODIFY is unaffected because it answers from the request).
    """
    try:
        from dos import lane_lease
        # `expire_dead=True`: the PRE-admission gate is a CONTENTION read — a crashed
        # worker's un-RELEASEd ACQUIRE (a phantom orphan whose TTL aged out or whose
        # holder PID is confidently gone on this host) must NOT silently revoke the
        # interactive session's Read/Edit on every tool call (docs/281 Defect 1).
        # The live set self-heals here instead of waiting for an external SCAVENGE;
        # only the provably-dead are dropped, so a genuinely-live lane still gates.
        return lane_lease.live_leases(cfg, expire_dead=True)
    except Exception:
        return []


def _find_self_lease(leases: list[dict], cfg: "_config.SubstrateConfig") -> "Optional[dict]":
    """The exact live-lease object THIS run holds, or None. Boundary read.

    The identity match `cli._resolve_self_lease` runs, returning the lease OBJECT (so
    a caller can identity-filter it out of a disjointness sweep, never a same-tree
    sibling), in priority order: matching `$CID_RUN_ID`/`$DISPATCH_RUN_ID`, then
    `$DISPATCH_LOOP_TS`, then this process's pid (or its parent's — the tool call
    often runs in a child shell of the leasing process). `cfg` is unused today but
    kept in the signature so a future host-shaped identity rule has a home without a
    call-site churn.
    """
    run_id = os.environ.get("CID_RUN_ID") or os.environ.get("DISPATCH_RUN_ID") or ""
    loop_ts = os.environ.get("DISPATCH_LOOP_TS") or ""
    my_pids = {os.getpid()}
    try:
        my_pids.add(os.getppid())
    except (AttributeError, OSError):
        pass
    for lease in leases:
        if run_id and str(lease.get("run_id") or "") == run_id:
            return lease
        if loop_ts and str(lease.get("loop_ts") or "") == loop_ts:
            return lease
        try:
            if int(lease.get("pid")) in my_pids:  # type: ignore[arg-type]
                return lease
        except (TypeError, ValueError):
            continue
    return None


def resolve_self_lease(
    leases: list[dict], cfg: "_config.SubstrateConfig"
) -> tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Which lane does THIS run hold, its declared tree, and the OTHER live trees.

    The boundary I/O the apply-gate needs but a pure predicate cannot derive — "which
    of the live leases is MINE?" — resolved here over the ALREADY-READ `leases` (no
    second WAL read), the same identity match `cli._resolve_self_lease` runs at the
    `dos apply` CLI boundary, so the gate behaves identically on both surfaces. The
    self-lease is `apply_gate.ApplyEvidence` data the caller freezes in, the
    `SelfModifyPredicate`-receives-its-set discipline.

    Identity, in priority order (no `--lane` exists at this surface — the PreToolUse
    ABI gives the agent no flag, so there is no operator-named lane to honor here):
      1. a WAL lease whose `run_id` matches `$CID_RUN_ID` / `$DISPATCH_RUN_ID`;
      2. else a WAL lease whose `loop_ts` matches `$DISPATCH_LOOP_TS`;
      3. else a WAL lease whose `pid` matches this process's pid (or its parent's —
         the agent's tool call often runs in a child shell of the leasing process).

    Returns `(self_lane, self_tree, other_trees)`. A self_lane that resolves but has
    NO declared tree (and no recorded lease tree) yields an EMPTY `self_tree` —
    `apply_gate.decide` then fails CLOSED on a non-empty footprint (an undeclared
    blast radius is refused, never admitted). `other_trees` is every OTHER live
    lease's tree (the collision-floor operands). When NO lease is this run's (an
    interactive session that never leased), `self_lane` is "" and `self_tree` empty
    — the apply-gate would then refuse any non-empty write, so the CALLER only runs
    the gate when a self-lease actually resolved (an un-leased session keeps the
    pre-apply-gate behavior; the gate binds a run that DECLARED a lane, never invents
    one for a session that never opted in).
    """
    self_lease = _find_self_lease(leases, cfg)
    self_lane = str(self_lease.get("lane") or "") if self_lease else ""
    # The DECLARED tree is authoritative (config), then the lease's recorded tree as
    # the fallback for a lane the taxonomy doesn't name (a keyword lane) — exactly
    # `cli._resolve_self_lease`'s precedence.
    self_tree: tuple[str, ...] = ()
    if self_lane:
        try:
            self_tree = tuple(cfg.lanes.tree_for(self_lane))
        except Exception:  # noqa: BLE001 — a missing taxonomy entry → fall to the lease tree
            self_tree = ()
    if not self_tree and self_lease and self_lease.get("tree"):
        self_tree = tuple(self_lease.get("tree") or ())
    other_trees = tuple(
        tuple(lease.get("tree") or ())
        for lease in leases
        if lease is not self_lease and lease.get("tree")
    )
    return self_lane, self_tree, other_trees


def prior_results(session_id: str, cfg: "_config.SubstrateConfig"):
    """The env-authored corpus of PRIOR tool results — the cross-moment join (docs/191 §2).

    The load-bearing PRE-soundness move: this call's result does not exist, but EARLIER
    calls' results do, and they are env-authored. We read them from the SAME accumulating
    `posttool_sensor` stream the BOUNDARY hook writes — so the PRE provenance check sees
    every prior RESULT digest, tagged `TOOL_RESULT` (env source — `CorpusSource` has no
    `AGENT_AUTHORED` member, so a minted id can never launder itself into the corpus).

    NOTE the honest limit (docs/191 §8 coupling tension): the posttool stream stores DIGESTS
    of prior results, not their raw bytes, so the provenance corpus here is built from the
    result digests + tool names the stream retained. A missing/stale stream degrades to an
    EMPTY corpus, which `classify_call` reads as "cannot prove mintage → ABSTAIN-all" — the
    safe direction (no false deny), at the cost of PRE coverage only as good as the POST
    stream that feeds it. Any failure → empty corpus (fail-safe).
    """
    from dos.arg_provenance import EnvBlob, PriorResults, CorpusSource
    blobs: list = []
    try:
        from dos import posttool_sensor as _pts
        stream = _pts.read_stream(session_id, cfg)
        for step in stream.steps:
            # The env-authored evidence available from the stream: the prior result digest
            # and the tool name. We fold each prior RESULT digest into the corpus as a
            # TOOL_RESULT blob. (A future phase that retains raw result bytes would carry
            # them here verbatim; v1 carries the digest the stream kept.)
            if step.result_digest:
                blobs.append(EnvBlob(text=str(step.result_digest), source=CorpusSource.TOOL_RESULT))
    except Exception:
        return PriorResults(())
    # The task text, when the event/host surfaced it, would be a TASK_TEXT blob — not
    # available from the PreToolUse event in v1, so the corpus is prior RESULTS only.
    return PriorResults(tuple(blobs))


def toolcall_from_event(event: dict):
    """Build the `arg_provenance.ToolCall` for the proposed call. PURE-ish (no I/O).

    Flattens the agent-authored `tool_input` dict into `ToolArg`s (the value the model
    emitted per arg) and sets `is_mutating` via the fail-open classifier. Returns None for a
    non-mutating call (a read short-circuits provenance to ABSTAIN-all) or a malformed event.
    """
    from dos.arg_provenance import ToolArg, ToolCall
    tool_name = event.get("tool_name")
    if not (isinstance(tool_name, str) and tool_name):
        return None
    if not is_mutating_tool(event):
        return None  # reads are never gated — short-circuit
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    args = tuple(ToolArg(name=str(k), value=v) for k, v in tool_input.items())
    return ToolCall(tool_name=str(tool_name), args=args, is_mutating=True)


# ---------------------------------------------------------------------------
# The hook-surface remedy swap (issue #14, the amended half). The SELF_MODIFY
# predicate's refusal ends with "Pass --force only if …" — real at the
# `dos arbitrate` CLI, where the operator can pass it. The hook surface has NO
# force (the PreToolUse ABI deliberately gives the agent none), so emitting that
# sentence here points the agent at a door that does not exist — the fuel of the
# observed 21-attempt retry storm. At THIS boundary the sentence is swapped for
# the remedies that DO exist; the predicate text itself is untouched (the CLI
# deny keeps --force). Byte-twinned with the Go fast-path (`hookSurfaceReason`
# in go/internal/hook/decide.go) and the parity corpus generator.
# ---------------------------------------------------------------------------
_CLI_FORCE_TAIL = (
    "Pass --force only if you are deliberately editing the kernel between "
    "loop runs."
)
_HOOK_SURFACE_TAIL = (
    "Do not retry — there is no force override at this surface, and repeated "
    "denies raise an operator decision (dos decisions). Inspect with the "
    "read-only tools; the edit itself is the operator's, made between loop "
    "runs or under their armed override window (dos override status)."
)


def hook_surface_reason(reason: str, reason_class: str) -> str:
    """Swap a SELF_MODIFY refusal's CLI-only remedy for the hook-surface ones.

    Pure. Any other reason class passes through unchanged; a SELF_MODIFY reason
    that does not carry the CLI tail (a host's custom predicate text) gets the
    hook-surface remedies appended instead, so the agent-facing guidance is
    never the missing `--force`.
    """
    if (reason_class or "") != "SELF_MODIFY":
        return reason
    if _CLI_FORCE_TAIL in reason:
        return reason.replace(_CLI_FORCE_TAIL, _HOOK_SURFACE_TAIL)
    return f"{reason} {_HOOK_SURFACE_TAIL}"


# ---------------------------------------------------------------------------
# The composed PRE decision — the two rungs, in order. Returns the CC dialect dict
# to emit (or None for passthrough) PLUS the structured outcome for the journal.
# ---------------------------------------------------------------------------
def decide(
    event: dict,
    cfg: "_config.SubstrateConfig",
    *,
    handler_name: str = "observe",
) -> tuple[Optional[dict], dict]:
    """Run the PRE division on one event → `(dialect_or_None, outcome_record)`.

    `dialect_or_None` is the CC PreToolUse JSON to print (deny / warn) or None (passthrough —
    emit nothing). `outcome_record` is the structured forensic body the CLI journals on a
    non-passthrough outcome (the `OP_ENFORCE` evidence, docs/189 §C4).

    Rung A (admission) runs first: a structural refusal denies immediately. Rung B
    (provenance → intervention → enforce.run_handler) runs only if Rung A admitted. The
    DEFAULT `handler_name="observe"` is the PDP-only floor — `ObserveHandler` proposes
    OBSERVE on everything, so a default install emits ZERO deny from Rung B (a deny there
    requires a wired ruling handler). All of `decide`'s own faults fail toward passthrough.
    """
    # ---- Rung A: structural admission (auto-deny-safe, fail-CLOSED-to-refuse) ----
    from dos import admission
    from dos import override_facts as _ovr
    tree, tree_known = _tree_from_event(event)

    # docs/296 — the override-arm PERIMETER, before admission and never subject
    # to the disposition below: an agent write that touches the operator's arm
    # file (`.dos/override/`) is denied outright. Arming is the operator's hand
    # on the file by design (there is no arm verb), and a window must not be
    # able to extend itself.
    if tree and is_mutating_tool(event) and _ovr.touches_arm_path(tree):
        reason = (
            f"this call would write the operator's SELF_MODIFY override arm file "
            f"({_ovr.ARM_RELPATH}) — only the operator arms a window, by hand "
            f"(docs/296). `dos override status` reports it; `dos override disarm` "
            f"is always allowed."
        )
        outcome = {
            "rung": "admission",
            "decision": "deny",
            "reason_class": "SELF_MODIFY",
            "reason": reason,
            "tree_known": tree_known,
        }
        return deny_payload(f"DOS PRE-admission: {reason}"), outcome

    request = admission.AdmissionRequest(
        lane=str(event.get("tool_name") or "tool"),
        kind="tool-call",
        tree=tree,
    )
    leases = live_leases_for(cfg)

    # The apply-gate (Rung A.5 below) is the AUTHORITATIVE containment + collision
    # check for a run that holds a lease — it alone knows WHICH live lease is THIS
    # run's, so it can tell an in-lane write (contained, allowed) from an escape
    # (refused) and a SIBLING collision (refused) from a SELF "collision" (a write to
    # your own lane, which is not a collision at all). The built-in
    # `DisjointnessPredicate` cannot make that distinction — it sees the write
    # footprint as a NEW lease requesting admission against EVERY live lease,
    # including the run's own, so a write to your own held lane reads as a 100%
    # self-overlap and is wrongly refused. So when the gate is ENABLED and a
    # self-lease resolves, we drop the run's OWN lease from the disjointness sweep and
    # let the apply-gate adjudicate containment + sibling collision (which it does
    # correctly, excluding self via `other_trees`). SELF_MODIFY (request-absolute) is
    # untouched — it fires regardless — and a NON-self sibling lease still gates
    # through disjointness. Gate OFF ⇒ this is a no-op (the leases are unchanged).
    self_lease_id = None
    if _apply_gate_enabled() and is_mutating_tool(event) and tree_known and tree:
        sl, st, _ot = resolve_self_lease(leases, cfg)
        if st:
            # Identify the exact self-lease object by the same match resolve used, so
            # we drop ONLY it (never a same-tree sibling). resolve_self_lease returns
            # the lane+tree; re-find the one lease that is this run's.
            self_lease_id = _find_self_lease(leases, cfg)
    sweep_leases = (
        [lz for lz in leases if lz is not self_lease_id]
        if self_lease_id is not None else leases
    )
    predicates = admission.active_predicates(config=cfg)
    averdict = admission.run_predicates(predicates, request, sweep_leases, cfg)
    if not averdict.admitted:
        # A non-admit is one of TWO very different things, and only one is deny-safe at PRE:
        #
        #   (a) a STRUCTURAL refusal we can PROVE — a typed `reason_class` (SELF_MODIFY), or a
        #       region collision on a KNOWN **and non-empty** tree (`tree_known and tree` — a
        #       parseable footprint that really overlaps a held lease). This is the operator-
        #       visible, --force-overridable admission gate; a pre-dispatch deny here strictly
        #       dominates a post-hoc WARN (docs/191 §3). → deny.
        #
        #   (b) a CONTENTION-only refusal we CANNOT prove collides — an UNKNOWN tree
        #       (`tree_known=False`, an un-parseable mutating footprint) OR a KNOWN-but-EMPTY
        #       tree (a read: `_tree_from_event` → `((), True)`) that got refused only because the
        #       requested lane was contended ("no lane available" / the empty-requested-tree
        #       "unknown blast radius" rule), with NO structural `reason_class`. In neither case
        #       can we show the call actually collides — a read touches NOTHING, and a pathless
        #       write footprint is unknown — so it may be an innocent read / `git status` / `npm
        #       test` running while an UNRELATED lane is leased. Denying it is the docs/143 −9 pp
        #       spurious-disruption mistake (and the PreToolUse ABI gives the agent no --force
        #       escape — a wrong deny just fails the turn). → WARN-and-pass (additionalContext
        #       only, no permissionDecision), the turn-preserving safe direction.
        #
        # The load-bearing correction (FQ-532 Defect 3): `tree_known` ALONE is NOT proof of a
        # collision — a read has a KNOWN but EMPTY tree, and the old `reason_class or tree_known`
        # gate escalated that contention-only refusal to a hard DENY for every Read/Edit while a
        # Bash (unknown tree) only WARNed (the "route-through-Bash" asymmetry). Requiring a
        # NON-EMPTY known tree keeps a contention-only refusal ADVISORY regardless of tree_known,
        # and only a parseable footprint that really overlaps denies — the same "never invent a
        # collision we cannot prove" line `_tree_from_event` already draws for the empty-tree case.
        reason = averdict.reason or "DOS admission refused this call (no lane available)."
        # The hook surface names only the remedies it has (issue #14): swap the
        # predicate's CLI-only `--force` tail before ANY downstream use, so the
        # emitted dialect, the journaled OP_ENFORCE record, and an override
        # note's quoted verdict all carry the same hook-true guidance.
        reason = hook_surface_reason(reason, averdict.reason_class or "")
        provable = bool(averdict.reason_class) or (tree_known and bool(tree))
        # Operator-session softening (mirrors the Go decider's OperatorSession path).
        # A CONTENTION refusal (no `reason_class`) means the call overlaps a held
        # lane's DECLARED region — a defensive claim, not proof the holder is writing
        # this exact file. A dispatch loop must still be DENIED (sibling loops race and
        # a declared collision is their only safe-to-arbitrate signal). But an
        # INTERACTIVE operator (no loop-context env: DOS_LOOP / CID_RUN_ID /
        # DISPATCH_LOOP_TS) is the human-in-command — they own the blast radius of
        # their own deliberate edit (the `--force` principle), so a fleet lane's broad
        # glob DOWNGRADES to an advisory WARN for them, never a hard block. The
        # SELF_MODIFY refusal (reason_class set) is request-absolute and NEVER softened.
        operator_session = (
            not os.environ.get("DOS_LOOP")
            and not os.environ.get("CID_RUN_ID")
            and not os.environ.get("DISPATCH_LOOP_TS")
        )
        if provable and operator_session and not averdict.reason_class:
            outcome = {
                "rung": "admission",
                "decision": "warn",
                "reason_class": "",
                "reason": reason,
                "tree_known": tree_known,
            }
            return (
                warn_payload(
                    f"DOS PRE-admission (advisory, operator session): {reason} A held "
                    f"lane's DECLARED region overlaps this edit, but you are an "
                    f"interactive operator (not a dispatch loop) — you own the blast "
                    f"radius of your own change, so DOS warns instead of blocking. If "
                    f"a fleet loop is actively writing this exact file, coordinate "
                    f"before saving."
                ),
                outcome,
            )
        if provable:
            # docs/296 — the operator's armed override window, consulted at the
            # ENFORCEMENT boundary only (the verdict above is unchanged and still
            # says SELF_MODIFY). Boundary I/O beside the lease read: the arm file
            # + the clock, both fail-closed — a broken reader never admits. Only
            # a SELF_MODIFY refusal is ever converted (`dispose` enforces that),
            # and the admit is emitted as ALLOW-with-note, never a silent pass.
            if (averdict.reason_class or "") == "SELF_MODIFY":
                note = None
                try:
                    import datetime as _dt
                    facts = _ovr.read_override(cfg.root)
                    note = _ovr.dispose(
                        averdict.reason_class or "", tuple(tree), facts,
                        now=_dt.datetime.now(_dt.timezone.utc))
                except Exception:  # noqa: BLE001 — fail-closed: the deny stands
                    note = None
                if note is not None:
                    outcome = {
                        "rung": "admission",
                        "decision": "override-admit",
                        "reason_class": averdict.reason_class or "",
                        "reason": reason,
                        "override_note": note,
                        "tree_known": tree_known,
                    }
                    return (
                        warn_payload(
                            f"DOS PRE-admission (operator override): {note} "
                            f"[the refused verdict was: {reason}]"
                        ),
                        outcome,
                    )
            outcome = {
                "rung": "admission",
                "decision": "deny",
                "reason_class": averdict.reason_class or "",
                "reason": reason,
                "tree_known": tree_known,
            }
            return deny_payload(f"DOS PRE-admission: {reason}"), outcome
        # (c) PROVEN no-footprint (issue #46) — a KNOWN-and-EMPTY tree with no
        #     structural reason_class is a read (Read/Grep/Glob, a no-write Bash, a
        #     read-only MCP tool): `_tree_from_event → ((), True)`. It provably
        #     touches NOTHING, so it cannot collide with ANY live lease — the
        #     advisory below even concedes "a read touches nothing". Firing it on
        #     every read is ambient noise that trains the operator to skim past
        #     PRE-admission output, the wrong reflex for the one call that matters.
        #     So a proven no-footprint call passes CLEAN (no warn) — the advisory is
        #     reserved for the genuinely-unknown case (`tree_known=False`, an
        #     un-parseable mutating footprint), where "scope it to a path" is real
        #     guidance. This only suppresses output; the call passed either way
        #     (WARN was already pass-with-context), so no admission decision changes.
        if tree_known and not tree:
            return None, {
                "rung": "admission",
                "decision": "passthrough",
                "reason_class": "",
                "reason": "proven no-footprint call (a read touches nothing) — "
                          "cannot collide with any live lease",
                "tree_known": tree_known,
            }
        outcome = {
            "rung": "admission",
            "decision": "warn",
            "reason_class": averdict.reason_class or "",
            "reason": reason,
            "tree_known": tree_known,
        }
        return (
            warn_payload(
                f"DOS PRE-admission (advisory): {reason} This call's footprint does not prove a "
                f"collision (an unresolved write footprint is unknown), "
                f"so DOS cannot prove it collides — proceeding, but "
                f"if this call mutates shared state, scope it to a declared path/lane."
            ),
            outcome,
        )

    # ---- Rung A.5: the apply-gate binding turnstile (docs/126 Phase 1.5, opt-in) ----
    # The keystone of docs/342 M1 (P-GATE): generalize the SELF_MODIFY deny above —
    # which binds a write to the kernel's OWN T1 files — to ANY held lease's tree. The
    # SELF_MODIFY predicate already denied a T1 write; `run_predicates` just admitted
    # everything else. The apply-gate adds the one check `run_predicates` does NOT do:
    # does this write ESCAPE the lane THIS run holds (SCOPE_CREEP / WRONG_TARGET), or
    # COLLIDE with a sibling lease's region? `apply_gate.decide` answers it — the same
    # pure verdict the `dos apply` CLI binds (no new decision logic; §2). This is the
    # change that converts DOS from "checker the host may consult" to "gate the write
    # must pass": a NON-cooperating agent's out-of-lane Write is refused AT the tool
    # call, not just by an opt-in CLI it can skip.
    #
    # OPT-IN, by design (docs/126 §3 rule 3: an ADDITIONAL binding surface, never a
    # mutation of the always-on verdicts). It binds ONLY when the operator set
    # `DOS_APPLY_GATE` AND this run actually holds a lease (`self_tree` resolved) —
    # an un-leased interactive session keeps its pre-apply-gate behavior, because the
    # gate binds a run that DECLARED a lane, never one that never opted in. The
    # always-on SELF_MODIFY guard above is untouched; this only ADDS refusals
    # (refuse-MORE only). NOTE the honest deployment limit: the native fast-path
    # binary (`try_native_hook`) serves the default SELF_MODIFY/disjointness path and
    # does NOT run this Python-side opt-in gate — when `DOS_APPLY_GATE` is set the host
    # must reach this decider (the witness drives `decide()` directly, the canonical
    # decision function).
    if _apply_gate_enabled() and is_mutating_tool(event) and tree_known and tree:
        self_lane, self_tree, other_trees = resolve_self_lease(leases, cfg)
        if self_tree or other_trees:
            from dos import apply_gate
            ev = apply_gate.ApplyEvidence(
                touched_files=frozenset(tree),
                self_lane=self_lane,
                self_tree=self_tree,
                other_trees=other_trees,
            )
            gate = apply_gate.decide(ev)
            if not gate.allowed:
                # The docs/126 P1.5 / docs/143 soften trap, resolved (option a): a
                # `SCOPE_ESCAPE` is a MISROUTE/CONTENTION-class refusal — "work aimed
                # at a lane it doesn't own." A dispatch LOOP must be hard-DENIED
                # (sibling loops race; a declared-tree escape is their only
                # safe-to-arbitrate signal, and the agent has no --force here). But an
                # INTERACTIVE operator (no loop-context env) is the human-in-command —
                # they own the blast radius of their own deliberate edit (the --force
                # principle, the CLI gate's audited override), so the gate DOWNGRADES to
                # an advisory WARN for them, never a dead-end block (the docs/143 −9 pp
                # spurious-disruption mistake). This mirrors the operator-session
                # softening above for a DisjointnessPredicate collision exactly.
                gate_reason = (
                    f"{gate.reason} [held lane: {self_lane or '<unresolved>'}] "
                    f"(apply-gate, docs/126)"
                )
                operator_session = (
                    not os.environ.get("DOS_LOOP")
                    and not os.environ.get("CID_RUN_ID")
                    and not os.environ.get("DISPATCH_LOOP_TS")
                )
                if operator_session:
                    outcome = {
                        "rung": "apply-gate",
                        "decision": "warn",
                        "reason_class": gate.reason_class,
                        "reason": gate_reason,
                        "refused_files": list(gate.refused_files),
                        "tree_known": tree_known,
                    }
                    return (
                        warn_payload(
                            f"DOS PRE apply-gate (advisory, operator session): "
                            f"{gate_reason} You are an interactive operator (not a "
                            f"dispatch loop) — you own the blast radius of your own "
                            f"change, so DOS warns instead of blocking. If a fleet "
                            f"loop is actively writing this region, coordinate before "
                            f"saving; the audited CLI override is `dos apply --force`."
                        ),
                        outcome,
                    )
                outcome = {
                    "rung": "apply-gate",
                    "decision": "deny",
                    "reason_class": gate.reason_class,
                    "reason": gate_reason,
                    "refused_files": list(gate.refused_files),
                    "tree_known": tree_known,
                }
                return deny_payload(f"DOS PRE apply-gate: {gate_reason}"), outcome

    # ---- Rung B: behavioral provenance (confidence-gated, fail-to-OBSERVE) ----
    call = toolcall_from_event(event)
    if call is None:
        return None, {"rung": "none", "decision": "passthrough", "reason": "read / non-mutating call"}
    from dos import arg_provenance, intervention, enforce
    prior = prior_results(str(event.get("session_id") or ""), cfg)
    pverdict = arg_provenance.classify_call(call, prior, arg_provenance.DEFAULT_POLICY)
    decision = intervention.choose_intervention(
        pverdict, intervention.DEFAULT_POLICY, cfg.interventions
    )
    handler = enforce.resolve_handler(handler_name)
    proposal = enforce.run_handler(handler, decision, cfg)
    base = {
        "rung": "provenance",
        "intervention": proposal.intervention.value,
        "confidence": decision.confidence.value,
        "handler": proposal.handler,
        "unsupported": list(decision.unsupported),
    }
    if proposal.withholds_call:
        # A turn-preserving BLOCK → deny, with the synthetic corrective surfaced in the
        # reason (names the unresolved arg by NAME + component TOKENS only — the
        # anti-laundering shape; never echoes the minted id value).
        synth = proposal.synthetic_result or intervention.synthetic_corrective_result(
            pverdict, call.tool_name
        )
        ctx = json.dumps(synth, sort_keys=True, ensure_ascii=False)
        reason = proposal.note or decision.reason or "an id argument was minted, not resolved."
        return deny_payload(f"DOS PRE-provenance: {reason}", additional_context=ctx), {
            **base, "decision": "deny",
        }
    if proposal.intervention is intervention.Intervention.WARN and proposal.note:
        # WARN-and-pass: additionalContext only, no permissionDecision (passthrough).
        return warn_payload(f"DOS PRE-provenance: {proposal.note}"), {**base, "decision": "warn"}
    # OBSERVE (or a WARN with nothing to surface) → emit nothing.
    return None, {**base, "decision": "passthrough"}
