"""FORBIDDEN_CALL_SHAPE — the declared-call-shape admission predicate (docs/364).

The SELF_MODIFY sibling, generalized from "the proposed write touches the
kernel's own code" to "the proposed call matches a shape the host DECLARED
out-of-policy for this lane." It is the kernel verdict for OWASP **ASI02 Tool
Misuse & Exploitation** (and the lethal-trifecta egress leg): the surface that
lets a host refuse, at PreToolUse, a tool call whose *arguments* — not just its
file tree — match a forbidden program / argument-substring / path glob.

Why this is a new predicate and not `exec_capability`
=====================================================

`exec_capability` (XCAP, docs/224) reads the same agent-authored command bytes
but is deliberately a *classifier the consumer consults*, advisory-only — it
reports a capability, it does not refuse. This module is the *admission* half:
it plugs into the arbiter conjunction beside `SelfModifyPredicate`, so a match
becomes a real `AdmissionVerdict.refuse(reason_class=FORBIDDEN_CALL_SHAPE)` that
the PreToolUse sensor renders as a `permissionDecision: deny`. The difference is
the policy surface: XCAP had no host-declared deny list, so it stayed a WARN;
`[call_shape]` IS that declared deny list, so a match is a real refusal.

The five contract rails this leaf is built to satisfy
=====================================================

  * **DECLARED, never sniffed.** The kernel matches agent-authored bytes against
    a host-declared set — forbidden command-prefixes, literal arg substrings,
    path globs. It never decides "this looks malicious." No regex, no intent
    inference. The match is SHAPE-not-word (the docs/158 law `exec_capability`
    already obeys): the invoked-program token `curl` matches; the substring
    `curl` inside `echo "see curl_notes.txt"` does not.
  * **Conjunctive / refuse-MORE only.** A `DeclaredCallShapePredicate` returns
    only `admit()` / `refuse(...)`; `AdmissionVerdict` has no force-admit
    constructor, so it can only ever make admission stricter, AND-ed under the
    disjointness floor. It is appended LAST in `built_in_predicates`, after the
    disjointness + self-modify guards, so it can never displace them.
  * **Sound at PRE.** It reads only the proposed call's own bytes (the command
    string, the string argument values, the file tree) + the declared policy —
    all present BEFORE the call runs. No tool RESULT, so the verdict is honest
    at the one moment a deny can prevent an effect (docs/191).
  * **OFF by default / byte-unchanged generic workspace.** A workspace that
    declares no `[call_shape]` table gets `EMPTY_CALL_SHAPE`; the predicate's
    first line short-circuits to `admit()` before touching any bytes, so its
    presence changes ONLY `dos doctor`'s predicate list, never a verdict. The
    generic kernel admission stream is byte-identical to the two-predicate
    conjunction that preceded this module.
  * **Fail toward ADMIT on ambiguity (under-match).** A command present but not
    confidently tokenizable is the normal lossy-parse case the whole sensor is
    built around (`_paths_from_command`: "never invent a collision we cannot
    prove"). Refusing every unparseable Bash on a workspace that declared one
    forbidden prefix would recreate the docs/143 -9pp spurious-disruption
    mistake and violate "no false deny on the call we cannot prove." So a clear
    match refuses; anything unparseable/ambiguous admits. (A predicate that
    *raises* is still caught by `run_predicates` and converted to a refuse — but
    that is the MALFUNCTION path, which this leaf avoids by wrapping its own
    tokenization, so a parse fault degrades to admit, not to that fail-closed
    branch.)

Pure stdlib + the `_tree` prefix algebra — no I/O, no host names, no vendor
names. Policy is passed in (resolved at the config-load boundary), evidence is
the parsed call. The same kernel-leaf shape as `self_modify` / `exec_capability`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dos._tree import norm_tree_prefix as _norm_tree_prefix
from dos._tree import prefixes_collide as _prefixes_collide
from dos.admission import AdmissionRequest, AdmissionVerdict

# The typed reason a forbidden-call-shape refusal carries — declared in
# `dos.reasons.BASE_REASONS` (NOT dos.toml) because the KERNEL's own predicate
# emits it: a dos.toml-only token would be undeclared on a foreign workspace
# whose kernel still raises it, the exact rule SELF_MODIFY / NO_FREE_REGION obey.
FORBIDDEN_CALL_SHAPE_REASON = "FORBIDDEN_CALL_SHAPE"

# Shell operators that join command segments — each segment invokes its own
# program, so each is tokenized independently (a `git log && curl …` must fire on
# the `curl` segment). Pinned to `pretool_sensor._SEGMENT_SEPARATORS`; duplicated
# here (not imported) because `pretool_sensor` is the helper layer that imports
# the kernel — a kernel leaf cannot import upward from it.
_SEGMENT_SEPARATORS: tuple[str, ...] = ("&&", "||", ";", "|", "&", "\n")


def _segment_lead_tokens(segment: str, limit: int = 3) -> list[str]:
    """The invoked-program token + up to two non-flag subcommand tokens. PURE.

    The SHAPE extraction, byte-for-byte the discipline of
    `exec_capability._program_token` / `pretool_sensor._segment_lead_tokens`
    (the canonical sources — duplicated here, not imported, to keep this a kernel
    leaf): skip a leading `VAR=value` assignment, take the program's basename
    lower-cased, then collect following non-flag tokens (so `git --no-pager log`
    reads as `["git", "log"]`). A flag that consumes a value (`git -C dir log`)
    mis-reads the value as a subcommand — which simply fails the closed-set
    lookup and the call admits (under-match, the safe direction).
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


def _command_segments(command: str) -> list[str]:
    """Split a command on shell segment separators. PURE — not a shell parser."""
    work = command
    for sep in _SEGMENT_SEPARATORS:
        work = work.replace(sep, "\x00")
    return [s for s in (seg.strip() for seg in work.split("\x00")) if s]


@dataclass(frozen=True)
class CallShapePolicy:
    """The forbidden shapes for ONE lane — policy, not mechanism, as data.

    The kernel owns the mechanism (tokenize the command, match the program
    prefix; substring-match the args; prefix-collide the tree). The SET of
    forbidden shapes is host data, declared per-lane in `dos.toml [call_shape.*]`.
    All three rungs are optional; an all-empty policy `is_empty()` and admits
    everything (the OFF-by-default contract).

      forbidden_command_prefixes — leading program-token tuples that are
        out-of-policy. ``("curl",)`` forbids the program `curl`; ``("git",
        "push")`` forbids `git push` but not `git status`. SHAPE-not-word: the
        match is on the invoked-program leading tokens, never a substring of the
        whole command.
      forbidden_arg_patterns — LITERAL substrings (not regex) matched against
        each string argument value AND the raw command string. The lethal-
        trifecta egress leg: a host declares specific exfil hosts (``@evil.example``)
        or a scheme (``://`` scoped to a data lane). The kernel matches the
        declared literal; it never decides what "egress" is.
      forbidden_path_globs — repo-relative globs matched against the proposed
        write tree via the `_tree` prefix algebra (the same `self_modify` uses).
        ``**/.env`` forbids writing a dotenv anywhere.
    """

    forbidden_command_prefixes: tuple[tuple[str, ...], ...] = ()
    forbidden_arg_patterns: tuple[str, ...] = ()
    forbidden_path_globs: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        """True iff this policy forbids nothing — the OFF-by-default short-circuit."""
        return not (
            self.forbidden_command_prefixes
            or self.forbidden_arg_patterns
            or self.forbidden_path_globs
        )

    def union(self, other: "CallShapePolicy") -> "CallShapePolicy":
        """Additive merge — the conservative direction for a refuse-more predicate.

        Per-lane UNION workspace-wide: a per-lane table can only ADD forbidden
        shapes onto the workspace floor, never remove one (a lane cannot be made
        *less* restricted than the workspace-wide policy). De-dup preserves order.
        """
        def _dedup(seq):
            seen = set()
            out = []
            for x in seq:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return tuple(out)

        return CallShapePolicy(
            forbidden_command_prefixes=_dedup(
                self.forbidden_command_prefixes + other.forbidden_command_prefixes),
            forbidden_arg_patterns=_dedup(
                self.forbidden_arg_patterns + other.forbidden_arg_patterns),
            forbidden_path_globs=_dedup(
                self.forbidden_path_globs + other.forbidden_path_globs),
        )


EMPTY_CALL_SHAPE_POLICY = CallShapePolicy()


@dataclass(frozen=True)
class CallShapeRuleset:
    """The workspace's whole `[call_shape]` declaration — a per-lane lookup.

    ``workspace_wide`` is the floor every lane inherits (`dos.toml [call_shape]`);
    ``per_lane`` are the additions a specific lane declares (`[call_shape.<lane>]`).
    `policy_for(lane)` returns the workspace-wide policy UNION the lane's own —
    so a lane is always AT LEAST as restricted as the workspace floor. The
    default `EMPTY_CALL_SHAPE` forbids nothing for any lane.
    """

    workspace_wide: CallShapePolicy = EMPTY_CALL_SHAPE_POLICY
    per_lane: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        """True iff NO lane forbids anything — the generic-workspace contract."""
        return self.workspace_wide.is_empty() and not any(
            not p.is_empty() for p in self.per_lane.values()
        )

    def policy_for(self, lane: str) -> CallShapePolicy:
        """The effective policy for ``lane``: workspace-wide UNION the lane's own."""
        lane_pol = self.per_lane.get(lane)
        if lane_pol is None:
            return self.workspace_wide
        return self.workspace_wide.union(lane_pol)


EMPTY_CALL_SHAPE = CallShapeRuleset()


def _command_matches_forbidden_prefix(
    command: str, prefixes: tuple[tuple[str, ...], ...]
) -> tuple[str, ...] | None:
    """The forbidden command-prefix a command's leading tokens match, or None.

    Checks EACH command segment (so `git log && curl x` fires on the `curl`
    segment). A forbidden prefix matches a segment iff it is a leading slice of
    that segment's tokens — `("git", "push")` matches `git push origin` but not
    `git status`. Returns the FIRST forbidden prefix matched (declaration order),
    so the refusal can name exactly what fired. None = no segment matched.
    """
    if not command or not command.strip() or not prefixes:
        return None
    for segment in _command_segments(command):
        toks = _segment_lead_tokens(segment)
        if not toks:
            continue
        for prefix in prefixes:
            if not prefix:
                continue
            if tuple(toks[: len(prefix)]) == tuple(prefix):
                return prefix
    return None


def _arg_matches_forbidden_pattern(
    command: str, arg_values: tuple[str, ...], patterns: tuple[str, ...]
) -> tuple[str, str] | None:
    """The (pattern, haystack) of the first forbidden arg-substring hit, or None.

    LITERAL substring match (never regex) against each string argument value and
    the raw command string. The command string is included so an egress URL
    embedded in a Bash command (`curl https://evil`) is caught even when no
    structured arg carries it. Returns (matched_pattern, the_value_it_hit) so the
    refusal names what fired; None = nothing matched.
    """
    if not patterns:
        return None
    haystacks = list(arg_values)
    if command:
        haystacks.append(command)
    for pattern in patterns:
        if not pattern:
            continue
        for hay in haystacks:
            if pattern in hay:
                return (pattern, hay)
    return None


def _tree_matches_forbidden_glob(
    tree: tuple[str, ...], globs: tuple[str, ...]
) -> str | None:
    """The first forbidden path-glob the proposed tree collides with, or None.

    Prefix-collision in both directions via the `_tree` algebra (the same rule
    `self_modify._tree_touches_runtime` uses): a forbidden `**/.env` collides
    with a request to write `.env`; a forbidden `.ssh/**` collides with
    `.ssh/id_rsa`. Returns the offending glob (un-normalized) so the refusal can
    name it; None = the tree touches no forbidden glob.
    """
    if not tree or not globs:
        return None
    req_prefixes = [_norm_tree_prefix(p) for p in tree if p]
    if not req_prefixes:
        return None
    for original in globs:
        gp = _norm_tree_prefix(original)
        if any(_prefixes_collide(rp, gp) for rp in req_prefixes):
            return original
    return None


class DeclaredCallShapePredicate:
    """Refuse a tool call whose agent-authored arguments match a declared forbidden shape.

    Request-absolute, like `SelfModifyPredicate`: the hazard is a property of the
    PROPOSED call, not of contention with a live lease, so it answers from the
    request alone and ignores ``live_lease`` (it still implements the per-lease
    signature so it composes in the same conjunction; it returns the same verdict
    for every live lease, harmless because `run_predicates` short-circuits on the
    first refusal).

    Reads the effective `CallShapePolicy` for the request's lane from the
    ``ruleset`` it was built with (resolved at the config-load boundary, exactly
    like `SelfModifyPredicate`'s `runtime_files`). The FIRST line short-circuits
    to admit when the lane forbids nothing — so a generic workspace pays nothing
    and changes no verdict. The three rungs are checked in a fixed order
    (command-prefix, arg-substring, path-glob); the first hit refuses.

    Fail-safe: the rung helpers under-match (an unparseable command yields no
    leading tokens → no prefix match → admit). The whole body is wrapped so an
    internal parse fault degrades to ADMIT, never to `run_predicates`'s
    fail-closed (which is reserved for a true malfunction).
    """

    name = "call-shape"

    def __init__(self, ruleset: CallShapeRuleset = EMPTY_CALL_SHAPE) -> None:
        self._ruleset = ruleset

    def __call__(self, request: AdmissionRequest, live_lease: dict,
                 config: object) -> AdmissionVerdict:
        try:
            policy = self._ruleset.policy_for(request.lane)
            if policy.is_empty():
                return AdmissionVerdict.admit()

            command = getattr(request, "command", "") or ""
            arg_values = tuple(getattr(request, "arg_values", ()) or ())
            tree = tuple(request.tree or ())

            prefix_hit = _command_matches_forbidden_prefix(
                command, policy.forbidden_command_prefixes)
            if prefix_hit is not None:
                shown = " ".join(prefix_hit)
                return AdmissionVerdict.refuse(
                    f"lane {request.lane!r} proposed a call invoking {shown!r}, "
                    f"a command shape the workspace's declared [call_shape] policy "
                    f"forbids (FORBIDDEN_CALL_SHAPE). Relax the declared shape, run "
                    f"OUTSIDE the gated lane, or pass --force (operator override).",
                    reason_class=FORBIDDEN_CALL_SHAPE_REASON,
                )

            arg_hit = _arg_matches_forbidden_pattern(
                command, arg_values, policy.forbidden_arg_patterns)
            if arg_hit is not None:
                pattern, _hay = arg_hit
                return AdmissionVerdict.refuse(
                    f"lane {request.lane!r} proposed a call whose arguments contain "
                    f"{pattern!r}, a substring the workspace's declared [call_shape] "
                    f"policy forbids (FORBIDDEN_CALL_SHAPE). Relax the declared "
                    f"pattern, run OUTSIDE the gated lane, or pass --force.",
                    reason_class=FORBIDDEN_CALL_SHAPE_REASON,
                )

            glob_hit = _tree_matches_forbidden_glob(
                tree, policy.forbidden_path_globs)
            if glob_hit is not None:
                return AdmissionVerdict.refuse(
                    f"lane {request.lane!r} proposed a write to a path matching "
                    f"{glob_hit!r}, a path glob the workspace's declared [call_shape] "
                    f"policy forbids (FORBIDDEN_CALL_SHAPE). Relax the declared "
                    f"glob, run OUTSIDE the gated lane, or pass --force.",
                    reason_class=FORBIDDEN_CALL_SHAPE_REASON,
                )

            return AdmissionVerdict.admit()
        except Exception:
            # Internal parse fault is NOT a malfunction worth fail-closing on —
            # it is the lossy-parse case. Degrade to admit (under-match), the same
            # "never invent a refusal we cannot prove" posture the sensor draws.
            return AdmissionVerdict.admit()


# ---------------------------------------------------------------------------
# The dos.toml loader — the `[call_shape]` data attachment. Mirrors
# `config.load_overlap_from_toml` in shape: an absent table inherits ``base``; a
# present-but-malformed table RAISES (surfaced by `load_workspace_config`'s shared
# warn-and-fall-back). Lives here, beside the policy it parses (co-located data +
# parser, the DOM design rule), and is imported by `config.load_workspace_config`.
# ---------------------------------------------------------------------------
_POLICY_KEYS = {
    "forbidden_command_prefixes",
    "forbidden_arg_patterns",
    "forbidden_path_globs",
}


def _coerce_prefix_list(raw, key: str) -> tuple[tuple[str, ...], ...]:
    """Coerce a `forbidden_command_prefixes` value to tuples of lower tokens.

    Accepts either a list of strings (``["curl", "git push"]`` — each string is
    split into its tokens) or a list of lists (``[["git", "push"]]``). A string
    entry `git push` becomes the prefix tuple `("git", "push")`. Empty after
    tokenizing is dropped. Anything not a list, or a list with a non-string/
    non-list element, RAISES.
    """
    if not isinstance(raw, list):
        raise ValueError(f"[call_shape] {key} must be a list, got {type(raw).__name__}")
    out: list[tuple[str, ...]] = []
    for entry in raw:
        if isinstance(entry, str):
            toks = tuple(t.lower() for t in entry.split() if t.strip())
        elif isinstance(entry, list):
            toks = tuple(str(t).strip().lower() for t in entry if str(t).strip())
        else:
            raise ValueError(
                f"[call_shape] {key} entries must be strings or lists, "
                f"got {type(entry).__name__}")
        if toks:
            out.append(toks)
    return tuple(out)


def _coerce_str_list(raw, key: str) -> tuple[str, ...]:
    """Coerce a string-list value (arg patterns / path globs). RAISES on non-list."""
    if not isinstance(raw, list):
        raise ValueError(f"[call_shape] {key} must be a list, got {type(raw).__name__}")
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ValueError(
                f"[call_shape] {key} entries must be strings, got {type(entry).__name__}")
        s = entry.strip() if key == "forbidden_path_globs" else entry
        if s:
            out.append(s)
    return tuple(out)


def _policy_from_table(table: dict, *, where: str) -> CallShapePolicy:
    """Build a `CallShapePolicy` from one `[call_shape]`/`[call_shape.<lane>]` table."""
    unknown = set(table) - _POLICY_KEYS
    if unknown:
        raise ValueError(
            f"unknown {where} key(s): {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(sorted(_POLICY_KEYS))})")
    return CallShapePolicy(
        forbidden_command_prefixes=_coerce_prefix_list(
            table.get("forbidden_command_prefixes", []), "forbidden_command_prefixes"),
        forbidden_arg_patterns=_coerce_str_list(
            table.get("forbidden_arg_patterns", []), "forbidden_arg_patterns"),
        forbidden_path_globs=_coerce_str_list(
            table.get("forbidden_path_globs", []), "forbidden_path_globs"),
    )


def load_call_shape_from_toml(path, *, base: CallShapeRuleset = EMPTY_CALL_SHAPE):
    """Read the `[call_shape]` table → a `CallShapeRuleset`. Absent → ``base``.

    Schema (all keys + sub-tables optional):

        [call_shape]                       # the workspace-wide floor
        forbidden_command_prefixes = ["curl", "wget", "git push"]
        forbidden_arg_patterns     = ["://raw.", "@evil.example"]
        forbidden_path_globs       = ["**/.env", ".ssh/**"]

        [call_shape.data-egress]           # a per-lane addition (UNION the floor)
        forbidden_command_prefixes = ["scp", "rsync", "ssh"]
        forbidden_arg_patterns     = ["://"]

    The workspace-wide keys live directly under ``[call_shape]``; any SUB-table
    (``[call_shape.<lane>]``, parsed by TOML as a nested dict value) is a per-lane
    policy that UNIONs the workspace floor. A present-but-malformed value RAISES —
    a forbidden-shape typo silently doing nothing is the exact hazard (an operator
    believing a ban is in force when it is not). Imported lazily by
    `config.load_workspace_config`; uses `config._load_toml_table` to read.
    """
    from dos.config import _load_toml_table  # lazy: config imports this leaf back
    table = _load_toml_table(path, "call_shape")
    if table is None:
        return base
    # Split scalar/list keys (workspace-wide) from nested sub-tables (per-lane).
    wide_table = {k: v for k, v in table.items() if not isinstance(v, dict)}
    lane_tables = {k: v for k, v in table.items() if isinstance(v, dict)}
    workspace_wide = _policy_from_table(wide_table, where="[call_shape]")
    per_lane = {
        lane: _policy_from_table(sub, where=f"[call_shape.{lane}]")
        for lane, sub in lane_tables.items()
    }
    return CallShapeRuleset(workspace_wide=workspace_wide, per_lane=per_lane)
