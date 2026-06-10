"""Generic registry → CLI wiring for verdict verbs (docs/86 §2, the modular seam).

The point: a consumer (`cli.py`) hard-wires `dos verify`/`dos liveness`/… today —
one `cmd_X` + `add_parser` + `set_defaults` per verb. That is the coupling the
registry exists to dissolve. This module is the **generic dispatcher**: it reads
`verdicts.all_specs()` and builds a subparser + a uniform run-handler for every
verb that carries a CLI adapter, so:

    adding a verb = one register(...) (+ its CLI adapter) — and `cli.py` is
    edited ONCE (a single `verdict_cli.attach(sub, ...)` call), NEVER per verb.

That single-line hook into `cli.py` is the only edit the real CLI needs; it is
deliberately deferred until the (currently hot) `cli.py` settles, so this module
is built and tested STANDALONE first. Everything here works against a plain
`argparse` parser with no dependency on `cli.py`'s internals — the consumer
injects how it resolves a `SubstrateConfig` via `config_resolver`, so this module
stays decoupled from `cli.py`'s workspace plumbing.

Layering: this is a CONSUMER, like `cli.py` / `dos_mcp` — it imports the registry
and the verbs; nothing under `src/dos/*.py` that is a verb imports it. The
verb-agnostic CORE of each spec (name/classify/summary) lives in `verdicts.py`
(no I/O); the CLI ADAPTER (argument flags, the git-diff gather, exit codes) is a
consumer concern and lives HERE — the mechanism-vs-consumer split the kernel
draws everywhere. `_ensure_cli_specs()` enriches the registry's core specs with
their adapters the first time the dispatcher is built, confining the boundary I/O
(git) to this module.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import verdicts, scope


_GIT_TIMEOUT_S = 15


# ---------------------------------------------------------------------------
# Boundary I/O — the git-diff reader (the `git_delta` mold: the subprocess lives
# in the consumer, the pure classifier gets an already-gathered set).
# ---------------------------------------------------------------------------
def _git_diff_names(base: str, head: str, *, root: Path | str) -> frozenset[str]:
    """`git diff --name-only <base>..<head>` over `root`, as a frozenset of paths.

    Degrades to an empty set on any failure (non-git dir, bad ref, missing git,
    timeout, non-zero exit) — the same fail-safe as `git_delta.commits_since`: a
    scope verdict in a repo with no diff is the honest "empty footprint", never a
    crash.
    """
    try:
        raw = subprocess.run(
            ["git", "diff", "--name-only", f"{base}..{head}"],
            cwd=str(root), capture_output=True, text=True,
            check=False, timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    if raw.returncode != 0:
        return frozenset()
    return frozenset(ln.strip() for ln in raw.stdout.splitlines() if ln.strip())


# ---------------------------------------------------------------------------
# The CLI adapter for `scope` (a consumer concern — args, gather, exit codes).
# Lives here, not in scope.py (pure) or verdicts.py (no I/O), so the kernel verb
# stays pure and the registry stays light. A future verb adds its adapter the
# same way; the dispatcher below is unchanged.
# ---------------------------------------------------------------------------
def _scope_add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lane", required=True,
                   help="the lane the diff is stamped against (a key in [lanes])")
    p.add_argument("--base", default="HEAD~1",
                   help="base ref of the diff range (default HEAD~1)")
    p.add_argument("--head", default="HEAD",
                   help="head ref of the diff range (default HEAD)")


def _scope_gather(args: argparse.Namespace, cfg: Any) -> scope.ScopeEvidence:
    """Build `ScopeEvidence` from CLI args + config: the touched-file set from
    `git diff`, the lane tree from `cfg.lanes.trees[lane]` (generic `("**/*",)`
    if the lane is undeclared — the no-plan floor)."""
    root = cfg.paths.root
    touched = _git_diff_names(args.base, args.head, root=root)
    lane_tree = tuple(cfg.lanes.trees.get(args.lane, ("**/*",)))
    return scope.ScopeEvidence(touched_files=touched, lane_tree=lane_tree, lane=args.lane)


# Exit codes per scope verdict (distinct from argparse's 2=usage and liveness's
# 3/4): a clean footprint is 0; a violation is non-zero so a shell `if dos scope`
# branches on it. SCOPE_CREEP < WRONG_TARGET by severity.
_SCOPE_EXIT = {"IN_SCOPE": 0, "SCOPE_CREEP": 5, "WRONG_TARGET": 6}


_CLI_SPECS_READY = False


def _ensure_cli_specs() -> None:
    """Enrich the registry's core specs with their CLI adapters (idempotent).

    `verdicts.py` seed-registers the verb-agnostic core (name/classify/…, no I/O).
    Here we add the consumer-side adapter — argument flags, the git-diff gather,
    exit codes — by re-registering with `replace=True`. Confines git I/O to this
    module and keeps the registry the single source of truth for *which* verbs
    exist while letting each consumer own *how* it surfaces them.
    """
    global _CLI_SPECS_READY
    if _CLI_SPECS_READY:
        return
    core = verdicts.get("scope")
    verdicts.register(verdicts.VerdictSpec(
        name=core.name, classify=core.classify, summary=core.summary,
        distrusts=core.distrusts, reviewed=core.reviewed,
        add_arguments=_scope_add_args,
        gather=_scope_gather,
        exit_codes=dict(_SCOPE_EXIT),
    ), replace=True)
    # NOTE: `liveness` already has a bespoke `cmd_liveness` in cli.py (with the
    # journal-rung logic); migrating it onto this dispatcher is a follow-up to do
    # against the real cli.py, not blind here. `verify` waits on ShipVerdict
    # harmonization (it has no typed `.verdict` yet). So `scope` is the first verb
    # surfaced through the generic path — the proof the wiring is real.
    _CLI_SPECS_READY = True


def _render(verdict_obj: Any, output: str) -> None:
    if output == "json":
        print(json.dumps(verdict_obj.to_dict(), indent=2))
    else:
        print(f"{verdict_obj.verdict.value}: {verdict_obj.reason}")


def attach(
    subparsers: Any,
    *,
    config_resolver: Callable[[argparse.Namespace], Any],
) -> list[str]:
    """Build a subcommand for every registered verb that carries a CLI adapter.

    `subparsers` is the object returned by `parser.add_subparsers()`.
    `config_resolver(args) -> SubstrateConfig` is injected by the consumer (cli.py
    passes its own workspace-resolution), so this module never duplicates cli.py's
    plumbing. Returns the list of verb names it wired (for tests / help). The
    `cli.py` integration is one line: `verdict_cli.attach(sub, config_resolver=...)`.
    """
    _ensure_cli_specs()
    wired: list[str] = []
    for spec in verdicts.all_specs().values():
        if spec.add_arguments is None or spec.gather is None:
            continue  # a library-only verb (no CLI surface) — skip, don't crash
        p = subparsers.add_parser(spec.name, help=spec.summary)
        spec.add_arguments(p)
        p.add_argument("--workspace", default=".",
                       help="the workspace whose state the verb reads")
        p.add_argument("--output", choices=["text", "json"], default="text")
        p.set_defaults(_verdict_spec=spec, _config_resolver=config_resolver)
        wired.append(spec.name)
    return wired


def run(args: argparse.Namespace) -> int:
    """The uniform handler for any verdict verb wired by `attach`.

    gather (boundary I/O) → classify (pure) → render → exit code. One function for
    ALL verbs — the dispatcher's payoff. Set as the subparser's func by `attach`
    via `_verdict_spec`; a consumer with its own dispatch can call this directly.
    """
    spec: verdicts.VerdictSpec = args._verdict_spec
    cfg = args._config_resolver(args)
    evidence = spec.gather(args, cfg)
    policy = spec.policy_from(cfg) if spec.policy_from is not None else _default_policy(spec)
    verdict_obj = spec.classify(evidence, policy) if policy is not None else spec.classify(evidence)
    _render(verdict_obj, getattr(args, "output", "text"))
    return spec.exit_codes.get(verdict_obj.verdict.value, 0)


def _default_policy(spec: verdicts.VerdictSpec) -> Any:
    """The verb's own default policy when the spec declares no `policy_from`.

    Looked up by name so we don't couple to a specific module here. (The
    `dos.toml [<name>]` policy seam is wired per-verb via `policy_from`; until a
    verb declares it, its module default applies — e.g. `scope.DEFAULT_POLICY`.)"""
    if spec.name == "scope":
        return scope.DEFAULT_POLICY
    return None  # classify(evidence) with its own default argument
