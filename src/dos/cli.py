"""The `dos` umbrella CLI — one entrypoint over the kernel syscalls.

New here?  →  dos quickstart      the caught-lie + collision demo, throwaway repo (60s)
            →  dos init [DIR]       scaffold the one dos.toml most repos need
            →  dos doctor          report the active workspace + lane taxonomy

TRUTH — did it actually happen? (the verdict IS the exit code; `dos exit-codes`)
    dos verify PLAN PHASE          did (plan,phase) ship?  (git ancestry, not self-report)
    dos commit-audit [REF]         does a commit's SUBJECT match its own diff?
    dos coverage ...               did the test actually execute the target?
    dos liveness --run-id R ...    is the run moving (ADVANCING) or spinning (STALLED)?
    dos productivity --deltas ...  is the run still doing work, or fading?
    dos efficiency --work W ...    did the tokens buy work?  (work per token spent)
    dos efficiency-trend ...       is work-per-token fading ACROSS runs? (fold the fossils)
    dos improve --work W ...       may a self-improving loop KEEP this candidate?
    dos test-witness ...           does a NEW test witness the change? (red->green, never pass/pass)
    dos complete ...               is this unit done, or quietly incomplete?
    dos verify-result ...          is a subagent's terminal record real-model authored?
    dos attest / verify-receipt    a portable signed receipt over an effect-witness

ADMISSION — may this act run? (the lock manager; refuses with a way forward)
    dos arbitrate --lane L ...     may a loop start on lane L?  (auto-picks a free lane)
    dos scope-gate ...             does the footprint stay inside the declared tree?
    dos pickable / enumerate       is there anything pickable, and why-not?
    dos cooldown / reconcile       have I tried it; did the claim hold?
    dos pick-priority              prefer new work over churn (freshness sort-key)
    dos breaker / exec-capability  circuit-breaker; arbitrary-exec classifier
    dos lint [--strict]            dead policy in this workspace's own declarations?

SPINE — correlation + the durable record
    dos run-id mint PROCESS        mint a sortable, lineage-carrying run-id
    dos lease-lane {acquire,release,heartbeat,spawn,live}   durable lane lease (WAL write-back)
    dos lease {acquire,release,status}                the archive-lock (cross-process mutex)
    dos journal {tail,replay,seq,compact}             the lane write-ahead log
    dos resume / rewind / status   recover a died/paused run from its fossils
    dos trace RUN_ID               walk the spine + ledger + WAL + git for one run

OPS — what needs me, what's running, what is this workspace?
    dos decisions [show N]         the operator-decision queue (list + drill-in TUI)
    dos top                        the live fleet watchdog (what's running now)
    dos observe [--run RID]        the verdict journal — every adjudication, folded
    dos notify {decisions,top}     push a projection to a transport (Slack first)
    dos man {wedge,lane} [ID]      the self-describing manual over the registries
    dos guard / hook {pretool,posttool,stop}          bind the verdict to an agent runtime
    dos doctor [--json] / lint     the workspace report; the config-integrity linter
    dos reindex / projects / learn the cross-project store, registry, and aggregates

(`dos <verb> --help` for a verb's flags and its "USE THIS WHEN" body. The list
above is curated; `dos --help` shows the full registered set too.)

Every subcommand resolves the active workspace via `dos.config` (the
`--workspace` flag / `DISPATCH_WORKSPACE` env / cwd). The CLI is a thin shell
over the package functions; it carries no policy of its own.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Windows defaults to cp1252, which crashes on the em-dash / middot the man-page
# renderer emits. Match the spine modules' force-UTF-8 discipline.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dos import attest  # the portable signed receipt (docs/246); pure stdlib, cheap to import
from dos import config as _config
from dos import id_alloc  # parser default (DEFAULT_START) is read at build time
from dos import interpret as _interpret  # the --explain next-action hint (shared with MCP)


def _resolve_driver_config(name: str, workspace=None):
    """Resolve a host policy driver BY CONVENTION — no hardcoded host name.

    Detail: docs/CLI.md § _resolve_driver_config.
    """
    import importlib

    if "." in name or "/" in name or "\\" in name:
        raise ValueError(
            f"unknown driver {name!r}: a driver name is a single module token "
            f"(no '.', '/' or '\\'); drivers live in src/dos/drivers/, see "
            f"dos.drivers.workshop for a template")
    try:
        mod = importlib.import_module(f"dos.drivers.{name}")
    except ModuleNotFoundError as e:
        if (e.name or "") in (f"dos.drivers.{name}", "dos.drivers"):
            raise ValueError(
                f"unknown driver {name!r} (no module dos.drivers.{name}); "
                f"drivers live in src/dos/drivers/, see dos.drivers.workshop "
                f"for a template") from None
        raise
    factory = getattr(mod, f"{name}_config", None)
    if factory is None:
        raise ValueError(
            f"driver {name!r} (dos.drivers.{name}) exposes no "
            f"{name}_config(workspace) factory")
    return factory(workspace)


def _apply_workspace(args: argparse.Namespace) -> None:
    """Install the workspace the user pointed at as the process-active config.

    Detail: docs/CLI.md § _apply_workspace.
    """
    # `--driver <name>` (and its back-compat alias `--job` == `--driver job`) is
    # resolved to a base config HERE, by convention, so neither this helper nor
    # `config` hardcodes a host name. An explicit `--driver` wins over `--job`.
    driver = getattr(args, "driver", None)
    if not driver and getattr(args, "job", False):
        driver = "job"
    base = None
    if driver:
        try:
            base = _resolve_driver_config(driver, getattr(args, "workspace", None))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            raise SystemExit(2)
    cfg = _config.load_workspace_config(getattr(args, "workspace", None), base=base)
    _config.set_active(cfg)


def _observe_enabled(args: argparse.Namespace) -> bool:
    """Is verdict-journal recording armed for this invocation? (docs/262 Phase 2)

    Detail: docs/CLI.md § _observe_enabled.
    """
    if getattr(args, "observe", False):
        return True
    raw = (os.environ.get("DISPATCH_OBSERVE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _maybe_observe(args: argparse.Namespace, syscall: str, token: str, verdict=None,
                   *, run_id: str = "", subject: str = "", lane: str = "",
                   detail: dict | None = None, source: str = "kernel") -> None:
    """Record one `VerdictEvent` to the verdict journal IFF observation is armed.

    Detail: docs/CLI.md § _maybe_observe.
    """
    if not _observe_enabled(args):
        return
    try:
        from dos import verdict_journal as _vj
        rid = run_id or str(getattr(args, "run_id", "") or "")
        ln = lane or str(getattr(args, "lane", "") or "")
        det = detail
        if det is None and verdict is not None and hasattr(verdict, "to_dict"):
            try:
                vd = verdict.to_dict()
                # Keep the evidence-ish scalar fields; drop the prose reason + the
                # redundant verdict token (already the event's `verdict`). Nested
                # evidence dicts are flattened with dotted keys (`evidence.work`,
                # `evidence.tokens`, `evidence.breakdown.input`, …) — docs/300: the
                # journal fossil must carry the counts a later fold (the efficiency
                # trend) and the exporter (#39: the OTel GenAI egress) read back, not
                # just the one-word verdict. TWO levels of nesting (#39): the second
                # hoists the spend split under `evidence.breakdown.*` so the
                # per-kind token counts reach the journal instead of being dropped
                # as a non-scalar value. The detail stays scalar-only (byte-clean,
                # docs/138) — a deeper structure would not.
                det = {}
                for k, v in vd.items():
                    if k in {"reason", "verdict"}:
                        continue
                    if isinstance(v, (int, float, str, bool)):
                        det[k] = v
                    elif isinstance(v, dict):
                        for kk, vv in v.items():
                            if isinstance(vv, (int, float, str, bool)):
                                det[f"{k}.{kk}"] = vv
                            elif isinstance(vv, dict):
                                for kkk, vvv in vv.items():
                                    if isinstance(vvv, (int, float, str, bool)):
                                        det[f"{k}.{kk}.{kkk}"] = vvv
                det = det or None
            except Exception:
                det = None
        _vj.record(_vj.VerdictEvent(
            syscall=syscall, verdict=token, run_id=rid, lane=ln,
            subject=subject, detail=det or {}, source=source))
    except Exception:
        # Belt-and-suspenders: observation must never break the observed verb.
        pass


def _ensure_home_if_persisting() -> None:
    """Lazily scaffold `.dos/` for the active workspace — the first-write hook.

    Detail: docs/CLI.md § _ensure_home_if_persisting.
    """
    from dos import home
    home.ensure_project_home(_config.active())


def _resolve_output_name(args: argparse.Namespace) -> str:
    """Which renderer a command should use, reconciling `--output` and `--json`.

    Detail: docs/CLI.md § _resolve_output_name.
    """
    output = getattr(args, "output", None)
    if output:
        return output
    if getattr(args, "json", False):
        return "json"
    return "text"


def _render_to_stdout(args: argparse.Namespace, method: str, obj, *, colorize=None) -> int:
    """Resolve the selected renderer, call `method(obj)`, print the string.

    Detail: docs/CLI.md § _render_to_stdout.
    """
    from dos import render
    name = _resolve_output_name(args)
    try:
        renderer = render.resolve_renderer(name)
    except render.UnknownRenderer as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = _render_one(renderer, method, obj)
    if colorize is not None and renderer is render.TEXT:
        out = colorize(out)
    print(out)
    return 0


def _emit_with_explanation(args, obj, method: str, interpretation: str, *,
                           colorize=None, exit_code: int) -> int:
    """Emit a verdict WITH the opt-in `--explain` next-action interpretation.

    Detail: docs/CLI.md § _emit_with_explanation.
    """
    from dos import render
    name = _resolve_output_name(args)
    try:
        renderer = render.resolve_renderer(name)
    except render.UnknownRenderer as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if renderer is render.JSON:
        payload = dict(obj.to_dict())
        payload["interpretation"] = interpretation
        if getattr(args, "pretty", False):
            print(json.dumps(payload, sort_keys=True, indent=2))
        else:
            print(json.dumps(payload, sort_keys=True))
        return exit_code
    # Text (or a plugin): print the rendered line, then the interpretation. The
    # interpretation is dimmed only when color is enabled for a human at a TTY.
    line = _render_one(renderer, method, obj)
    if colorize is not None and renderer is render.TEXT:
        line = colorize(line)
    print(line)
    hint = interpretation
    if colorize is not None and renderer is render.TEXT:
        hint = f"{_ANSI['dim']}{hint}{_ANSI['reset']}"
    print(hint)
    return exit_code


def _render_one(renderer, method: str, obj) -> str:
    """Call `renderer.method(obj)`, falling back to the built-in text form when
    the renderer doesn't implement the surface OR its method raises (RND).

    Detail: docs/CLI.md § _render_one.
    """
    from dos import render
    fallback = getattr(render.TEXT, method)
    fn = getattr(renderer, method, None)
    if fn is None:
        return fallback(obj)
    if renderer is render.TEXT or renderer is render.JSON:
        # The trusted built-ins are total and well-tested; call directly so a
        # genuine kernel bug surfaces rather than being silently masked.
        return fn(obj)
    try:
        return fn(obj)
    except Exception as e:  # a third-party plugin's bug — degrade, never crash
        rname = getattr(renderer, "name", type(renderer).__name__)
        print(f"warning: renderer {rname!r}.{method} raised ({e}); "
              f"falling back to text output", file=sys.stderr)
        return fallback(obj)


# ---------------------------------------------------------------------------
# The provenance-rung tell — color at the CLI boundary, NOT in the renderer.
#   (full prose: docs/CLI.md § "The provenance-rung tell — color at the CLI boundary, NOT in")
_ANSI = {
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "dim": "\033[2m", "bold": "\033[1m", "reset": "\033[0m",
}


def _color_enabled(args: argparse.Namespace) -> bool:
    """True iff we should emit ANSI for a human at an interactive terminal.

    Detail: docs/CLI.md § _color_enabled.
    """
    import os
    if _resolve_output_name(args) != "text":
        return False
    # NO_COLOR is the user's hard opt-out and wins over a force-on (the no-color.org
    # contract: presence of the variable, with any value, disables color).
    if os.environ.get("NO_COLOR") is not None:
        return False
    force = os.environ.get("DOS_COLOR", "").strip().lower()
    if force == "always":
        return True
    if force in ("never", "0", "false"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _color_verdict_line(line: str, verdict) -> str:
    """Wrap a rendered verdict line so the SHIPPED/NOT_SHIPPED mark and the
    `(via <rung>)` rung read as the tell — additive only, byte-identical content.

    Detail: docs/CLI.md § _color_verdict_line.
    """
    shipped = bool(getattr(verdict, "shipped", False))
    source = getattr(verdict, "source", None)
    mark_color = _ANSI["green"] if shipped else _ANSI["red"]
    mark = "SHIPPED" if shipped else "NOT_SHIPPED"
    # Color the leading mark token (it is always at line start).
    if line.startswith(mark):
        line = f"{_ANSI['bold']}{mark_color}{mark}{_ANSI['reset']}" + line[len(mark):]
    # Color the `(via <rung>)` suffix, graded by forgeability (docs/118): the
    # forgeable subject rung is yellow, no-evidence is bold-red, every artefact /
    # registry / bare-grep rung is green.
    if source:
        suffix = f"(via {source})"
        if line.endswith(suffix):
            if source == "none":
                rung_color = _ANSI["red"] + _ANSI["bold"]
            elif source == "grep-subject":
                rung_color = _ANSI["yellow"]
            else:
                rung_color = _ANSI["green"]
            colored = f"{rung_color}{suffix}{_ANSI['reset']}"
            line = line[: -len(suffix)] + colored
    return line


# ---------------------------------------------------------------------------
# init
#   (full prose: docs/CLI.md § "init")
_INIT_CONFIG_HEADER = """# DOS workspace config — the per-repo policy the installed `dos` package reads.
# The mechanism (oracle, arbiter, refusal enum) lives in the package; THIS file
# is the only policy that crosses into your tree (brownfield-port §5.5.3).
#
# Every table below is read by `dos` at workspace load (no dead scaffold remains
# after SCV+WCR) — see docs/HACKING.md for the full readback contract:
#   [lanes]  REPLACES the generic main/global taxonomy with yours.
#   [paths]  OVERRIDES individual layout fields (e.g. where your plans live).
#   [stamp]  OVERRIDES the ship-subject grammar `verify`'s grep rung recognises.
#   [reasons.*] (not scaffolded) ADD block reasons onto the built-in set.
workspace = "."
"""

_INIT_CONFIG_TAIL = """
# Where this repo keeps the state DOS reads. Only the fields you name are
# overridden; the rest inherit the default layout. `plans_glob` lives HERE (it is
# a path), distinct from `[stamp]` (which is the ship-subject *grammar*).
[paths]
plans_glob = "docs/**/*-plan.md"

# How this repo stamps a shipped phase in its commit subjects. The default is the
# strict reference grammar; declare `subject_dirs` (or `subject_dirs = []` for the
# generic, dir-free shape) to make `verify` recognise YOUR convention.
[stamp]
style = "grep"                 # ship oracle grep rung over `git log` subjects
# subject_dirs = ["src", "lib"]  # uncomment to declare your own ship-dir prefixes
# trailer_stamp = true           # uncomment if ships are stamped as an end-of-subject
#                                # trailer — `feat(x): … (<PLAN> <PHASE>)` (docs/289)
"""

# Cap on the number of auto-derived concurrent lanes. A repo with hundreds of
#   (full prose: docs/CLI.md § "Cap on the number of auto-derived concurrent lanes. A repo w")
_INIT_LANE_MAX = 12

# Top-level entries that are never a source lane: VCS, caches, build output,
# dependency trees, virtualenvs, and IDE/tooling dirs. (Dotfiles/dotdirs are
# skipped wholesale by the leading-`.` check in `_detect_source_dirs`.)
_INIT_NOISE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", "dist", "build", "target",
    "venv", ".venv", "env", ".env", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", "site-packages", ".dos",
    "htmlcov", ".eggs",
})


def _detect_source_dirs(target: Path) -> list[str]:
    """The repo's top-level source directories, sorted, noise-filtered, capped.

    Detail: docs/CLI.md § _detect_source_dirs.
    """
    try:
        entries = sorted(
            p.name for p in target.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and p.name not in _INIT_NOISE_DIRS
        )
    except OSError:
        return []
    return entries[:_INIT_LANE_MAX]


def _toml_str_list(items) -> str:
    """Render a Python list of strings as a TOML inline array (double-quoted)."""
    return "[" + ", ".join('"' + str(i) + '"' for i in items) + "]"


def _render_init_config(target: Path) -> tuple[str, str]:
    """Build the scaffolded `dos.toml` text + a one-line summary of what it did.

    Detail: docs/CLI.md § _render_init_config.
    """
    dirs = _detect_source_dirs(target)
    lines = [_INIT_CONFIG_HEADER]
    lines.append(
        "# Lane taxonomy — concurrent clusters run in parallel iff their file "
        "trees are\n"
        "# disjoint; exclusive lanes run alone. These were auto-derived from "
        "this repo's\n"
        "# top-level directories; curate them as your real concurrency topics "
        "emerge.\n"
        "# `dos arbitrate` reads this; `dos doctor --check` flags a lane with no "
        "tree.\n"
        "[lanes]"
    )
    if dirs:
        lines.append(f"concurrent = {_toml_str_list(dirs)}")
        lines.append('exclusive = ["global"]')
        lines.append(f"autopick = {_toml_str_list(dirs)}")
        lines.append("")
        lines.append("[lanes.trees]")
        for d in dirs:
            lines.append(f'{d} = ["{d}/**"]')
        lines.append('global = ["**/*"]')
        summary = (f"derived {len(dirs)} concurrent lane(s) "
                   f"({', '.join(dirs)}) + an exclusive 'global'")
    else:
        # Honest single-writer fallback: no source dirs to make disjoint, so one
        # exclusive whole-repo lane (the pre-auto-derive behavior).
        lines.append('concurrent = []')
        lines.append('exclusive = ["main"]')
        lines.append('autopick = []')
        lines.append("")
        lines.append("[lanes.trees]")
        lines.append('main = ["**/*"]')
        summary = "no source dirs detected — scaffolded a single-writer 'main' lane"
    lines.append(_INIT_CONFIG_TAIL)
    return "\n".join(lines), summary


def _resolve_driver_taxonomy(name: str):
    """The `LaneTaxonomy` of a named driver pack — the single source of truth.

    Detail: docs/CLI.md § _resolve_driver_taxonomy.
    """
    import importlib
    if not name.isidentifier():
        raise ValueError(f"driver name must be a bare identifier, got {name!r}")
    mod = importlib.import_module(f"dos.drivers.{name}")
    factory = getattr(mod, f"{name}_config")
    return factory(None).lanes


def _render_driver_config(name: str, taxonomy) -> str:
    """Serialize a driver's `LaneTaxonomy` into a `dos.toml` the workspace reads back.

    Detail: docs/CLI.md § _render_driver_config.
    """
    lines = [_INIT_CONFIG_HEADER]
    lines.append(
        f"# Lane taxonomy from the '{name}' reference driver (dos.drivers.{name}).\n"
        f"# Concurrent clusters run in parallel iff their trees are disjoint; "
        "exclusive\n# lanes run alone. This was scaffolded by "
        f"`dos quickstart --driver {name}` /\n# `dos init --example {name}` — curate "
        "it as your real concurrency topics emerge.\n"
        "[lanes]"
    )
    lines.append(f"concurrent = {_toml_str_list(taxonomy.concurrent)}")
    lines.append(f"exclusive = {_toml_str_list(taxonomy.exclusive)}")
    lines.append(f"autopick = {_toml_str_list(taxonomy.autopick)}")
    if getattr(taxonomy, "aliases", None):
        lines.append("")
        lines.append("[lanes.aliases]")
        for kw, lane in taxonomy.aliases.items():
            lines.append(f'{kw} = "{lane}"')
    lines.append("")
    lines.append("[lanes.trees]")
    for lane, trees in taxonomy.trees.items():
        lines.append(f"{lane} = {_toml_str_list(trees)}")
    lines.append(_INIT_CONFIG_TAIL)
    return "\n".join(lines)


def _quickstart_driver_arbitration_beat(work: Path, name: str, say) -> None:
    """Demo a driver's CONCURRENCY policy via the real `arbiter.arbitrate` kernel.

    Detail: docs/CLI.md § _quickstart_driver_arbitration_beat.
    """
    from dos import arbiter
    cfg = _config.load_workspace_config(workspace=work)
    lanes = cfg.lanes
    clusters = list(lanes.concurrent)
    if len(clusters) < 2:
        say("  (this driver has fewer than 2 concurrent lanes — nothing to overlap)")
        return
    a, b = clusters[0], clusters[1]
    tree_a = list(lanes.trees.get(a, []))
    tree_b = list(lanes.trees.get(b, []))
    # A live lease on lane A; ask whether lane B may start alongside it.
    live = [{"lane": a, "lane_kind": "cluster", "tree": tree_a}]
    d_b = arbiter.arbitrate(requested_lane=b, requested_kind="cluster",
                            requested_tree=tree_b, live_leases=live, config=cfg)
    say(f"  $ dos arbitrate --lane {a}   → acquire   (lease taken)")
    verb_b = "ACQUIRE" if d_b.outcome == "acquire" else f"REFUSE ({d_b.reason})"
    say(f"  $ dos arbitrate --lane {b}   → {verb_b.lower()}   "
        f"(disjoint from {a} → runs concurrently)")
    # An exclusive lane (if any) must refuse while A is live.
    excl = [e for e in lanes.exclusive if e in lanes.trees]
    if excl:
        x = excl[0]
        tree_x = list(lanes.trees.get(x, []))
        d_x = arbiter.arbitrate(requested_lane=x, requested_kind="global",
                                requested_tree=tree_x, live_leases=live, config=cfg)
        verb_x = "acquire" if d_x.outcome == "acquire" else "refuse"
        say(f"  $ dos arbitrate --lane {x}   → {verb_x}   "
            f"(exclusive — waits for the lane to clear)")


def _quickstart_fleet_act(cfg, say) -> None:
    """The fleet act of the DEFAULT quickstart — the admission half of the pitch.

    Detail: docs/CLI.md § _quickstart_fleet_act.
    """
    from dos import lane_lease
    lanes = cfg.lanes
    tree_src = list(lanes.trees.get("src", []))
    tree_docs = list(lanes.trees.get("docs", []))
    if not tree_src or not tree_docs:
        say("  (the scaffold derived no src/docs lanes — nothing to referee)")
        return

    # Each beat is the REAL durable verb against the demo repo's lease journal —
    # not three pure arbitrate() calls threading an in-memory list. That choice is
    # the lesson: B and C's verdicts differ from A's because A's grant was
    # JOURNALED where the next caller reads it (the WAL), so a reader who replays
    # these commands in a kept demo repo (--keep) sees the same escalation.
    def _take(owner: str):
        return lane_lease.acquire(cfg, lane="src", kind="cluster",
                                  tree=tree_src, owner=owner)

    d_a = _take("agent-A").decision
    say("$ dos lease-lane acquire --lane src --owner agent-A")
    say(f"  {d_a.outcome} {d_a.lane!r}   (lease on {', '.join(d_a.tree)} — "
        "journaled, so the NEXT caller sees it)")
    if d_a.outcome != "acquire":
        say(f"  ({d_a.reason})")
        return

    d_b = _take("agent-B").decision
    say()
    say("$ dos lease-lane acquire --lane src --owner agent-B   # B wants the "
        "SAME region")
    say(f"  {d_b.outcome} {d_b.lane!r}   ({d_b.reason.rstrip('.')})")
    if d_b.outcome == "acquire" and d_b.lane != "src":
        say("  ^ the collision never happened — B saw A's journaled lease and was "
            "handed free DISJOINT work; both run in parallel")

    d_c = _take("agent-C").decision
    say()
    say("$ dos lease-lane acquire --lane src --owner agent-C   # C — every lane "
        "is now held")
    say(f"  {d_c.outcome}   ({d_c.reason.split('.', 1)[0]}.)")
    if d_c.outcome == "refuse":
        say("  ^ exit=1: C waits. A silent overwrite became a typed, scriptable "
            "refusal.")

    # The state that made B and C answer differently from A, made visible: the
    # workspace's lease journal, not any process's memory or any agent's say-so.
    say()
    say("# Who holds what? The fleet's memory is the repo's lease journal "
        "(a WAL on disk):")
    say("$ dos lease-lane live")
    for lease in lane_lease.live_leases(cfg):
        say(f"  lane {str(lease.get('lane', '?')):<5}  held by "
            f"{lease.get('holder', '?')}")
    say()
    say("# (`dos arbitrate --lane src` answers the same may-I question as a pure")
    say("#  DECISION — it reads the journal but never writes it, so nothing stays")
    say("#  held. Ask with `arbitrate`; take the lane with `lease-lane acquire`.)")


def _quickstart_spinning_act(work: Path, run_git, say, keep_at) -> int:
    """The spinning-loop scene of `dos quickstart --spinning` (issue #59).

    The false-"still working" sibling of the default scene's false "done": a
    scripted agent narrates progress for four steps while landing zero commits,
    then `dos liveness` rules on the run from the git delta + heartbeat — never
    the prose — and calls it SPINNING; `dos efficiency` prices the waste; one
    honest commit (the only change) flips the verdict to ADVANCING. Every
    verdict goes through the real kernel classifiers against the throwaway
    repo's actual git history — nothing is canned. Returns the process exit
    code (0 = the contrasts came out; 2 = environment fault), the same contract
    as the caught-lie scene.
    """
    import subprocess

    from dos import efficiency, git_delta, liveness, run_id

    # 0. The story — the overnight burn: the most common first encounter with
    #    the narration-vs-truth gap is a loop that reports motion all night and
    #    is caught only by the morning bill.
    say("# The story: you left a coding agent running overnight. Every few minutes")
    say('# it reported: "Still on it — making progress, almost there." Next')
    say("# morning: a real token bill, zero commits. The narration said motion;")
    say("# the repo says otherwise. Catching that gap WHILE it runs — from")
    say("# evidence the loop did not author — is this demo.")
    say()

    # 1. A throwaway repo with one seed commit — the world before the run. The
    #    seed's SHA is the run's start SHA; commits after it are the forward
    #    delta the liveness verdict reads.
    run_git("init", "-q")
    run_git("config", "user.email", "quickstart@example.com")
    run_git("config", "user.name", "DOS Quickstart")
    run_git("config", "commit.gpgsign", "false")
    (work / "README.md").write_text(
        "# the repo the agent was asked to improve\n", encoding="utf-8")
    run_git("add", "-A")
    run_git("commit", "-q", "-m", "seed: the repo before the run")
    start_sha = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "--short", "HEAD"],
        check=True, capture_output=True, text=True,
        stdin=subprocess.DEVNULL).stdout.strip()  # docs/295
    rid = run_id.mint("quickstart-spin-demo")
    say("$ git rev-parse --short HEAD     # the run's start SHA — its liveness baseline")
    say(f"  {start_sha}")
    say("# The run gets a correlation id (its start time is encoded in the token):")
    say("$ dos run-id mint quickstart-spin-demo")
    say(f"  {rid.run_id}")
    say()

    # 2. The loop runs — all narration, no commits. The per-step token figure is
    #    the demo's stand-in for a real loop's spend; it feeds `efficiency` below.
    steps = (
        "Making progress — exploring the codebase.",
        "Almost there — refining the approach.",
        "Still on it — reworking the edge cases.",
        "Nearly done — polishing the solution.",
    )
    for i, line in enumerate(steps, start=1):
        say(f'  [step {i}]  agent: "{line}"   (~12,000 tokens)')
    say("# Four steps of confident narration. The repo over the same window:")
    say(f"$ git log --oneline {start_sha}..HEAD")
    say("  (no output — 0 commits)")
    say()

    # 3. Forty minutes in, ask the kernel — not the agent. The demo fast-forwards
    #    the clock by injecting --now-ms (the deterministic-script injection the
    #    real verb offers); in a live fleet you just run `dos liveness` while the
    #    loop narrates. The heartbeat is the loop wrapper's keepalive — in a
    #    fleet the lane journal supplies it; here the demo passes the age
    #    directly (the loop printed a step a minute ago, so it is ALIVE). The
    #    git delta says nothing MOVED. Alive + not moving + old enough to judge
    #    = SPINNING.
    now_ms = rid.ts_ms + 40 * 60 * 1000
    commits = git_delta.count_commits_since(start_sha, root=work)
    v1 = liveness.classify(liveness.ProgressEvidence(
        run_started_ms=rid.ts_ms, now_ms=now_ms,
        commits_since_start=commits, journal_events_since=0,
        last_heartbeat_age_ms=60_000,
    ))
    say("# 40 minutes in, ask the kernel — not the agent:")
    say(f"$ dos liveness --run-id {rid.run_id} --start-sha {start_sha} "
        f"--last-heartbeat-age-ms 60000 --now-ms {now_ms}")
    say(f"  {v1.verdict.value}  {v1.reason}")
    say(f"  exit={_LIVENESS_EXIT_CODES.get(v1.verdict.value, _LIVENESS_EXIT_UNKNOWN)}"
        "  (3 = SPINNING — alive and narrating, but ground truth is not moving)")
    say()

    # 4. Price the waste — the same zero, as spend.
    tokens = 12_000 * len(steps)
    e1 = efficiency.classify(
        efficiency.EfficiencyEvidence.of(work=commits, tokens=tokens))
    say("# Price what the narration cost:")
    say(f"$ dos efficiency --work {commits} --tokens {tokens}")
    say(f"  {e1.verdict.value}  {e1.reason}")
    say(f"  exit={_EFFICIENCY_EXIT_CODES.get(e1.verdict.value, _EFFICIENCY_EXIT_UNKNOWN)}"
        "  (4 = WASTEFUL — the tokens bought no work)")
    say()

    # 5. The honest step — the agent actually lands work. Same narration, same
    #    heartbeat, same clock; the COMMIT is the only change, and it alone
    #    flips the verdict.
    (work / "parser.py").write_text("def parse(): ...\n", encoding="utf-8")
    run_git("add", "-A")
    run_git("commit", "-q", "-m", "feat: extract the parser")
    commits2 = git_delta.count_commits_since(start_sha, root=work)
    v2 = liveness.classify(liveness.ProgressEvidence(
        run_started_ms=rid.ts_ms, now_ms=now_ms,
        commits_since_start=commits2, journal_events_since=0,
        last_heartbeat_age_ms=60_000,
    ))
    say("# Now one step that actually lands:")
    say("$ git commit -m 'feat: extract the parser'")
    say(f"$ dos liveness --run-id {rid.run_id} --start-sha {start_sha} "
        f"--last-heartbeat-age-ms 60000 --now-ms {now_ms}")
    say(f"  {v2.verdict.value}  {v2.reason}")
    say(f"  exit={_LIVENESS_EXIT_CODES.get(v2.verdict.value, _LIVENESS_EXIT_UNKNOWN)}"
        "  (0 = ADVANCING — state moved)")
    say()

    # The demo only "worked" if the contrasts came out: SPINNING + WASTEFUL off
    # the zero delta, ADVANCING off the one real commit. Anything else is an
    # environment fault we should fail on, not paper over.
    if not (v1.verdict is liveness.Liveness.SPINNING
            and e1.verdict is efficiency.Efficiency.WASTEFUL
            and v2.verdict is liveness.Liveness.ADVANCING):
        print("error: quickstart --spinning did not produce the expected "
              "SPINNING/WASTEFUL/ADVANCING contrast — your git or environment "
              "may be unusual.", file=sys.stderr)
        return 2

    say("Same narration before and after — only the COMMIT changed the verdict.")
    say("The evidence is the git delta and the heartbeat, never the prose.")
    say("And the verdict is ADVISORY: DOS reports, it never kills the loop — a")
    say("supervisor, the loop itself, or a CI step branches on the exit code")
    say("(0 advancing / 3 spinning / 4 stalled) and decides what to do.")

    if keep_at is not None:
        say(f"\nThe demo repo is at {keep_at} — replay the verdict against it:")
        say(f"  dos liveness --workspace {keep_at} --run-id {rid.run_id} "
            f"--start-sha {start_sha} --last-heartbeat-age-ms 60000 "
            f"--now-ms {now_ms}")
        say("  (it answers ADVANCING now — the honest commit is in the repo's git)")
    else:
        say("\nReplay it hands-on:        dos quickstart --spinning --keep dos-spin-demo")
        say("Run it on a real run:      dos liveness --run-id RID --start-sha SHA "
            "--workspace .")
        say('And the false-"done" half: dos quickstart   (the caught-lie sibling)')
    return 0


# docs/207 Phase 7 — the skill on-ramp. `dos init --skills` copies the generic
#   (full prose: docs/CLI.md § "docs/207 Phase 7 — the skill on-ramp. `dos init --skills` co")

# The core set `--skills` copies by default (the plan-and-ship loop a new host
# needs first). `--all` copies the full pack. The names are the skill DIRECTORY
# names under `src/dos/skills/`.
_CORE_SKILLS = ("dos-next-up", "dos-dispatch", "dos-dispatch-loop", "dos-replan")


def _skills_root() -> Path:
    """The shipped skill-pack root (package-data under the installed `dos`)."""
    import dos as _dos
    return Path(_dos.__file__).parent / "skills"


def _available_skills() -> list[str]:
    """Every skill the wheel ships (a dir under skills/ holding a SKILL.md)."""
    root = _skills_root()
    if not root.is_dir():
        return []
    return sorted(p.parent.name for p in root.rglob("SKILL.md"))


def _install_skills(
    dest_root: Path, names: list[str], *, force: bool
) -> tuple[list[str], list[str], list[str]]:
    """Copy the named generic skills into `dest_root/.claude/skills/<name>/SKILL.md`.

    Detail: docs/CLI.md § _install_skills.
    """
    import shutil
    src_root = _skills_root()
    available = set(_available_skills())
    dest_skills = dest_root / ".claude" / "skills"
    written: list[str] = []
    skipped: list[str] = []
    unknown: list[str] = []
    for name in names:
        if name not in available:
            unknown.append(name)
            continue
        src = src_root / name / "SKILL.md"
        dst_dir = dest_skills / name
        dst = dst_dir / "SKILL.md"
        if dst.exists() and not force:
            skipped.append(name)
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        written.append(name)
    return written, skipped, unknown


# docs/134 §6 / docs/165 — the runtime-binding on-ramp. `dos init --with-hooks`
#   (full prose: docs/CLI.md § "docs/134 §6 / docs/165 — the runtime-binding on-ramp. `dos i")
_DOS_HOOK_COMMANDS = {
    "Stop": "dos hook stop --workspace .",
    "PostToolUse": "dos hook posttool --workspace .",
    "PreToolUse": "dos hook pretool --workspace .",
}


def _dos_hook_entry(command: str) -> dict:
    """One CC hook matcher-group running `command` (the verified settings shape)."""
    return {"hooks": [{"type": "command", "command": command}]}


def _is_dos_hook_group(group: object) -> bool:
    """True if a matcher-group already runs a `dos hook …` command (idempotency)."""
    if not isinstance(group, dict):
        return False
    for h in group.get("hooks", []):
        cmd = h.get("command", "") if isinstance(h, dict) else ""
        if isinstance(cmd, str) and cmd.strip().startswith("dos hook "):
            return True
    return False


def _install_hooks(dest_root: Path, *, force: bool) -> tuple[list[str], list[str]]:
    """Merge the DOS hooks into `dest_root/.claude/settings.json`.

    Detail: docs/CLI.md § _install_hooks.
    """
    settings_path = dest_root / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            if not force:
                raise ValueError(
                    f"{settings_path} is not valid JSON ({e}); fix it or pass "
                    f"--force to overwrite the hooks block") from e
            loaded = {}
        settings = loaded if isinstance(loaded, dict) else {}

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):  # a malformed `hooks` value — replace it
        hooks = {}
        settings["hooks"] = hooks

    wired: list[str] = []
    already: list[str] = []
    for event, command in _DOS_HOOK_COMMANDS.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            groups = []
            hooks[event] = groups
        has_dos = any(_is_dos_hook_group(g) for g in groups)
        if has_dos and not force:
            already.append(event)
            continue
        if has_dos and force:
            # Drop existing DOS groups, then re-add the canonical one (repair path).
            groups[:] = [g for g in groups if not _is_dos_hook_group(g)]
        groups.append(_dos_hook_entry(command))
        wired.append(event)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return wired, already


# docs/221 — `dos init --hooks <host>` is the CROSS-VENDOR generalization of
#   (full prose: docs/CLI.md § "docs/221 — `dos init --hooks <host>` is the CROSS-VENDOR gen")
def _install_host_hooks(
    dest_root: Path, host: str, *, force: bool, dry_run: bool = False
) -> tuple["object", Path, list[str], list[str], str]:
    """Wire the DOS hooks into `host`'s config file under `dest_root`.

    Detail: docs/CLI.md § _install_host_hooks.
    """
    from dos import hook_install as _hi

    spec = _hi.host_spec(host)  # fail-LOUD on an unknown host name.
    config_path = dest_root.joinpath(*spec.config_path)

    if spec.fmt is _hi.ConfigFormat.TOML:
        existing_text = ""
        if config_path.exists():
            existing_text = config_path.read_text(encoding="utf-8")
        new_text, wired, already = _hi.merge_toml(existing_text, spec, force=force)
        if not dry_run:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(new_text, encoding="utf-8")
        return spec, config_path, wired, already, new_text

    # JSON hosts (Claude Code / Cursor / Gemini).
    existing: dict = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            if not force:
                raise ValueError(
                    f"{config_path} is not valid JSON ({e}); fix it or pass "
                    f"--force to overwrite the hooks block") from e
            loaded = {}
        existing = loaded if isinstance(loaded, dict) else {}

    merged, wired, already = _hi.merge_json(existing, spec, force=force)
    new_text = json.dumps(merged, indent=2, sort_keys=True) + "\n"
    if not dry_run:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(new_text, encoding="utf-8")
    return spec, config_path, wired, already, new_text


def _detect_auto_hosts(
    target: Path,
) -> tuple[list[tuple[str, tuple[str, ...]]], list[str]]:
    """Resolve `--hooks auto` at the I/O boundary (docs/303).

    Probe each known host's config dir under `target` (the spec's own
    `detection_probe` parts) and its `env_markers` (is the installer running
    *inside* that host right now?), then dedupe hosts sharing one config file
    via the pure `choose_auto_hosts`. Returns `(chosen, probed)` — `chosen` as
    `(host, also_covered_names)` pairs, `probed` as the display list of dirs
    looked for (so a nothing-detected refusal can name them).
    """
    from dos import hook_install as _hi

    detected: list[tuple[str, tuple[str, ...]]] = []
    probed: list[str] = []
    for name in _hi.host_names():
        try:
            spec = _hi.host_spec(name)
        except ValueError:
            continue  # a broken plugin spec never breaks detection
        probe = _hi.detection_probe(spec)
        probed.append("/".join(probe) + ("/" if len(spec.config_path) > 1 else ""))
        present = target.joinpath(*probe).exists() or any(
            marker in os.environ for marker in spec.env_markers)
        if present:
            detected.append((name, spec.config_path))
    return _hi.choose_auto_hosts(detected), sorted(set(probed))


def cmd_init(args: argparse.Namespace) -> int:
    # `init` is workspace-INSENSITIVE for config READBACK (it scaffolds the very
    #   (full prose: docs/CLI.md § "`init` is workspace-INSENSITIVE for config READBACK (it scaf")
    dir_arg = Path(args.dir)
    ws = getattr(args, "workspace", None)
    if ws is not None and not dir_arg.is_absolute():
        target = (Path(ws) / dir_arg).resolve()
    else:
        target = dir_arg.resolve()
    target.mkdir(parents=True, exist_ok=True)

    # docs/207 Phase 7 — `dos init --skills [names…]` / `--all`: copy the generic
    #   (full prose: docs/CLI.md § "docs/207 Phase 7 — `dos init --skills [names…]` / `--all`: c")
    want_skills = (
        getattr(args, "skills", False)
        or getattr(args, "skill", None)
        or getattr(args, "all", False)
    )
    # docs/221 — the runtime to wire hooks into. `--hooks <host>` selects it
    # explicitly (claude-code/cursor/codex/gemini/antigravity/claude-cowork);
    # `--with-hooks` is the backward-compatible alias for `--hooks claude-code`.
    # None of either → no hooks wired.
    hook_host = getattr(args, "hooks", None)
    if hook_host is None and getattr(args, "with_hooks", False):
        hook_host = "claude-code"
    want_hooks = hook_host is not None

    # `--dry-run` is a PREVIEW of the hook merge — it touches nothing. It is only
    # meaningful with --hooks (there is nothing else to preview), and it skips the
    # dos.toml scaffold + skills copy so "dry-run wrote nothing" is literally true.
    dry_run = getattr(args, "dry_run", False)
    if dry_run and not want_hooks:
        print("dos init --dry-run previews the --hooks merge; pass --hooks <host> "
              "(nothing to preview otherwise).", file=sys.stderr)
        return 1

    cfg_path = target / "dos.toml"
    config_existed = cfg_path.exists()
    if dry_run:
        pass  # preview-only: do not scaffold dos.toml or copy skills
    elif config_existed and not args.force:
        # When ONLY copying skills / wiring hooks into an existing workspace, the
        # pre-existing dos.toml is not an error — skip the scaffold and proceed.
        if not (want_skills or want_hooks):
            print(f"{cfg_path} already exists (use --force to overwrite)", file=sys.stderr)
            return 1
    else:
        example = getattr(args, "example", None)
        if example:
            # `--example NAME`: scaffold from a named reference driver pack instead of
            # auto-deriving lanes. Uses the driver's own taxonomy as the source of
            # truth (no duplicated lane data); same by-convention contract as
            # `dos --driver NAME` and `dos quickstart --driver NAME`.
            try:
                taxonomy = _resolve_driver_taxonomy(example)
            except (ImportError, AttributeError, ValueError) as e:
                print(f"error: --example {example!r} could not be resolved: {e}\n"
                      f"  reference drivers live in dos.drivers.<name>; try "
                      f"--example workshop.", file=sys.stderr)
                return 1
            config_text = _render_driver_config(example, taxonomy)
            cfg_path.write_text(config_text, encoding="utf-8")
            print(f"wrote {cfg_path}")
            print(f"scaffolded from the '{example}' reference driver — lanes: "
                  f"{', '.join(taxonomy.trees)}")
        else:
            config_text, summary = _render_init_config(target)
            cfg_path.write_text(config_text, encoding="utf-8")
            print(f"wrote {cfg_path}")
            print(summary)

    if want_skills and not dry_run:
        if getattr(args, "all", False):
            names = _available_skills()
        elif getattr(args, "skill", None):
            names = list(args.skill)        # explicit --skill NAME (repeatable)
        else:
            names = list(_CORE_SKILLS)       # bare --skills → the core set
        written, skipped, unknown = _install_skills(target, names, force=args.force)
        if written:
            print(f"installed {len(written)} skill(s) into "
                  f"{target / '.claude' / 'skills'}: {', '.join(written)}")
        if skipped:
            print(f"skipped {len(skipped)} existing skill(s) (use --force to overwrite): "
                  f"{', '.join(skipped)}")
        if unknown:
            print(f"warning: unknown skill(s) ignored: {', '.join(unknown)} "
                  f"(available: {', '.join(_available_skills())})", file=sys.stderr)
            return 1

    if want_hooks:
        # docs/134 §6 / docs/221 — bind the verdict to the chosen runtime by wiring
        # the three DOS hooks into that host's own config file (merged, never
        # clobbering the user's). claude-code → .claude/settings.json (today's path);
        # cursor/codex/gemini/antigravity → their config files with the right --dialect;
        # claude-cowork → the SAME .claude/settings.json (shared harness, docs/298).
        # `--hooks auto` (docs/303) resolves HERE, at the I/O boundary: probe which
        # runtimes this workspace already uses, then wire each one through the same
        # per-host path an explicit name takes. Nothing detected fails LOUD with the
        # probe list — a guessed host would wire a no-op deny against the real one.
        dry_run = getattr(args, "dry_run", False)
        from dos import hook_install as _hi
        if hook_host == _hi.AUTO_HOST:
            chosen, probed = _detect_auto_hosts(target)
            if not chosen:
                print(f"dos init --hooks auto: no agent runtime detected under "
                      f"{target} — none of {', '.join(probed)} exists here and no "
                      f"runtime marker is in the environment. Run it from the repo "
                      f"your agent works in, or name the host: dos init --hooks "
                      f"<host> — one of: {', '.join(_hi.host_names())}.",
                      file=sys.stderr)
                return 1
            print("--hooks auto: detected " + ", ".join(
                name for name, _ in chosen))
        else:
            chosen = [(hook_host, ())]
        for one_host, also_covers in chosen:
            try:
                spec, config_path, wired, already, proposed = _install_host_hooks(
                    target, one_host, force=args.force, dry_run=dry_run)
            except ValueError as e:
                print(f"dos init --hooks {one_host}: {e}", file=sys.stderr)
                return 1
            if dry_run:
                # Preview the merge before committing it — print what WOULD be
                # written, write nothing. The "dress rehearsal" for hook wiring: an
                # operator with a pre-existing config sees the exact result first.
                verb = "would wire" if wired else "no new"
                print(f"--dry-run: {verb} {len(wired)} DOS hook(s) "
                      f"{'into' if wired else 'for'} {config_path}"
                      + (f": {', '.join(wired)}" if wired else "")
                      + " (nothing written)")
                if already:
                    print(f"  {len(already)} existing DOS hook(s) would be left "
                          f"untouched (use --force to repair): {', '.join(already)}")
                print(f"\n----- proposed {config_path.name} -----")
                print(proposed.rstrip("\n"))
                print("----- end preview (re-run without --dry-run to apply) -----")
                continue
            if wired:
                print(f"wired {len(wired)} DOS hook(s) into {config_path}: "
                      f"{', '.join(wired)}")
            if already:
                print(f"left {len(already)} existing DOS hook(s) untouched "
                      f"(use --force to repair): {', '.join(already)}")
            print(f"  bound to {spec.host}: a refused call is DENIED (pretool), "
                  "a stalled stream is re-surfaced (posttool), a stop on an "
                  "unverified claim is refused (stop).")
            if also_covers:
                print(f"  also covers {', '.join(also_covers)} — the same config "
                      "file, one set of hooks.")
            if spec.note:
                print(f"  note: {spec.note}")
        if dry_run:
            return 0

    print("DOS workspace initialised. Try:  dos doctor --workspace .")
    return 0


# ---------------------------------------------------------------------------
# The verdict-IS-the-exit-code contract — one source per verb.
#   (full prose: docs/CLI.md § "The verdict-IS-the-exit-code contract — one source per verb.")
@dataclasses.dataclass(frozen=True)
class ExitMap:
    """A verb's verdict-token → exit-code map (the verdict IS the exit code).

    Detail: docs/CLI.md § ExitMap.
    """

    codes: dict[str, int]
    unknown: int = 2
    contract_error: int = 2
    # The verdict-journal dimension this verb emits under (docs/262 P2). Optional —
    #   (full prose: docs/CLI.md § "The verdict-journal dimension this verb emits under (docs/26")
    syscall: str = ""

    def __getitem__(self, token: str):
        """Index a token. `contract_error` always resolves (every handler can hit
        a usage fault); any other unknown token raises, as the old dict did."""
        if token == "contract_error":
            return self.codes.get(token, self.contract_error)
        return self.codes[token]

    def code_for(self, token) -> int:
        """Map a verdict token → its code, falling to `unknown` if unrecognized."""
        return self.codes.get(token, self.unknown)

    def emit(self, args: argparse.Namespace, verdict, token: str) -> int:
        """Render a single-line verdict verb's tail and return its exit code.

        Detail: docs/CLI.md § emit.
        """
        if args.json or getattr(args, "output", None) == "json":
            print(json.dumps(verdict.to_dict(), sort_keys=True))
        else:
            print(f"{token}  {verdict.reason}")
        # docs/262 P2 — record the verdict to the observability journal when armed.
        # No-op unless `--observe`/`DISPATCH_OBSERVE=1`; fail-soft (never changes the
        # exit code). Only verbs that set `self.syscall` auto-record here.
        if self.syscall:
            _maybe_observe(args, self.syscall, token, verdict)
        return self.code_for(token)

    def contract(self) -> dict[str, int]:
        """The published `dos doctor --json` row: the verb's verdicts, plus
        `contract_error` and the `unknown` floor where each is a distinct code.

        Detail: docs/CLI.md § contract.
        """
        row = dict(self.codes)
        row.setdefault("contract_error", self.contract_error)
        if self.unknown != self.contract_error:
            row["unknown"] = self.unknown
        return row


# verify / arbitrate are two-valued (success / refusal); they list `contract_error`
# in the map itself (handlers index `["contract_error"]`) and have no separate
# `unknown` floor (every token is known), so `unknown` defaults to the contract code.
_VERIFY_EXITS = ExitMap({"shipped": 0, "not_shipped": 1, "contract_error": 2})
_ARBITRATE_EXITS = ExitMap({"acquire": 0, "refuse": 1, "contract_error": 2})
_VERIFY_EXIT_CODES = _VERIFY_EXITS.codes
_ARBITRATE_EXIT_CODES = _ARBITRATE_EXITS.codes


# commit-audit  (the author-NEUTRAL claim-vs-diff verdict for ANY git project)
#   (full prose: docs/CLI.md § "commit-audit  (the author-NEUTRAL claim-vs-diff verdict for")
_COMMIT_AUDIT_EXITS = ExitMap({"clean": 0, "unwitnessed": 1, "contract_error": 2})
_COMMIT_AUDIT_EXIT_CODES = _COMMIT_AUDIT_EXITS.codes


def cmd_commit_audit(args: argparse.Namespace) -> int:
    """Does a commit's CLAIM match what its DIFF actually did? Author-neutral.

    Detail: docs/CLI.md § cmd_commit_audit.
    """
    _apply_workspace(args)
    from dos import commit_audit as _ca

    root = _config.active().paths.root
    policy = _ca.ClaimPolicy(
        docs_satisfy_code_claim=getattr(args, "docs_ok", False))

    target = args.ref
    if ".." in target:
        verdicts = _ca.audit_range(target, root=root, policy=policy)
        if not verdicts:
            # empty range is legitimately clean; an unreadable range is an error.
            # Distinguish by a cheap rev-parse of the range's right side.
            verdicts = []
    else:
        v = _ca.audit_commit(target, root=root, policy=policy)
        if v is None:
            print(f"commit-audit: cannot read '{target}' in {root} "
                  f"(not a git repo, or bad ref)", file=sys.stderr)
            return _COMMIT_AUDIT_EXIT_CODES["contract_error"]
        verdicts = [v]

    unwitnessed = [v for v in verdicts if v.verdict is _ca.Verdict.CLAIM_UNWITNESSED]
    sweep = getattr(args, "sweep", False)

    if getattr(args, "json", False):
        import json as _json
        if sweep:
            print(_json.dumps(_ca.sweep_summary(verdicts), indent=2))
        else:
            print(_json.dumps([v.to_dict() for v in verdicts], indent=2))
    elif sweep:
        # The "how honest are this repo's commit messages?" RATE — the aggregate,
        # not the per-commit firehose. drift = unwitnessed / checkable (abstains
        # excluded: a wip/merge makes no claim to be honest about).
        s = _ca.sweep_summary(verdicts)
        print(f"commit-audit sweep over {s['commits']} commit(s):")
        print(f"  checkable (made a concrete claim) : {s['checkable']}")
        print(f"  witnessed by their diff           : {s['witnessed']}")
        print(f"  UNWITNESSED (claim vs diff)       : {s['unwitnessed']}")
        print(f"  no checkable claim (abstained)    : {s['abstained']}")
        print(f"  DRIFT RATE (unwitnessed/checkable): {s['drift_rate']:.1%}")
        if s["by_kind"]:
            print("  by claim kind (unwitnessed/witnessed/abstain):")
            for k, row in sorted(s["by_kind"].items()):
                print(f"    {k:12} {row['unwitnessed']:>4} / {row['witnessed']:>4} "
                      f"/ {row['abstain']:>4}")
        if s["unwitnessed_shas"]:
            head = ", ".join(s["unwitnessed_shas"][:12])
            more = "" if len(s["unwitnessed_shas"]) <= 12 else " …"
            print(f"  unwitnessed: {head}{more}")
    else:
        if not verdicts:
            print("commit-audit: no commits in range (nothing to check)")
        for v in verdicts:
            if v.verdict is _ca.Verdict.CLAIM_UNWITNESSED:
                mark = "⚑ UNWITNESSED"
            elif v.verdict is _ca.Verdict.OK:
                mark = "✓ witnessed  "
            else:
                mark = "· abstain    "
            line = f"{mark} {v.sha}  [{v.witness.value}]  {v.reason}"
            print(_color_audit_line(line, v) if _color_enabled(args) else line)
        if unwitnessed and not getattr(args, "warn_only", False):
            print(f"\ncommit-audit: {len(unwitnessed)}/{len(verdicts)} commit(s) "
                  f"make a claim their diff does not witness.", file=sys.stderr)

    if getattr(args, "warn_only", False):
        return _COMMIT_AUDIT_EXIT_CODES["clean"]
    return (_COMMIT_AUDIT_EXIT_CODES["unwitnessed"] if unwitnessed
            else _COMMIT_AUDIT_EXIT_CODES["clean"])


def _color_audit_line(line: str, verdict) -> str:
    """Color the audit line at the TTY boundary — red for unwitnessed, green for
    witnessed, dim for abstain. Bytes unchanged when color is disabled."""
    from dos import commit_audit as _ca
    if verdict.verdict is _ca.Verdict.CLAIM_UNWITNESSED:
        return f"\033[31m{line}\033[0m"
    if verdict.verdict is _ca.Verdict.OK:
        return f"\033[32m{line}\033[0m"
    return f"\033[2m{line}\033[0m"


def _gather_non_git_rung(cfg, sha: str):
    """Gather the workspace's non-git oracle verdict for `sha` → a `NonGitRung`.

    Detail: docs/CLI.md § _gather_non_git_rung.
    """
    from dos.oracle import NonGitRung

    name = (getattr(cfg, "non_git_oracle", "") or "").strip()
    if not name:
        return None
    try:
        driver = _load_witness_driver(name)
    except ValueError:
        # Unknown/uninstalled oracle name — fail-safe: leave the git verdict as-is
        # rather than crash `verify` on a mis-declared `[verify] non_git_oracle`.
        return None
    status_of = getattr(driver, "status_of", None)
    if not callable(status_of):
        # The named source has no CI-shaped `status_of` (e.g. a paste/log source).
        # `verify`'s conjunctive rung consumes a GREEN/RED/PENDING verdict; a source
        # that can't produce one is simply not consulted here (it stays a judge hint).
        return None
    ci_table = getattr(cfg, "ci", None) or {}
    repo = ci_table.get("repo") if isinstance(ci_table, dict) else None
    try:
        verdict = status_of(sha, repo=repo) if repo else status_of(sha)
    except Exception:
        return None  # fail-safe: a raising oracle never reddens/fabricates the ship
    state = str(getattr(getattr(verdict, "verdict", None), "value", "") or "").strip().upper()
    reason = str(getattr(verdict, "reason", "") or "")
    if state == "GREEN":
        return NonGitRung(source="ci-green", reason=reason, state="GREEN")
    # RED / PENDING / NO_SIGNAL (and anything unexpected) carry their state word; the
    # kernel fold upgrades only on GREEN, withholds on RED, passes the rest through.
    return NonGitRung(source="ci-green", reason=reason, state=state or "NO_SIGNAL")


def cmd_verify(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    from dos import oracle
    cfg = _config.active()
    # Pass the active workspace config so the registry miss falls through to the
    # git-log grep rung — `dos verify` then finds a phase recorded only in git
    # history (no plan doc, no registry), per the no-plan contract. An unshipped
    # claim still resolves to `shipped=False, source='none'`.
    verdict = oracle.is_shipped(args.plan, args.phase, cfg=cfg)
    # docs/265 — the non-git evidence rung. When THIS workspace wired a non-git
    #   (full prose: docs/CLI.md § "docs/265 — the non-git evidence rung. When THIS workspace wi")
    if (
        getattr(cfg, "non_git_oracle", "")
        and not getattr(args, "no_ci", False)
        and verdict.shipped
        and (verdict.sha or "").strip()
    ):
        rung = _gather_non_git_rung(cfg, verdict.sha)
        if rung is not None:
            verdict = oracle._apply_non_git_rung(verdict, rung)
    # Output goes through the renderer seam (RND). `--output` selects the named
    #   (full prose: docs/CLI.md § "Output goes through the renderer seam (RND). `--output` sele")
    colorize = (
        (lambda line: _color_verdict_line(line, verdict))
        if _color_enabled(args) else None
    )
    # `--explain` is the opt-in agent affordance: append the same one-line
    #   (full prose: docs/CLI.md § "`--explain` is the opt-in agent affordance: append the same")
    shipped_code = _VERIFY_EXIT_CODES["shipped" if verdict.shipped else "not_shipped"]
    # docs/262 P2 — record the truth-syscall verdict when observation is armed.
    # SHIPPED/NOT_SHIPPED keyed to (plan,phase); the rung that answered (`source`:
    # registry/grep/none) is byte-clean evidence (git ancestry, never narration).
    _maybe_observe(
        args, "verify", "SHIPPED" if verdict.shipped else "NOT_SHIPPED", None,
        subject=f"{args.plan}::{args.phase}",
        detail={"source": getattr(verdict, "source", "") or ""})
    if getattr(args, "explain", False):
        return _emit_with_explanation(
            args, verdict, "render_verdict", _interpret.verify(verdict.to_dict()),
            colorize=colorize, exit_code=shipped_code)
    if _render_to_stdout(args, "render_verdict", verdict, colorize=colorize) != 0:
        return _VERIFY_EXIT_CODES["contract_error"]
    return shipped_code


# ---------------------------------------------------------------------------
# attest  (the portable, signed receipt over an effect-witness verdict, docs/246)
#   (full prose: docs/CLI.md § "attest  (the portable, signed receipt over an effect-witness")
_ATTEST_EXITS = ExitMap({
    "confirmed": 0, "refuted": 1, "unwitnessed": 3, "no_claim": 3, "contract_error": 2,
})
_ATTEST_EXIT_CODES = _ATTEST_EXITS.codes


def _load_attest_key(args: argparse.Namespace) -> "bytes | None":
    """Read the HMAC signing key at the boundary: --key-file › $DOS_ATTEST_KEY.

    Detail: docs/CLI.md § _load_attest_key.
    """
    key_file = getattr(args, "key_file", None)
    if key_file:
        with open(key_file, "rb") as f:
            return f.read()
    env_key = os.environ.get(attest.ATTEST_KEY_ENV)
    if env_key:
        return env_key.encode("utf-8")
    return None


def _load_witness_driver(name: str):
    """Resolve a witness DRIVER (`os_acceptance` / `state_diff`) by name — never a
    static import.

    Detail: docs/CLI.md § _load_witness_driver.
    """
    import importlib

    mod_name = f"dos.drivers.{name}"
    try:
        return importlib.import_module(mod_name)
    except ModuleNotFoundError as e:  # pragma: no cover - the drivers ship in-tree
        if (e.name or "") in (mod_name, "dos.drivers"):
            raise ValueError(
                f"the {name} witness driver ({mod_name}) is not installed; "
                f"`dos attest` needs it — reinstall the package") from None
        raise  # a broken INTERNAL import of the driver is a real bug, not "absent"


def _gather_attest_readback(args: argparse.Namespace, cfg) -> "tuple[object, str, str] | None":
    """Gather the independent read-back at the boundary, returning (verdict, surface, err).

    Detail: docs/CLI.md § _gather_attest_readback.
    """
    from dos.effect_witness import EffectClaim, witness_effect
    from dos.evidence import Accountability as _Acct
    from dos.evidence import gather_evidence

    claim = EffectClaim(key=args.claim, narrated=getattr(args, "narrated", "") or "")
    accept_cmd = getattr(args, "accept_cmd", None)
    before = getattr(args, "before", None)
    after = getattr(args, "after", None)

    if accept_cmd and (before or after):
        return None, "", "give EITHER --accept-cmd OR --before/--after, not both"
    if accept_cmd:
        os_acceptance = _load_witness_driver("os_acceptance")
        source = os_acceptance.OsAcceptanceEvidenceSource(cwd=str(cfg.paths.root))
        facts = gather_evidence(source, accept_cmd, cfg)
        return witness_effect(claim, [facts]), accept_cmd, ""
    if before or after:
        if not (before and after):
            return None, "", "state-diff witness needs BOTH --before and --after"
        state_diff = _load_witness_driver("state_diff")
        rung = _Acct.THIRD_PARTY if getattr(args, "third_party", False) else _Acct.OS_RECORDED
        try:
            b = state_diff.read_state_json(before)
            a = state_diff.read_state_json(after)
        except (OSError, ValueError, json.JSONDecodeError):
            # A read fault is NOT a refutation: degrade to an UNWITNESSED verdict (no
            # read-back), never a fabricated empty delta that would falsely REFUTE.
            return witness_effect(claim, []), f"{before}→{after}", ""
        v = state_diff.witness_effect_via_state_diff(claim, b, a, accountability=rung)
        return v, f"{before}→{after}", ""
    return None, "", "no witness surface — give --accept-cmd or --before/--after"


def cmd_attest(args: argparse.Namespace) -> int:
    """Mint a portable, signed receipt over an effect-witness verdict (docs/246 Phase 1).

    Detail: docs/CLI.md § cmd_attest.
    """
    _apply_workspace(args)
    cfg = _config.active()

    # 1. The signing key — boundary I/O. No key → contract error (signing was asked for;
    #    we never silently emit an unsigned receipt).
    try:
        key = _load_attest_key(args)
    except OSError as e:
        print(f"attest: could not read --key-file ({e})", file=sys.stderr)
        return _ATTEST_EXIT_CODES["contract_error"]
    if key is None:
        print(
            f"attest: no signing key — pass --key-file PATH or set ${attest.ATTEST_KEY_ENV} "
            f"(a signed receipt needs a key; refusing to emit an unsigned one)",
            file=sys.stderr,
        )
        return _ATTEST_EXIT_CODES["contract_error"]

    # 2. Gather the independent read-back + join the claim — boundary I/O, then a pure
    #    verdict. A usage/read fault is a contract error.
    verdict, surface, err = _gather_attest_readback(args, cfg)
    if verdict is None:
        print(f"attest: {err}", file=sys.stderr)
        return _ATTEST_EXIT_CODES["contract_error"]

    # 3. Package + sign — the receipt-from-verdict is pure; the timestamp is the one
    #    clock read (boundary), the signature the one signing step (boundary).
    import datetime as _dt
    ts = (args.timestamp or "").strip() or (
        _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    unsigned = attest.receipt_from_verdict(verdict, timestamp=ts, witness_surface=surface)
    signed = unsigned.with_signature(attest.sign_hmac(unsigned, key))

    # 4. Emit. --json is the full signed receipt (what a third party receives); text is
    #    the headline + fields. The verdict token (lowercased) keys the exit code.
    if getattr(args, "json", False):
        print(json.dumps(signed.to_dict(), sort_keys=True))
    else:
        tier = signed.accountability_tier.value if signed.accountability_tier else "-"
        print(f"VERDICT   {signed.verdict}   (believe={signed.believe} refuted={signed.refuted})")
        print(f"CLAIM     {signed.claim}")
        print(f"WITNESS   {signed.witness_author or '(none)'} ({tier}) over {signed.witness_surface}")
        print(f"ALGORITHM {signed.algorithm.value}")
        print(f"TIMESTAMP {signed.timestamp}")
        print(f"SIGNATURE {signed.signature}")
        print(f"REASON    {verdict.reason}")

    return _ATTEST_EXITS.code_for(signed.verdict.lower())


# ---------------------------------------------------------------------------
# verify-receipt  (the third-party check of a portable receipt, docs/246 Phase 2)
#   (full prose: docs/CLI.md § "verify-receipt  (the third-party check of a portable receipt")
_VERIFY_RECEIPT_EXITS = ExitMap({"valid": 0, "invalid": 1, "contract_error": 2})
_VERIFY_RECEIPT_EXIT_CODES = _VERIFY_RECEIPT_EXITS.codes


def cmd_verify_receipt(args: argparse.Namespace) -> int:
    """Check a portable receipt's signature — the third-party surface, NO loop access (docs/246).

    Detail: docs/CLI.md § cmd_verify_receipt.
    """
    # 1. Read the receipt — from --receipt PATH, or stdin. A malformed receipt is a
    #    contract error (we never pretend to have checked an unparseable certificate).
    raw = ""
    rcpt_path = getattr(args, "receipt", None)
    if rcpt_path:
        try:
            with open(rcpt_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            print(f"verify-receipt: could not read --receipt ({e})", file=sys.stderr)
            return _VERIFY_RECEIPT_EXIT_CODES["contract_error"]
    else:
        try:
            if not sys.stdin.isatty():
                raw = sys.stdin.read()
        except Exception:
            raw = ""
    if not raw.strip():
        print("verify-receipt: no receipt — pass --receipt PATH or a receipt JSON on stdin",
              file=sys.stderr)
        return _VERIFY_RECEIPT_EXIT_CODES["contract_error"]
    try:
        obj = json.loads(raw)
        receipt = attest.Receipt.from_dict(obj)
    except (ValueError, TypeError, KeyError) as e:
        print(f"verify-receipt: malformed receipt ({e})", file=sys.stderr)
        return _VERIFY_RECEIPT_EXIT_CODES["contract_error"]

    # 2. The verifying key — boundary I/O (here the SHARED/public half). No key →
    #    contract error.
    try:
        key = _load_attest_key(args)
    except OSError as e:
        print(f"verify-receipt: could not read --key-file ({e})", file=sys.stderr)
        return _VERIFY_RECEIPT_EXIT_CODES["contract_error"]
    if key is None:
        print(
            f"verify-receipt: no key — pass --key-file PATH or set ${attest.ATTEST_KEY_ENV} "
            f"(the shared/public half to check the signature against)",
            file=sys.stderr,
        )
        return _VERIFY_RECEIPT_EXIT_CODES["contract_error"]

    # 3. Check the signature. Phase 1 ships HMAC; an Ed25519 receipt is reported as
    #    not-yet-supported here (the Phase-2 asymmetric verifier is behind [attest]).
    if receipt.algorithm is attest.SignatureAlgorithm.HMAC_SHA256:
        result = attest.verify_hmac(receipt, key)
    else:
        result = attest.VerifyResult(
            valid=False,
            reason=(
                f"INVALID — receipt names algorithm {receipt.algorithm.value!r}, which "
                f"this build cannot verify (asymmetric signing is the docs/246 Phase-2 "
                f"[attest] extra)"
            ),
        )

    # 4. Emit. --json carries the verify result + the receipt's carried verdict/tier so
    #    a consumer reads both the STAMP standing and WHAT was attested. UNWITNESSED is
    #    surfaced as a distinct, non-adverse note.
    carried = receipt.verdict
    if getattr(args, "json", False):
        out = result.to_dict()
        out["receipt"] = receipt.to_dict()
        out["adverse"] = bool(result.valid and carried == "REFUTED")
        print(json.dumps(out, sort_keys=True))
    else:
        head = "VALID" if result.valid else "INVALID"
        tier = receipt.accountability_tier.value if receipt.accountability_tier else "-"
        print(f"{head}     {result.reason}")
        if result.valid:
            note = ""
            if carried == "REFUTED":
                note = "  ← ADVERSE: an accountable witness saw the effect ABSENT"
            elif carried in ("UNWITNESSED", "NO_CLAIM"):
                note = "  ← non-adverse: could-not-tell (NOT a finding of absence)"
            print(f"CARRIES   {carried} (tier {tier}){note}")

    if result.valid:
        return _VERIFY_RECEIPT_EXIT_CODES["valid"]
    return _VERIFY_RECEIPT_EXIT_CODES["invalid"]


# ---------------------------------------------------------------------------
# verify-result  (the fold-site result-state witness, docs/197 §7(1))
#   (full prose: docs/CLI.md § "verify-result  (the fold-site result-state witness, docs/197")
_VERIFY_RESULT_EXITS = ExitMap({"healthy": 0, "unreadable": 0, "dead": 3, "contract_error": 2})
_VERIFY_RESULT_EXIT_CODES = _VERIFY_RESULT_EXITS.codes


def cmd_verify_result(args: argparse.Namespace) -> int:
    """The fold-site result-state witness: did a subagent's terminal record DIE? (docs/197 §7(1)).

    Detail: docs/CLI.md § cmd_verify_result.
    """
    from dos import result_state

    # 1. Resolve the transcript path: explicit --transcript › a stdin event's
    #    `transcript_path`. No path at all → contract error (nothing to witness).
    path = getattr(args, "transcript", None)
    if not path:
        raw = ""
        try:
            if not sys.stdin.isatty():
                raw = sys.stdin.read()
        except Exception:
            raw = ""
        if raw.strip():
            try:
                ev = json.loads(raw)
                if isinstance(ev, dict):
                    tp = ev.get("transcript_path")
                    if isinstance(tp, str) and tp:
                        path = tp
            except (ValueError, TypeError):
                path = None
    if not path:
        print(
            "verify-result: no transcript — pass --transcript PATH or a hook event "
            "with transcript_path on stdin",
            file=sys.stderr,
        )
        return _VERIFY_RESULT_EXIT_CODES["contract_error"]

    # 2. The composed boundary-read + pure verdict.
    verdict = result_state.verify_transcript(str(path))

    # 3. Emit. --json carries the full object + the refusal envelope (so the same
    #    DEAD result can ride the wedge_reason refusal plumbing); the default text
    #    form is a single grep-friendly line.
    if getattr(args, "json", False):
        out = verdict.to_dict()
        out["envelope"] = result_state.refusal_envelope(verdict)
        print(json.dumps(out, sort_keys=True))
    else:
        head = "DEAD" if verdict.dead else verdict.state.value
        status = f" apiErrorStatus={verdict.api_status}" if verdict.api_status is not None else ""
        cls = f" class={verdict.cls.value}" if verdict.cls is not result_state.TerminalClass.NONE else ""
        print(f"{head} {verdict.state.value}{status}{cls} — {verdict.reason}")

    # 4. The exit code IS the branch signal (the docs/197 fold-partition).
    if verdict.dead:
        return _VERIFY_RESULT_EXIT_CODES["dead"]
    if verdict.state is result_state.TerminalState.UNREADABLE:
        return _VERIFY_RESULT_EXIT_CODES["unreadable"]
    return _VERIFY_RESULT_EXIT_CODES["healthy"]


# ---------------------------------------------------------------------------
# coverage  (the cheap, non-git fan-out coverage fold, docs/197 §7(1))
#   (full prose: docs/CLI.md § "coverage  (the cheap, non-git fan-out coverage fold, docs/19")
_COVERAGE_EXITS = ExitMap({
    "full": 0, "empty": 0,
    "underfilled": 3, "starved": 3, "overfilled": 3,
    "contract_error": 2,
})
_COVERAGE_EXIT_CODES = _COVERAGE_EXITS.codes


def cmd_coverage(args: argparse.Namespace) -> int:
    """The cheap, NON-GIT fan-out coverage fold: how many of N workers REALLY returned? (docs/197 §7(1)).

    Detail: docs/CLI.md § cmd_coverage.
    """
    import glob as _glob
    from dos import coverage as _coverage
    from dos import result_state as _rs

    declared = getattr(args, "declared", None)
    if declared is None:
        print("coverage: --declared N is required (the expected fan-out width — never "
              "inferred from the input length)", file=sys.stderr)
        return _COVERAGE_EXIT_CODES["contract_error"]

    policy = _coverage.CoveragePolicy(min_quorum=getattr(args, "min_quorum", None))

    # Mode select: --states (caller-asserted) › transcripts (harness-grounded). The two
    # are mutually exclusive; --states wins if both given, but we flag the mix.
    states_arg = getattr(args, "states", None)
    grounded: bool
    if states_arg:
        grounded = False  # PROVENANCE-DEGRADED: the caller asserts the states.
        tokens = [t.strip() for t in states_arg.split(",") if t.strip()]
        try:
            returns = [_rs.TerminalState(t.upper()) for t in tokens]
        except ValueError as exc:
            print(f"coverage: un-coercible state token ({exc}); valid: "
                  f"{', '.join(s.value for s in _rs.TerminalState)}", file=sys.stderr)
            return _COVERAGE_EXIT_CODES["contract_error"]
        verdict = _coverage.classify_coverage(declared, returns, policy)
    else:
        grounded = True  # HARNESS-GROUNDED: coverage runs verify_transcript itself.
        paths = list(getattr(args, "transcript", None) or [])
        g = getattr(args, "transcripts_glob", None)
        if g:
            paths.extend(sorted(_glob.glob(g)))
        if not paths:
            print("coverage: nothing to fold — pass --transcript PATH … / "
                  "--transcripts-glob GLOB (harness-grounded), or --states "
                  "HEALTHY,SYNTHETIC,… (caller-asserted)", file=sys.stderr)
            return _COVERAGE_EXIT_CODES["contract_error"]
        verdict = _coverage.coverage_from_transcripts(declared, paths, policy)

    if getattr(args, "json", False):
        out = verdict.to_dict()
        out["grounded"] = grounded  # the Fix-4 provenance stamp.
        print(json.dumps(out, sort_keys=True))
    else:
        g_tag = "" if grounded else " [caller-asserted]"
        print(f"{verdict.state.value} {verdict.healthy}/{verdict.declared} healthy "
              f"({verdict.dead} dead, {verdict.unaccounted} unaccounted){g_tag} — "
              f"{verdict.reason}")

    return {
        _coverage.Coverage.FULL: _COVERAGE_EXIT_CODES["full"],
        _coverage.Coverage.EMPTY: _COVERAGE_EXIT_CODES["empty"],
        _coverage.Coverage.UNDERFILLED: _COVERAGE_EXIT_CODES["underfilled"],
        _coverage.Coverage.STARVED: _COVERAGE_EXIT_CODES["starved"],
        _coverage.Coverage.OVERFILLED: _COVERAGE_EXIT_CODES["overfilled"],
    }[verdict.state]


# ---------------------------------------------------------------------------
# model-health  (the per-MODEL fleet death rollup — the auto-routing surface)
#   EXIT: 0 = all models healthy, 5 = a model is DOWN (reroute), 2 = contract error.
#   The non-zero "a model is down" code lets a shell conductor branch into a
#   reroute without parsing JSON — the auto-healing trigger the goal keys on.
# ---------------------------------------------------------------------------
_MODEL_HEALTH_EXITS = ExitMap({
    "healthy": 0,
    "model_down": 5,
    "contract_error": 2,
})
_MODEL_HEALTH_EXIT_CODES = _MODEL_HEALTH_EXITS.codes


def cmd_model_health(args: argparse.Namespace) -> int:
    """The per-MODEL fleet death rollup: WHICH model is down across the children
    and grandchildren — and what to reroute (the '3x observability' projection).

    Folds a set of descendant transcripts into a per-model tally of
    MODEL_UNAVAILABLE deaths, so a down model on any descendant is visible at a
    glance with the heal (reroute to a sibling model) attached. Read-only,
    harness-grounded (runs result_state itself), mints zero new labels.
    """
    import glob as _glob
    from dos import model_health as _mh

    session = getattr(args, "session", None)
    paths = list(getattr(args, "transcript", None) or [])
    g = getattr(args, "transcripts_glob", None)
    if g:
        paths.extend(sorted(_glob.glob(g)))

    if session:
        # Auto-discover the descendant agents from ONE session JSONL (the
        # child/grandchild depth axis) — no need to know where the descendant
        # transcripts live. Mutually exclusive with the explicit-path modes.
        if paths:
            print("model-health: --session is mutually exclusive with "
                  "--transcript/--transcripts-glob (it discovers the descendants "
                  "itself)", file=sys.stderr)
            return _MODEL_HEALTH_EXIT_CODES["contract_error"]
        health = _mh.model_health_from_session(session)
    elif paths:
        health = _mh.model_health_from_transcripts(paths)
    else:
        print("model-health: nothing to fold — pass --session SESSION.jsonl "
              "(auto-discover descendants), or --transcript PATH … / "
              "--transcripts-glob GLOB", file=sys.stderr)
        return _MODEL_HEALTH_EXIT_CODES["contract_error"]

    if getattr(args, "json", False):
        print(json.dumps(health.to_dict(), sort_keys=True))
    else:
        print(health.headline)
        for t in health.tallies:
            src = f" [{', '.join(t.sources[:3])}{'…' if len(t.sources) > 3 else ''}]" if t.sources else ""
            print(f"  {t.model}: {t.deaths} death(s){src}")

    return (
        _MODEL_HEALTH_EXIT_CODES["model_down"]
        if health.any_model_down
        else _MODEL_HEALTH_EXIT_CODES["healthy"]
    )


# ---------------------------------------------------------------------------
# model-reroute  (the auto-HEALING actuator — propose a reroute to a sibling)
#   EXIT: 0 = nothing to heal / all REROUTE proposed, 5 = an ESCALATE is needed
#   (no sibling / unnamed — an operator must act), 2 = contract error.
# ---------------------------------------------------------------------------
_MODEL_REROUTE_EXITS = ExitMap({
    "healed": 0,        # nothing down, or every down model got a REROUTE proposal
    "escalate": 5,      # at least one ESCALATE — an operator must act
    "contract_error": 2,
})
_MODEL_REROUTE_EXIT_CODES = _MODEL_REROUTE_EXITS.codes


def _load_model_reroute():
    """Resolve the model-reroute DRIVER by name — never a static import (the
    watchdog idiom: the kernel CLI reaches a driver lazily, with a clean
    'not installed' message rather than an import crash)."""
    import importlib

    try:
        return importlib.import_module("dos.drivers.model_reroute")
    except ModuleNotFoundError as e:  # pragma: no cover - the driver ships in-tree
        if (e.name or "") in ("dos.drivers.model_reroute", "dos.drivers"):
            raise ValueError(
                "the model-reroute driver (dos.drivers.model_reroute) is not "
                "installed; `dos model-reroute` needs it — reinstall the package") from None
        raise


def cmd_model_reroute(args: argparse.Namespace) -> int:
    """The auto-HEALING actuator: from a model-health verdict + a host roster,
    PROPOSE a re-dispatch of each down model's units on a sibling (never launched).

    The other half of model-health: model-health says WHICH model is down; this
    says WHAT to route to (a sibling from --roster) and carries the re-dispatch
    command — one paste away. Propose-not-enact (the watchdog boundary): it
    launches nothing. The roster is host policy, passed in.
    """
    import glob as _glob
    from dos import model_health as _mh

    roster = [m.strip() for m in (getattr(args, "roster", "") or "").split(",") if m.strip()]
    if not roster:
        print("model-reroute: --roster M1,M2,… is required (the host's model roster, "
              "best-first — the kernel names no model)", file=sys.stderr)
        return _MODEL_REROUTE_EXIT_CODES["contract_error"]

    session = getattr(args, "session", None)
    paths = list(getattr(args, "transcript", None) or [])
    g = getattr(args, "transcripts_glob", None)
    if g:
        paths.extend(sorted(_glob.glob(g)))

    if session and paths:
        print("model-reroute: --session is mutually exclusive with "
              "--transcript/--transcripts-glob", file=sys.stderr)
        return _MODEL_REROUTE_EXIT_CODES["contract_error"]
    if session:
        health = _mh.model_health_from_session(session)
    elif paths:
        health = _mh.model_health_from_transcripts(paths)
    else:
        print("model-reroute: nothing to fold — pass --session SESSION.jsonl, or "
              "--transcript PATH … / --transcripts-glob GLOB", file=sys.stderr)
        return _MODEL_REROUTE_EXIT_CODES["contract_error"]

    reroute = _load_model_reroute()
    proposals = reroute.propose_reroutes(
        health, roster, command_template=getattr(args, "command_template", "") or "")

    if getattr(args, "json", False):
        print(json.dumps([p.to_dict() for p in proposals], sort_keys=True))
    else:
        print(reroute.render_text(proposals))

    needs_escalate = any(p.action is reroute.RerouteAction.ESCALATE for p in proposals)
    return (
        _MODEL_REROUTE_EXIT_CODES["escalate"]
        if needs_escalate
        else _MODEL_REROUTE_EXIT_CODES["healed"]
    )


# ---------------------------------------------------------------------------
# arbitrate  (the admission kernel)
# ---------------------------------------------------------------------------
def _arbitrate_followup_note(decision, requested_lane: str, lanes) -> str | None:
    """The interactive follow-up for an `arbitrate` ACQUIRE (stderr, TTY only).

    Names, at the moment they bite, the two things a first interactive run
    misreads:

    * `arbitrate` is the PURE decision — nothing is journaled, so a second call
      sees the same world and "acquires" again. The durable verb is
      `dos lease-lane acquire` (the WAL write-back, docs/96).
    * a bare request naming a lane this workspace never declared degrades to
      auto-pick (the docs/104 soft-hint redirect). The decision's reason already
      SAYS so; the fix (`dos init .` / `dos man lane`) lives here.

    Pure (decision + taxonomy in, text out) so it is unit-testable; the caller
    gates emission on stderr being a TTY, so piped/scripted/CI output stays
    byte-identical with or without it.
    """
    if getattr(decision, "outcome", "") != "acquire":
        return None
    lines: list[str] = []
    if requested_lane:
        known = {str(n or "").casefold() for n in (
            *lanes.concurrent, *lanes.exclusive, *lanes.autopick,
            *lanes.trees.keys(), *lanes.aliases.keys(), "global")}
        if requested_lane.casefold() not in known:
            lines.append(
                f"note: {requested_lane!r} is not a lane in this workspace. "
                f"`dos init .` derives lanes from your top-level dirs; "
                f"`dos man lane` lists the declared ones.")
    lane = decision.lane or requested_lane
    lines.append(
        "note: arbitrate DECIDES only — nothing was journaled, so the next "
        "call sees the same world. To hold "
        + (f"lane {lane!r}" if lane else "a lane")
        + f" durably: dos lease-lane acquire --lane {lane or '<lane>'} "
          "--owner <id>")
    return "\n".join(lines)


def cmd_arbitrate(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    from dos import arbiter
    from dos import admission as _admission
    cfg = _config.active()
    tree = list(args.tree or [])
    if not tree and args.lane:
        tree = cfg.lanes.tree_for(args.lane)
    # `--leases` is a JSON array of live-lease dicts. A malformed value is operator
    #   (full prose: docs/CLI.md § "`--leases` is a JSON array of live-lease dicts. A malformed")
    if args.leases:
        try:
            live = json.loads(args.leases)
        except json.JSONDecodeError as e:
            print(f"error: --leases is not valid JSON ({e}); expected a JSON array "
                  f"of live-lease objects, e.g. '[]' or "
                  f'\'[{{"lane":"api","lane_kind":"cluster","tree":["src/**"]}}]\'',
                  file=sys.stderr)
            return _ARBITRATE_EXIT_CODES["contract_error"]
        if not isinstance(live, list):
            print(f"error: --leases must be a JSON array (got "
                  f"{type(live).__name__}); e.g. '[]' or a list of lease objects.",
                  file=sys.stderr)
            return _ARBITRATE_EXIT_CODES["contract_error"]
    else:
        # No explicit override → the durable live set. A missing/empty journal folds
        # to [] (a fresh workspace genuinely has no leases), so this is safe before
        # any `lease-lane acquire` has ever run.
        from dos import lane_lease as _lane_lease
        try:
            live = _lane_lease.live_leases(cfg)
        except Exception:  # noqa: BLE001 — a read of an append-only WAL is best-effort;
            live = []       # a corrupt log must not crash the verdict, only empty it.
    # ADM Phase 3 — resolve the FULL admission conjunction at the CALL BOUNDARY
    #   (full prose: docs/CLI.md § "ADM Phase 3 — resolve the FULL admission conjunction at the")
    preds = _admission.active_predicates(config=cfg)
    # Class budgets (docs/97, C13): the config-declared [[concurrency_class]] set,
    # OVERLAID by any --class-budget KIND=N operator flags (the explicit flag wins).
    # A malformed flag is operator error → the contract-error exit, never a traceback.
    from dos import concurrency_class as _cc
    budgets = dict(cfg.class_budgets.as_arbiter_budgets())
    try:
        budgets.update(_cc.parse_cli_budgets(getattr(args, "class_budget", None)))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _ARBITRATE_EXIT_CODES["contract_error"]
    decision = arbiter.arbitrate(
        requested_lane=args.lane or "",
        requested_kind=args.kind or "",
        requested_tree=tree,
        live_leases=live,
        config=cfg,
        force=args.force,
        predicates=preds,
        class_budgets=budgets or None,
    )
    # Output through the renderer seam (RND Phase 2). The default `text`/`json`
    #   (full prose: docs/CLI.md § "Output through the renderer seam (RND Phase 2). The default")
    from dos import render as _render
    name = _resolve_output_name(args)
    try:
        renderer = _render.resolve_renderer(name)
    except _render.UnknownRenderer as e:
        print(f"error: {e}", file=sys.stderr)
        return _ARBITRATE_EXIT_CODES["contract_error"]
    # The decision's exit code comes FROM `_ARBITRATE_EXIT_CODES` (not a literal)
    # so the published `exit_codes` table and the binary read the SAME map.
    decision_code = _ARBITRATE_EXIT_CODES[
        "acquire" if decision.outcome == "acquire" else "refuse"]
    # `--explain` (off by default): append the same GO/STOP next-action hint the
    #   (full prose: docs/CLI.md § "`--explain` (off by default): append the same GO/STOP next-a")
    if getattr(args, "explain", False) and not (args.force
                                                 and decision.outcome == "acquire"):
        # Common case: no resolved-decision capture to do, so emit + return here.
        # (The rare --force-override-capture path below still needs to run, so we
        # fall through to the normal emission for it rather than early-returning.)
        return _emit_with_explanation(
            args, decision, "render_decision",
            _interpret.arbitrate(decision.to_dict()),
            exit_code=decision_code)
    # Via _render_one so a buggy plugin's render_decision degrades to text rather
    # than crashing arbitrate (the renderer-can-only-uglify invariant).
    rendered = _render_one(renderer, "render_decision", decision)
    if args.pretty:
        try:
            rendered = json.dumps(json.loads(rendered), sort_keys=True, indent=2)
        except (ValueError, TypeError):
            pass  # a non-JSON renderer: pretty is a no-op, print as-is
    print(rendered)
    if getattr(args, "explain", False):
        # The --force-override-capture path fell through to here; still honor
        # --explain by appending the hint line after the (JSON) decision.
        print(_interpret.arbitrate(decision.to_dict()))

    # Resolved-decision capture (docs/75 §5.7): a `--force` that turned a refusal
    #   (full prose: docs/CLI.md § "Resolved-decision capture (docs/75 §5.7): a `--force` that t")
    if args.force and decision.outcome == "acquire":
        unforced = arbiter.arbitrate(
            requested_lane=args.lane or "", requested_kind=args.kind or "",
            requested_tree=tree, live_leases=live, config=cfg, force=False,
            predicates=preds,  # same conjunction, so the unforced re-run is faithful
        )
        if unforced.outcome != "acquire":
            _ensure_home_if_persisting()
            from dos import home
            home.append_decision(cfg, {
                "kind": "ARBITER_REFUSE",
                "resolver_kind": "HUMAN",
                "lane": args.lane or "",
                "reason_token": "",
                "reason_category": "",
                "run_ts": "",
                "resolution": {"action": "force_acquire",
                               "lane": args.lane or "", "forced": True},
            })
    # The interactive follow-up (TTY-only, stderr): "did anything get held?"
    # and "why did my lane name redirect?", answered at the moment they bite.
    # stdout stays byte-identical (a piped `dos arbitrate --output json | jq`
    # sees no extra bytes), and a non-TTY stderr (scripts, CI) sees nothing.
    note = _arbitrate_followup_note(decision, args.lane or "", cfg.lanes)
    if note and sys.stderr.isatty():
        print(note, file=sys.stderr)
    return decision_code


# ---------------------------------------------------------------------------
# scope-gate  (docs/102 §5 — the BINDING pre-effect scope gate: may this
#   (full prose: docs/CLI.md § "scope-gate  (docs/102 §5 — the BINDING pre-effect scope gate")
_SCOPE_GATE_EXITS = ExitMap(
    {
        ("allow", "IN_SCOPE"): 0,
        ("refuse", "SCOPE_CREEP"): 5,
        ("refuse", "WRONG_TARGET"): 6,
    },
)
_SCOPE_GATE_EXIT_CODES = _SCOPE_GATE_EXITS.codes
_SCOPE_GATE_EXIT_CONTRACT_ERROR = _SCOPE_GATE_EXITS.contract_error


def _scope_gate_proposed_files(args, root) -> "frozenset[str]":
    """Gather the PROPOSED (pre-effect) write-set the gate decides on.

    Detail: docs/CLI.md § _scope_gate_proposed_files.
    """
    explicit = getattr(args, "file", None)
    if explicit:
        return frozenset(p.strip() for p in explicit if p and p.strip())
    import subprocess as _sp
    git_args = (["diff", "--cached", "--name-only"] if getattr(args, "staged", False)
                else ["diff", "--name-only", f"{args.base}..{args.head}"])
    try:
        raw = _sp.run(["git", *git_args], cwd=str(root), capture_output=True,
                      text=True, check=False, timeout=15)
    except (OSError, _sp.TimeoutExpired):
        return frozenset()
    if raw.returncode != 0:
        return frozenset()
    return frozenset(ln.strip() for ln in raw.stdout.splitlines() if ln.strip())


def cmd_scope_gate(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    from dos import scope
    cfg = _config.active()
    root = cfg.paths.root
    touched = _scope_gate_proposed_files(args, root)
    # The lane tree comes from the workspace's declared lanes (the SAME source the
    #   (full prose: docs/CLI.md § "The lane tree comes from the workspace's declared lanes (the")
    lane_tree = tuple(cfg.lanes.tree_for(args.lane)) if args.lane else ("**/*",)
    ev = scope.ScopeEvidence(touched_files=touched, lane_tree=lane_tree, lane=args.lane or "")
    decision = scope.gate(ev, scope.DEFAULT_GATE_POLICY)
    if getattr(args, "output", "text") == "json" or getattr(args, "json", False):
        print(json.dumps(decision.to_dict(), indent=2 if getattr(args, "pretty", False) else None))
    else:
        print(f"{'ALLOW' if decision.allowed else 'REFUSE'}: {decision.reason}")
    key = ("allow" if decision.allowed else "refuse", decision.verdict.value)
    return _SCOPE_GATE_EXIT_CODES.get(key, _SCOPE_GATE_EXIT_CONTRACT_ERROR)


# ---------------------------------------------------------------------------
# liveness  (the temporal verdict — is the run moving, or spinning?)
#   (full prose: docs/CLI.md § "liveness  (the temporal verdict — is the run moving, or spin")
_LIVENESS_EXITS = ExitMap(
    {"ADVANCING": 0, "SPINNING": 3, "STALLED": 4},
    unknown=5,  # a future verdict the CLI hasn't caught up to — non-zero, distinct
    syscall="liveness",  # docs/262 P2 — auto-record this verdict when observing
)               # from the known verdicts and contract_error, never read as ADVANCING.
_LIVENESS_EXIT_CODES = _LIVENESS_EXITS.codes
_LIVENESS_EXIT_UNKNOWN = _LIVENESS_EXITS.unknown
_LIVENESS_EXIT_CONTRACT_ERROR = _LIVENESS_EXITS.contract_error


def cmd_liveness(args: argparse.Namespace) -> int:
    """Classify whether a run is ADVANCING / SPINNING / STALLED (docs/82, LVN).

    Detail: docs/CLI.md § cmd_liveness.
    """
    _apply_workspace(args)
    import time
    from dos import liveness, run_id

    started_ms = run_id.ts_ms_of(args.run_id)
    if started_ms is None:
        print(f"error: {args.run_id!r} is not a valid run-id token "
              f"(expected an RID-… minted by `dos run-id mint`)", file=sys.stderr)
        return _LIVENESS_EXIT_CONTRACT_ERROR
    # The clock is read ONCE, HERE at the boundary (never inside the verdict) —
    # injectable via --now-ms for deterministic tests/scripts, exactly the
    # run_id.mint clock-injection idiom.
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)

    cfg = _config.active()
    commits = _git_delta_count(args.start_sha, cfg)

    # LVN Phase 2 — the journal rungs, scoped to THIS run's lease (identity
    # required, the operator's "require identity always" call): both --lane and
    # --loop-ts must be present for the journal to be attributed to this run.
    lane = getattr(args, "lane", None)
    loop_ts = getattr(args, "loop_ts", None)
    lease_key = (str(loop_ts), str(lane)) if lane and loop_ts else None
    jd = _journal_delta(cfg, started_ms=started_ms, now_ms=now_ms, lease_key=lease_key)

    # The unforgeable OS proc rung (docs/95), gathered at THIS boundary like every
    # other evidence read. Silent (None) unless --pid is given and --no-proc is
    # not set; a foreign --host-id keeps it None (proc_delta refuses cross-host).
    # None leaves the verdict byte-identical to before this rung existed.
    process_alive = None
    pid = getattr(args, "pid", None)
    if pid is not None and not getattr(args, "no_proc", False):
        import os as _os
        from dos import proc_delta
        from dos.lane_lease import _hostname
        # The host we're probing FROM — same resolution the lease writer stamps
        # (DISPATCH_HOST_ID override › hostname), so a --host-id read off a lease
        # compares against the same identity that lease recorded.
        this_host = _os.environ.get("DISPATCH_HOST_ID") or _hostname()
        process_alive = proc_delta.probe(
            pid, host_id=getattr(args, "host_id", "") or "", this_host=this_host
        ).alive

    # The OPTIONAL waste signal (docs/300 §7, issue #41): `--usage-json` feeds the
    # normalized provider token total into `tokens_spent_since`. Read ONCE here at
    # the boundary (the same `_read_usage_breakdown` the efficiency family uses), so
    # the verdict stays pure. NOT a verdict input — docs/219 holds: tokens never
    # decide ADVANCING/SPINNING/STALLED; the count only enriches the SPINNING reason
    # ("…and it has burned N tokens while not moving"). Absent ⇒ None ⇒ byte-identical
    # to before this slot was fed. A malformed record is a CONTRACT error (exit 2),
    # never a silently-dropped count.
    try:
        usage = _read_usage_breakdown(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _LIVENESS_EXIT_CONTRACT_ERROR
    tokens_spent_since = usage.total if usage is not None else None

    ev = liveness.ProgressEvidence(
        run_started_ms=started_ms,
        now_ms=now_ms,
        commits_since_start=commits,
        journal_events_since=jd.events_since_start,
        # --last-heartbeat-age-ms is an OVERRIDE: it WINS when supplied, else the
        # journal-derived age is used. `is not None` (never a bare `or`) so an
        # explicit 0 (freshest) doesn't fall through to the journal value.
        last_heartbeat_age_ms=(
            args.last_heartbeat_age_ms if args.last_heartbeat_age_ms is not None
            else jd.newest_heartbeat_age_ms
        ),
        tokens_spent_since=tokens_spent_since,   # docs/300 §7: the waste signal, fed
        process_alive=process_alive,
    )
    verdict = liveness.classify(ev)

    return _LIVENESS_EXITS.emit(args, verdict, verdict.verdict.value)


# ---------------------------------------------------------------------------
# productivity  (the loop-economics verdict — is the run still doing work?)
#   (full prose: docs/CLI.md § "productivity  (the loop-economics verdict — is the run still")
_PRODUCTIVITY_EXITS = ExitMap(
    {"PRODUCTIVE": 0, "DIMINISHING": 3, "STALLED": 4},
    unknown=5,  # a future verdict the CLI hasn't caught up to — non-zero, distinct
    syscall="productivity",  # docs/262 P2 — auto-record when observing
)               # from the known verdicts and contract_error, never read as PRODUCTIVE.
_PRODUCTIVITY_EXIT_CODES = _PRODUCTIVITY_EXITS.codes
_PRODUCTIVITY_EXIT_UNKNOWN = _PRODUCTIVITY_EXITS.unknown
_PRODUCTIVITY_EXIT_CONTRACT_ERROR = _PRODUCTIVITY_EXITS.contract_error


def cmd_productivity(args: argparse.Namespace) -> int:
    """Classify whether a run is PRODUCTIVE / DIMINISHING / STALLED (docs/218, PRD).

    Detail: docs/CLI.md § cmd_productivity.
    """
    _apply_workspace(args)
    from dos import productivity

    raw = args.deltas if args.deltas is not None else ""
    # Parse the comma list at the boundary; a non-numeric token is a CONTRACT error
    # (exit 2), never silently dropped — a malformed delta would corrupt the trend.
    tokens = [t.strip() for t in raw.split(",") if t.strip() != ""]
    try:
        deltas = [int(t) for t in tokens]
    except ValueError:
        print(
            f"error: --deltas must be a comma list of non-negative integers "
            f"(work units per step, oldest first); got {raw!r}",
            file=sys.stderr,
        )
        return _PRODUCTIVITY_EXIT_CONTRACT_ERROR

    policy = productivity.ProductivityPolicy(
        min_steps=args.min_steps if args.min_steps is not None
        else productivity.DEFAULT_POLICY.min_steps,
        floor=args.floor if args.floor is not None
        else productivity.DEFAULT_POLICY.floor,
    )
    try:
        history = productivity.WorkHistory.of(deltas)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _PRODUCTIVITY_EXIT_CONTRACT_ERROR

    verdict = productivity.classify(history, policy)

    return _PRODUCTIVITY_EXITS.emit(args, verdict, verdict.verdict.value)


# ---------------------------------------------------------------------------
# efficiency  (the token-effectiveness verdict — did the tokens buy work?)
#   (full prose: docs/CLI.md § "efficiency  (the token-effectiveness verdict — did the token")
_EFFICIENCY_EXITS = ExitMap(
    {"EFFICIENT": 0, "COSTLY": 3, "WASTEFUL": 4},
    unknown=5,  # a future verdict the CLI hasn't caught up to — non-zero, distinct
    syscall="efficiency",  # docs/262 P2 — auto-record when observing
)               # from the known verdicts and contract_error, never read as EFFICIENT.
_EFFICIENCY_EXIT_CODES = _EFFICIENCY_EXITS.codes
_EFFICIENCY_EXIT_UNKNOWN = _EFFICIENCY_EXITS.unknown
_EFFICIENCY_EXIT_CONTRACT_ERROR = _EFFICIENCY_EXITS.contract_error


def _read_usage_breakdown(args: argparse.Namespace):
    """Parse `--usage-json PATH|-` into a `SpendBreakdown`, or None when absent.

    docs/300 — the ONE place a provider usage record is read for the efficiency
    family: a file (or stdin via `-`), JSON-decoded and normalized through
    `spend.parse_usage` (which detects the additive vs inclusive wire shape and
    kills the double-count asymmetry at this boundary). All I/O happens HERE;
    the classifiers stay pure. Raises ValueError with an operator-facing message
    on any malformed input — the caller maps it to the contract-error exit.
    """
    raw_path = getattr(args, "usage_json", None)
    if not raw_path:
        return None
    from dos import spend

    if raw_path == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(raw_path).read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError(f"--usage-json: cannot read {raw_path!r}: {e}") from None
    try:
        record = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"--usage-json: not valid JSON ({e})") from None
    if not isinstance(record, dict):
        raise ValueError(
            f"--usage-json: expected a JSON object (a usage record), "
            f"got {type(record).__name__}"
        )
    return spend.parse_usage(record)


def cmd_efficiency(args: argparse.Namespace) -> int:
    """Classify whether a run's tokens were spent EFFICIENT / COSTLY / WASTEFUL (docs/263, EFF).

    Detail: docs/CLI.md § cmd_efficiency.
    """
    _apply_workspace(args)
    from dos import efficiency

    policy = efficiency.EfficiencyPolicy(
        min_tokens=args.min_tokens if args.min_tokens is not None
        else efficiency.DEFAULT_POLICY.min_tokens,
        floor=args.floor if args.floor is not None
        else efficiency.DEFAULT_POLICY.floor,
    )
    try:
        breakdown = _read_usage_breakdown(args)
        evidence = efficiency.EfficiencyEvidence.of(
            work=args.work, tokens=args.tokens, breakdown=breakdown
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EFFICIENCY_EXIT_CONTRACT_ERROR

    verdict = efficiency.classify(evidence, policy)

    return _EFFICIENCY_EXITS.emit(args, verdict, verdict.verdict.value)


# ---------------------------------------------------------------------------
# efficiency-trend  (the cross-run fold — is work-per-token fading across runs?)
#   (full prose: docs/CLI.md § "efficiency-trend  (the cross-run fold — is work-per-token fa")
_EFFICIENCY_TREND_EXITS = ExitMap(
    {"IMPROVING": 0, "STEADY": 0, "DEGRADING": 3},
    unknown=5,  # a future verdict the CLI hasn't caught up to — non-zero, distinct
    syscall="efficiency_trend",  # docs/262 P2 — auto-record when observing
)
_EFFICIENCY_TREND_EXIT_CODES = _EFFICIENCY_TREND_EXITS.codes
_EFFICIENCY_TREND_EXIT_UNKNOWN = _EFFICIENCY_TREND_EXITS.unknown
_EFFICIENCY_TREND_EXIT_CONTRACT_ERROR = _EFFICIENCY_TREND_EXITS.contract_error


def _trend_samples_from_journal(args: argparse.Namespace) -> list[tuple[int, int]]:
    """Gather (work, tokens) samples from the verdict journal's efficiency fossils.

    docs/300 P3 — the boundary read behind `--from-journal`: every `--observe`d
    `dos efficiency` verdict recorded its evidence (`evidence.work` /
    `evidence.tokens`, the dotted-key flatten) to the journal; this collects
    them in append order (oldest first — exactly the order the pure fold wants),
    optionally scoped to one `--run`. Read-only, the `dos observe` discipline:
    no lease, no mutation, and the fold itself stays pure.
    """
    from dos import verdict_journal

    pairs: list[tuple[int, int]] = []
    for ev in verdict_journal.read_events():
        if ev.syscall != "efficiency":
            continue
        if getattr(args, "run", None) and ev.run_id != args.run:
            continue
        detail = ev.detail or {}
        work = detail.get("evidence.work")
        tokens = detail.get("evidence.tokens")
        # An event that predates the dotted-key flatten carries no counts —
        # skipped, not guessed (a fossil with no bones is not a sample).
        if isinstance(work, (int, float)) and isinstance(tokens, (int, float)):
            pairs.append((int(work), int(tokens)))
    limit = getattr(args, "last", None)
    if limit is not None and limit > 0:
        pairs = pairs[-limit:]
    return pairs


def cmd_efficiency_trend(args: argparse.Namespace) -> int:
    """Classify a loop's cross-run token effectiveness: IMPROVING / STEADY / DEGRADING (docs/300, TRD).

    Detail: docs/CLI.md § cmd_efficiency_trend.
    """
    _apply_workspace(args)
    from dos import efficiency_trend

    if bool(args.samples) == bool(args.from_journal):
        print(
            "error: give exactly one evidence source — --samples \"w:t,w:t,…\" "
            "(caller-assembled, oldest first) or --from-journal (fold the "
            "verdict journal's recorded efficiency evidence)",
            file=sys.stderr,
        )
        return _EFFICIENCY_TREND_EXIT_CONTRACT_ERROR

    if args.samples:
        pairs = []
        for token in (t.strip() for t in args.samples.split(",")):
            if not token:
                continue
            head, sep, tail = token.partition(":")
            try:
                if not sep:
                    raise ValueError
                pairs.append((int(head), int(tail)))
            except ValueError:
                print(
                    f"error: --samples must be a comma list of work:tokens pairs "
                    f"(non-negative integers, oldest first); got {token!r}",
                    file=sys.stderr,
                )
                return _EFFICIENCY_TREND_EXIT_CONTRACT_ERROR
    else:
        pairs = _trend_samples_from_journal(args)

    policy = efficiency_trend.TrendPolicy(
        min_samples=args.min_samples if args.min_samples is not None
        else efficiency_trend.DEFAULT_POLICY.min_samples,
        tolerance=args.tolerance if args.tolerance is not None
        else efficiency_trend.DEFAULT_POLICY.tolerance,
    )
    try:
        history = efficiency_trend.TrendHistory.of(pairs)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _EFFICIENCY_TREND_EXIT_CONTRACT_ERROR

    verdict = efficiency_trend.classify(history, policy)

    return _EFFICIENCY_TREND_EXITS.emit(args, verdict, verdict.verdict.value)


# ---------------------------------------------------------------------------
# work-account  (the work-kind account — what KINDS of work did this iteration land?)
#   (docs/310 — the composition sibling: productivity reads a trend, efficiency a
#   ratio, this reads a typed account by witnessed kind. The healthy kinds all
#   exit 0; CAUGHT is the actionable 3 — a worker over-claimed and the oracle
#   refused it; IDLE is the honest-nothing 4.)
_WORK_ACCOUNT_EXITS = ExitMap(
    {"SHIPPED": 0, "ADVANCED": 0, "GROOMED": 0, "SURFACED": 0, "CAUGHT": 3, "IDLE": 4},
    unknown=5,  # a future verdict the CLI hasn't caught up to — non-zero, distinct
    syscall="work_account",  # docs/262 P2 — auto-record when observing
)
_WORK_ACCOUNT_EXIT_CODES = _WORK_ACCOUNT_EXITS.codes
_WORK_ACCOUNT_EXIT_UNKNOWN = _WORK_ACCOUNT_EXITS.unknown
_WORK_ACCOUNT_EXIT_CONTRACT_ERROR = _WORK_ACCOUNT_EXITS.contract_error


def cmd_work_account(args: argparse.Namespace) -> int:
    """Name the dominant KIND of work one iteration landed (docs/310, WKA).

    Each count comes from the witness that owns it (the oracle's verified
    count, git's commit count, the decisions-queue delta) — gathered by the
    caller at the boundary; the classifier is pure.
    """
    _apply_workspace(args)
    from dos import work_account

    try:
        account = work_account.WorkAccount(
            verified_ships=args.verified_ships,
            claimed_ships=args.claimed_ships,
            catches=args.catches,
            advance_commits=args.advance_commits,
            grooms=args.grooms,
            unblocks=args.unblocks,
            surfaced=args.surfaced,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _WORK_ACCOUNT_EXIT_CONTRACT_ERROR

    verdict = work_account.classify_work(account)

    return _WORK_ACCOUNT_EXITS.emit(args, verdict, verdict.kind.value)


# ---------------------------------------------------------------------------
# improve  (the self-improving-loop keep-gate — may this loop KEEP this candidate?)
#   (full prose: docs/CLI.md § "improve  (the self-improving-loop keep-gate — may this loop")
_IMPROVE_EXITS = ExitMap(
    {"KEEP": 0, "REVERT": 3, "ESCALATE": 4},
    unknown=5,  # a future verdict the CLI hasn't caught up to — non-zero, distinct.
    syscall="improve",  # docs/262 P2 — auto-record when observing
)
_IMPROVE_EXIT_CODES = _IMPROVE_EXITS.codes
_IMPROVE_EXIT_UNKNOWN = _IMPROVE_EXITS.unknown
_IMPROVE_EXIT_CONTRACT_ERROR = _IMPROVE_EXITS.contract_error


def cmd_improve(args: argparse.Namespace) -> int:
    """Decide whether a self-improving loop may KEEP one candidate (docs/280, IMP).

    Detail: docs/CLI.md § cmd_improve.
    """
    _apply_workspace(args)
    from dos import improve

    try:
        policy = improve.ImprovePolicy(
            max_consecutive_reverts=args.max_reverts if args.max_reverts is not None
            else improve.DEFAULT_POLICY.max_consecutive_reverts,
            efficiency_floor=args.efficiency_floor if args.efficiency_floor is not None
            else improve.DEFAULT_POLICY.efficiency_floor,
            min_tokens_for_efficiency=args.min_tokens if args.min_tokens is not None
            else improve.DEFAULT_POLICY.min_tokens_for_efficiency,
        )
        evidence = improve.CandidateEvidence(
            suite_passed=args.suite_passed,
            truth_clean=args.truth_clean,
            work=args.work,
            baseline_work=args.baseline_work,
            tokens=args.tokens,
            consecutive_reverts=args.consecutive_reverts,
            narrated=args.narrated or "",
            breakdown=_read_usage_breakdown(args),  # docs/300 P4 — optional price facts
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _IMPROVE_EXIT_CONTRACT_ERROR

    verdict = improve.classify(evidence, policy)

    return _IMPROVE_EXITS.emit(args, verdict, verdict.verdict.value)


# ---------------------------------------------------------------------------
# breaker  (the circuit breaker — this keeps failing; stop, escalate the rung)
#   (full prose: docs/CLI.md § "breaker  (the circuit breaker — this keeps failing; stop, es")
_BREAKER_EXITS = ExitMap(
    {"CLOSED": 0, "OPEN": 3},
    unknown=5,  # a future state the CLI hasn't caught up to — non-zero, distinct.
    syscall="breaker",  # docs/262 P2 — auto-record when observing
)
_BREAKER_EXIT_CODES = _BREAKER_EXITS.codes
_BREAKER_EXIT_UNKNOWN = _BREAKER_EXITS.unknown
_BREAKER_EXIT_CONTRACT_ERROR = _BREAKER_EXITS.contract_error


def cmd_breaker(args: argparse.Namespace) -> int:
    """Classify a circuit breaker's state: CLOSED / OPEN (docs/223, BRK).

    Detail: docs/CLI.md § cmd_breaker.
    """
    _apply_workspace(args)
    from dos import breaker

    _ESCALATIONS = {
        "none": breaker.Escalation.NONE,
        "judge": breaker.Escalation.JUDGE,
        "human": breaker.Escalation.HUMAN,
    }
    on_trip = _ESCALATIONS[(args.on_trip or "none").lower()]

    try:
        policy = breaker.BreakerPolicy(
            max_consecutive=args.max_consecutive
            if args.max_consecutive is not None
            else breaker.DEFAULT_POLICY.max_consecutive,
            max_total=args.max_total if args.max_total is not None
            else breaker.DEFAULT_POLICY.max_total,
            on_trip=on_trip,
        )
        counts = breaker.BreakerCounts(
            consecutive=args.consecutive or 0,
            total=args.total or 0,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _BREAKER_EXIT_CONTRACT_ERROR

    verdict = breaker.classify(counts, policy)

    return _BREAKER_EXITS.emit(args, verdict, verdict.state.value)


# ---------------------------------------------------------------------------
# exec-capability  (does this command grant arbitrary code execution? a SHAPE)
#   (full prose: docs/CLI.md § "exec-capability  (does this command grant arbitrary code exe")
_EXEC_CAPABILITY_EXITS = ExitMap(
    {"BOUNDED": 0, "EMPTY": 0, "GRANTS_ARBITRARY_EXEC": 3},
    unknown=5,
    syscall="exec_capability",  # docs/262 P2 — auto-record when observing
)
_EXEC_CAPABILITY_EXIT_CODES = _EXEC_CAPABILITY_EXITS.codes
_EXEC_CAPABILITY_EXIT_UNKNOWN = _EXEC_CAPABILITY_EXITS.unknown


def cmd_exec_capability(args: argparse.Namespace) -> int:
    """Classify whether a command grants arbitrary code execution (docs/224, XCAP).

    Detail: docs/CLI.md § cmd_exec_capability.
    """
    _apply_workspace(args)
    from dos import exec_capability

    extra_raw = args.extra if args.extra is not None else ""
    extra = [t.strip() for t in extra_raw.split(",") if t.strip()]
    policy = exec_capability.DEFAULT_POLICY.with_extra(extra) if extra \
        else exec_capability.DEFAULT_POLICY

    verdict = exec_capability.classify_command(args.command or "", policy)

    return _EXEC_CAPABILITY_EXITS.emit(args, verdict, verdict.capability.value)


# ---------------------------------------------------------------------------
# hook-exit  (a plain shell hook's exit code → an intervention verb)
#   (full prose: docs/CLI.md § "hook-exit  (a plain shell hook's exit code → an intervention")
_HOOK_EXIT_EXITS = ExitMap(
    {"PASS": 0, "BLOCK": 3, "WARN": 4, "DEFER": 5, "OBSERVE": 6},
    unknown=7,
    syscall="hook_exit",  # docs/262 P2 — auto-record when observing
)
_HOOK_EXIT_EXIT_CODES = _HOOK_EXIT_EXITS.codes
_HOOK_EXIT_EXIT_UNKNOWN = _HOOK_EXIT_EXITS.unknown
_HOOK_EXIT_EXIT_CONTRACT_ERROR = _HOOK_EXIT_EXITS.contract_error


def cmd_hook_exit(args: argparse.Namespace) -> int:
    """Map a plain shell hook's exit code → an intervention verb (docs/226, HEX).

    Detail: docs/CLI.md § cmd_hook_exit.
    """
    _apply_workspace(args)
    from dos import hook_exit
    from dos.intervention import Intervention

    # Parse --map CODE=VERB,… at the boundary; a malformed entry is a contract error.
    mapping: dict[int, Intervention] = {}
    raw = args.map if args.map is not None else ""
    for entry in [e.strip() for e in raw.split(",") if e.strip()]:
        if "=" not in entry:
            print(f"error: --map entry {entry!r} must be CODE=VERB (e.g. 3=DEFER)",
                  file=sys.stderr)
            return _HOOK_EXIT_EXIT_CONTRACT_ERROR
        code_s, verb_s = entry.split("=", 1)
        try:
            code_i = int(code_s.strip())
            verb = Intervention(verb_s.strip().upper())
        except ValueError:
            print(f"error: --map entry {entry!r}: CODE must be an integer and VERB one "
                  f"of OBSERVE/WARN/BLOCK/DEFER", file=sys.stderr)
            return _HOOK_EXIT_EXIT_CONTRACT_ERROR
        mapping[code_i] = verb

    policy = hook_exit.DEFAULT_POLICY.with_mapping(mapping) if mapping \
        else hook_exit.DEFAULT_POLICY
    verdict = hook_exit.classify_exit(args.code, policy)

    # The headline token: PASS when no intervention, else the intervention name.
    token = "PASS" if verdict.passed else verdict.intervention.value
    return _HOOK_EXIT_EXITS.emit(args, verdict, token)


# ---------------------------------------------------------------------------
# answer-shape  (is this output an ANSWER, or a structural non-answer?)
#   (full prose: docs/CLI.md § "answer-shape  (is this output an ANSWER, or a structural non")
_ANSWER_SHAPE_EXITS = ExitMap(
    {"ANSWER_SHAPED": 0, "NON_ANSWER": 3, "INDETERMINATE": 4},
    unknown=5,  # a future verdict the CLI hasn't caught up to — non-zero, distinct.
)
_ANSWER_SHAPE_EXIT_CODES = _ANSWER_SHAPE_EXITS.codes
_ANSWER_SHAPE_EXIT_UNKNOWN = _ANSWER_SHAPE_EXITS.unknown
_ANSWER_SHAPE_EXIT_CONTRACT_ERROR = _ANSWER_SHAPE_EXITS.contract_error


def cmd_answer_shape(args: argparse.Namespace) -> int:
    """Classify an output's SHAPE: ANSWER_SHAPED / NON_ANSWER / INDETERMINATE (docs/156 §4, ASH).

    Detail: docs/CLI.md § cmd_answer_shape.
    """
    _apply_workspace(args)
    from dos import answer_shape

    # Resolve the candidate text at the boundary: --file/--text, with "-" = stdin.
    #   (full prose: docs/CLI.md § "Resolve the candidate text at the boundary: --file/--text, w")
    text: "str | None" = None
    if args.file is not None:
        if args.file == "-":
            text = sys.stdin.read()
        else:
            try:
                text = Path(args.file).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"error: --file {args.file!r}: {e}", file=sys.stderr)
                return _ANSWER_SHAPE_EXIT_CONTRACT_ERROR
    elif args.text is not None:
        text = sys.stdin.read() if args.text == "-" else args.text
    else:
        print(
            "error: pass the candidate output via --text TEXT (or --text - / "
            "--file - to read stdin, --file PATH to read a file)",
            file=sys.stderr,
        )
        return _ANSWER_SHAPE_EXIT_CONTRACT_ERROR

    # Build the policy from the generic default + the operator overlays. Extra
    # non-answer patterns are ADDED to the generic set (the exec-capability --extra
    # idiom: augment, never replace the cross-domain floor).
    base = answer_shape.GENERIC_ANSWER_SHAPE_POLICY
    extra_patterns = tuple(
        t.strip() for t in (args.non_answer or "").split(",") if t.strip()
    )
    markers = tuple(t.strip() for t in (args.markers or "").split(",") if t.strip())
    policy = answer_shape.AnswerShapePolicy(
        min_viable_chars=args.min_chars if args.min_chars is not None
        else base.min_viable_chars,
        non_answer_patterns=base.non_answer_patterns + extra_patterns,
        answer_markers=markers,
    )

    verdict = answer_shape.classify(text, policy=policy)

    return _ANSWER_SHAPE_EXITS.emit(args, verdict, verdict.state.value)


# ---------------------------------------------------------------------------
# reward  (may a training run TRAIN on this trajectory? — the lab on-ramp, docs/230/234)
#   (full prose: docs/CLI.md § "reward  (may a training run TRAIN on this trajectory? — the")
_REWARD_EXITS = ExitMap(
    {"ACCEPT": 0, "REJECT_POISON": 3, "ABSTAIN": 4, "NO_CLAIM": 5},
    unknown=6,
    syscall="reward",  # docs/262 P2 — auto-record when observing
)
_REWARD_EXIT_CODES = _REWARD_EXITS.codes
_REWARD_EXIT_UNKNOWN = _REWARD_EXITS.unknown
_REWARD_EXIT_CONTRACT_ERROR = _REWARD_EXITS.contract_error


def cmd_reward(args: argparse.Namespace) -> int:
    """May a fine-tune TRAIN on this trajectory? The reward-set admission verdict (docs/230/234).

    Detail: docs/CLI.md § cmd_reward.
    """
    _apply_workspace(args)
    from dos import reward
    from dos.evidence import Accountability, EvidenceFacts

    # The claim-present bit. Exactly one of --claim / --no-claim; --claim is the default
    # intent if neither is given (you are usually classifying a row that made a claim).
    if getattr(args, "no_claim", False) and getattr(args, "claim", False):
        print("error: pass at most one of --claim / --no-claim", file=sys.stderr)
        return _REWARD_EXIT_CONTRACT_ERROR
    claim_present = not getattr(args, "no_claim", False)

    # Build the read-back witness at the boundary. The accountability rung is the
    # load-bearing choice: non-forgeable by default (a real env/OS/ledger witness),
    # AGENT_AUTHORED under --forgeable (the floor demo — a self-report that is ignored).
    rung = (Accountability.AGENT_AUTHORED if getattr(args, "forgeable", False)
            else Accountability.OS_RECORDED)
    w = (args.witness or "none").lower()
    if w == "confirm":
        readbacks = [EvidenceFacts.attest("witness", rung, "effect",
                                          detail="re-read the world: effect PRESENT")]
    elif w == "refute":
        readbacks = [EvidenceFacts.refute("witness", rung, "effect",
                                          detail="re-read the world: effect ABSENT")]
    elif w == "none":
        readbacks = []  # no accountable witness reached -> ABSTAIN on a present claim
    else:
        print(f"error: --witness must be one of confirm/refute/none (got {w!r})",
              file=sys.stderr)
        return _REWARD_EXIT_CONTRACT_ERROR

    label = reward.admit(claim_present, readbacks, narrated=args.narrated or "")
    return _REWARD_EXITS.emit(args, label, label.verdict.value)


# ---------------------------------------------------------------------------
# test-witness  (does this NEW test actually witness this change? — docs/288, TWV)
#   (full prose: docs/CLI.md § "test-witness  (does this NEW test actually witness this chan")
_TEST_WITNESS_EXITS = ExitMap(
    {"DISCRIMINATES": 0, "VACUOUS": 3, "UNSATISFIED": 4, "REGRESSIVE": 5,
     "ABSTAIN": 6},
    unknown=7,
    syscall="test-witness",  # docs/262 P2 — auto-record when observing
)
_TEST_WITNESS_EXIT_CODES = _TEST_WITNESS_EXITS.codes
_TEST_WITNESS_EXIT_UNKNOWN = _TEST_WITNESS_EXITS.unknown
_TEST_WITNESS_EXIT_CONTRACT_ERROR = _TEST_WITNESS_EXITS.contract_error


def cmd_test_witness(args: argparse.Namespace) -> int:
    """Does this NEW test actually witness this change? The test-witness verdict (docs/288).

    Detail: docs/CLI.md § cmd_test_witness.
    """
    _apply_workspace(args)
    from dos import testwitness

    try:
        baseline = testwitness.RunOutcome.parse(args.baseline)
        candidate = testwitness.RunOutcome.parse(args.candidate)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return _TEST_WITNESS_EXIT_CONTRACT_ERROR

    evidence = testwitness.TestRunEvidence.of(
        baseline, candidate, forgeable=bool(getattr(args, "forgeable", False)))
    verdict = testwitness.classify(evidence)
    return _TEST_WITNESS_EXITS.emit(args, verdict, verdict.verdict.value)


# ---------------------------------------------------------------------------
# resume  (the third ARIES phase: replay → re-verify → PROPOSE — docs/107)
#   (full prose: docs/CLI.md § "resume  (the third ARIES phase: replay → re-verify → PROPOSE")
_RESUME_EXITS = ExitMap(
    {"RESUMABLE": 0, "COMPLETE": 0, "DIVERGED": 3, "UNRESUMABLE": 4},
    unknown=5,
)
_RESUME_EXIT_CODES = _RESUME_EXITS.codes
_RESUME_EXIT_UNKNOWN = _RESUME_EXITS.unknown
_RESUME_EXIT_CONTRACT_ERROR = _RESUME_EXITS.contract_error


def cmd_resume(args: argparse.Namespace) -> int:
    """Replay a run's intent ledger, re-verify progress against ancestry, PROPOSE the continuation (docs/107).

    Detail: docs/CLI.md § cmd_resume.
    """
    _apply_workspace(args)
    from dos import intent_ledger, resume as _resume, resume_evidence
    cfg = _config.active()
    rid = (args.run_id or "").strip()
    if not rid:
        print("error: --run-id is required (the intent-ledger key)", file=sys.stderr)
        return _RESUME_EXIT_CONTRACT_ERROR

    # Replay the ledger (schema-gated, torn-tail tolerant) → the folded intent.
    entries = intent_ledger.read_all(rid, cfg=cfg)
    if not entries:
        # No ledger at all: nothing to resume, the honest floor (not a crash).
        plan = _resume.ResumePlan(
            verdict=_resume.Resume.UNRESUMABLE,
            reason=(f"no intent ledger for run {rid!r} (no "
                    f"{intent_ledger.INTENT_JSONL_NAME} in its run-dir) — nothing "
                    f"to resume"),
            run_id=rid,
        )
    else:
        state = intent_ledger.replay(entries)
        # The lane-divergence signal: the CALLER decides whether ground truth moved
        #   (full prose: docs/CLI.md § "The lane-divergence signal: the CALLER decides whether groun")
        anc = resume_evidence.gather_ancestry(
            state, cfg=cfg,
            lane_advanced_past_resume=getattr(args, "diverged", False),
        )
        plan = _resume.resume_plan(state, anc)

    # On RESUMABLE, idempotently record the proposal (§5 req 4) unless inspect-only
    # or already proposed. The record is a DOS emission, so ensure the home tier.
    proposed_now = False
    if (plan.verdict is _resume.Resume.RESUMABLE
            and not getattr(args, "no_record", False)
            and not plan.already_proposed):
        _ensure_home_if_persisting()
        try:
            intent_ledger.append(
                rid,
                intent_ledger.resume_proposed_entry(
                    predecessor_run_id=rid,
                    resume_sha=plan.resume_sha,
                    residual=plan.residual,
                ),
                cfg=cfg,
            )
            proposed_now = True
        except OSError:
            proposed_now = False

    # The proposed re-dispatch command — DATA the kernel prints for a driver/operator
    # to run; the kernel never runs it (the §8 non-goal / docs/99 advisory floor).
    redispatch = (
        f"dos loop dispatch --resume {rid}  # re-enter at {plan.resume_sha[:12] or '(start)'}, "
        f"do: {', '.join(plan.residual) if plan.residual else '(nothing)'}"
    ) if plan.verdict is _resume.Resume.RESUMABLE else ""

    out = plan.to_dict()
    out["proposed_now"] = proposed_now
    if redispatch:
        out["proposed_command"] = redispatch

    if args.json or getattr(args, "output", None) == "json":
        print(json.dumps(out, sort_keys=True))
    else:
        print(f"{plan.verdict.value}  {plan.reason}")
        if plan.resume_sha:
            print(f"  re-entry SHA : {plan.resume_sha}")
        if plan.residual:
            print(f"  residual     : {', '.join(plan.residual)}")
        if redispatch:
            print(f"  propose      : {redispatch}")
            if proposed_now:
                print(f"  (recorded RESUME_PROPOSED on run {rid}'s ledger — idempotent)")
            elif plan.already_proposed:
                print(f"  (a resume was already proposed for {rid} — not re-proposing)")
    return _RESUME_EXIT_CODES.get(plan.verdict.value, _RESUME_EXIT_UNKNOWN)


# ---------------------------------------------------------------------------
# rewind  (the conversation-rewind verdict — docs/164 F1.5: backjump + no-good note)
#   (full prose: docs/CLI.md § "rewind  (the conversation-rewind verdict — docs/164 F1.5: ba")
_REWIND_EXITS = ExitMap(
    {"REWIND": 0, "NO_REWIND": 0, "UNANCHORED": 3},
    unknown=4,
)
_REWIND_EXIT_CODES = _REWIND_EXITS.codes
_REWIND_EXIT_UNKNOWN = _REWIND_EXITS.unknown
_REWIND_EXIT_CONTRACT_ERROR = _REWIND_EXITS.contract_error


def cmd_rewind(args: argparse.Namespace) -> int:
    """`dos rewind` — the conversation-rewind verdict (docs/164 F1.5). Read-only/advisory.

    Detail: docs/CLI.md § cmd_rewind.
    """
    _apply_workspace(args)
    from dos import intent_ledger, rewind as _rewind, rewind_evidence
    from dos.completion import Convergence
    from dos.resume import Resume
    cfg = _config.active()
    rid = (args.run_id or "").strip()
    if not rid:
        print("error: --run-id is required (the intent-ledger key)", file=sys.stderr)
        return _REWIND_EXIT_CONTRACT_ERROR

    # The FIRE: which ground-truth stop signal the boundary observed. A richer driver
    # computes it (resume.resume_plan → DIVERGED, completion.convergence → THRASHING);
    # the bare CLI lets the operator assert it. Default (none) → NO_REWIND (no stop).
    fire_choice = (getattr(args, "fire", "") or "").strip().upper()
    if fire_choice == "DIVERGED":
        fire = rewind_evidence.fire_from(resume_verdict=Resume.DIVERGED)
    elif fire_choice == "THRASHING":
        fire = rewind_evidence.fire_from(convergence_verdict=Convergence.THRASHING)
    elif fire_choice == "STARVED":
        fire = rewind_evidence.fire_from(convergence_verdict=Convergence.STARVED)
    else:
        fire = rewind_evidence.fire_from()  # no stop signal → NO_REWIND

    # Replay the ledger (schema-gated, torn-tail tolerant) → the minted checkpoint.
    entries = intent_ledger.read_all(rid, cfg=cfg)
    state = intent_ledger.replay(entries) if entries else None
    checkpoint = (
        rewind_evidence.read_checkpoint(state)
        if state is not None
        else _rewind.SuspendCheckpoint.absent()
    )
    # The transcript turns (host-owned, beside the ledger). Missing → no turns →
    # UNANCHORED (fail-closed: refuse to rewind to a turn we cannot confirm was stamped).
    turns = rewind_evidence.gather_turns(rid, cfg=cfg)

    # The no-good note tokens: ONLY closed kernel verdict tokens whose STRUCTURED fields
    #   (full prose: docs/CLI.md § "The no-good note tokens: ONLY closed kernel verdict tokens w")
    tokens: list = []
    if fire_choice == "DIVERGED":
        tokens.append(_rewind.VerdictToken(kind="DIVERGED"))

    plan = _rewind.rewind_plan(
        turns, checkpoint, fire, verdict_tokens=tuple(tokens))

    out = plan.to_dict()
    out["run_id"] = rid
    if getattr(args, "json", False):
        print(json.dumps(out, sort_keys=True))
    else:
        print(f"{plan.verdict.value}  {plan.reason}")
        if plan.verdict is _rewind.Rewind.REWIND:
            print(f"  rewind to turn : {plan.rewind_to_turn} "
                  f"(digest {plan.transcript_digest[:12]})")
            print(f"  drop turns     : "
                  f"{list(plan.dropped_turns) or '(none)'}")
        note_lines = plan.no_good_note.render_lines()
        if note_lines:
            print("  no-good note (byte-clean — verdict + env bytes only):")
            for ln in note_lines:
                print(f"    {ln}")

    return _REWIND_EXIT_CODES.get(plan.verdict.value, _REWIND_EXIT_UNKNOWN)


# ---------------------------------------------------------------------------
# complete  (the live completion verdict — docs/117: the end of working-in-passes)
#   (full prose: docs/CLI.md § "complete  (the live completion verdict — docs/117: the end o")
_COMPLETE_EXITS = ExitMap(
    {"COMPLETE": 0, "INCOMPLETE": 3, "INDETERMINATE": 4, "UNDERDECLARED": 5},
    unknown=6,
)
_COMPLETE_EXIT_CODES = _COMPLETE_EXITS.codes
_COMPLETE_EXIT_UNKNOWN = _COMPLETE_EXITS.unknown
_COMPLETE_EXIT_CONTRACT_ERROR = _COMPLETE_EXITS.contract_error


def cmd_complete(args: argparse.Namespace) -> int:
    """Is the WHOLE declared job verifiably done? — the live completion verdict (docs/117).

    Detail: docs/CLI.md § cmd_complete.
    """
    _apply_workspace(args)
    from dos import intent_ledger, completion as _completion, resume_evidence
    cfg = _config.active()
    rid = (args.run_id or "").strip()
    if not rid:
        print("error: --run-id is required (the intent-ledger key)", file=sys.stderr)
        return _COMPLETE_EXIT_CONTRACT_ERROR

    entries = intent_ledger.read_all(rid, cfg=cfg)
    if not entries:
        # No ledger at all → no declared extent to close against. INDETERMINATE is
        # the honest floor (we can neither confirm done nor name a residual).
        verdict = _completion.CompletionVerdict(
            state=_completion.Completion.INDETERMINATE,
            reason=(f"no intent ledger for run {rid!r} (no "
                    f"{intent_ledger.INTENT_JSONL_NAME} in its run-dir) — no declared "
                    f"extent to adjudicate completion against"),
            run_id=rid,
        )
    else:
        state = intent_ledger.replay(entries)
        anc = resume_evidence.gather_ancestry(
            state, cfg=cfg,
            lane_advanced_past_resume=getattr(args, "diverged", False),
        )
        verdict = _completion.classify(state, anc)

    if args.json or getattr(args, "output", None) == "json":
        print(json.dumps(verdict.to_dict(), sort_keys=True))
    else:
        print(f"{verdict.state.value}  {verdict.reason}")
        frac = verdict.fraction_done
        if frac is not None:
            print(f"  verified     : {len(verdict.verified)}/{len(verdict.declared)} "
                  f"({frac * 100:.0f}%)")
        if verdict.residual:
            print(f"  residual     : {', '.join(verdict.residual)}")
    return _COMPLETE_EXIT_CODES.get(verdict.state.value, _COMPLETE_EXIT_UNKNOWN)


# ---------------------------------------------------------------------------
# status  (the folded fact: one fail-closed digest of a run — docs/120 Phase 2)
#   (full prose: docs/CLI.md § "status  (the folded fact: one fail-closed digest of a run —")
_STATUS_EXITS = _LIVENESS_EXITS          # status reuses the liveness scheme verbatim
_STATUS_EXIT_CODES = _STATUS_EXITS.codes
_STATUS_EXIT_UNKNOWN = _STATUS_EXITS.unknown
_STATUS_EXIT_CONTRACT_ERROR = _STATUS_EXITS.contract_error


def _run_region(run_id: str, cfg: _config.SubstrateConfig) -> tuple[str, ...]:
    """The held-lease region (path globs) for `run_id`, or () if it holds no lease.

    Detail: docs/CLI.md § _run_region.
    """
    from dos import lane_journal
    try:
        leases = lane_journal.replay(lane_journal.read_all(path=cfg.paths.lane_journal))
    except Exception:  # noqa: BLE001 — a bad journal must not crash the digest
        return ()
    for lease in leases:
        if str(lease.get("run_id") or "") == run_id:
            tree = lease.get("tree")
            if isinstance(tree, (list, tuple)):
                return tuple(str(g) for g in tree)
            return ()
    return ()


def cmd_arg_provenance(args: argparse.Namespace) -> int:
    """The argument-provenance verdict: did the model MINT an id/FK arg, or RESOLVE it?
    (docs/143 R1 — the EnterpriseOps-Gym survivor binding.)

    Detail: docs/CLI.md § cmd_arg_provenance.
    """
    import json as _json
    from dos.arg_provenance import (
        CorpusSource, EnvBlob, PriorResults, ToolArg, ToolCall, classify_call,
    )

    def _is_mutating(tool: str) -> bool:
        # A minimal write-verb stem heuristic so the kernel CLI needs no consumer import; a
        # real wrapper (benchmark.enterpriseops.dos_react) carries the fuller set + schema.
        stems = (
            "create", "update", "delete", "remove", "add", "send", "set", "assign",
            "insert", "patch", "put", "post", "modify", "edit", "submit", "close",
            "resolve", "cancel", "approve", "reject", "schedule", "move", "rename",
            "upload", "share", "grant", "revoke", "transfer", "merge", "link", "attach",
        )
        n = (tool or "").strip().lower().replace("-", "_").replace(".", "_")
        return any(s in n.split("_") for s in stems) or any(n.startswith(s) for s in stems)

    try:
        call_args = _json.loads(args.args) if args.args else {}
        if not isinstance(call_args, dict):
            raise ValueError("--args must be a JSON object")
    except (ValueError, _json.JSONDecodeError) as e:
        print(f"error: --args is not a JSON object ({e})", file=sys.stderr)
        return 2

    blobs: list = []
    if args.task_text:
        blobs.append(EnvBlob(text=args.task_text, source=CorpusSource.TASK_TEXT))
    priors = list(args.prior or [])
    if args.prior_file:
        try:
            with open(args.prior_file, encoding="utf-8") as f:
                priors.extend(line for line in f.read().splitlines() if line.strip())
        except OSError as e:
            print(f"error: cannot read --prior-file ({e})", file=sys.stderr)
            return 2
    for p in priors:
        blobs.append(EnvBlob(text=p, source=CorpusSource.TOOL_RESULT))

    new_keys = {k.lower() for k in (args.new_key or [])}
    targs = tuple(
        ToolArg(name=k, value=v, is_reference=(k.lower() not in new_keys))
        for k, v in call_args.items()
    )
    is_mut = (not args.read) and _is_mutating(args.tool)
    call = ToolCall(tool_name=args.tool, args=targs, is_mutating=is_mut)
    verdict = classify_call(call, PriorResults(blobs=tuple(blobs)))

    if getattr(args, "json", False):
        print(_json.dumps(verdict.to_dict(), indent=2))
    else:
        head = "BELIEVE" if verdict.believe else "UNSUPPORTED"
        print(f"{head}  {args.tool}  (mutating={is_mut})")
        print(f"  {verdict.reason}")
        for a in verdict.args:
            print(f"    {a.stance.value:<11} {a.arg_name}={a.value_repr}  {a.reason}")
    return 0 if verdict.believe else 3


def cmd_status(args: argparse.Namespace) -> int:
    """Fold a run's four shipped verdicts into one fail-closed digest (docs/120 Phase 2).

    Detail: docs/CLI.md § cmd_status.
    """
    _apply_workspace(args)
    import time
    from dos import (intent_ledger, liveness, resume as _resume,
                     resume_evidence, run_id as _run_id, status as _status)

    rid = (args.run_id or "").strip()
    if not rid:
        print("error: a run-id (RID-…) is required — the digest is keyed on it",
              file=sys.stderr)
        return _STATUS_EXIT_CONTRACT_ERROR
    started_ms = _run_id.ts_ms_of(rid)
    if started_ms is None:
        print(f"error: {rid!r} is not a valid run-id token "
              f"(expected an RID-… minted by `dos run-id mint`)", file=sys.stderr)
        return _STATUS_EXIT_CONTRACT_ERROR

    cfg = _config.active()
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)

    # ── read 2 (FIRST, it sources start_sha for read 1): the intent ledger. ──
    # Fail-closed: no ledger → an empty LedgerState, never a raise (the no-intent run
    # is a valid "nothing declared, nothing verified" fact — docs/120 §3).
    entries = intent_ledger.read_all(rid, cfg=cfg)
    ledger_state = (intent_ledger.replay(entries) if entries
                    else intent_ledger.LedgerState(run_id=rid))

    # start_sha: explicit --start-sha wins, else the ledger's declared anchor, else "".
    start_sha = (getattr(args, "start_sha", "") or "").strip() or ledger_state.start_sha

    # ── read 1: liveness — the SAME git+journal evidence-gather as cmd_liveness. ──
    commits = _git_delta_count(start_sha, cfg)
    lane = getattr(args, "lane", None)
    loop_ts = getattr(args, "loop_ts", None)
    lease_key = (str(loop_ts), str(lane)) if lane and loop_ts else None
    jd = _journal_delta(cfg, started_ms=started_ms, now_ms=now_ms, lease_key=lease_key)
    ev = liveness.ProgressEvidence(
        run_started_ms=started_ms,
        now_ms=now_ms,
        commits_since_start=commits,
        journal_events_since=jd.events_since_start,
        last_heartbeat_age_ms=(
            args.last_heartbeat_age_ms if args.last_heartbeat_age_ms is not None
            else jd.newest_heartbeat_age_ms
        ),
    )
    liveness_verdict = liveness.classify(ev)

    # ── read 3: the held-lease region (the spine join), or () if no lease. ──
    region = _run_region(rid, cfg)

    # ── read 4: resume — CONDITIONAL on the stopped predicate (docs/142 §3.4). ──
    #   (full prose: docs/CLI.md § "── read 4: resume — CONDITIONAL on the stopped predicate (do")
    stopped = bool(ledger_state.suspended
                   or liveness_verdict.verdict is liveness.Liveness.STALLED)
    if getattr(args, "stopped", False):
        stopped = True
    if getattr(args, "live", False):
        stopped = False
    resume_plan = None
    if stopped and ledger_state.has_intent:
        anc = resume_evidence.gather_ancestry(ledger_state, cfg=cfg)
        resume_plan = _resume.resume_plan(ledger_state, anc)

    digest = _status.status_digest(
        run_id=rid,
        ledger_state=ledger_state,
        liveness_verdict=liveness_verdict,
        live_region=region,
        resume_plan=resume_plan,
    )

    if args.json or getattr(args, "output", None) == "json":
        print(json.dumps(digest.to_dict(), sort_keys=True))
    else:
        lv = digest.liveness
        print(f"{lv.verdict.value}  {lv.reason}")
        p = digest.progress
        print(f"  progress     : {p.verified_count}/{p.declared_count} verified"
              + (f" ({', '.join(p.verified_steps)})" if p.verified_steps else ""))
        print(f"  region       : {', '.join(digest.region) if digest.region else '(no lease)'}")
        if digest.resume is not None:
            print(f"  resume       : {digest.resume.verdict.value}  {digest.resume.reason}")
            if digest.resume.residual:
                print(f"  residual     : {', '.join(digest.resume.residual)}")
        elif not stopped:
            print("  resume       : (live — a resume verdict is a stopped-run question)")
        else:
            # Stopped, but no INTENT to ground a residual on — resume is correctly
            # absent (the UNRESUMABLE floor), distinct from a live run.
            print("  resume       : (stopped, but no declared intent to resume)")
    return _STATUS_EXIT_CODES.get(liveness_verdict.verdict.value, _STATUS_EXIT_UNKNOWN)


def _git_delta_count(start_sha: str, cfg: _config.SubstrateConfig) -> int:
    """Commits since `start_sha` on the served workspace (the LVN git rung).

    Detail: docs/CLI.md § _git_delta_count.
    """
    from dos import git_delta
    return git_delta.count_commits_since(start_sha, root=cfg.paths.root)


def _journal_delta(cfg: _config.SubstrateConfig, *, started_ms: int, now_ms: int, lease_key):
    """Fold the lane journal for this run's lease (the LVN Phase-2 journal rung).

    Detail: docs/CLI.md § _journal_delta.
    """
    from dos import journal_delta, lane_journal
    try:
        entries = lane_journal.read_all(path=cfg.paths.lane_journal)
    except Exception:  # noqa: BLE001 — a bad journal must not crash the verdict
        return journal_delta.JournalDelta(0, None, False)
    return journal_delta.fold_since(
        entries, run_started_ms=started_ms, now_ms=now_ms, lease_key=lease_key,
    )


# ---------------------------------------------------------------------------
# loop  (the supervisor: keep N dispatch-loops alive across the lane roster)
#   (full prose: docs/CLI.md § "loop  (the supervisor: keep N dispatch-loops alive across th")
def _epoch_ms_iso(ts) -> "int | None":
    """Absolute epoch-ms of an ISO timestamp; None on any unparseable/missing input.

    Detail: docs/CLI.md § _epoch_ms_iso.
    """
    if not ts:
        return None
    import datetime as _dt
    try:
        parsed = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    try:
        return int(parsed.timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return None


def _heartbeat_age_ms(ts, now_ms: int) -> "int | None":
    """`now_ms − epoch_ms(ts)`, clamped at 0; None when `ts` is unparseable.

    Detail: docs/CLI.md § _heartbeat_age_ms.
    """
    epoch = _epoch_ms_iso(ts)
    if epoch is None:
        return None
    return max(0, now_ms - epoch)


def _supervise_evidence(cfg: _config.SubstrateConfig, *, target, now_ms, pending_lanes=frozenset(), policy=None):
    """Gather the per-tick `supervise.SuperviseEvidence` for a workspace's roster.

    Detail: docs/CLI.md § _supervise_evidence.
    """
    from dos import lane_journal, liveness, supervise

    # 1. roster — concurrent lanes then exclusive lanes, de-duped, declaration
    # order (no dispatch_top on this base; build inline). This is the SPAWN-walk
    # order the verdict relies on being deterministic.
    seen = set()
    roster = []
    for lane in (*cfg.lanes.concurrent, *cfg.lanes.exclusive):
        if lane and lane not in seen:
            seen.add(lane)
            roster.append(lane)

    # The DERIVED-CLAIM budget (docs/283): which roster lanes are fungible auto-pick
    #   (full prose: docs/CLI.md § "The DERIVED-CLAIM budget (docs/283): which roster lanes are")
    autopick_set = {a for a in cfg.lanes.autopick if a and not cfg.lanes.is_exclusive(a)}
    budget_on = policy is not None and getattr(policy, "max_concurrency", None) is not None

    # 2. live leases — replay the WAL (read-only; a missing journal is []), keyed
    #   (full prose: docs/CLI.md § "2. live leases — replay the WAL (read-only; a missing journa")
    try:
        entries = lane_journal.read_all(path=cfg.paths.lane_journal)
    except Exception:  # noqa: BLE001 — a bad journal must not crash the gather
        entries = []
    try:
        leases = lane_journal.replay(entries)
    except Exception:  # noqa: BLE001
        leases = []
    leases_by_lane = {}
    for l in leases:
        lane_name = str(l.get("lane") or "")
        prior = leases_by_lane.get(lane_name)
        if prior is None:
            leases_by_lane[lane_name] = l
            continue
        # Same-lane collision: keep the newer acquire (defined tie-break).
        if (_epoch_ms_iso(l.get("acquired_at")) or 0) >= (
            _epoch_ms_iso(prior.get("acquired_at")) or 0
        ):
            leases_by_lane[lane_name] = l

    # The DERIVED-CLAIM roster extension (docs/283), only when a budget is declared:
    #   (full prose: docs/CLI.md § "The DERIVED-CLAIM roster extension (docs/283), only when a b")
    repeatable_lanes = set(autopick_set)
    synthetic_handle = None
    if budget_on:
        for lane_name in leases_by_lane:
            if lane_name and lane_name not in seen and not cfg.lanes.is_exclusive(lane_name):
                seen.add(lane_name)
                roster.append(lane_name)
                repeatable_lanes.add(lane_name)
        # The synthetic free auto-pick handle. Named distinctly so it never shadows a
        # real lane; empty tree (its claim is derived per-pick by the arbiter). If a
        # live lease already happens to use this name, skip the synthetic one (the
        # real lease's state wins).
        synthetic_handle = "auto"
        while synthetic_handle in seen:
            synthetic_handle += "*"
        seen.add(synthetic_handle)
        roster.append(synthetic_handle)
        repeatable_lanes.add(synthetic_handle)

    # 3. one LaneLiveness per declared lane.
    lane_livenesses = []
    for lane in roster:
        tree = tuple(cfg.lanes.tree_for(lane))
        is_exclusive = cfg.lanes.is_exclusive(lane)
        repeatable = lane in repeatable_lanes and not is_exclusive
        pending = lane in pending_lanes
        lv = None
        spinning_age_ms = None  # set ONLY for a SPINNING lane (acting-on-spin, docs/90 §5)
        lease = leases_by_lane.get(lane)
        if lease is not None and not pending:
            # A live lease records no start SHA, so the commit rung is 0 (the
            # honest floor); the journal rung + heartbeat age carry the signal.
            # started_ms = the lease's acquired_at (fallback: now, so a stampless
            # lease is judged too-young rather than ancient).
            started_ms = _epoch_ms_iso(lease.get("acquired_at"))
            if started_ms is None:
                started_ms = now_ms
            # Fold the journal for THIS lease (the LVN P2 rung) FIRST — its
            # newest-beat age (from the entry's own append-ts) is the TRUSTED
            # heartbeat; the lease's self-reported heartbeat_at is only a fallback.
            lease_key = (str(lease.get("loop_ts") or ""), lane)
            try:
                jd = _journal_delta(cfg, started_ms=started_ms, now_ms=now_ms,
                                    lease_key=lease_key)
                events_since = jd.events_since_start
                journal_hb_age = jd.newest_heartbeat_age_ms
            except Exception:  # noqa: BLE001 — degrade to no journal signal
                events_since = 0
                journal_hb_age = None
            hb_age = (
                journal_hb_age if journal_hb_age is not None
                else _heartbeat_age_ms(
                    lease.get("heartbeat_at") or lease.get("acquired_at"), now_ms)
            )
            try:
                ev = liveness.ProgressEvidence(
                    run_started_ms=started_ms,
                    now_ms=now_ms,
                    commits_since_start=0,
                    journal_events_since=events_since,
                    last_heartbeat_age_ms=hb_age,
                )
                lv = liveness.classify(ev).verdict
            except Exception:  # noqa: BLE001 — an unclassifiable lease reads as FREE
                lv = None
            # Acting-on-spin evidence (docs/90 §5): a SPINNING lane carries HOW LONG
            #   (full prose: docs/CLI.md § "Acting-on-spin evidence (docs/90 §5): a SPINNING lane carrie")
            if lv == liveness.Liveness.SPINNING:
                spinning_age_ms = hb_age
        # else: no live lease (FREE) or pending → liveness/age stay None.
        lane_livenesses.append(
            supervise.LaneLiveness(
                lane=lane, liveness=lv, tree=tree,
                is_exclusive=is_exclusive, pending=pending,
                spinning_age_ms=spinning_age_ms, repeatable=repeatable))

    # 4. freeze the roster-level evidence.
    return supervise.SuperviseEvidence(lanes=tuple(lane_livenesses), target=target)


def cmd_loop(args: argparse.Namespace) -> int:
    """The SUPERVISOR (docs/99, SUP): keep N dispatch-loops alive across the roster.

    Detail: docs/CLI.md § cmd_loop.
    """
    _apply_workspace(args)
    import dataclasses
    import time
    from dos import supervise

    cfg = _config.active()
    # The standing population policy comes from `dos.toml [supervise]` (the
    #   (full prose: docs/CLI.md § "The standing population policy comes from `dos.toml [supervi")
    base_policy = cfg.supervise
    # `--target` and `--max-concurrency` each override just their field for THIS run
    #   (full prose: docs/CLI.md § "`--target` and `--max-concurrency` each override just their")
    overrides = {}
    if args.target is not None:
        overrides["target"] = args.target
    if getattr(args, "max_concurrency", None) is not None:
        overrides["max_concurrency"] = args.max_concurrency
    policy = dataclasses.replace(base_policy, **overrides) if overrides else base_policy
    target = policy.target

    def _tick(tick_now_ms: int):
        ev = _supervise_evidence(cfg, target=target, now_ms=tick_now_ms, policy=policy)
        return supervise.supervise(ev, policy)

    def _emit(v) -> None:
        if args.json or getattr(args, "output", None) == "json":
            print(json.dumps(v.to_dict(), sort_keys=True))
            return
        # Human + greppable. Lead with the header so --watch shows a changing top
        # line; then one row per emitted plan, SPAWN carrying the launch command.
        print(f"SUPERVISE {v.verdict.value}: "
              f"alive {v.alive}/{target}, admissible {v.admissible}")
        print(v.reason)
        for p in v.spawn:
            print(f"spawn {p.lane}  — run: "
                  + policy.worker_launch_template.format(lane=p.lane))
        for p in v.reap:
            print(f"reap {p.lane}  (STALLED — the supervisor driver scavenges; "
                  f"or release its lease manually)")
        for p in v.flag:
            print(f"flag {p.lane}  ({p.reason})")
        # Acting-on-spin (docs/90 §5): a *proposed* halt of a live-but-stuck spinner.
        #   (full prose: docs/CLI.md § "Acting-on-spin (docs/90 §5): a *proposed* halt of a live-but")
        for p in v.proposed_halt:
            print(f"propose-halt {p.lane}  ({p.reason}) — enact with: "
                  f"dos halt --handle <run> --lane {p.lane}")

    # --now-ms pins the clock; absent ⇒ wall clock, read here at the boundary.
    if args.now_ms is not None:
        now_ms = args.now_ms
    else:
        now_ms = int(time.time() * 1000)

    if not getattr(args, "watch", False):
        _emit(_tick(now_ms))
        return 0

    # --watch: re-emit on a cadence until the operator interrupts. Still emit-only.
    # A pinned --now-ms makes every tick deterministic (used in tests); otherwise
    # each tick re-reads the wall clock.
    try:
        while True:
            tick_now_ms = (args.now_ms if args.now_ms is not None
                           else int(time.time() * 1000))
            _emit(_tick(tick_now_ms))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# watch  (the push-model watchdog: poll liveness per-run + propose halts — docs/101)
#   (full prose: docs/CLI.md § "watch  (the push-model watchdog: poll liveness per-run + pro")
def _load_watchdog():
    """Resolve the watchdog DRIVER by name at the call boundary — never a static import.

    Detail: docs/CLI.md § _load_watchdog.
    """
    import importlib

    try:
        return importlib.import_module("dos.drivers.watchdog")
    except ModuleNotFoundError as e:  # pragma: no cover - the driver ships in-tree
        if (e.name or "") in ("dos.drivers.watchdog", "dos.drivers"):
            raise ValueError(
                "the watchdog driver (dos.drivers.watchdog) is not installed; "
                "`dos watch` needs it — reinstall the package") from None
        raise  # a broken INTERNAL import of the driver is a real bug, not "absent"


def _parse_track_spec(spec: str, watchdog):
    """Parse a `--track run_id[:start_sha[:lane[:loop_ts[:handle]]]]` spec.

    Detail: docs/CLI.md § _parse_track_spec.
    """
    parts = (spec or "").split(":")
    rid = parts[0].strip() if parts else ""
    if not rid:
        raise ValueError(f"--track spec {spec!r} has no run-id (the first field)")
    def _at(i):
        return parts[i].strip() if len(parts) > i else ""
    return watchdog.TrackedRun(
        run_id=rid, start_sha=_at(1), lane=_at(2), loop_ts=_at(3), handle=_at(4))


def _load_memory_recall():
    """Resolve the memory-recall DRIVER by name — never a static import.

    Detail: docs/CLI.md § _load_memory_recall.
    """
    import importlib

    try:
        return importlib.import_module("dos.drivers.memory_recall")
    except ModuleNotFoundError as e:  # pragma: no cover - the driver ships in-tree
        if (e.name or "") in ("dos.drivers.memory_recall", "dos.drivers"):
            raise ValueError(
                "the memory-recall driver (dos.drivers.memory_recall) is not "
                "installed; `dos memory` needs it — reinstall the package") from None
        raise  # a broken INTERNAL import of the driver is a real bug, not "absent"


def cmd_watch(args: argparse.Namespace) -> int:
    """The WATCHDOG (docs/101): poll `liveness` per tracked run + propose halts.

    Detail: docs/CLI.md § cmd_watch.
    """
    _apply_workspace(args)
    _ensure_home_if_persisting()  # a journaled OP_HALT is a DOS emission
    import time
    try:
        watchdog = _load_watchdog()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    cfg = _config.active()
    budget_ms = args.budget_ms
    command = args.command or ""
    repropose_ms = (args.repropose_ms if args.repropose_ms is not None
                    else watchdog.DEFAULT_REPROPOSE_MS)

    runs = []
    for spec in (args.track or []):
        try:
            tr = _parse_track_spec(spec, watchdog)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        runs.append(dataclasses.replace(tr, budget_ms=budget_ms, stop_command=command))
    if args.discover:
        runs.extend(watchdog.discover_tracked_runs(cfg, budget_ms=budget_ms))
    if not runs:
        print("error: no runs to watch — pass --track <run_id[:sha[:lane[:loop_ts[:handle]]]]> "
              "(repeatable) and/or --discover", file=sys.stderr)
        return 2

    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)

    def _emit(verdicts, actions) -> None:
        if args.json or getattr(args, "output", None) == "json":
            print(json.dumps({
                "watched": len(runs),
                "verdicts": {rid: v.verdict.value for rid, v in verdicts.items()},
                "proposed_halts": actions.proposed_halts,
                "advancing": actions.advancing,
                "spinning": actions.spinning,
                "stalled_within_budget": actions.stalled_within_budget,
                "skipped": actions.skipped,
            }, sort_keys=True))
            return
        print(f"WATCH {len(runs)} run(s): "
              f"{len(actions.advancing)} advancing, {len(actions.spinning)} spinning, "
              f"{len(actions.proposed_halts)} halt proposed")
        for rid in actions.proposed_halts:
            tr = next((r for r in runs if r.run_id == rid), None)
            cmd = (tr.stop_command if tr else "") or "(no command — stop by hand)"
            print(f"  HALT proposed: {rid} ({verdicts[rid].verdict.value}) — run: {cmd}")
        for rid in actions.stalled_within_budget:
            print(f"  stalled-within-budget: {rid} (not yet halted)")

    # A single tick (default) or the bounded/looping cadence. `proposed` persists
    # across ticks within this process so a spinning run earns ONE proposal per
    # repropose window (the idempotence memory).
    proposed: dict = {}
    if not args.watch and args.max_ticks is None:
        verdicts, actions = watchdog.tick(
            cfg, runs, now_ms=now_ms, proposed=proposed,
            repropose_ms=repropose_ms)
        _emit(verdicts, actions)
        return 0

    # --watch (unbounded) or --max-ticks N (bounded): re-poll on a cadence.
    ticks = 0
    try:
        while True:
            tick_now_ms = (args.now_ms if args.now_ms is not None
                           else int(time.time() * 1000))
            verdicts, actions = watchdog.tick(
                cfg, runs, now_ms=tick_now_ms, proposed=proposed,
                repropose_ms=repropose_ms)
            _emit(verdicts, actions)
            ticks += 1
            if args.max_ticks is not None and ticks >= args.max_ticks:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    return 0


# ---------------------------------------------------------------------------
# lease  (cross-process archive lock)
# ---------------------------------------------------------------------------
def cmd_lease(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    _ensure_home_if_persisting()  # the archive lock is a DOS emission → first-write hook
    from dos import archive_lock
    if args.lease_cmd == "acquire":
        return archive_lock.cmd_acquire(args)
    if args.lease_cmd == "release":
        return archive_lock.cmd_release(args)
    return archive_lock.cmd_status(args)


# ---------------------------------------------------------------------------
# lease-lane  (the lane-lease WRITE-BACK over the pure arbiter — docs/96)
#   (full prose: docs/CLI.md § "lease-lane  (the lane-lease WRITE-BACK over the pure arbiter")
def cmd_lease_lane(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    _ensure_home_if_persisting()  # a journaled lane lease is a DOS emission
    from dos import lane_lease
    cfg = _config.active()

    if args.lease_lane_cmd == "live":
        leases = lane_lease.live_leases(cfg)
        print(json.dumps(leases, indent=2 if args.pretty else None,
                         sort_keys=True, default=str))
        return 0

    if args.lease_lane_cmd == "release":
        ok = lane_lease.release(cfg, lane=args.lane, owner=args.owner,
                                loop_ts=args.loop_ts or "")
        print(json.dumps({"released": ok, "lane": args.lane,
                          "owner": args.owner}, sort_keys=True))
        return 0 if ok else 1

    if args.lease_lane_cmd == "heartbeat":
        # Refresh a HELD lease — the writer that makes liveness SPINNING reachable
        #   (full prose: docs/CLI.md § "Refresh a HELD lease — the writer that makes liveness SPINNI")
        ok = lane_lease.heartbeat(cfg, lane=args.lane, owner=args.owner,
                                  loop_ts=args.loop_ts or "",
                                  coalesce_within_s=args.coalesce_within_s or 0.0)
        print(json.dumps({"beat": ok, "lane": args.lane,
                          "owner": args.owner}, sort_keys=True))
        return 0 if ok else 1

    if args.lease_lane_cmd == "spawn":
        # Record an INTENT to take this lane — the FIRST thing a launcher does, before
        #   (full prose: docs/CLI.md § "Record an INTENT to take this lane — the FIRST thing a launc")
        run_id = (
            (getattr(args, "run_id", "") or "").strip()
            or os.environ.get("CID_RUN_ID", "").strip()
            or os.environ.get("DISPATCH_RUN_ID", "").strip()
        )
        result = lane_lease.spawn(
            cfg, lane=args.lane, owner=args.owner or "",
            loop_ts=args.loop_ts or "", run_id=run_id, reason=args.reason or "",
        )
        print(json.dumps({"recorded": result.recorded, "lane": result.lane,
                          "loop_ts": result.loop_ts, "holder": result.holder},
                         sort_keys=True))
        return 0 if result.recorded else 1

    # acquire
    tree = list(args.tree or [])
    if not tree and args.lane:
        tree = cfg.lanes.tree_for(args.lane)
    try:
        extra = json.loads(args.leases) if args.leases else []
    except json.JSONDecodeError as e:
        print(f"error: --leases is not valid JSON ({e}); expected a JSON array "
              f"of live-lease objects (e.g. '[]')", file=sys.stderr)
        return 2
    if not isinstance(extra, list):
        print("error: --leases must be a JSON array of lease objects.",
              file=sys.stderr)
        return 2
    # Resolve the CID spine id at the BOUNDARY (docs/137): explicit flag, else the
    #   (full prose: docs/CLI.md § "Resolve the CID spine id at the BOUNDARY (docs/137): explici")
    run_id = (
        (getattr(args, "run_id", "") or "").strip()
        or os.environ.get("CID_RUN_ID", "").strip()
        or os.environ.get("DISPATCH_RUN_ID", "").strip()
    )
    try:
        result = lane_lease.acquire(
            cfg, lane=args.lane, kind=args.kind, tree=tree, owner=args.owner,
            loop_ts=args.loop_ts or "", extra_leases=extra,
            retries=args.retries, retry_interval=args.retry_interval,
            run_id=run_id,
        )
    except TimeoutError as e:
        # could not take the lane-lease mutex within budget — a contended lock,
        # not a kernel fault. Report and exit non-acquire (distinct from refuse so
        # an orchestrator can tell "lock busy, retry" from "lane genuinely taken").
        print(json.dumps({"outcome": "lock-busy", "lane": args.lane,
                          "reason": str(e)}, sort_keys=True), file=sys.stderr)
        return 4
    d = result.decision
    out = d.to_dict()
    out["journaled"] = result.journaled
    out["owner"] = result.owner
    if run_id:
        out["run_id"] = run_id  # the join key recorded on the lease (docs/137)
    print(json.dumps(out, indent=2 if args.pretty else None, sort_keys=True,
                     default=str))
    return 0 if d.outcome == "acquire" else 1


# ---------------------------------------------------------------------------
# override  (docs/296 — the operator's SELF_MODIFY override window: report or
# disarm, NEVER arm. Arming is the operator's hand on the arm file by design —
# any verb an agent's shell can call is an arming path an agent can take, so
# the asymmetry IS the security property: anyone may disarm, only the human
# arms. `status` exit code is the state: 0 armed / 1 disarmed-or-expired.)
# ---------------------------------------------------------------------------
def cmd_override(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    from dos import override_facts as _ovr
    import datetime as _dt
    cfg = _config.active()
    p = _ovr.arm_path(cfg.paths.root)

    if args.override_cmd == "disarm":
        # Always safe, for anyone — lowering the window can only restore the deny.
        existed = p.exists()
        if existed:
            try:
                p.unlink()
            except OSError as e:
                print(f"error: could not remove the arm file ({e})", file=sys.stderr)
                return 2
        print(json.dumps({"disarmed": existed, "path": str(p)}, sort_keys=True))
        return 0

    # status
    facts = _ovr.read_override(cfg.paths.root)
    now = _dt.datetime.now(_dt.timezone.utc)
    if facts is None:
        print(json.dumps({"armed": False, "path": str(p),
                          "note": "no valid arm file — the SELF_MODIFY deny "
                                  "stands (docs/296; arming is by hand)"},
                         sort_keys=True))
        return 1
    armed = now <= facts.until
    print(json.dumps({
        "armed": armed,
        "expired": not armed,
        "until": facts.until.isoformat(),
        "reason": facts.reason,
        "scope": list(facts.scope),
        "path": str(p),
    }, sort_keys=True))
    return 0 if armed else 1


# ---------------------------------------------------------------------------
# halt  (docs/99 — record a STOP DECISION for an in-flight run + propose the
#   (full prose: docs/CLI.md § "halt  (docs/99 — record a STOP DECISION for an in-flight run")
# ---------------------------------------------------------------------------
def cmd_halt(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    _ensure_home_if_persisting()  # a journaled HALT is a DOS emission
    from dos import lane_lease
    cfg = _config.active()
    result = lane_lease.halt(
        cfg,
        handle=args.handle,
        lane=args.lane or "",
        owner=args.owner or "",
        loop_ts=args.loop_ts or "",
        reason=args.reason or "",
        run_id=args.run_id or "",
        command=args.command or None,
    )
    out = {
        "recorded": result.recorded,
        "handle": result.handle,
        "command": result.command,
        "lane": result.lane,
        "loop_ts": result.loop_ts,
    }
    # --resumable (docs/107 §4) — the halt that stops a run *resumably* rather than
    #   (full prose: docs/CLI.md § "--resumable (docs/107 §4) — the halt that stops a run *resum")
    if getattr(args, "resumable", False):
        rid = (args.run_id or "").strip()
        if not rid:
            out["suspended"] = False
            out["suspend_note"] = (
                "--resumable needs --run-id (the intent-ledger key); recorded a "
                "plain HALT only — no run to suspend"
            )
        else:
            from dos import intent_ledger
            entry = intent_ledger.suspend_entry(
                reason=args.reason or (f"resumable-halt:{args.owner}"
                                       if args.owner else "resumable-halt"),
                resume_sha=getattr(args, "resume_sha", "") or "",
            )
            try:
                intent_ledger.append(rid, entry, cfg=cfg)
                out["suspended"] = True
                out["suspend_note"] = (
                    f"appended SUSPEND to run {rid}'s intent ledger — parked & "
                    f"resumable (scavenge-immune); `dos resume --run-id {rid}` to "
                    f"re-adjudicate and propose the continuation"
                )
            except OSError as e:
                out["suspended"] = False
                out["suspend_note"] = f"SUSPEND append failed: {e}"
    print(json.dumps(out, indent=2 if getattr(args, "pretty", False) else None,
                     sort_keys=True, default=str))
    return 0 if result.recorded else 1


# ---------------------------------------------------------------------------
# health  (pre-dispatch lane-health gate)
# ---------------------------------------------------------------------------
def cmd_health(args: argparse.Namespace) -> int:
    # No workspace config needed — leases + lane tree are passed in, and the
    # history is read from the cwd's git log (same as cmd_run_id, no flags).
    from dos import health
    return health.cmd_check(args)


# ---------------------------------------------------------------------------
# scout  (pre-dispatch CHOOSER — pick the activity BEFORE leasing a lane)
# ---------------------------------------------------------------------------
def cmd_scout(args: argparse.Namespace) -> int:
    from dos import scout
    return scout.cmd_check(args)


# ---------------------------------------------------------------------------
# run-id  (correlation spine)
# ---------------------------------------------------------------------------
def cmd_run_id(args: argparse.Namespace) -> int:
    from dos import run_id
    run = run_id.mint(args.process, parent=args.parent, root_id=args.root)
    print(json.dumps(run.to_dict(), indent=2, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# guard  (the headless-launch wrapper — the argv shim, docs/134 §4)
# ---------------------------------------------------------------------------
def cmd_guard(args: argparse.Namespace) -> int:
    """Frame a headless agent launch with the DOS wiring, then exec the host.

    Detail: docs/CLI.md § cmd_guard.
    """
    from dos import guard as _guard

    try:
        plan = _guard.build_guard_plan(
            list(args.host_command),
            mount_mcp=not args.no_mcp,
            verify_on_stop=args.verify_on_stop,
            add_claim_prompt=args.claim_prompt,
            strict_mcp=args.strict_mcp,
        )
    except ValueError as e:
        print(f"dos guard: {e}", file=sys.stderr)
        return 2

    if args.print_config:
        if args.json:
            print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
        else:
            print(_guard.render_plan_human(plan))
        return 0

    # Surface any honesty caveats to stderr (e.g. the Stop hook targeting a
    # not-yet-built verb) so they don't pollute the host's stdout stream.
    for note in plan.notes:
        print(f"dos guard: note: {note}", file=sys.stderr)

    # The one impure act: hand control to the host with the framed argv. Use
    # os.execvp so the host fully replaces this process (correct for a launcher —
    # signals, exit code, and stdio all belong to the host, not to dos).
    try:
        os.execvp(plan.argv[0], plan.argv)
    except FileNotFoundError:
        print(
            f"dos guard: host command not found on PATH: {plan.argv[0]!r}",
            file=sys.stderr,
        )
        return 127
    return 0  # unreachable when execvp succeeds (it replaces the process)


# ---------------------------------------------------------------------------
# hook stop  (the verify-on-stop binding — docs/134 §2/§2.2)
# ---------------------------------------------------------------------------
def _record_hook_observation(cfg, *, verb: str, outcome: str, started: float,
                             debug: bool = False, **fields) -> None:
    """Append one `hook-observation` record for a DECIDED hook call (docs/297 P3).

    The Python hook verbs' half of the kernel-owned observation contract
    (`dos.hook_observation`) — the second conforming writer beside the native
    binary. Called only AFTER a verb has decided + emitted, with the
    `time.monotonic()` the verb captured at entry, so telemetry stays strictly
    downstream of the decision (docs/99). The native-served path never reaches
    this (the binary already recorded its own observation); a DELEGATED call
    does — its record is the call's real verdict, which is exactly what the
    docs/297 denominator rule pairs with the binary's `delegate` handoff row.
    Fail-soft by construction: any fault is swallowed, so a telemetry write can
    never change a verdict, a dialect, or an exit code.
    """
    try:
        from dos import hook_observation as _hobs
        entry = _hobs.observation_entry(
            verb, outcome,
            latency_ms=round((time.monotonic() - started) * 1000.0, 3),
            run_id=os.environ.get("CID_RUN_ID", "") or "",
            **fields)
        _hobs.append(entry, cfg=cfg, debug=debug)
    except Exception:  # noqa: BLE001 — telemetry is advisory, never load-bearing
        return


def cmd_hook_stop(args: argparse.Namespace) -> int:
    """A `Stop`/`SubagentStop` hook: refuse to let an agent stop on a false done.

    Detail: docs/CLI.md § cmd_hook_stop.
    """
    from dos import claim_extract

    started = time.monotonic()

    # 1. Read the hook event from stdin (the host's contract). Any failure → let
    #    the agent stop (we never block on our own inability to read).
    event: dict = {}
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    if raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                event = parsed
        except (ValueError, TypeError):
            event = {}

    # The block object in the dialect CC honors — built once so the two emit sites
    # (and the no-op'd anti-loop path) can't drift. `_emit` writes the right bytes
    # for the active surface: `--json` → the rich object; default → the CC dialect
    # (a block dict, or nothing when there is nothing to block).
    def _emit(*, blocked: bool, reason: str = "",
              results: list[dict] | None = None, checked: int | None = None) -> None:
        if args.json:
            obj: dict[str, Any] = {"ok": not blocked}
            if blocked:
                obj["reason"] = reason
            if checked is not None:
                obj["checked"] = checked
            if results is not None:
                obj["results"] = results
            print(json.dumps(obj, sort_keys=True))
            return
        # Default: the bytes the host parses. A block is the ONLY non-empty output;
        #   (full prose: docs/CLI.md § "Default: the bytes the host parses. A block is the ONLY non-")
        if not blocked:
            return
        dialect_name = getattr(args, "dialect", None)
        if not dialect_name:
            print(json.dumps({"decision": "block", "reason": reason}, sort_keys=True))
            return
        try:
            from dos import hook_dialect as _hd
            # MOMENT.STOP, not PRE: a stop refusal fires on the host's stop/AfterAgent
            #   (full prose: docs/CLI.md § "MOMENT.STOP, not PRE: a stop refusal fires on the host's sto")
            verdict = _hd.HookVerdict(
                moment=_hd.HookMoment.STOP, action=_hd.HookAction.DENY, reason=reason)
            shaped = _hd.resolve_dialect(dialect_name).render(verdict)
        except ValueError as exc:  # unknown dialect — surface, fall back to CC bytes
            print(f"dos hook stop: unknown --dialect {dialect_name!r} ({exc}); "
                  f"emitting the Claude-Code block shape", file=sys.stderr)
            shaped = {"decision": "block", "reason": reason}
        if shaped is not None:
            print(json.dumps(shaped, sort_keys=True))

    # 2. Anti-loop guard: if CC is already in a forced continuation from a prior
    #    Stop block, bow out (let it stop) — one push-back per work stretch. `--force`
    #    lets a host that owns the loop opt out of the guard.
    if event.get("stop_hook_active") and not getattr(args, "force", False):
        _emit(blocked=False, results=[], checked=0)
        return 0

    # 3. Resolve the workspace: explicit --workspace › the event's cwd › cwd.
    #    Frontmatter claim, if the firing skill passed dos.plan/dos.phase through.
    if not getattr(args, "workspace", None):
        ev_cwd = event.get("cwd")
        if isinstance(ev_cwd, str) and ev_cwd:
            args.workspace = ev_cwd
    _apply_workspace(args)

    # 4. Gather claims. Frontmatter rung (explicit flags) + transcript rungs.
    claims = list(claim_extract.claim_from_frontmatter(
        getattr(args, "plan", None), getattr(args, "phase", None)))
    transcript_path = args.transcript or event.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        text = claim_extract.assistant_text_from_transcript(
            transcript_path, last_turns=args.last_turns)
        claims.extend(claim_extract.extract_claims(
            text, allow_heuristic=args.strict))

    if not claims:
        # Nothing the agent confidently claimed → nothing to check. Let it stop.
        _emit_help_digest(event, cfg=_config.active(), args=args)
        _emit(blocked=False, results=[], checked=0)
        _record_hook_observation(_config.active(), verb="stop", outcome="no-claims",
                                 started=started, debug=bool(getattr(args, "debug", False)))
        return 0

    # 5. Verify each claim against git (the truth syscall). Block on a NOT_SHIPPED
    #    that we're allowed to act on (confident always; heuristic only --strict).
    from dos import oracle
    cfg = _config.active()
    results: list[dict] = []
    failures: list[dict] = []
    for c in claims:
        verdict = oracle.is_shipped(c.plan, c.phase, cfg=cfg)
        row = {**c.to_dict(), "shipped": verdict.shipped,
               "source": verdict.source}
        results.append(row)
        actionable = c.confident or args.strict
        if not verdict.shipped and actionable:
            failures.append(row)

    if failures:
        # The one place a verdict becomes a control-flow signal — a PURE transform
        # of the oracle verdict into CC's block object. The HOST declines to stop;
        # DOS only computed. (docs/99 advisory-only via the user-owned hook seam;
        # docs/134 §3.1.)
        bits = "; ".join(
            f"{f['plan']} {f['phase']} (via {f['source']})" for f in failures)
        reason = (
            f"DOS verify: you claimed {bits} shipped, but git has no commit "
            f"backing {'it' if len(failures) == 1 else 'them'}. Land the commit "
            f"(with the ship-stamp grammar) or correct the claim before stopping."
        )
        _emit(blocked=True, reason=reason, results=results)
        _record_hook_observation(cfg, verb="stop", outcome="block", started=started,
                                 debug=bool(getattr(args, "debug", False)),
                                 claims_seen=len(claims),
                                 verify_source=str(failures[0].get("source") or ""),
                                 blocked_plan=str(failures[0].get("plan") or ""),
                                 blocked_phase=str(failures[0].get("phase") or ""))
        return 0  # exit 0 + {"decision":"block"} is CC's "keep working" signal

    # Every actionable claim verified. Let the agent stop.
    _emit_help_digest(event, cfg=cfg, args=args)
    _emit(blocked=False, results=results, checked=len(results))
    _record_hook_observation(cfg, verb="stop", outcome="all-verified", started=started,
                             debug=bool(getattr(args, "debug", False)),
                             claims_seen=len(claims))
    return 0


def _emit_help_digest(event: dict, *, cfg, args) -> None:
    """Print a once-per-session "this session DOS caught N things" digest to STDERR.

    Detail: docs/CLI.md § _emit_help_digest.
    """
    try:
        holder = str(event.get("session_id") or "").strip()
        if not holder:
            return
        from dos import help_summary as _help
        from dos import lane_journal as _lane_journal
        from dos import lane_lease as _lane_lease
        # Once-per-session guard: a per-session stamp under the state home. A present
        # stamp = already emitted this session → bow out silently.
        stamp_dir = _lane_lease._journal_path(cfg).parent / "help-digest"
        safe_sid = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in holder)
        stamp = stamp_dir / safe_sid
        if stamp.exists():
            return
        records = _lane_journal.read_all(path=_lane_lease._journal_path(cfg))
        summary = _help.summarize(records, holder=holder)
        if not summary.total:
            return  # nothing caught this session — no bookend
        # Write the stamp BEFORE printing so a crash mid-print can't double-emit.
        try:
            stamp_dir.mkdir(parents=True, exist_ok=True)
            stamp.write_text(summary.latest or "", encoding="utf-8")
        except Exception:  # noqa: BLE001 — a stamp-write fault must not block the digest
            pass
        print("[dos] " + _help.nudge_line(summary).replace(" this session", ""),
              file=sys.stderr)
    except Exception:  # noqa: BLE001 — best-effort; never block a true stop
        return


def cmd_hook_marker(args: argparse.Namespace) -> int:
    """A `Stop` hook: refuse a keep-alive wait-marker once its budget is spent (loop_decide §wait-marker).

    Detail: docs/CLI.md § cmd_hook_marker.
    """
    from dos import noop_streak as _nos
    from dos import marker_gate as _mg
    from dos import marker_sensor as _ms

    started = time.monotonic()
    debug = bool(getattr(args, "debug", False))

    def _dbg(msg: str) -> None:
        if debug:
            print(f"[dos hook marker] {msg}", file=sys.stderr)

    # 1. Read the hook event from stdin. Any failure → emit nothing, exit 0 (let the
    #    agent stop — we never trap a loop open on our own inability to read).
    event: dict = {}
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    if raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                event = parsed
        except (ValueError, TypeError):
            event = {}

    # 2. Resolve the workspace: explicit --workspace › the event's cwd › cwd (the
    #    cmd_hook_stop / cmd_hook_posttool path), so the tally lands under the served root.
    if not getattr(args, "workspace", None):
        ev_cwd = event.get("cwd")
        if isinstance(ev_cwd, str) and ev_cwd:
            args.workspace = ev_cwd
    _apply_workspace(args)
    cfg = _config.active()

    # 3. The session identity. No session_id (the override flag or the event) → no
    #    accumulator (an unkeyed tally cannot count a per-session marker run); let the
    #    agent stop. This is also the fail-safe for a host that never passes one.
    session_id = getattr(args, "session_id", None) or event.get("session_id")
    if not (isinstance(session_id, str) and session_id.strip()):
        _dbg("event has no session_id — no accumulator without an identity; allow stop")
        return 0

    # 3b. The forward-delta RESET path (docs/259 §Follow-up 2). A host wires
    #   (full prose: docs/CLI.md § "3b. The forward-delta RESET path (docs/259 §Follow-up 2). A")
    if getattr(args, "reset", False):
        try:
            _ms.record_reset(session_id, cfg, reason="forward-delta reset",
                             run_id=os.environ.get("CID_RUN_ID"))
            _dbg(f"recorded RESET for session {session_id} — no-op tally zeroed")
        except Exception as exc:  # noqa: BLE001 — advisory fail-safe: a reset write must not crash the turn
            _dbg(f"record_reset write error ({exc!r}) — count not reset")
        return 0

    # 3c. ⚠ The ARMING decision (docs/274 — the load-bearing fix), now a PURE call into
    #   (full prose: docs/CLI.md § "3c. ⚠ The ARMING decision (docs/274 — the load-bearing fix),")
    arm = _mg.decide(
        stop_hook_active=event.get("stop_hook_active") is True,
        loop_flag=bool(getattr(args, "loop", False)),
        env=os.environ,
        policy=cfg.marker,
    )
    if not arm.armed:
        _dbg(arm.reason)
        return 0

    # 4. Read the running no-op-turn count (ground-truth durable state, not a flag the
    #   (full prose: docs/CLI.md § "4. Read the running no-op-turn count (ground-truth durable s")
    cap = getattr(args, "max_markers", None)
    if cap is None:
        cap = cfg.marker.max_streak
    try:
        emitted = _ms.marker_count(session_id, cfg)
    except Exception as exc:  # noqa: BLE001 — advisory fail-safe: any I/O error → allow stop
        _dbg(f"marker_count read error ({exc!r}) — allow stop")
        return 0

    decision = _nos.classify(_nos.NoOpHistory(emitted), _nos.NoOpStreakPolicy(max_streak=cap))
    # The marker-grammar reason (the `wait-marker N/M — turn held open` wording the Go
    #   (full prose: docs/CLI.md § "The marker-grammar reason (the `wait-marker N/M — turn held")
    if decision.allow:
        budget_reason = f"wait-marker {decision.noop_turns}/{cap} — turn held open"
    else:
        budget_reason = (
            f"wait-marker budget exhausted ({decision.noop_turns}/{cap}) — each further "
            f"marker replays full context out of cache for no work; wait on the Bash "
            f"task-notification, OC1's orphan sweep is the safety net"
        )
    _dbg(f"emitted={emitted} max={cap} allow={decision.allow} "
         f"reason={budget_reason} ({arm.reason})")

    # docs/297 P3: one observation per ARMED budget decision (an unarmed ordinary
    # turn reached no verdict — nothing to record). Downstream of the classify;
    # the emission below follows deterministically from `decision.allow`.
    _record_hook_observation(cfg, verb="marker",
                             outcome="allow" if decision.allow else "refuse",
                             started=started, debug=debug,
                             marker_count=decision.noop_turns, max_markers=cap)

    if args.json:
        # A machine-readable surface for tooling / non-CC hosts — never the bytes CC
        # reads. Mirrors `cmd_hook_stop --json`.
        print(json.dumps(
            {"allow_marker": decision.allow, "markers_emitted": decision.noop_turns,
             "max_markers": cap, "reason": budget_reason},
            sort_keys=True))
        # In --json mode we still record an allowed marker (the durable count must
        # advance for the NEXT invocation) but emit no CC dialect.
        if decision.allow:
            try:
                _ms.record_marker(session_id, cfg, reason=budget_reason,
                                  run_id=os.environ.get("CID_RUN_ID"))
            except Exception as exc:  # noqa: BLE001 — advisory: a write failure must not crash the turn
                _dbg(f"record_marker write error ({exc!r}) — count not advanced")
        return 0

    if not decision.allow:
        # Budget spent → stop polling. Emit NOTHING (CC's "allow stop"); the loop
        # waits on the real task-notification. Do NOT record (a refused marker was
        # not emitted).
        return 0

    # Budget remains → hold the turn open one more marker. Record the marker FIRST
    #   (full prose: docs/CLI.md § "Budget remains → hold the turn open one more marker. Record")
    try:
        _ms.record_marker(session_id, cfg, reason=budget_reason,
                          run_id=os.environ.get("CID_RUN_ID"))
    except Exception as exc:  # noqa: BLE001 — advisory fail-safe
        _dbg(f"record_marker write error ({exc!r}) — allow stop (count not advanced)")
        return 0
    reason = (
        f"DOS wait-marker budget: {budget_reason}. The keep-alive turn is held "
        f"open; continue waiting on the background task's completion signal rather "
        f"than re-polling. (This block is withdrawn once the budget is spent, at "
        f"which point you should end the turn and let the task-notification re-invoke "
        f"you.)"
    )
    print(json.dumps({"decision": "block", "reason": reason}, sort_keys=True))
    return 0  # exit 0 + {"decision":"block"} is CC's "keep working" signal


def cmd_hook_posttool(args: argparse.Namespace) -> int:
    """A `PostToolUse` hook: re-surface a repeated ENV value as non-blocking context (docs/173 §4).

    Detail: docs/CLI.md § cmd_hook_posttool.
    """
    from dos import posttool_sensor as _pts

    started = time.monotonic()
    debug = bool(getattr(args, "debug", False))

    def _dbg(msg: str) -> None:
        if debug:
            print(f"[dos hook posttool] {msg}", file=sys.stderr)

    # 0. The native fast path (docs/286): if a per-platform wheel bundled the static
    #   (full prose: docs/CLI.md § "0. The native fast path (docs/286): if a per-platform wheel")
    from dos import hook_binary as _hb
    _native = _hb.try_native_hook("posttool", _hb.hook_argv_from_args(args))
    if _native is not None:
        _dbg(f"served by native dos-hook (exit {_native})")
        return _native

    # 1. Read the hook event from stdin. Any failure → emit nothing, exit 0 (we never
    #    block on our own inability to read — the advisory fail-safe).
    event: dict = {}
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    if raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                event = parsed
        except (ValueError, TypeError):
            event = {}
    if not event:
        _dbg("no/invalid stdin event — emitting nothing")
        return 0

    # 2. Resolve the workspace: explicit --workspace › the event's cwd › cwd (the
    #    cmd_hook_stop path), so the session stream lands under the served root.
    if not getattr(args, "workspace", None):
        ev_cwd = event.get("cwd")
        if isinstance(ev_cwd, str) and ev_cwd:
            args.workspace = ev_cwd
    _apply_workspace(args)
    cfg = _config.active()

    # 3. Build the StreamStep (PURE). No tool_name → nothing to record.
    step = _pts.step_from_event(event, policy=cfg.stream_policy)
    if step is None:
        _dbg("event has no tool_name — nothing to record")
        return 0

    # 4. The session identity. No session_id (the override flag or the event) → no
    #    accumulator (an unkeyed stream cannot accumulate a per-session repeat run).
    session_id = getattr(args, "session_id", None) or event.get("session_id")
    if not (isinstance(session_id, str) and session_id.strip()):
        _dbg("event has no session_id — no accumulator without an identity")
        return 0

    # 5. Replay-then-classify-then-append-ONCE, so the durable record can carry the
    #   (full prose: docs/CLI.md § "5. Replay-then-classify-then-append-ONCE, so the durable rec")
    from dos.tool_stream import StreamState, ToolStream, classify_stream

    try:
        prior = _pts.read_stream(session_id, cfg)
    except Exception as exc:  # noqa: BLE001 — advisory fail-safe: any I/O error → silent
        _dbg(f"accumulator read error ({exc!r}) — emitting nothing")
        return 0

    step_index = len(prior.steps)  # 0-based ordinal of THIS step within the session
    stream = ToolStream(prior.steps + (step,))
    verdict = classify_stream(stream, cfg.stream_policy)

    # The firing fact is recorded ONLY when the detector fired (REPEATING/STALLED) —
    # ADVANCING never stamps a verdict_state, so the presence of the field IS the
    # firing. run_id is the spine join key when resolvable from the active env, else
    # absent (an absent run_id is an honest BROKEN_LINK in the labeler, never a guess).
    fired = verdict.state in (StreamState.REPEATING, StreamState.STALLED)
    firing_run_id = os.environ.get("CID_RUN_ID") if fired else None
    try:
        _pts.append_step(
            session_id, step, cfg,
            run_id=firing_run_id,
            step_index=step_index,
            verdict_state=(verdict.state.value if fired else None),
        )
    except Exception as exc:  # noqa: BLE001 — advisory fail-safe: any write error → silent
        _dbg(f"accumulator write error ({exc!r}) — emitting nothing")
        return 0

    payload = _pts.warn_payload(verdict)
    _dbg(f"verdict={verdict.state} repeat_run={verdict.repeat_run} "
         f"step_index={step_index} run_id={firing_run_id or '-'} "
         f"warn={'yes' if payload else 'no'}")
    warn_emitted = False
    if payload is not None:
        # The ONLY thing on stdout: the host's PostToolUse dialect. `warn_payload`
        #   (full prose: docs/CLI.md § "The ONLY thing on stdout: the host's PostToolUse dialect. `w")
        try:
            from dos import hook_dialect as _hd
            renderer = _hd.resolve_dialect(getattr(args, "dialect", None))
            host_dialect = renderer.render(_hd.parse_cc(payload, moment=_hd.HookMoment.POST))
        except ValueError as exc:  # unknown dialect name — surface, emit nothing (advisory)
            _dbg(f"dialect error ({exc}) — emitting nothing")
            host_dialect = None
        if host_dialect is not None:
            print(json.dumps(host_dialect, sort_keys=True))
            warn_emitted = True
    _record_hook_observation(cfg, verb="posttool",
                             outcome="warn" if warn_emitted else "passthrough",
                             started=started, debug=debug,
                             stream_state=verdict.state.value)
    return 0


def cmd_hook_pretool(args: argparse.Namespace) -> int:
    """A `PreToolUse` hook: DENY a structurally-refused tool call BEFORE it runs (docs/191).

    Detail: docs/CLI.md § cmd_hook_pretool.
    """
    from dos import pretool_sensor as _prt

    started = time.monotonic()
    debug = bool(getattr(args, "debug", False))

    def _dbg(msg: str) -> None:
        if debug:
            print(f"[dos hook pretool] {msg}", file=sys.stderr)

    # 0. The native fast path (docs/286): if a per-platform wheel bundled the static
    #   (full prose: docs/CLI.md § "0. The native fast path (docs/286): if a per-platform wheel")
    from dos import hook_binary as _hb
    _native = _hb.try_native_hook("pretool", _hb.hook_argv_from_args(args))
    if _native is not None:
        _dbg(f"served by native dos-hook (exit {_native})")
        return _native

    # 1. Read the hook event from stdin. Any failure → emit nothing, exit 0 (we never
    #    block on our own inability to read — the advisory fail-to-passthrough).
    event: dict = {}
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    if raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                event = parsed
        except (ValueError, TypeError):
            event = {}
    if not event:
        _dbg("no/invalid stdin event — emitting nothing")
        return 0

    # 2. The structural PRE guard: a tool_name present AND no tool RESULT key. A mis-routed
    #    PostToolUse event (carrying a result) is declined — we never treat agent-unseen
    #    result bytes as PRE evidence.
    if not _prt.is_pre_event(event):
        _dbg("not a PreToolUse event (no tool_name, or a result key present) — passthrough")
        return 0

    # 3. Resolve the workspace: explicit --workspace › the event's cwd › cwd (the
    #    cmd_hook_posttool path), so admission reads this repo's leases + runtime files.
    if not getattr(args, "workspace", None):
        ev_cwd = event.get("cwd")
        if isinstance(ev_cwd, str) and ev_cwd:
            args.workspace = ev_cwd
    _apply_workspace(args)
    cfg = _config.active()

    # 4. Run the two PRE rungs. ALL of decide()'s own faults fail toward passthrough.
    handler_name = getattr(args, "handler", None) or "observe"
    try:
        dialect, outcome = _prt.decide(event, cfg, handler_name=handler_name)
    except Exception as exc:  # noqa: BLE001 — advisory fail-safe: any error → passthrough
        _dbg(f"decide error ({exc!r}) — emitting nothing (passthrough)")
        return 0

    decision = outcome.get("decision", "passthrough")
    _dbg(f"rung={outcome.get('rung')} decision={decision} "
         f"reason_class={outcome.get('reason_class', '-')} "
         f"intervention={outcome.get('intervention', '-')}")

    # 5. Journal every NON-passthrough outcome as an OP_ENFORCE record (docs/189 §C4) — a
    #    durable, joinable forensic fact, never self-report. Fail-safe: a journal write
    #    error never changes the emitted dialect (advisory — the deny still stands; we just
    #    failed to record it, which we note on stderr).
    if decision != "passthrough":
        try:
            _journal_pretool_outcome(event, outcome, cfg)
        except Exception as exc:  # noqa: BLE001 — recording is best-effort, never blocking
            _dbg(f"OP_ENFORCE journal write error ({exc!r}) — outcome still emitted")

    # 5b. The OPERATOR observability nudge (help_summary): on the 1st + every 5th
    #   (full prose: docs/CLI.md § "5b. The OPERATOR observability nudge (help_summary): on the")
    nudge = ""
    if decision != "passthrough":
        try:
            nudge = _operator_help_nudge(event, outcome, cfg)
        except Exception as exc:  # noqa: BLE001 — observability is best-effort, never blocking
            _dbg(f"help-nudge fold error ({exc!r}) — emitting outcome without nudge")

    # 6. The ONLY thing on stdout: the host's PRE dialect, or nothing. `decide()`
    #   (full prose: docs/CLI.md § "6. The ONLY thing on stdout: the host's PRE dialect, or noth")
    if dialect is not None:
        # Fold the operator nudge into the CC dialect's additionalContext BEFORE
        # transcoding, so it rides through whatever host renderer is selected. Additive
        # only — it appends to (never replaces) the corrective context and leaves the
        # permissionDecision untouched.
        if nudge:
            dialect = _append_additional_context(dialect, nudge)
        try:
            from dos import hook_dialect as _hd
            renderer = _hd.resolve_dialect(getattr(args, "dialect", None))
            verdict = _hd.parse_cc(dialect, moment=_hd.HookMoment.PRE)
            host_dialect = renderer.render(verdict)
        except ValueError as exc:  # unknown dialect name — surface, do not guess a host
            _dbg(f"dialect error ({exc}) — emitting nothing (passthrough)")
            host_dialect = None
        if host_dialect is not None:
            print(json.dumps(host_dialect, sort_keys=True))

    # 7. docs/297 P3: one observation per decided call — THE denominator record
    #    (one pretool record = one tool call adjudicated). On the native fast path
    #    the binary recorded its own; this is the Python-decided record, including
    #    the call a binary DELEGATED here (the docs/297 pairing). Fail-soft,
    #    strictly downstream of the emitted dialect.
    from dos.hook_dialect import DEFAULT_DIALECT as _default_dialect
    _record_hook_observation(cfg, verb="pretool", outcome=str(decision),
                             started=started, debug=debug,
                             rung=str(outcome.get("rung") or ""),
                             reason_class=str(outcome.get("reason_class") or ""),
                             dialect=str(getattr(args, "dialect", None) or _default_dialect),
                             tree_known=(bool(outcome["tree_known"])
                                         if "tree_known" in outcome else None))
    return 0


def _append_additional_context(dialect: dict, text: str) -> dict:
    """Append `text` to a CC hook dialect's `additionalContext` (additive, pure).

    Detail: docs/CLI.md § _append_additional_context.
    """
    out = dict(dialect)
    hso = dict(out.get("hookSpecificOutput") or {})
    existing = str(hso.get("additionalContext") or "").strip()
    hso["additionalContext"] = (existing + "\n\n" + text) if existing else text
    out["hookSpecificOutput"] = hso
    return out


def _operator_help_nudge(event: dict, outcome: dict, cfg) -> str:
    """The one-line "DOS has caught N things this session" nudge, or "" (pure-ish).

    Detail: docs/CLI.md § _operator_help_nudge.
    """
    from dos import help_summary as _help
    from dos import lane_journal as _lane_journal
    from dos import lane_lease as _lane_lease

    # This call only advances the operator count if it was itself a help.
    rung = (outcome.get("intervention")
            or ("BLOCK" if outcome.get("decision") == "deny" else "WARN"))
    if str(rung).strip().upper() not in _help.HELP_RUNGS:
        return ""

    holder = str(event.get("session_id") or "")
    records = _lane_journal.read_all(path=_lane_lease._journal_path(cfg))
    summary = _help.summarize(records, holder=holder)
    if not _help.should_nudge(summary.total):
        return ""
    return _help.nudge_line(summary)


def _journal_pretool_outcome(event: dict, outcome: dict, cfg) -> None:
    """Append an OP_ENFORCE record for a non-passthrough PRE outcome (docs/189 §C4).

    Detail: docs/CLI.md § _journal_pretool_outcome.
    """
    from dos import lane_journal
    from dos import lane_lease as _lane_lease
    body = {
        "intervention": outcome.get("intervention") or (
            "BLOCK" if outcome.get("decision") == "deny" else "WARN"),
        "dispatch_call": outcome.get("decision") != "deny",
        "handler": outcome.get("handler", outcome.get("rung", "")),
        "reason": outcome.get("reason") or outcome.get("reason_class") or outcome.get("rung", ""),
        "rung": outcome.get("rung", ""),
        "decision": outcome.get("decision", ""),
        "reason_class": outcome.get("reason_class", ""),
    }
    entry = lane_journal.enforce_entry(
        lane=str(event.get("tool_name") or "tool"),
        loop_ts=os.environ.get("DISPATCH_LOOP_TS", ""),
        host_id=os.environ.get("DISPATCH_HOST_ID", ""),
        run_id=os.environ.get("CID_RUN_ID", ""),
        owner=str(event.get("session_id") or ""),
        tool=str(event.get("tool_name") or ""),
        proposal=body,
    )
    lane_journal.append(entry, path=_lane_lease._journal_path(cfg))


# ---------------------------------------------------------------------------
# id-alloc  (atomic monotonic id allocator — the TAG_COLLISION structural fix)
# ---------------------------------------------------------------------------
def cmd_id_alloc(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    cfg = _config.active()
    if args.id_alloc_cmd == "allocate":
        value = id_alloc.allocate(cfg, args.scope, start=args.start)
        print(json.dumps({"scope": args.scope, "id": value}, sort_keys=True))
        return 0
    if args.id_alloc_cmd == "peek":
        value = id_alloc.peek(cfg, args.scope)
        print(json.dumps({"scope": args.scope, "id": value}, sort_keys=True))
        return 0
    return 2  # unreachable: subparser is required


# ---------------------------------------------------------------------------
# journal  (lane WAL)
# ---------------------------------------------------------------------------
def cmd_journal(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    from dos import lane_journal
    if args.journal_cmd == "replay":
        print(json.dumps(lane_journal.replay(lane_journal.read_all()), indent=2,
                         sort_keys=True, default=str))
        return 0
    if args.journal_cmd == "seq":
        print(lane_journal.next_seq() - 1)
        return 0
    if args.journal_cmd == "compact":
        # Bound an unbounded WAL: fold the whole journal to a single CHECKPOINT
        #   (full prose: docs/CLI.md § "Bound an unbounded WAL: fold the whole journal to a single C")
        _ensure_home_if_persisting()
        from dos import lane_lease
        res = lane_lease.compact_journal(_config.active())
        print(json.dumps({"entries_before": res.entries_before,
                          "entries_after": res.entries_after,
                          "bytes_reclaimed": res.bytes_reclaimed},
                         sort_keys=True))
        return 0
    for e in lane_journal.tail(args.n):
        print(json.dumps(e, sort_keys=True, default=str))
    return 0


# ---------------------------------------------------------------------------
# man  (the self-describing manual — DOM concept, projected over the registries)
# ---------------------------------------------------------------------------
def cmd_man(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    if args.section == "wedge":
        # Project the man page over the ACTIVE workspace's ReasonRegistry — the
        #   (full prose: docs/CLI.md § "Project the man page over the ACTIVE workspace's ReasonRegis")
        cfg = _config.active()
        reg = cfg.reasons
        if not args.id:
            for spec in reg.specs:
                print(f"{spec.key:42} {spec.category}")
            return 0
        spec = reg.get(args.id)
        if spec is None:
            print(f"UNCLASSIFIED — {args.id!r} is not in the active reason "
                  f"registry (this is drift; declare it in dos.toml or "
                  f"BASE_REASONS.extend(...), don't tolerate it).",
                  file=sys.stderr)
            return 1
        # Assemble the man page as DECIDED content (lines + structured fields),
        # then hand it to the selected renderer (RND Phase 3b). `text` joins the
        # lines — byte-identical to the old sequence of prints; `json` emits the
        # fields. Content is the kernel's; layout is the renderer's.
        lines: list[str] = []
        fields: dict = {
            "section": "wedge", "key": spec.key, "summary": spec.summary,
            "category": spec.category, "refusal": spec.refusal, "fix": spec.fix,
        }
        lines.append(f"NAME        {spec.key}"
                     + (f"  — {spec.summary}" if spec.summary else ""))
        lines.append(f"CATEGORY    {spec.category}")
        lines.append(f"REFUSAL?    {'yes — route to /replan' if spec.refusal else 'no — advisory only'}")
        if spec.fix:
            lines.append(f"TYPICAL FIX {spec.fix}")
        see = "   ·   ".join(spec.see_also) if spec.see_also else "dos man wedge · dos verify · dos arbitrate"
        lines.append(f"SEE ALSO    {see}")
        fields["see_also"] = list(spec.see_also) if spec.see_also else []
        # LIVE footer (DOM dynamic-footer rule): how many decisions are carrying
        #   (full prose: docs/CLI.md § "LIVE footer (DOM dynamic-footer rule): how many decisions ar")
        try:
            from dos import decisions as _decisions
            live = [d for d in _decisions.collect_decisions(cfg, resolver=None)
                    if d.reason_token == spec.key]
            if live:
                lines.append(f"LIVE        {len(live)} decision(s) carrying this reason now "
                             f"— see `dos decisions --all`")
                fields["live"] = len(live)
        except Exception:
            pass
        # `--explain` (off by default): append the same next-action gloss the MCP
        #   (full prose: docs/CLI.md § "`--explain` (off by default): append the same next-action gl")
        if getattr(args, "explain", False):
            interpretation = _interpret.check_reason({
                "reason_class": spec.key,
                "known": True,
                "category": spec.category,
                "refusal": spec.refusal,
                "summary": spec.summary,
                "fix": spec.fix,
                "see_also": list(spec.see_also),
            })
            fields["interpretation"] = interpretation
            lines = list(lines) + [interpretation]
        from dos import render as _render
        entry = _render.ManEntry(lines, fields)
        return _render_to_stdout(args, "render_man", entry)
    if args.section == "lane":
        cfg = _config.active()
        lanes = cfg.lanes
        if not args.id:
            for name in sorted(set(lanes.concurrent) | set(lanes.exclusive)
                               | set(lanes.trees)):
                kind = ("exclusive" if name in lanes.exclusive
                        else "concurrent" if name in lanes.concurrent else "named")
                print(f"{name:16} {kind}")
            return 0
        name = args.id
        kind = ("exclusive" if lanes.is_exclusive(name)
                else "concurrent" if lanes.is_concurrent(name) else "named")
        from dos import render as _render
        lines = [
            f"NAME        {name}",
            f"KIND        {kind}",
            f"EXCLUSIVITY {'runs alone' if kind == 'exclusive' else 'concurrent-safe if tree-disjoint'}",
            f"TREE        {', '.join(lanes.tree_for(name)) or '(none declared)'}",
        ]
        entry = _render.ManEntry(lines, {
            "section": "lane", "name": name, "kind": kind,
            "tree": lanes.tree_for(name),
        })
        return _render_to_stdout(args, "render_man", entry)
    print(f"unknown man section {args.section!r}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# hosts  (the self-describing host-support matrix — man wedge/lane, aimed at the
#         dos.hook_installs + dos.hook_dialects registries; docs #93)
# ---------------------------------------------------------------------------
def cmd_hosts(args: argparse.Namespace) -> int:
    """Print the host-support matrix FROM THE REGISTRIES, never hand-kept prose.

    One row per host DOS can wire (`dos.hook_installs`): its tier, the host event
    names DOS binds, the dialect its wired command targets, the config file it
    writes, the exact wiring command, and the host's own caveat. The roster is the
    registry's contents (`hook_install.host_matrix()` walks `host_names()`), so the
    matrix can never drift from what `dos init --hooks` actually wires — the same
    self-describing move as `dos man wedge` / `dos man lane`.

    A host with NO install spec (Trae, docs/294) has no row: its absence IS the
    information — it is advisory-only (MCP + skills), printed as a footer, not faked
    into a `hooks` row. The renderer set (`dos.hook_dialects`) is reported alongside
    so a host the installer doesn't cover yet but whose envelope DOES ship is visible.
    """
    from dos import hook_install
    from dos import hook_dialect

    rows = hook_install.host_matrix()
    dialects = hook_dialect.available_dialects()
    # Dialects with a renderer but NO install spec — a host whose envelope ships
    # but whose hook-config wiring is not (yet) automated. Honest to surface: the
    # operator can hand-wire the config and point the command at `--dialect <name>`.
    # A name is "renderer-only" iff it is NEITHER an install host (it has a row, so
    # it is wired regardless of which envelope it renders — e.g. claude-cowork rides
    # the default `claude-code` dialect yet is fully wired) NOR a dialect some row
    # already targets.
    wired = {r.host for r in rows} | {r.dialect for r in rows}
    renderer_only = [d for d in dialects if d not in wired]

    if getattr(args, "json", False):
        payload = {
            "hosts": [r.to_dict() for r in rows],
            "dialects": list(dialects),
            "renderer_only_dialects": renderer_only,
            "advisory_only_note": (
                "A host with no row has no hook seam DOS can wire (e.g. Trae, "
                "docs/294); its DOS surface is advisory — the MCP server + skills."
            ),
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print("# DOS host-support matrix  (from the dos.hook_installs registry)\n")
    for r in rows:
        flag = "  [default]" if r.is_default else ""
        print(f"{r.host}{flag}")
        print(f"  tier      {r.tier} — the host enforces (denies on a DOS verdict)")
        print(f"  config    {r.config_path}")
        print(f"  events    {', '.join(r.events)}")
        print(f"  dialect   {r.dialect}"
              + ("  (the implicit default envelope)" if r.dialect == hook_dialect.DEFAULT_DIALECT
                 and not r.is_default else ""))
        print(f"  wire      {r.wiring}")
        if r.note:
            print(f"  note      {r.note}")
        print()
    print("Every other DOS surface is advisory — a host with no row here (e.g. Trae,")
    print("docs/294) has no hook seam to wire; bind it through the MCP server + skills.")
    if renderer_only:
        print(f"\nRenderers shipped without an install spec: {', '.join(renderer_only)}")
        print("(hand-wire the host's hook-config, then point each command at "
              "--dialect <name>).")
    return 0


# ---------------------------------------------------------------------------
# judge  (the deterministic adjudicator — picker_oracle as a verb)
# ---------------------------------------------------------------------------
def cmd_judge(args: argparse.Namespace) -> int:
    """Adjudicate a no-pick / WEDGE verdict — deterministically.

    Detail: docs/CLI.md § cmd_judge.
    """
    _apply_workspace(args)
    from dos import picker_oracle
    cfg = _config.active()
    state = picker_oracle._load_yaml(cfg.paths.execution_state)
    verdict = picker_oracle._classify_one(args.run_ts, state)

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, default=str))
        return 1 if verdict.oracle_disagrees else 0

    cause = verdict.no_pick_cause.value if verdict.no_pick_cause else "-"
    print(f"RUN         {verdict.run_ts}")
    print(f"LANE        {verdict.lane}")
    print(f"OUTCOME     {verdict.outcome.value}")
    print(f"CAUSE       {cause}")
    disagree = verdict.oracle_disagrees
    print(f"VERDICT     {'oracle_disagrees=TRUE — provable picker bug' if disagree else 'oracle agrees / abstains — no picker bug'}")
    if verdict.evidence:
        print("EVIDENCE    " + "\n            ".join(verdict.evidence))
    if verdict.picker_reason:
        print(f"PICKER SAID {verdict.picker_reason[:200]}")
    # When the cause is UNCLASSIFIED the deterministic judge can only abstain —
    # point at the optional LLM driver that CAN rule on it (outside the kernel).
    if cause == "UNCLASSIFIED":
        print("ESCALATE    deterministic judge abstains (no reason_class to verify);")
        print("            an LLM adjudicator can rule — see `dos.drivers.llm_judge`")
    return 1 if disagree else 0


def _load_judge_cases(path: str):
    """Load labelled adjudication cases from a JSONL file → [(Claim, truth)].

    Detail: docs/CLI.md § _load_judge_cases.
    """
    from dos.judges import Claim
    cases = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: not valid JSON ({e})") from None
            if "claim_text" not in obj or "truth" not in obj:
                raise ValueError(
                    f"{path}:{lineno}: each case needs 'claim_text' and 'truth' "
                    f"(got keys: {sorted(obj)})"
                )
            claim = Claim(
                claim_text=str(obj["claim_text"]),
                stated_reason=str(obj.get("stated_reason", "")),
                evidence=tuple(obj.get("evidence", []) or ()),
                subject=str(obj.get("subject", "")),
            )
            cases.append((claim, bool(obj["truth"])))
    return cases


def cmd_judge_eval(args: argparse.Namespace) -> int:
    """Score a JUDGE-rung adjudicator against labelled claims — the research instrument.

    Detail: docs/CLI.md § cmd_judge_eval.
    """
    from dos import judge_eval as _je
    from dos import judges as _judges
    try:
        cases = _load_judge_cases(args.cases)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cases:
        print(f"error: no cases found in {args.cases}", file=sys.stderr)
        return 2
    try:
        judge = _judges.resolve_judge(args.judge)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    report = _je.score(judge, cases)

    if args.json:
        out = {"judge": args.judge, "cases": args.cases, **report.to_dict()}
        print(json.dumps(out, indent=2))
        return 1 if report.false_clear else 0

    g = report
    print(f"JUDGE          {args.judge}")
    print(f"CASES          {report.n}  ({report.n_false_claims} false, "
          f"{report.n - report.n_false_claims} true)")
    print(f"confusion      AGREE={g.n_agree} DISAGREE={g.n_disagree} ABSTAIN={g.n_abstain}")
    print(f"  correct-clear {g.correct_clear:>4}   (agreed, was true)")
    print(f"  FALSE-CLEAR   {g.false_clear:>4}   (agreed, was FALSE — the dangerous cell)")
    print(f"  correct-flag  {g.correct_flag:>4}   (disagreed, was false)")
    print(f"  false-flag    {g.false_flag:>4}   (disagreed, was true — safe, costs a review)")
    print(f"  abstained     {g.n_abstain:>4}   (punted to a human)")
    print(f"false-clear rate   {g.false_clear_rate:.3f}   (of claims it cleared)")
    print(f"lie-leak rate      {g.lie_leak_rate:.3f}   (of all false claims)")
    print(f"decisive accuracy  {g.decisive_accuracy:.3f}   (when it committed)")
    print(f"abstention rate    {g.abstention_rate:.3f}")
    print(f"cost / claim       {g.cost_per_claim:.6f}")
    return 1 if report.false_clear else 0


def _load_overlap_cases(path: str):
    """Load labelled concurrent-pair outcomes from a JSONL file → [OverlapCase].

    Detail: docs/CLI.md § _load_overlap_cases.
    """
    from dos.overlap_eval import OverlapCase
    cases = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: not valid JSON ({e})") from None
            missing = [k for k in ("tree_a", "tree_b", "collided") if k not in obj]
            if missing:
                raise ValueError(
                    f"{path}:{lineno}: each case needs 'tree_a', 'tree_b', and "
                    f"'collided' (missing: {', '.join(missing)}; got keys: "
                    f"{sorted(obj)})"
                )
            if not isinstance(obj["tree_a"], list) or not isinstance(obj["tree_b"], list):
                raise ValueError(
                    f"{path}:{lineno}: 'tree_a' and 'tree_b' must be lists of glob "
                    f"strings"
                )
            cases.append(OverlapCase(
                tree_a=[str(x) for x in obj["tree_a"]],
                tree_b=[str(x) for x in obj["tree_b"]],
                collided=bool(obj["collided"]),
                label=str(obj.get("label", "")),
            ))
    return cases


def cmd_overlap_eval(args: argparse.Namespace) -> int:
    """Score a disjointness SCORER against labelled concurrent-pair outcomes (Axis 7).

    Detail: docs/CLI.md § cmd_overlap_eval.
    """
    _apply_workspace(args)
    cfg = _config.active()
    from dos import overlap_eval as _oe
    from dos import overlap_policy as _op
    try:
        cases = _load_overlap_cases(args.cases)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cases:
        print(f"error: no cases found in {args.cases}", file=sys.stderr)
        return 2
    try:
        policy = _op.resolve_overlap_policy(args.policy)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    report = _oe.score(policy, cases, config=cfg)

    if args.json:
        out = {"policy": args.policy, "cases": args.cases, **report.to_dict()}
        print(json.dumps(out, indent=2))
        return 1 if report.leaked else 0

    g = report
    print(f"POLICY         {args.policy}")
    print(f"CASES          {report.n}  ({report.n_collided} collided, "
          f"{report.n_safe} safe)")
    print(f"confusion      ADMIT={g.n_admit} REFUSE={g.n_refuse}")
    print(f"  correct-admit {g.correct_admit:>4}   (admitted, was safe)")
    print(f"  FALSE-ADMIT   {g.false_admit:>4}   (admitted, COLLIDED — the dangerous cell)")
    print(f"  correct-refuse{g.correct_refuse:>4}   (refused, did collide)")
    print(f"  safe-forgone  {g.safe_forgone:>4}   (refused, was safe — costs parallelism)")
    print(f"false-admit rate     {g.false_admit_rate:.3f}   (of pairs it admitted)")
    print(f"collision-leak rate  {g.collision_leak_rate:.3f}   (of all colliding pairs)")
    print(f"safe-forgone rate    {g.safe_forgone_rate:.3f}   (of all safe pairs)")
    print(f"admit rate           {g.admit_rate:.3f}   (read with the leak it bought)")
    print(f"decisive accuracy    {g.decisive_accuracy:.3f}")
    return 1 if report.leaked else 0


def _verdict_from_dict(obj: dict):
    """Rehydrate a `dos.arg_provenance.ProvenanceVerdict` from a JSON dict.

    Detail: docs/CLI.md § _verdict_from_dict.
    """
    from dos.arg_provenance import ArgProvenance, ProvenanceStance, ProvenanceVerdict
    from dos.arg_provenance import CorpusSource
    args = []
    for a in obj.get("args", []):
        matched = tuple(
            CorpusSource(s) for s in a.get("matched_in", []) if s in CorpusSource._value2member_map_
        )
        args.append(ArgProvenance(
            arg_name=str(a.get("arg_name", "")),
            value_repr=str(a.get("value_repr", "")),
            stance=ProvenanceStance(a.get("stance", "ABSTAIN")),
            id_shaped=bool(a.get("id_shaped", False)),
            is_reference=bool(a.get("is_reference", True)),
            matched_in=matched,
            components_checked=tuple(str(c) for c in a.get("components_checked", [])),
            components_unmatched=tuple(str(c) for c in a.get("components_unmatched", [])),
            reason=str(a.get("reason", "")),
        ))
    return ProvenanceVerdict(
        believe=bool(obj.get("believe", True)),
        args=tuple(args),
        unsupported=tuple(str(u) for u in obj.get("unsupported", [])),
        reason=str(obj.get("reason", "")),
    )


def _verdict_from_compact(confidence: str, unsupported, line_ref: str):
    """Synthesize the MINIMAL `ProvenanceVerdict` whose `assess_confidence` is `confidence`.

    Detail: docs/CLI.md § _verdict_from_compact.
    """
    from dos.arg_provenance import ArgProvenance, ProvenanceStance, ProvenanceVerdict
    conf = str(confidence).strip().upper()
    names = [str(u) for u in (unsupported or [])]
    if conf == "NONE":
        return ProvenanceVerdict(believe=True, args=(), unsupported=(),
                                 reason="synthesized: clean call (no mint)")
    if conf not in ("HIGH", "LOW"):
        raise ValueError(
            f"{line_ref}: confidence must be one of HIGH/LOW/NONE, got {confidence!r}")
    arg_name = names[0] if names else "arg"
    if conf == "HIGH":
        checked, unmatched, vr = ("9999999",), ("9999999",), "INC9999999"
    else:  # LOW — composite with exactly one missing component
        checked, unmatched, vr = ("user", "evil", "com"), ("evil",), "user@evil.com"
    arg = ArgProvenance(
        arg_name=arg_name, value_repr=vr, stance=ProvenanceStance.UNSUPPORTED,
        id_shaped=True, is_reference=True, matched_in=(),
        components_checked=checked, components_unmatched=unmatched,
        reason=f"synthesized: {conf}-confidence mint")
    return ProvenanceVerdict(
        believe=False, args=(arg,),
        unsupported=tuple(names) or (arg_name,),
        reason=f"synthesized: {conf}-confidence mint on {arg_name}")


def _load_intervention_cases(path: str):
    """Load labelled intervention cases from a JSONL file → [InterventionCase].

    Detail: docs/CLI.md § _load_intervention_cases.
    """
    from dos.intervention_eval import InterventionCase
    cases = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            ref = f"{path}:{lineno}"
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as e:
                raise ValueError(f"{ref}: not valid JSON ({e})") from None
            required = ("truly_minted", "mattered_to_score",
                        "recovered_if_blocked", "recovered_if_deferred")
            missing = [k for k in required if k not in obj]
            if missing:
                raise ValueError(
                    f"{ref}: each case needs {required} "
                    f"(missing: {', '.join(missing)}; got keys: {sorted(obj)})"
                )
            if "verdict" in obj:
                if not isinstance(obj["verdict"], dict):
                    raise ValueError(
                        f"{ref}: 'verdict' must be a ProvenanceVerdict.to_dict() object")
                verdict = _verdict_from_dict(obj["verdict"])
            elif "confidence" in obj:
                verdict = _verdict_from_compact(
                    obj["confidence"], obj.get("unsupported"), ref)
            else:
                raise ValueError(
                    f"{ref}: each case needs a 'confidence' (HIGH/LOW/NONE) or a full "
                    f"'verdict' object (got keys: {sorted(obj)})")
            cases.append(InterventionCase(
                verdict=verdict,
                truly_minted=bool(obj["truly_minted"]),
                mattered_to_score=bool(obj["mattered_to_score"]),
                recovered_if_blocked=bool(obj["recovered_if_blocked"]),
                recovered_if_deferred=bool(obj["recovered_if_deferred"]),
                label=str(obj.get("label", "")),
            ))
    return cases


def cmd_intervention_eval(args: argparse.Namespace) -> int:
    """Score an intervention POLICY by its NET TASK DELTA — not verdict accuracy (docs/143 §13.2).

    Detail: docs/CLI.md § cmd_intervention_eval.
    """
    _apply_workspace(args)
    cfg = _config.active()
    from dos import intervention_eval as _ie
    from dos.intervention import InterventionPolicy
    try:
        cases = _load_intervention_cases(args.cases)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cases:
        print(f"error: no cases found in {args.cases}", file=sys.stderr)
        return 2
    # Effective high rung: if --high is unset, a HIGH-confidence mint maps to the
    # ceiling — so `--ceiling DEFER` alone actually ENABLES the turn-spending rung
    # (raising the ceiling without raising on_high would leave DEFER unreachable, the
    # clamp would pin it back to BLOCK). The explicit `--high X` always wins.
    high = args.high if args.high is not None else args.ceiling
    try:
        policy = InterventionPolicy(
            on_high_confidence=high, on_low_confidence=args.low, ceiling=args.ceiling)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    report = _ie.score(policy, cases, ladder=cfg.interventions)

    if args.json:
        out = {
            "policy": {"on_high_confidence": high, "on_low_confidence": args.low,
                       "ceiling": args.ceiling},
            "cases": args.cases, **report.to_dict(),
        }
        print(json.dumps(out, indent=2))
        return 1 if report.net_harmful else 0

    g = report
    print(f"POLICY         high={high}  low={args.low}  ceiling={args.ceiling}")
    print(f"CASES          {report.n}  ({g.n_true_relevant} true-relevant, "
          f"{g.n_true_irrelevant} true-irrelevant, {g.n_false_flag} false-flag)")
    print(f"NET TASK DELTA {g.net_task_delta:+.4f}   (per case, verifier-flip units — "
          f"the headline; cf live -0.09 / +0.11)")
    print(f"actuation      ACTUATED={g.n_actuated} (turn withheld) "
          f"INFORMED-ONLY={g.n_informed_only} (turn kept)")
    print(f"  recovered          {g.recovered:>4}   (actuated relevant that recovered)")
    print(f"  actuated-relevant  {g.n_actuated_relevant:>4}   (disruption that could pay off)")
    print(f"  ACTUATED-IRRELEVANT{g.actuated_irrelevant:>4}   (true catch verifier ignored — the -9pp cell)")
    print(f"  actuated-false-flag{g.actuated_false_flag:>4}   (disrupted a legit id)")
    print(f"wasted-disruption rate {g.wasted_disruption_rate:.3f}   (of turns it withheld — the dangerous cell)")
    print(f"dangerous-cell rate    {g.dangerous_cell_rate:.3f}   (of true-irrelevant catches, actuated on)")
    print(f"disruption efficiency  {g.disruption_efficiency:.3f}   (of withheld turns, bought a gain)")
    print(f"coverage               {g.coverage:.3f}   (of true-relevant mints, acted on)")
    print(f"VERDICT        {'NET-HARMFUL' if g.net_harmful else 'net-positive-or-neutral'}")
    return 1 if report.net_harmful else 0


def _load_stream_cases(path: str):
    """Load labelled stall-reader cases from a JSONL file → [StreamCase] (docs/145 §9).

    Detail: docs/CLI.md § _load_stream_cases.
    """
    from dos.tool_stream import StreamStep, ToolStream
    from dos.tool_stream_eval import StreamCase
    cases = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            ref = f"{path}:{lineno}"
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as e:
                raise ValueError(f"{ref}: not valid JSON ({e})") from None
            required = ("actually_stuck", "legit_polling", "recovered_if_fired")
            missing = [k for k in required if k not in obj]
            if missing:
                raise ValueError(
                    f"{ref}: each case needs {required} "
                    f"(missing: {', '.join(missing)}; got keys: {sorted(obj)})"
                )
            if "steps" in obj:
                if not isinstance(obj["steps"], list):
                    raise ValueError(f"{ref}: 'steps' must be a list of [tool, args, result]")
                steps = []
                for i, st in enumerate(obj["steps"]):
                    if not isinstance(st, (list, tuple)) or len(st) < 2:
                        raise ValueError(
                            f"{ref}: steps[{i}] must be [tool, args_digest, result_digest?]")
                    tool, args = str(st[0]), str(st[1])
                    result = st[2] if len(st) > 2 else None
                    steps.append(StreamStep(
                        tool_name=tool, args_digest=args,
                        result_digest=(None if result is None else str(result))))
                stream = ToolStream(steps=tuple(steps))
            elif "repeat" in obj:
                n = int(obj["repeat"])
                if n < 0:
                    raise ValueError(f"{ref}: 'repeat' must be >= 0")
                tool = str(obj.get("tool", "tool"))
                args = str(obj.get("args", "a"))
                result = obj.get("result", "r")
                result = None if result is None else str(result)
                stream = ToolStream(steps=tuple(
                    StreamStep(tool_name=tool, args_digest=args, result_digest=result)
                    for _ in range(n)))
            else:
                raise ValueError(
                    f"{ref}: each case needs a 'repeat' (N) or a full 'steps' list "
                    f"(got keys: {sorted(obj)})")
            cases.append(StreamCase(
                stream=stream,
                actually_stuck=bool(obj["actually_stuck"]),
                legit_polling=bool(obj["legit_polling"]),
                recovered_if_fired=bool(obj["recovered_if_fired"]),
                label=str(obj.get("label", "")),
            ))
    return cases


def cmd_tool_stream_eval(args: argparse.Namespace) -> int:
    """Score a stall-reader POLICY by its NET RECOVERY — does firing recover more than it wastes?

    Detail: docs/CLI.md § cmd_tool_stream_eval.
    """
    _apply_workspace(args)
    cfg = _config.active()
    from dos import tool_stream_eval as _tse
    from dos.tool_stream import StreamPolicy
    try:
        cases = _load_stream_cases(args.cases)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cases:
        print(f"error: no cases found in {args.cases}", file=sys.stderr)
        return 2
    base = cfg.stream_policy
    ignore = base.ignore_tools
    if args.ignore_tools:
        ignore = frozenset(t.strip() for t in args.ignore_tools.split(",") if t.strip())
    try:
        policy = StreamPolicy(
            repeat_n=args.repeat_n if args.repeat_n is not None else base.repeat_n,
            stall_n=args.stall_n if args.stall_n is not None else base.stall_n,
            ignore_tools=ignore,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    report = _tse.score(policy, cases)

    if args.json:
        out = {
            "policy": {"repeat_n": policy.repeat_n, "stall_n": policy.stall_n,
                       "ignore_tools": sorted(policy.ignore_tools)},
            "cases": args.cases, **report.to_dict(),
        }
        print(json.dumps(out, indent=2))
        return 0 if report.net_positive else 1

    g = report
    print(f"POLICY         repeat_n={policy.repeat_n}  stall_n={policy.stall_n}  "
          f"ignore_tools={sorted(policy.ignore_tools) or '[]'}")
    print(f"CASES          {report.n}  ({g.n_stuck} actually-stuck, {g.n_polling} legit-polling)")
    print(f"RECOVERED RATE {g.recovered_rate:.3f}   (of stuck streams, fired-on AND recovered — "
          f"the headline payoff)")
    print(f"firing         FIRED={g.n_fired}  on-stuck={g.n_fired_stuck}  "
          f"on-polling={g.n_fired_polling}  recovered={g.n_recovered}")
    print(f"  fire-recall          {g.fire_recall:.3f}   (of stuck streams, fired on)")
    print(f"  FALSE-RESURFACE rate {g.false_resurface_rate:.3f}   (of pollers, also fired on — the dangerous cell)")
    print(f"  fire-precision       {g.fire_precision:.3f}   (of fires, on an actually-stuck stream)")
    print(f"VERDICT        {'net-positive' if g.net_positive else 'NET-NEGATIVE'}")
    return 0 if report.net_positive else 1


def _load_precursor_cases(path: str):
    """Load labelled `PrecursorCase`s from a JSONL file (one JSON object per line).

    Detail: docs/CLI.md § _load_precursor_cases.
    """
    from dos.precursor_gate import CallStream, MutatingCall, PriorCall
    from dos.precursor_gate_eval import PrecursorCase
    cases = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            ref = f"{path}:{lineno}"
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as e:
                raise ValueError(f"{ref}: not valid JSON ({e})") from None
            required = ("tool", "prior", "precursor_required", "precursor_actually_fired")
            missing = [k for k in required if k not in obj]
            if missing:
                raise ValueError(
                    f"{ref}: each case needs {required} "
                    f"(missing: {', '.join(missing)}; got keys: {sorted(obj)})"
                )
            prior = obj["prior"]
            if not isinstance(prior, list):
                raise ValueError(f"{ref}: 'prior' must be a list of prior tool names")
            stream = CallStream(calls=tuple(PriorCall(tool_name=str(t)) for t in prior))
            cases.append(PrecursorCase(
                call=MutatingCall(
                    tool_name=str(obj["tool"]),
                    is_mutating=bool(obj.get("is_mutating", True)),
                ),
                stream=stream,
                precursor_required=bool(obj["precursor_required"]),
                precursor_actually_fired=bool(obj["precursor_actually_fired"]),
                mattered_to_score=bool(obj.get("mattered_to_score", False)),
                label=str(obj.get("label", "")),
            ))
    return cases


def cmd_precursor_gate_eval(args: argparse.Namespace) -> int:
    """Score a precursor GRAMMAR by its RECALL vs WASTE — does it catch real skips cleanly?

    Detail: docs/CLI.md § cmd_precursor_gate_eval.
    """
    _apply_workspace(args)
    cfg = _config.active()
    from dos import precursor_gate_eval as _pge
    try:
        cases = _load_precursor_cases(args.cases)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cases:
        print(f"error: no cases found in {args.cases}", file=sys.stderr)
        return 2

    report = _pge.score(cfg.precursors, cases)

    if args.json:
        out = {
            "grammar": {
                "requires": {k: list(v) for k, v in cfg.precursors.requires.items()},
                "aliases": {k: list(v) for k, v in cfg.precursors.aliases.items()},
            },
            "cases": args.cases, **report.to_dict(),
        }
        print(json.dumps(out, indent=2))
        return 0 if report.net_positive else 1

    g = report
    n_tools = len(cfg.precursors.requires)
    print(f"GRAMMAR        {n_tools} mutating tool(s) with a declared precursor")
    print(f"CASES          {report.n}  ({g.n_real_skip} real-skip, "
          f"{g.n_correctly_sequenced} correctly-sequenced)")
    print(f"RECALL         {g.missed_precursor_recall:.3f}   (of real skips, fired REFUTED on — "
          f"the headline, bounded by grammar coverage)")
    print(f"firing         REFUTED={g.n_refuted}  on-skip={g.n_refuted_skip}  "
          f"on-fired={g.n_refuted_fired}  mattered={g.n_refuted_skip_mattered}")
    print(f"  FALSE-REFUTE rate {g.false_refute_rate:.3f}   (of sequenced calls, wrongly REFUTED "
          f"— grow aliases)")
    print(f"  fire-precision    {g.fire_precision:.3f}   (of REFUTEs, on a real skip)")
    print(f"  mattered-recall   {g.mattered_recall:.3f}   (of real skips, caught AND scored)")
    print(f"VERDICT        {'net-positive' if g.net_positive else 'NET-NEGATIVE'}")
    return 0 if report.net_positive else 1


# ---------------------------------------------------------------------------
# top  (dos top — the live-ops watchdog screen)
# ---------------------------------------------------------------------------
def _ensure_initialized(cfg: _config.SubstrateConfig) -> bool:
    """Best-effort: scaffold a `dos.toml` if this workspace has none. Non-fatal.

    Detail: docs/CLI.md § _ensure_initialized.
    """
    cfg_path = cfg.root / "dos.toml"
    if cfg_path.exists():
        return True
    try:
        config_text, _summary = _render_init_config(cfg.root)
        cfg_path.write_text(config_text, encoding="utf-8")
        print(f"(first run — scaffolded {cfg_path}; edit it to declare your lanes)",
              file=sys.stderr)
        return True
    except OSError:
        # Unwritable workspace — render against the generic default anyway.
        return False


def cmd_dispatch_top(args: argparse.Namespace) -> int:
    """`dos top` — the live fleet watchdog: lanes, leases, recent verdicts, commits.

    Detail: docs/CLI.md § cmd_dispatch_top.
    """
    _apply_workspace(args)
    cfg = _config.active()
    # Best-effort auto-init (rerun init if it has not been run before), THEN
    # re-resolve the workspace so a freshly-scaffolded `dos.toml`'s tables are read
    # back into the active config the snapshot renders.
    if not (cfg.root / "dos.toml").exists():
        _ensure_initialized(cfg)
        _apply_workspace(args)
        cfg = _config.active()

    from dos import dispatch_top as _dtop

    if args.json:
        frame = _dtop.snapshot(cfg)
        print(json.dumps(frame.to_dict(), indent=2, default=str))
        return 0

    from dos import dispatch_top_tui as _tui
    return _tui.run_top(cfg, once=args.once, interval=args.interval)


# ---------------------------------------------------------------------------
# plan  (the work-terrain projection — every phase, claimed vs oracle-confirmed)
# ---------------------------------------------------------------------------
def cmd_plan(args: argparse.Namespace) -> int:
    """`dos plan` — the work-terrain board: every phase, the plan's claim vs the oracle.

    Detail: docs/CLI.md § cmd_plan.
    """
    _apply_workspace(args)
    cfg = _config.active()
    if not (cfg.root / "dos.toml").exists():
        _ensure_initialized(cfg)
        _apply_workspace(args)
        cfg = _config.active()

    # Positional phases are an EXPLICIT (plan, phase) row list — the no-schema escape
    # hatch. They come as a flat token stream; pair them up (an odd trailing token is a
    # plan with an empty phase, which the oracle reads as "no phase").
    rows = _parse_explicit_phases(getattr(args, "phases", None))
    source_name = getattr(args, "source", None)

    from dos import plan_board as _pb

    if args.json:
        frame = _pb.snapshot(cfg, rows=rows, source_name=source_name)
        print(json.dumps(frame.to_dict(), indent=2, default=str))
        return 0

    from dos import plan_board_tui as _tui
    return _tui.run_plan(
        cfg, once=args.once, interval=args.interval, rows=rows, source_name=source_name,
    )


def _parse_explicit_phases(tokens):
    """Pair a flat ``[plan, phase, plan, phase, …]`` token stream into PlanRows, or None.

    Detail: docs/CLI.md § _parse_explicit_phases.
    """
    if not tokens:
        return None
    from dos import plan_source as _ps
    out = []
    it = list(tokens)
    for i in range(0, len(it), 2):
        plan = it[i]
        phase = it[i + 1] if i + 1 < len(it) else ""
        out.append(_ps.PlanRow(plan=plan, phase=phase))
    return out


# ---------------------------------------------------------------------------
# decisions  (the operator-decision queue — list + drill-in TUI)
# ---------------------------------------------------------------------------
def cmd_decisions(args: argparse.Namespace) -> int:
    """List the pending operator decisions, or drill into one.

    Detail: docs/CLI.md § cmd_decisions.
    """
    _apply_workspace(args)
    from dos import decisions as _decisions
    cfg = _config.active()
    resolver = None if args.all else "HUMAN"
    rows = _decisions.collect_decisions(cfg, resolver=resolver)

    # Parse the optional drill-in target: `dos decisions N` or `decisions show N`.
    show_index: int | None = None
    target = [t for t in (args.target or []) if t.lower() != "show"]
    if target:
        try:
            show_index = int(target[0])
        except ValueError:
            print(f"expected a decision number, got {target[0]!r}", file=sys.stderr)
            return 2

    # Legacy `--json` keeps its exact bytes (indent=2). `--output json` resolves
    # to the same form (JsonRenderer.render_decisions matches it), so the two
    # coincide; the explicit `--output` is handled in the plain-list path below.
    if args.json and not getattr(args, "output", None):
        print(json.dumps([d.to_dict() for d in rows], indent=2, default=str))
        return 0

    # `show <#>` — non-interactive drill-in for one decision (1-based index).
    # This is a per-decision DETAIL projection (the TUI's detail pane), not the
    # list surface the renderer seam covers; it stays on render_detail_plain.
    if show_index is not None:
        if not rows:
            print("no pending decisions", file=sys.stderr)
            return 1
        if show_index < 1 or show_index > len(rows):
            print(f"decision #{show_index} out of range (1..{len(rows)})", file=sys.stderr)
            return 2
        print(_decisions.render_detail_plain(rows[show_index - 1], cfg))
        return 0

    # The interactive TUI is an out-of-scope curses surface (docs/72 Phase 3
    # "Out of scope"); it is only entered when no `--output` was requested. An
    # explicit `--output` forces the one-shot list render through the seam.
    use_tui = (not args.no_tui) and sys.stdout.isatty() and not getattr(args, "output", None)
    if use_tui:
        from dos import decisions_tui
        return decisions_tui.run_tui(cfg, resolver=resolver)
    # The plain LIST surface routes through the renderer seam (RND): the default
    # `text` renderer's render_decisions IS `render_list_plain` (byte-identical),
    # and `--output terse`/etc. selects a workspace renderer, falling back to
    # text for a renderer that doesn't implement the surface.
    return _render_to_stdout(args, "render_decisions", rows)


# ---------------------------------------------------------------------------
# notify  (the notification spine — push a projection to a transport, docs/225)
# ---------------------------------------------------------------------------
def cmd_notify(args: argparse.Namespace) -> int:
    """`dos notify {decisions,top}` — push a read-only projection to a transport.

    Detail: docs/CLI.md § cmd_notify.
    """
    _apply_workspace(args)
    from dos import notify as _notify
    cfg = _config.active()

    # --- build the Notification from the requested projection ------------------
    surface = args.notify_cmd
    if surface == "decisions":
        from dos import decisions as _decisions
        resolver = None if getattr(args, "all", False) else "HUMAN"
        rows = _decisions.collect_decisions(cfg, resolver=resolver)
        summary = _decisions.render_list_plain(rows)
        note = _notify.notification_for_decisions(
            rows, summary=summary, top=max(0, int(getattr(args, "top", 5))))
    elif surface == "top":
        from dos import dispatch_top as _dtop
        frame = _dtop.snapshot(cfg)
        summary = _dtop.render_frame_text(frame)
        note = _notify.notification_for_top(frame, summary=summary)
    else:  # pragma: no cover - argparse `required=True` prevents this
        print("error: `dos notify` needs a surface (decisions | top)", file=sys.stderr)
        return 2

    # --- resolve the notifier (loud on an unknown name) ------------------------
    notifier_name = getattr(args, "notifier", None) or "null"
    kwargs: dict = {}
    if notifier_name != "null":
        # Transport occupants take a subset of channel/url/token/dry-run/root; the
        #   (full prose: docs/CLI.md § "Transport occupants take a subset of channel/url/token/dry-r")
        kwargs = {
            "channel": getattr(args, "channel", "") or "",
            "url": getattr(args, "url", "") or "",
            "token": getattr(args, "token", "") or "",
            "dry_run": bool(getattr(args, "dry_run", False)),
            "root": str(cfg.root),
        }
    try:
        notifier = _notify.resolve_notifier(notifier_name, **kwargs)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    result = _notify.send_safely(notifier, note)
    delivered_via_null = notifier_name == "null"

    if args.json:
        print(json.dumps(
            {"notification": note.to_dict(), "result": result.to_dict(),
             "notifier": notifier_name},
            indent=2, default=str))
        return 0

    # Plain: the Notification headline + fields, then the delivery line.
    emoji = {"INFO": "·", "WARN": "▲", "URGENT": "■"}.get(note.severity.value, "·")
    print(f"{emoji} [{note.severity.value}] {note.title}")
    for label, value in note.fields:
        print(f"    {label}: {value}")
    if delivered_via_null:
        print("  → not sent (notifier=null; pass --notifier slack --channel NAME to deliver)")
    else:
        status = "sent" if result.delivered else "NOT sent"
        print(f"  → {status} via {notifier_name}: {result.detail}")
    # Exit non-zero only when a REAL send was attempted and failed (so a cron can
    # alert on a broken transport); the null/dry-run no-op is a success.
    if (not delivered_via_null and not bool(getattr(args, "dry_run", False))
            and not result.delivered):
        return 1
    return 0


# ---------------------------------------------------------------------------
# trace  (the cross-surface join — walk one run across spine + ledger + WAL + git)
# ---------------------------------------------------------------------------
def cmd_trace(args: argparse.Namespace) -> int:
    """Walk one run across every DOS surface, joined by its run_id (docs/137).

    Detail: docs/CLI.md § cmd_trace.
    """
    _apply_workspace(args)
    from dos import trace as _trace
    cfg = _config.active()
    run_id = (args.run_id or "").strip()
    if not run_id:
        print("error: a run-id is required (e.g. dos trace RID-…)", file=sys.stderr)
        return 2
    frame = _trace.build_trace(run_id, cfg)
    if args.json:
        print(json.dumps(frame.to_dict(), indent=2, default=str))
    else:
        print(_trace.render_text(frame))
    return 0 if frame.found else 1


# ---------------------------------------------------------------------------
# observe  (the verdict-journal projection — every adjudication, folded; docs/262)
# ---------------------------------------------------------------------------
def cmd_observe(args: argparse.Namespace) -> int:
    """Project the verdict journal: the kernel's own adjudication stream (docs/262).

    Detail: docs/CLI.md § cmd_observe.
    """
    _apply_workspace(args)
    from dos import observe as _observe
    run = (getattr(args, "run", "") or "").strip()
    syscall = (getattr(args, "syscall", "") or "").strip()
    by = (getattr(args, "by", "") or "syscall").strip()
    tail_n = int(getattr(args, "tail", 0) or 0)

    frame = _observe.build_frame(run=run, syscall=syscall, by=by)

    if args.json:
        out = frame.to_dict()
        if tail_n > 0:
            out["events"] = out["events"][-tail_n:]
        print(json.dumps(out, indent=2, default=str))
        return 1 if frame.corrupt else 0

    # A run filter or an explicit --tail wants the event HISTORY; the bare verb
    # (and a --syscall/--by filter without --run) wants the ROLLUP table.
    if run or tail_n > 0:
        print(_observe.render_history_text(frame, limit=tail_n))
    else:
        print(_observe.render_rollup_text(frame))
    return 1 if frame.corrupt else 0


# ---------------------------------------------------------------------------
# census  (the verdict-USAGE census — per-verb invocation counts + the
#          never-fired/orphan list, folded over BOTH telemetry logs; issue #20)
# ---------------------------------------------------------------------------
def cmd_census(args: argparse.Namespace) -> int:
    """Project per-verb invocation counts + the never-fired orphan list (issue #20).

    Folds the verdict journal (docs/262) AND the hook observation log (docs/297)
    into one per-verb count over the derived CLI verb universe, surfacing which
    verdict-bearing surfaces never fired. Read-only, the `dos observe` discipline.
    """
    _apply_workspace(args)
    from dos import verdict_census as _census

    c = _census.build_census()
    if args.json:
        print(_census.render_json(c))
        return 1 if c.corrupt else 0
    print(_census.render_text(c))
    return 1 if c.corrupt else 0


# ---------------------------------------------------------------------------
# headline  (the quotable, receipt-linked one-liner over the hook observation
#            log — share-shaped, honest zeros + coverage; issue #71)
# ---------------------------------------------------------------------------
def cmd_headline(args: argparse.Namespace) -> int:
    """Emit a quotable, receipt-linked headline over the observation log (issue #71).

    Folds ONLY the per-call observation log (docs/297) — never the lane journal,
    the like-for-like discipline — into a one-liner an operator can paste, with
    honest zeros + a coverage clause. `--receipts` expands each nonzero count to
    its env-authored records + the command that regenerates the verdict. Read-only.
    """
    _apply_workspace(args)
    cfg = _config.active()
    from dos import hook_observation as _hobs

    since = (getattr(args, "since", "") or "").strip()
    with_receipts = bool(getattr(args, "receipts", False))
    try:
        records = list(_hobs.read_observations(cfg=cfg))
    except Exception:  # noqa: BLE001 — a read fault degrades to "nothing", never a crash
        records = []
    summary = _hobs.headline_summary(records, since=since,
                                     with_receipts=with_receipts or bool(args.json))
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, default=str))
        return 0
    print(_hobs.render_headline_text(summary, with_receipts=with_receipts))
    return 0


# ---------------------------------------------------------------------------
# helped  (the operator-facing "what did DOS catch for me?" rollup — the last
#          observability rung, from the WAL out to the human; help_summary.py)
# ---------------------------------------------------------------------------
def cmd_helped(args: argparse.Namespace) -> int:
    """`dos helped` — surface how many things DOS productively caught for the operator.

    Detail: docs/CLI.md § cmd_helped.
    """
    _apply_workspace(args)
    cfg = _config.active()
    from dos import help_summary as _help
    from dos import lane_journal as _lane_journal
    from dos import lane_lease as _lane_lease

    holder = (getattr(args, "session", "") or "").strip()
    since = (getattr(args, "since", "") or "").strip()
    explain = bool(getattr(args, "explain", False))
    try:
        records = _lane_journal.read_all(path=_lane_lease._journal_path(cfg))
    except Exception:  # noqa: BLE001 — a read fault degrades to "nothing", never a crash
        records = []
    # `--explain` (and `--json`) bank the per-reason examples; the bare rollup doesn't.
    summary = _help.summarize(records, holder=holder, since=since,
                              with_examples=explain or bool(getattr(args, "json", False)))

    # The denominator (docs/297 P2, issue #24): the per-call hook-observation log,
    # read ONCE at this boundary. Like-for-like only — BOTH sides of the rate come
    # from that one log; the lane-journal counts above never enter it. Graceful
    # absence: no log / nothing adjudicated / an unreadable file → rate stays None
    # and every output below is byte-identical to the rate-less form. A --session
    # scope also suppresses it (observation records carry no session key, so a
    # session-scoped rate would silently widen to the whole fleet — dishonest).
    rate = None
    if not holder:
        from dos import hook_observation as _hobs
        try:
            folded = _hobs.intervention_rate(
                _hobs.read_observations(cfg=cfg), since=since)
            if folded.adjudicated > 0:
                rate = folded
        except Exception:  # noqa: BLE001 — a rate fold fault degrades to "no rate line"
            rate = None

    if getattr(args, "json", False):
        payload = summary.to_dict()
        if rate is not None:
            payload["tool_calls"] = rate.to_dict()
        print(json.dumps(payload, indent=2, default=str))
        return 0

    scope = ""
    if holder:
        scope = f"session {holder}"
    if since:
        scope = (scope + " · " if scope else "") + f"since {since}"
    if explain:
        print(_help.render_explain_text(summary, scope=scope))
    else:
        print(_help.render_summary_text(summary, scope=scope, rate=rate))
    return 0


# ---------------------------------------------------------------------------
# export  (the verdict-journal DRAIN — ship the stream outward to observability;
#   (full prose: docs/CLI.md § "export  (the verdict-journal DRAIN — ship the stream outward")
# ---------------------------------------------------------------------------
def cmd_export(args: argparse.Namespace) -> int:
    """`dos export --to NAME` — drain the verdict journal outward to a backend.

    Detail: docs/CLI.md § cmd_export.
    """
    _apply_workspace(args)
    from dos import exporter as _exporter
    from dos import export_cursor as _cursor
    cfg = _config.active()

    exporter_name = getattr(args, "to", None) or "null"
    drained_via_null = exporter_name == "null"

    # --- resolve the exporter once (loud on an unknown name) -------------------
    kwargs: dict = {}
    if not drained_via_null:
        # Transport occupants take a subset of path/host/port/endpoint/dry-run/root; the
        # null sink takes none. Build the SUPERSET, then let resolve filter it to each
        # constructor's accepted params (so `file` ignores host/port, `statsd` ignores
        # path) without the CLI branching per driver — the `cmd_notify` posture.
        kwargs = {
            "path": getattr(args, "path", "") or "",
            "host": getattr(args, "host", "") or "",
            "port": getattr(args, "port", 0) or 0,
            "endpoint": getattr(args, "endpoint", "") or "",
            "dry_run": bool(getattr(args, "dry_run", False)),
            "root": str(cfg.root),
        }
    try:
        exporter = _exporter.resolve_exporter(exporter_name, **kwargs)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # --- resolve the --since offset (supports the `auto` persisted-cursor sentinel) ---
    # The per-transport suffix keeps a `file` drain and an `otlp` drain from clobbering
    # each other's progress (each tracks `.dos/export-cursor.<transport>`).
    try:
        since, auto = _cursor.resolve_since(
            getattr(args, "since", "") or "", transport=exporter_name)
    except ValueError:
        print("error: --since must be an integer seq cursor or 'auto' "
              f"(got {getattr(args, 'since', '')!r})", file=sys.stderr)
        return 2

    follow = bool(getattr(args, "follow", False))
    persist = auto or follow  # --follow always threads the cursor forward across ticks
    as_json = bool(args.json)

    def _drain_once(since_floor: int):
        """One read → slice → ship. Returns (result, pending, high_water). Read-only
        on DOS state apart from the cursor file (written by the caller, fail-soft)."""
        from dos import verdict_journal as _vj
        tail_n = int(getattr(args, "tail", 0) or 0)
        raw = _vj.tail(tail_n) if tail_n > 0 else _vj.read_all()
        evs = [
            _vj.VerdictEvent.from_record(rec)
            for rec in raw
            if rec.get("op") != "_CORRUPT"
        ]
        if since_floor:
            evs = [e for e in evs if int(getattr(e, "seq", 0) or 0) > since_floor]
        res = _exporter.export_safely(exporter, evs)
        # The new high-water mark: the result's cursor when it shipped something, else
        # the floor we came in with (an empty drain does not move the cursor backward).
        hw = since_floor
        if res.cursor:
            try:
                hw = max(hw, int(res.cursor))
            except (TypeError, ValueError):
                pass
        return res, len(evs), hw

    def _render(result, pending: int, since_floor: int) -> None:
        if as_json:
            print(json.dumps(
                {"result": result.to_dict(), "exporter": exporter_name,
                 "shipped": pending, "since": since_floor, "persist": persist},
                indent=2, default=str))
            return
        print(f"# export · {pending} event(s) pending"
              + (f" (since seq {since_floor})" if since_floor else "")
              + f" · transport={exporter_name}")
        if drained_via_null:
            print(f"  → {result.detail}")
            print("  (pass --to file --path PATH to ship; --to statsd/otlp for metrics/traces)")
        else:
            verb = "shipped" if result.exported else "NOT shipped"
            print(f"  → {verb} {result.exported}/{pending} via {exporter_name}: {result.detail}")
        if result.cursor:
            tail = "  (persisted)" if persist else f"  (resume with --since {result.cursor})"
            print(f"  cursor: {result.cursor}{tail}")

    def _maybe_persist(high_water: int, shipped_ok: bool) -> None:
        # Persist only when the operator opted in (`auto`/`--follow`) AND a real
        # transport actually shipped (a null/dry-run/failed drain must not advance the
        # cursor past unshipped events — fail-soft, the docs/266 §1c "ship past the last
        # SHIPPED seq" rule). The write itself is fail-soft (never crashes the drain).
        if persist and not drained_via_null and shipped_ok and high_water > 0:
            _cursor.write_cursor(high_water, transport=exporter_name)

    # --- single drain (the default + every cron/`/loop` tick) ------------------
    if not follow:
        result, pending, high_water = _drain_once(since)
        _render(result, pending, since)
        shipped_ok = (result.exported > 0)
        _maybe_persist(high_water, shipped_ok)
        # Non-zero only when a REAL transport was asked to ship events and shipped none.
        if (not drained_via_null and not bool(getattr(args, "dry_run", False))
                and pending > 0 and result.exported == 0):
            return 1
        return 0

    # --- bounded --follow loop (foreground convenience; NOT a daemon) ----------
    #   (full prose: docs/CLI.md § "--- bounded --follow loop (foreground convenience; NOT a dae")
    import time

    interval = max(0.0, float(getattr(args, "follow_interval", 0) or 2.0))
    max_iter = int(getattr(args, "follow_max", 0) or 0)
    floor = since
    iters = 0
    last_rc = 0
    try:
        while True:
            result, pending, high_water = _drain_once(floor)
            _render(result, pending, floor)
            shipped_ok = (result.exported > 0)
            _maybe_persist(high_water, shipped_ok)
            if shipped_ok:
                floor = high_water  # advance so the next tick only sees new events
            if (not drained_via_null and not bool(getattr(args, "dry_run", False))
                    and pending > 0 and result.exported == 0):
                last_rc = 1  # a broken collector this tick — surface it, keep following
            iters += 1
            if max_iter and iters >= max_iter:
                break
            if interval > 0:
                time.sleep(interval)
    except KeyboardInterrupt:  # pragma: no cover - interactive ^C
        if not as_json:
            print("\n(stopped following)", file=sys.stderr)
    return last_rc


# ---------------------------------------------------------------------------
# memory  (re-verify recalled agent-memory claims — docs/103, the recall driver)
# ---------------------------------------------------------------------------
def cmd_memory(args: argparse.Namespace) -> int:
    """Re-verify an agent-memory store at recall time (docs/103).

    Detail: docs/CLI.md § cmd_memory.
    """
    _apply_workspace(args)
    try:
        mr = _load_memory_recall()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    cfg = _config.active()
    store = getattr(args, "store", None) or None
    store_kind = getattr(args, "store_kind", "") or "file"
    explain = bool(getattr(args, "explain", False))

    if args.memory_cmd == "admit":
        # docs/314 P1 — the WRITE gate: adjudicate a candidate memory's bytes
        # BEFORE they enter any store. Text from --text-file or stdin; the
        # candidate touches no store, the writer consumes the verdict.
        if getattr(args, "text_file", None):
            try:
                text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"error: cannot read --text-file: {e}", file=sys.stderr)
                return 2
        else:
            text = sys.stdin.read()
        if not text.strip():
            print("error: empty candidate (pass --text-file or pipe the memory "
                  "body on stdin)", file=sys.stderr)
            return 2
        v = mr.admit_text(text, name=getattr(args, "name", "") or "candidate", cfg=cfg)
        d = v.to_dict()
        if explain:
            d["interpretation"] = mr.interpret_admission(d)
        if args.json or getattr(args, "output", None) == "json":
            print(json.dumps(d, sort_keys=True, ensure_ascii=False))
        else:
            print(f"{d['admission']}  {d['memory']}")
            print(f"  {v.reason}")
            if explain:
                print(f"  → {d['interpretation']}")
        # The verdict IS the exit code: only POISON refuses (3); every admit
        # typing exits 0 — the gate types, it does not censor.
        return 3 if d["admission"] == "REJECT_POISON" else 0

    if args.memory_cmd == "recall":
        try:
            v = mr.recall_one(args.name, cfg=cfg, store=store, store_kind=store_kind)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        d = v.to_dict()
        if explain:
            d["interpretation"] = mr.interpret(d)
        if args.json or getattr(args, "output", None) == "json":
            print(json.dumps(d, sort_keys=True, ensure_ascii=False))
        else:
            print(f"{d['verdict']}  {d['memory']}")
            print(f"  {v.reason}")
            if explain:
                print(f"  → {d['interpretation']}")
        # The verdict IS the exit code so a recall hook can branch: FRESH=0,
        # STALE=3, UNVERIFIABLE=0 (surfaceable, not a failure).
        return 3 if d["verdict"] == "RECALL_STALE" else 0

    # `verify` — sweep the whole store. Fossils (docs/314 P4) answer a
    # byte-unchanged STALE from the journal; `--reprobe` forces the full probe.
    try:
        verdicts = mr.sweep(cfg=cfg, store=store, store_kind=store_kind,
                            consult_fossils=not getattr(args, "reprobe", False))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    routed = 0
    if getattr(args, "route", False):
        try:
            routed = mr.route(verdicts, cfg=cfg)
        except ValueError as e:
            print(f"error: --route failed: {e}", file=sys.stderr)
            return 2
    # Flap detection (docs/314 P4): a memory whose journaled verdict history
    # resurrected (STALE → later FRESH) is surfaced as suspicious — claim
    # history is itself evidence. Read-only over the verdict journal.
    flaps = mr.flap_suspects_for(cfg)

    if args.json or getattr(args, "output", None) == "json":
        rows = []
        for v in verdicts:
            d = v.to_dict()
            hist = flaps.get(v.evidence.mem_name)
            if hist:
                d["flap_history"] = list(hist)
            rows.append(d)
        print(json.dumps(rows, sort_keys=True, ensure_ascii=False))
        return 0
    from collections import Counter
    tally = Counter(v.verdict.value for v in verdicts)
    print(f"# recall sweep — {len(verdicts)} memories  ·  "
          + "  ".join(f"{k.replace('RECALL_', '')}:{tally[k]}"
                      for k in ("RECALL_STALE", "RECALL_UNVERIFIABLE", "RECALL_FRESH")))
    for v in verdicts:
        if v.verdict.value == "RECALL_FRESH":
            continue  # the list leads with what needs attention; FRESH is the floor
        cul = v.culprit
        marker = "  [fossil]" if v.fossil_ts else ""
        head = f"  {v.verdict.value.replace('RECALL_', ''):<12}  {v.evidence.mem_name}{marker}"
        print(head)
        if cul is not None:
            print(f"      {cul.claim.raw[:60]!r} → {cul.ground_truth[:80]}")
    for name, hist in sorted(flaps.items()):
        print(f"  ⚠ flapped      {name}  ({'→'.join(t.replace('RECALL_', '') for t in hist)}"
              f" — verdict history resurrected; treat its claims with suspicion)")
    if routed:
        print(f"\n  → routed {routed} non-FRESH verdict(s) to `dos decisions` (OP_REFUSE)")
    return 0


# ---------------------------------------------------------------------------
# doctor  (report the active workspace + taxonomy)
# ---------------------------------------------------------------------------
def _runtime_hook_status(root: Path) -> list[tuple[str, list[str]]]:
    """For each known runtime, which DOS hook events are wired in `root`. READ-ONLY.

    Detail: docs/CLI.md § _runtime_hook_status.
    """
    from dos import hook_install as _hi

    out: list[tuple[str, list[str]]] = []
    for name in _hi.host_names():
        try:
            spec = _hi.host_spec(name)
            path = root.joinpath(*spec.config_path)
            if not path.exists():
                out.append((name, []))
                continue
            if spec.fmt is _hi.ConfigFormat.TOML:
                evs = _hi.wired_events_toml(path.read_text(encoding="utf-8"), spec)
            else:
                evs = _hi.wired_events_json(json.loads(path.read_text(encoding="utf-8")), spec)
            out.append((name, evs))
        except Exception:
            out.append((name, []))
    return out


def _dot_dos_facts(cfg: "_config.SubstrateConfig") -> dict:
    """The `.dos` surface, sized (docs/313 P4): the policy provenance (`dos.toml`
    declared vs the generic default), the schema-versioned identity card, and
    which per-project fossils exist with how big each has grown. The operator's
    "what does my `.dos` know?" view — the throughline page (docs/DOT_DOS.md)
    explains the surface this reports. Boundary I/O, read-only and fail-soft:
    doctor reports, never writes — a missing/unreadable file reads as absent
    (None), never created and never a crashed report row.
    """
    root = cfg.paths.root

    def _jsonl(p) -> "dict | None":
        try:
            if p is None or not p.is_file():
                return None
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                rows = sum(1 for line in fh if line.strip())
            return {"path": str(p), "rows": rows, "bytes": p.stat().st_size}
        except OSError:
            return None

    def _dir(p) -> "dict | None":
        try:
            if p is None or not p.is_dir():
                return None
            return {"path": str(p), "entries": sum(1 for _ in p.iterdir())}
        except OSError:
            return None

    card = None
    if cfg.paths.project_card is not None:
        try:
            raw = json.loads(cfg.paths.project_card.read_text(encoding="utf-8"))
            card = {
                "path": str(cfg.paths.project_card),
                "schema": raw.get("schema"),
                "project_id": raw.get("project_id"),
                "created_at": raw.get("created_at"),
            }
        except (OSError, ValueError):
            card = None

    from dos import hook_observation as _hobs
    from dos import posttool_sensor as _post
    return {
        "config_declared": (root / "dos.toml").is_file(),
        "project_card": card,
        "fossils": {
            "lane_journal": _jsonl(cfg.paths.lane_journal),
            "verdict_journal": _jsonl(cfg.paths.verdict_journal),
            "observations": _jsonl(_hobs.observations_path(cfg)),
            "runs": _dir(cfg.paths.fanout_runs),
            "streams": _dir(_post.streams_dir_for(cfg)),
        },
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    _apply_workspace(args)
    cfg = _config.active()
    # The completeness findings are computed once and shared by both the text and
    # the JSON path, so `--check` behaves identically whichever output is selected
    # (SKP Phase 1a fix: a `--json` path that skipped `--check` would silently drop
    # the contract `test_stamp_doctor` pins). `check_requested` gates the exit code.
    check_requested = bool(getattr(args, "check", False))
    findings: list[str] = []
    # `info`-severity config-lint findings (a dead doc cross-ref) are surfaced but
    # do NOT gate the exit code (docs/227 §4: info is cosmetic). `gating` tracks
    # whether any error/warn finding was seen, so a clean-but-for-info report still
    # exits 0. The stamp/state rails are always-gating (they predate severities).
    gating = False
    if check_requested:
        stamp_finding = _stamp_coverage_finding(cfg)
        if stamp_finding:
            findings.append(stamp_finding)
            gating = True
        # The config-integrity linter (docs/227, G1 from docs/189): one pure kernel
        #   (full prose: docs/CLI.md § "The config-integrity linter (docs/227, G1 from docs/189): on")
        # The plan-namespace rail (docs/317 P2) rides the same call: the duplicate-
        # number map is gathered from the workspace's plans glob at this boundary.
        from dos import config_lint as _cl
        from dos.phase_shipped import _gather_ambiguous_plan_heads as _dup_plans
        for _f in _cl.lint(cfg.lanes, cfg.reasons, duplicate_plans=_dup_plans(cfg)):
            findings.append(_f.line())
            if _f.severity is not _cl.Severity.INFO:
                gating = True
        # State-file health rail: flag a bloated execution-state file (the gap
        # where doctor reported the path but never whether it was healthy).
        _state_findings = _state_health_findings(cfg)
        findings.extend(_state_findings)
        if _state_findings:
            gating = True

    # SKP Phase 1a — `dos doctor --json`: the machine-readable workspace report a
    #   (full prose: docs/CLI.md § "SKP Phase 1a — `dos doctor --json`: the machine-readable wor")
    if getattr(args, "json", False):
        report = {
            "dos_version": __import__("dos").__version__,
            "workspace": str(cfg.paths.root),
            "git": (cfg.paths.root / ".git").exists(),
            "paths": {
                "root": str(cfg.paths.root),
                "execution_state": str(cfg.paths.execution_state),
                "plans_glob": cfg.paths.plans_glob,
                "next_packets": str(cfg.paths.next_packets),
                # The run dir a dispatch/loop archives under (so a generic skill
                # reads it here instead of hardcoding a run path). Under the `.dos/`
                # layout the three run trees collapse to one `.dos/runs`.
                "runs": str(cfg.paths.fanout_runs),
                "style": cfg.paths.style,
            },
            "lanes": {
                "concurrent": list(cfg.lanes.concurrent),
                "exclusive": list(cfg.lanes.exclusive),
                "autopick": list(cfg.lanes.autopick),
                "trees": {k: list(v) for k, v in cfg.lanes.trees.items()},
            },
            "stamp": cfg.stamp.to_dict(),
            # docs/207 — the workflow-tier data tables a skill reads to discover the
            # declared phase grammar, anti-churn windows, and plan-class lifecycle.
            "enumerate": cfg.enumerate_grammar.to_dict(),
            "cooldown": cfg.cooldown.to_dict(),
            "lifecycle": cfg.lifecycle.to_dict(),
            # docs/99 — the always-on supervisor's standing population policy
            # (target + reap posture) a skill/operator reads to discover how many
            # dispatch-loops `dos loop` keeps alive here without re-parsing dos.toml.
            "supervise": cfg.supervise.to_dict(),
            # The always-honest verifiability cold-open (machine form): how many of
            #   (full prose: docs/CLI.md § "The always-honest verifiability cold-open (machine form): ho")
            "verifiability": _verifiability_facts(cfg),
            # Discovered FACTS about this workspace, gathered once at config-build
            #   (full prose: docs/CLI.md § "Discovered FACTS about this workspace, gathered once at conf")
            "workspace_facts": (
                {
                    "is_kernel_repo": cfg.workspace.is_kernel_repo,
                    "kernel_runtime_files_present": len(cfg.workspace.kernel_runtime_files),
                }
                if cfg.workspace is not None else None
            ),
            # docs/221 — which agent runtimes have the DOS hooks wired here, so a
            # skill/CI can confirm the enforcement binding took (a mis-wired hook is
            # a silent no-op). Each host → the list of DOS events wired (empty = not
            # wired). Read-only, like the rest of doctor.
            "runtime_hooks": {h: evs for h, evs in _runtime_hook_status(cfg.paths.root)},
            # The environment print (Axis "under-what", docs/115): the
            #   (full prose: docs/CLI.md § "The environment print (Axis "under-what", docs/115): the")
            "env": (cfg.env.to_dict() if cfg.env is not None else None),
            "home": str(_config.active_home().home),
            "projects_indexed": len(_home_list_projects()),
            # ADM Phase 3c — the active admission predicates (built-in
            #   (full prose: docs/CLI.md § "ADM Phase 3c — the active admission predicates (built-in")
            "admission_predicates": _admission_predicate_names(),
            # Axis 7 (docs/113) — the disjointness scorer the arbiter admits on:
            #   (full prose: docs/CLI.md § "Axis 7 (docs/113) — the disjointness scorer the arbiter admi")
            "overlap_policy": {
                "active": cfg.overlap_policy_name or "prefix",
                "available": _overlap_policy_names(),
                "ratio_max": cfg.overlap_ratio_max,
                "floor": "prefix",
            },
            # The verdict-IS-the-exit-code contract, per verb (item 1). An agent
            # reads this once to learn that `verify` exits 0/1, `liveness`
            # SPINNING is 3, `gate` DRAIN is 3, etc. — instead of reverse-
            # engineering `$?`. Sourced from the same maps the handlers return.
            "exit_codes": _exit_code_contract(),
            # docs/313 P4 — the `.dos` surface, sized: policy provenance, the
            # identity card, and each fossil's existence/row-count, so "what
            # does my .dos know?" is one doctor read (docs/DOT_DOS.md).
            "dot_dos": _dot_dos_facts(cfg),
        }
        if check_requested:
            report["findings"] = list(findings)
        print(json.dumps(report, sort_keys=True))
        # Gate on `gating` (error/warn), not the raw findings list — an info-only
        # report (a dead doc cross-ref) is surfaced but exits 0 (docs/227 §4).
        return 1 if (check_requested and gating) else 0

    print(f"DOS v{__import__('dos').__version__}")
    print(f"workspace root      {cfg.paths.root}")
    print(f"execution-state     {cfg.paths.execution_state}")
    print(f"plans glob          {cfg.paths.plans_glob}")
    # SCV (3b) — name the ACTIVE ship-stamp grammar so an operator can see which
    # subject convention `verify`'s grep rung will use against THIS repo. Doctor
    # reports, never writes (design-law 3).
    print(f"stamp convention    {_describe_stamp(cfg.stamp)}")
    # The always-honest cold-open (the iconicity on-ramp): one line, correct on
    #   (full prose: docs/CLI.md § "The always-honest cold-open (the iconicity on-ramp): one lin")
    print(_verifiability_headline(cfg))
    print(f"concurrent lanes    {', '.join(cfg.lanes.concurrent) or '(none)'}")
    print(f"exclusive lanes     {', '.join(cfg.lanes.exclusive) or '(none)'}")
    print(f"autopick ladder     {', '.join(cfg.lanes.autopick) or '(none)'}")
    # ADM Phase 3c — the conjunction of admission predicates the arbiter runs
    # (built-ins first, then discovered plugins). An operator sees exactly what
    # gates a lease here, the predicate analogue of the active reason set.
    print(f"admission predicates {', '.join(_admission_predicate_names())}")
    # Axis 6 — the judges resolvable at the JUDGE rung of the trust ladder
    # (built-in `abstain` first, then discovered `dos.judges` plugins). An operator
    # sees which adjudicators `dos judge-eval` / a JUDGE-routed decision can call,
    # the judge analogue of the active predicates / reason set.
    print(f"judges (JUDGE rung)  {', '.join(_judge_names())}")
    # Axis 8 (docs/121/265) — the non-git evidence sources resolvable as witnesses
    #   (full prose: docs/CLI.md § "Axis 8 (docs/121/265) — the non-git evidence sources resolva")
    _ev_active = (cfg.non_git_oracle or "").strip()
    _ev_all = _evidence_source_names()
    _ev_shown = ", ".join(f"{n}*" if n == _ev_active else n for n in _ev_all)
    print(f"evidence sources    {_ev_shown}"
          + (f"  (verify consults: {_ev_active})" if _ev_active else "  (verify: git-only)"))
    # docs/189 §A1 — the enforcement handlers that CONSUME an intervention decision
    #   (full prose: docs/CLI.md § "docs/189 §A1 — the enforcement handlers that CONSUME an inte")
    print(f"enforce handlers     {', '.join(_enforce_handler_names())}")
    # Axis 7 — the disjointness SCORER the arbiter admits on (docs/113). Shows the
    #   (full prose: docs/CLI.md § "Axis 7 — the disjointness SCORER the arbiter admits on (docs")
    _ov_active = cfg.overlap_policy_name or "prefix"
    _ov_all = _overlap_policy_names()
    _ov_shown = ", ".join(f"{n}*" if n == _ov_active else n for n in _ov_all)
    print(f"overlap policy      {_ov_shown}  (ratio_max={cfg.overlap_ratio_max:.3f}; "
          f"prefix floor always on)")
    # docs/145 — the loop-economics stall reader's active windows (the `[tool_stream]`
    # seam). An operator sees the REPEATING/STALLED run-length thresholds + any
    # exempted pollers `dos tool-stream-eval` (and a consumer's reader) will use here,
    # the stall-reader analogue of the active overlap policy / reason set.
    _sp = cfg.stream_policy
    _ig = ", ".join(sorted(_sp.ignore_tools)) or "(none)"
    print(f"stall reader        REPEATING>={_sp.repeat_n}, STALLED>={_sp.stall_n}  "
          f"(ignore_tools: {_ig})")
    # docs/99 — the always-on supervisor's standing population policy (the
    # `[supervise]` seam). An operator sees how many dispatch-loops `dos loop`
    # keeps alive here + whether a spinner counts as up + whether the dead are
    # reaped, the population analogue of the active stall-reader / overlap policy.
    _su = cfg.supervise
    _spin_halt = (f"{_su.spin_halt_after_ms}ms"
                  if _su.spin_halt_after_ms is not None else "off")
    print(f"supervisor target   {_su.target}  "
          f"(count_spinning_as_alive={'yes' if _su.count_spinning_as_alive else 'no'}, "
          f"reap_stalled={'yes' if _su.reap_stalled else 'no'}, "
          f"spin_halt_after={_spin_halt})")
    git = cfg.paths.root / ".git"
    print(f"is git workspace    {'yes' if git.exists() else 'no'}")
    # docs/221 — which agent runtimes have the DOS hooks wired in THIS workspace, so
    # an adopter can confirm `dos init --hooks <host>` actually took effect (a
    # mis-wired hook is otherwise a silent no-op). Read-only: probes each host's
    # config file under the root, reports which DOS events are present, writes nothing.
    hook_status = _runtime_hook_status(cfg.paths.root)
    if hook_status:
        wired_bits = [f"{h} ({len(evs)})" for h, evs in hook_status if evs]
        bound = ", ".join(wired_bits) if wired_bits else "none wired"
        print(f"runtime hooks       {bound}"
              + ("" if wired_bits else "   (run `dos init --hooks auto` to bind)"))
    # docs/296 — the operator's SELF_MODIFY override window, made visible: an
    # armed window changes what the PRE hook will admit, so the report that
    # answers "what am I configured as?" must show it. Read-only, fail-soft.
    try:
        from dos import override_facts as _ovr
        import datetime as _dt
        _facts = _ovr.read_override(cfg.paths.root)
        if _facts is not None and _dt.datetime.now(_dt.timezone.utc) <= _facts.until:
            _scope = ", ".join(_facts.scope) if _facts.scope else "the whole runtime set"
            print(f"self-mod override   ARMED until {_facts.until.isoformat()} "
                  f"({_facts.reason}) — scope: {_scope}")
        else:
            print("self-mod override   disarmed")
    except Exception:  # noqa: BLE001 — a report row must never break doctor
        print("self-mod override   disarmed")
    print(f"layout style        {cfg.paths.style}")
    # docs/313 P4 — the `.dos` surface, sized: where this repo's policy came
    # from, whether the identity card exists, and how much each per-project
    # fossil has accumulated (the throughline page, docs/DOT_DOS.md).
    _dd = _dot_dos_facts(cfg)
    _dd_card = _dd["project_card"]
    _dd_bits = []
    for _label, _key, _unit in (("WAL", "lane_journal", "rows"),
                                ("verdicts", "verdict_journal", "rows"),
                                ("observations", "observations", "rows"),
                                ("runs", "runs", "entries"),
                                ("streams", "streams", "entries")):
        _fact = _dd["fossils"][_key]
        _dd_bits.append(f"{_label} {_fact[_unit] if _fact else 0}")
    print(f".dos surface        policy: "
          f"{'dos.toml' if _dd['config_declared'] else 'generic default'}; "
          f"card: {('schema ' + str(_dd_card['schema'])) if _dd_card else '(none)'}; "
          + ", ".join(_dd_bits))
    # The environment print (docs/115): *under what* this kernel adjudicates. The
    #   (full prose: docs/CLI.md § "The environment print (docs/115): *under what* this kernel a")
    if cfg.env is not None:
        ep = cfg.env
        sha = (ep.kernel_sha[:12] if ep.kernel_sha else "(no git sha)")
        tools = ", ".join(f"{t.name} {t.version}".strip() for t in ep.tools) or "(none declared)"
        print(f"environment print   {ep.digest}  (kernel v{ep.kernel_version} @ {sha}; "
              f"py {ep.python}; {ep.platform})")
        print(f"  declared tools    {tools}")
    # The machine-local DOS_HOME + how many projects it has registered (read-only;
    # doctor reports, never writes — resolve the home WITHOUT creating it).
    h = _config.active_home()
    n_projects = len(_home_list_projects())
    print(f"dos home            {h.home}  ({n_projects} project(s) indexed)")
    # The env-override hazard (docs/75 §6.4): under the `.dos/` layout, a stray
    # DISPATCH_STATE_PATH / JOB_FANOUT_STATE_PATH in the shell makes verify/judge
    # read THAT file, not the .dos/ one. Surface it loudly rather than inherit it.
    if cfg.paths.style == "dos":
        import os
        for var in ("DISPATCH_STATE_PATH", "JOB_FANOUT_STATE_PATH"):
            val = os.environ.get(var)
            if val:
                print(f"warning: {var} overrides the .dos/ default — verify/judge "
                      f"will read {val}, not {cfg.paths.execution_state}",
                      file=sys.stderr)
    # Completeness rail (`--check`). Two independent rails accumulate findings
    #   (full prose: docs/CLI.md § "Completeness rail (`--check`). Two independent rails accumul")
    if check_requested:
        for f in findings:
            print(f"finding: {f}", file=sys.stderr)
        # Gate on `gating` (error/warn), not the raw findings list — an info-only
        # report (a dead doc cross-ref) is surfaced but exits 0 (docs/227 §4).
        return 1 if gating else 0
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    """`dos lint` — the config-integrity rail as a focused verb (docs/227, G1).

    Detail: docs/CLI.md § cmd_lint.
    """
    _apply_workspace(args)
    cfg = _config.active()
    from dos import config_lint as _cl
    from dos.phase_shipped import _gather_ambiguous_plan_heads as _dup_plans

    findings = _cl.lint(cfg.lanes, cfg.reasons, duplicate_plans=_dup_plans(cfg))
    strict = bool(getattr(args, "strict", False))

    if getattr(args, "json", False):
        print(json.dumps(
            {"findings": [f.to_dict() for f in findings],
             "counts": {
                 "error": sum(1 for f in findings if f.severity is _cl.Severity.ERROR),
                 "warn": sum(1 for f in findings if f.severity is _cl.Severity.WARN),
                 "info": sum(1 for f in findings if f.severity is _cl.Severity.INFO),
             }},
            sort_keys=True))
    else:
        if not findings:
            print("config clean — no dead policy in the lane taxonomy or reason registry")
        for f in findings:
            print(f.line())

    # The gate: `--strict` fails on error only; default fails on error OR warn;
    # info never gates (it is cosmetic). `has_error` is the strict predicate; the
    # default predicate is "any non-info finding."
    if strict:
        return 1 if _cl.has_error(findings) else 0
    return 1 if any(f.severity is not _cl.Severity.INFO for f in findings) else 0


def _exit_code_contract() -> dict:
    """The documented verdict-IS-the-exit-code table, keyed by verb (item 1).

    Detail: docs/CLI.md § _exit_code_contract.
    """
    return {
        "verify": _VERIFY_EXITS.contract(),
        "verify-result": _VERIFY_RESULT_EXITS.contract(),
        "coverage": _COVERAGE_EXITS.contract(),
        "arbitrate": _ARBITRATE_EXITS.contract(),
        "liveness": _LIVENESS_EXITS.contract(),
        "productivity": _PRODUCTIVITY_EXITS.contract(),
        "efficiency": _EFFICIENCY_EXITS.contract(),
        "efficiency-trend": _EFFICIENCY_TREND_EXITS.contract(),
        "work-account": _WORK_ACCOUNT_EXITS.contract(),
        "improve": _IMPROVE_EXITS.contract(),
        "breaker": _BREAKER_EXITS.contract(),
        "exec-capability": _EXEC_CAPABILITY_EXITS.contract(),
        "hook-exit": _HOOK_EXIT_EXITS.contract(),
        "answer-shape": _ANSWER_SHAPE_EXITS.contract(),
        "reward": _REWARD_EXITS.contract(),
        "test-witness": _TEST_WITNESS_EXITS.contract(),
        "resume": _RESUME_EXITS.contract(),
        "rewind": _REWIND_EXITS.contract(),
        "complete": _COMPLETE_EXITS.contract(),
        "status": _STATUS_EXITS.contract(),
        "gate": _GATE_EXITS.contract(),
        "pickable": _PICKABLE_EXITS.contract(),
        "enumerate": _ENUMERATE_EXITS.contract(),
        "cooldown": _COOLDOWN_EXITS.contract(),
        "pick-priority": _PICK_PRIORITY_EXITS.contract(),
        "reconcile": _RECONCILE_EXITS.contract(),
    }


def cmd_exit_codes(args: argparse.Namespace) -> int:
    """Print the verdict-IS-the-exit-code contract, all verbs or one (the on-ramp item).

    Detail: docs/CLI.md § cmd_exit_codes.
    """
    contract = _exit_code_contract()
    verb = getattr(args, "verb", None)
    if verb is not None:
        if verb not in contract:
            print(f"error: {verb!r} is not a verdict-bearing verb. Known: "
                  f"{', '.join(sorted(contract))}", file=sys.stderr)
            return 2
        contract = {verb: contract[verb]}

    if getattr(args, "json", False):
        print(json.dumps(contract, sort_keys=True))
        return 0

    # Text form: one block per verb, the verdict tokens sorted by their code so the
    # success row (0) leads and the contract-error/unknown floors trail — the order
    # a reader scans. The token column is width-aligned within each block.
    print("The exit code IS the verdict. A shell/CI step branches on it directly.\n")
    for name in sorted(contract):
        rows = contract[name]
        width = max((len(tok) for tok in rows), default=0)
        print(f"dos {name}")
        for token, code in sorted(rows.items(), key=lambda kv: (kv[1], kv[0])):
            print(f"  {code}  {token:{width}}")
        print()
    return 0


def cmd_quickstart(args: argparse.Namespace) -> int:
    """Run the DOS money-moment end to end in a throwaway repo (the 60-second on-ramp).

    Detail: docs/CLI.md § cmd_quickstart.
    """
    import shutil
    import subprocess
    import tempfile

    if shutil.which("git") is None:
        print("error: `dos quickstart` needs git on PATH (it builds a tiny real "
              "git repo to verify against).", file=sys.stderr)
        return 2

    spinning = bool(getattr(args, "spinning", False))
    if spinning and getattr(args, "driver", None):
        print("error: --spinning is its own scene — run it without --driver "
              "(the driver scene shows the caught-lie + arbitration demo).",
              file=sys.stderr)
        return 2

    keep_dir = getattr(args, "keep", None)
    if keep_dir is not None:
        work = Path(keep_dir).resolve()
        work.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        work = Path(tempfile.mkdtemp(prefix="dos-quickstart-"))
        cleanup = work

    def _say(line: str = "") -> None:
        print(line)

    def _run_git(*git_args: str) -> None:
        subprocess.run(["git", "-C", str(work), *git_args],
                       check=True, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)  # docs/295

    driver_name = getattr(args, "driver", None)

    try:
        # The --spinning scene (issue #59) — the false-"still working" sibling.
        # Same scaffolding contract as the default scene (throwaway repo, the
        # same cleanup in the finally below); a different lie gets caught.
        if spinning:
            return _quickstart_spinning_act(
                work, _run_git, _say,
                work if keep_dir is not None else None)

        from dos import _demo_story as _story
        from dos import oracle

        # 0. The scene — the universal experience the demo exists to catch. Naming
        #    the two claims up front (login endpoint / password reset) is what makes
        #    the catch legible to someone who has never heard of a "phase". Every
        #    story string interpolates dos._demo_story — the canonical example's
        #    single source (tests/test_canonical_example_lockstep.py pins the
        #    hand-written copies in docs/examples/figures to the same strings).
        _say("# The story: you asked a coding agent for a login feature. It replied:")
        _say(f"#   {_story.AGENT_CLAIM}")
        _say("# One claim is true. One never landed. Catching the difference — from the")
        _say("# artifacts, never the transcript — is the demo. Two parts, one throwaway repo.")
        _say()
        _say('# --- Part 1 — catch the false "done" '
             '-----------------------------------------')
        _say()

        # 1. Scaffold the workspace. Two modes:
        #   (full prose: docs/CLI.md § "1. Scaffold the workspace. Two modes:")
        if driver_name:
            try:
                taxonomy = _resolve_driver_taxonomy(driver_name)
            except (ImportError, AttributeError, ValueError) as e:
                print(f"error: --driver {driver_name!r} could not be resolved: {e}\n"
                      f"  drivers live in dos.drivers.<name>; try --driver workshop "
                      f"(the reference template).", file=sys.stderr)
                return 2
            cfg_text = _render_driver_config(driver_name, taxonomy)
            (work / "dos.toml").write_text(cfg_text, encoding="utf-8")
            _say(f"$ dos init . --example {driver_name}")
            _say(f"  wrote {work / 'dos.toml'}  (driver: {driver_name})")
            _say(f"  lanes: {', '.join(taxonomy.trees)}")
            _say()
        else:
            # Seed two real top-level dirs FIRST so `dos init` auto-derives a
            # two-lane concurrent taxonomy (docs, src) — the fleet act below needs
            # two disjoint regions to referee. The transcript stays honest: init
            # genuinely derives these lanes from dirs that exist on disk.
            (work / "src").mkdir(parents=True, exist_ok=True)
            (work / "docs").mkdir(parents=True, exist_ok=True)
            (work / "docs" / "notes.md").write_text("# demo notes\n",
                                                    encoding="utf-8")
            # We render it directly (not via cmd_init) so the demo is self-contained
            # and the summary line matches what `dos init` prints.
            cfg_text, summary = _render_init_config(work)
            (work / "dos.toml").write_text(cfg_text, encoding="utf-8")
            _say("$ dos init .    # the demo repo has src/ and docs/ — top-level "
                 "dirs become lanes")
            _say(f"  wrote {work / 'dos.toml'}")
            for ln in summary.splitlines():
                _say(f"  {ln}")
            _say()

        # 2. A real commit whose subject stamps AUTH1 (the ship-stamp the oracle
        #    reads). Throwaway identity + gpgsign off so it runs on any machine.
        _run_git("init", "-q")
        _run_git("config", "user.email", "quickstart@example.com")
        _run_git("config", "user.name", "DOS Quickstart")
        _run_git("config", "commit.gpgsign", "false")
        # Default mode ships the endpoint under src/ (the lane the fleet act
        # arbitrates over); driver mode keeps the original root-level file.
        login_path = (work / _story.WORK_FILE) if driver_name \
            else (work / "src" / _story.WORK_FILE)
        login_path.write_text(_story.WORK_CONTENT, encoding="utf-8")
        _run_git("add", "-A")
        _run_git("commit", "-q", "-m", _story.COMMIT_SUBJECT)
        head = subprocess.run(["git", "-C", str(work), "rev-parse", "--short", "HEAD"],
                              check=True, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL).stdout.strip()  # docs/295
        _say(f"# {_story.SHIPPED_FEATURE.capitalize()} really did land — one commit, "
             "stamped with its work-unit")
        _say(f"# id (any letters+digit token at the front of a subject; "
             f"{_story.SHIPPED_PHASE} here):")
        _say(f"$ git commit -m '{_story.COMMIT_SUBJECT}'")
        _say(f"  [committed {head}]")
        _say()

        # 3. The verdicts — through the kernel, against the throwaway workspace.
        #    Build a config rooted at `work` so the oracle reads THIS repo's git
        #    (the same loader the CLI and MCP server use), and render through the
        #    selected renderer so the lines are byte-identical to a real `dos verify`.
        from dos import render
        cfg = _config.load_workspace_config(workspace=work)
        renderer = render.resolve_renderer(_resolve_output_name(args))

        _say(f'# Claim 1 — "{_story.SHIPPED_FEATURE} ({_story.SHIPPED_PHASE}) '
             'shipped." Ask git, not the agent:')
        _say(f"$ dos verify {_story.PLAN} {_story.SHIPPED_PHASE}")
        v1 = oracle.is_shipped(_story.PLAN, _story.SHIPPED_PHASE, cfg=cfg)
        _say(f"  {renderer.render_verdict(v1)}")
        _say(f"  exit={_VERIFY_EXIT_CODES['shipped' if v1.shipped else 'not_shipped']}"
             "  (0 = the verdict is SHIPPED)")
        _say()

        _say(f'# Claim 2 — "{_story.UNSHIPPED_FEATURE} ({_story.UNSHIPPED_PHASE}) '
             'shipped too." Did it?')
        _say(f"$ dos verify {_story.PLAN} {_story.UNSHIPPED_PHASE}")
        v2 = oracle.is_shipped(_story.PLAN, _story.UNSHIPPED_PHASE, cfg=cfg)
        _say(f"  {renderer.render_verdict(v2)}")
        _say(f"  exit={_VERIFY_EXIT_CODES['shipped' if v2.shipped else 'not_shipped']}"
             "  (1 = NOT_SHIPPED — the claim is contradicted by the artifacts)")
        _say()

        # The demo only "worked" if the contrast came out: AUTH1 shipped (off the
        # real commit), AUTH2 not (nothing landed). Anything else is an environment
        # fault we should fail on, not paper over.
        if not (v1.shipped and not v2.shipped):
            print("error: quickstart did not produce the expected SHIPPED/NOT_SHIPPED "
                  "contrast — your git or environment may be unusual.", file=sys.stderr)
            return 2

        _say("That contrast is DOS in two lines: a claim is only believed when the "
             "artifacts back it.")
        _say("And the exit code IS the verdict (0/1) — a script or CI step branches "
             "on it without parsing a word.")

        # 4. The fleet act (default mode) — the admission half of the pitch. The
        #   (full prose: docs/CLI.md § "4. The fleet act (default mode) — the admission half of the")
        if not driver_name:
            _say()
            _say("# --- Part 2 — two agents, one repo "
                 "-------------------------------------------")
            _say("# A 20-agent fleet, or just two coding-agent tabs you opened "
                 "side by side —")
            _say("# same hazard: two writers, one file tree. Before touching "
                 "files, each takes")
            _say("# a LEASE on a disjoint region of the tree (a lane):")
            _say()
            try:
                _quickstart_fleet_act(cfg, _say)
            except Exception as e:  # demo-only beat: never fail the verdict contract
                _say(f"  (skipped the fleet act: {e})")

        # 5. (--driver only) The admission half — show the reference driver's
        #    concurrent-lane arbitration, the thing a stranger can't see from the
        #    truth syscall alone. Two disjoint-tree lanes admit together; the
        #    exclusive release lane refuses while another lease is live.
        if driver_name:
            _say()
            _say(f"# The {driver_name} driver also declares CONCURRENCY policy. "
                 "Two disjoint lanes run at once:")
            try:
                _quickstart_driver_arbitration_beat(work, driver_name, _say)
            except Exception as e:  # demo-only beat: never fail the verdict contract on it
                _say(f"  (skipped the arbitration demo: {e})")

        if keep_dir is not None:
            poke = (f"dos verify --workspace {work} "
                    f"{_story.PLAN} {_story.SHIPPED_PHASE}"
                    if not driver_name else
                    f"dos --workspace {work} doctor   # see the {driver_name} lanes")
            _say(f"\nThe demo repo is at {work} — poke at it: {poke}")
            if not driver_name:
                _say(f"(agent-A and agent-B still hold their leases in its journal "
                     f"— see them:\n dos --workspace {work} lease-lane live)")
        else:
            _say("\nReplay it hands-on:        dos quickstart --keep dos-demo   "
                 "(keeps the repo +")
            _say("                           its lease journal; rerun any command "
                 "above with")
            _say("                           --workspace dos-demo)")
            _say("Run it on your own repo:   dos verify --workspace . PLAN PHASE")
            if driver_name:
                _say(f"Adopt the {driver_name} lanes in your repo:  "
                     f"dos init --example {driver_name}")
            else:
                # The adoption router — one line per way people actually run
                # agents, so a newcomer who is NOT a fleet operator still sees
                # the move that applies to them.
                _say("\nThen plug the verdict in where you already run agents:")
                _say("  an agent runtime   dos init --hooks auto .   (detects "
                     "claude-code, cursor, codex,")
                _say("                     gemini, antigravity, claude-cowork) — "
                     'the host itself refuses a false "done"')
                _say('  an MCP host        {"mcpServers": {"dos": {"command": '
                     '"dos-mcp"}}} — then ask the')
                _say("                     agent to dos_verify its own last claim "
                     "(Claude Desktop, Cline, ...)")
                _say("  a CI step          run dos verify and branch on the exit "
                     "code: 0 shipped / 1 not")
                _say("  a fleet, one repo  dos init .   then   "
                     "dos lease-lane acquire --lane <dir> --owner <id>")
        return 0
    except subprocess.CalledProcessError as e:
        print(f"error: a git step failed during quickstart: "
              f"{(e.stderr or '').strip() or e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: quickstart could not set up the demo repo: {e}", file=sys.stderr)
        return 2
    finally:
        if cleanup is not None:
            # git leaves read-only files under .git/objects on Windows, which a
            #   (full prose: docs/CLI.md § "git leaves read-only files under .git/objects on Windows, wh")
            def _force_rm(func, path, _exc):
                import stat
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except OSError:
                    pass  # truly stuck (open handle) — last resort, don't crash exit
            shutil.rmtree(cleanup, onerror=_force_rm)


def _admission_predicate_names() -> list[str]:
    """The names of the active admission predicates, in conjunction order (ADM 3c).

    Detail: docs/CLI.md § _admission_predicate_names.
    """
    import io
    from dos import admission
    try:
        return admission.active_predicate_names(_stderr=io.StringIO())
    except Exception:
        # Fall back to the always-on built-ins if discovery somehow raises.
        return [getattr(p, "name", type(p).__name__)
                for p in admission.built_in_predicates()]


def _judge_names() -> list[str]:
    """The names of the resolvable judges (Axis 6), built-in then discovered.

    Detail: docs/CLI.md § _judge_names.
    """
    import io
    from dos import judges
    try:
        return judges.active_judge_names(_stderr=io.StringIO())
    except Exception:
        return [judges.AbstainJudge.name]


def _evidence_source_names() -> list[str]:
    """The names of the resolvable evidence sources (Axis 8, docs/121/265), built-in
    then discovered.

    Detail: docs/CLI.md § _evidence_source_names.
    """
    import io
    from dos import evidence
    try:
        return evidence.active_evidence_source_names(_stderr=io.StringIO())
    except Exception:
        return [evidence.NullEvidenceSource.name]


def _enforce_handler_names() -> list[str]:
    """The names of the resolvable enforcement handlers (docs/189 §A1), built-in then
    discovered.

    Detail: docs/CLI.md § _enforce_handler_names.
    """
    import io
    from dos import enforce
    try:
        return enforce.active_handler_names(_stderr=io.StringIO())
    except Exception:
        return [enforce.ObserveHandler.name]


def _overlap_policy_names() -> list[str]:
    """The names of the resolvable overlap policies (Axis 7), built-in then discovered.

    Detail: docs/CLI.md § _overlap_policy_names.
    """
    import io
    from dos import overlap_policy
    try:
        return overlap_policy.active_overlap_policy_names(_stderr=io.StringIO())
    except Exception:
        return [overlap_policy.PrefixOverlapPolicy.name]


# NOTE (docs/227): the lane-integrity rails that used to live here as in-CLI helpers
#   (full prose: docs/CLI.md § "NOTE (docs/227): the lane-integrity rails that used to live")


def _state_health_findings(cfg: _config.SubstrateConfig) -> list[str]:
    """The state-file health rail: is the workspace's execution-state file bloated?

    Detail: docs/CLI.md § _state_health_findings.
    """
    from dos import state_health as _sh

    path = cfg.paths.execution_state
    if path is None or not path.exists():
        return []
    try:
        total_bytes = path.stat().st_size
    except OSError:
        return []
    # Best-effort top-level section row count. A YAML mapping whose values are
    # lists is the execution-state shape; we count list lengths so the cold-section
    # rung has rows to judge. Any parse failure degrades to size-only (the file is
    # still byte-measurable even if its schema moved) — never an error.
    section_rows: dict[str, int] = {}
    try:
        import yaml  # PyYAML is a job-side dep; absent in a bare kernel install.

        loaded = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, dict):
            for key, val in loaded.items():
                if isinstance(val, list):
                    section_rows[str(key)] = len(val)
    except Exception:
        section_rows = {}
    # The cold sections to bound: the completed/abandoned/archive logs every reader
    # windows past. Generic names; a section absent from THIS file is simply skipped
    # by the pure fold (it only judges sections present in section_rows).
    policy = _sh.StateFilePolicy(
        max_total_bytes=_sh.GENERIC_STATE_FILE_POLICY.max_total_bytes,
        cold_section_max_rows=_sh.GENERIC_STATE_FILE_POLICY.cold_section_max_rows,
        cold_sections=("recently_completed", "abandoned", "archived"),
    )
    evidence = _sh.StateFileEvidence(total_bytes=total_bytes, section_rows=section_rows)
    # The clock is handed in (pure-verdict discipline); no obligations are passed,
    # so now_ms only affects rungs we don't exercise here — 0 is a safe sentinel.
    verdict = _sh.classify_state_file(evidence, policy, now_ms=0)
    out = []
    for line in verdict.findings():
        out.append(f"{path.name}: {line}")
    return out


def _stamp_coverage_finding(cfg: _config.SubstrateConfig) -> str | None:
    """Compute the SCV 3c stamp-coverage finding for `cfg` (or None). Reads the
    repo's recent commit subjects and whether its dos.toml declares [stamp];
    delegates the judgement to the pure `stamp.convention_coverage_finding`."""
    import subprocess
    from dos import stamp as _stamp

    # Did this workspace DECLARE a [stamp] table (vs inherit the default)?
    declared = False
    toml_path = cfg.paths.root / "dos.toml"
    if toml_path.exists():
        try:
            try:
                import tomllib  # py3.11+
            except ModuleNotFoundError:  # pragma: no cover
                import tomli as tomllib  # type: ignore
            # utf-8-sig strips a PowerShell BOM (consistent with the loaders).
            data = tomllib.loads(toml_path.read_text(encoding="utf-8-sig"))
            declared = isinstance(data.get("stamp"), dict) and bool(data.get("stamp"))
        except Exception:
            declared = False
    if not declared:
        return None
    # Recent commit subjects from the served workspace (best-effort; a non-git or
    # empty repo yields no subjects → no finding).
    try:
        res = subprocess.run(
            ["git", "log", "--format=%s", "-200"],
            cwd=str(cfg.paths.root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=10,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
        subjects = res.stdout.splitlines() if res.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        subjects = []
    return _stamp.convention_coverage_finding(cfg.stamp, subjects, declared=declared)


def _verifiability_headline(cfg: _config.SubstrateConfig) -> str:
    """The always-honest cold-open: in one line, can DOS check this repo's claims?

    Detail: docs/CLI.md § _verifiability_headline.
    """
    import subprocess
    from dos import stamp as _stamp

    try:
        res = subprocess.run(
            ["git", "log", "--format=%s", "-50"],
            cwd=str(cfg.paths.root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=10,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
        subjects = res.stdout.splitlines() if res.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        subjects = []
    if not subjects:
        return "verifiability        no commits to read (not a git repo, or empty history)"
    ship_shaped = [s for s in subjects if _stamp.ship_shaped_under_generic(s)]
    recognized = [s for s in ship_shaped if cfg.stamp.recognizes_direct_ship(s)]
    grammar = _describe_stamp(cfg.stamp).split("  ", 1)[0]
    if recognized:
        return (
            f"verifiability        {len(recognized)} of your last {len(subjects)} commits "
            f"name a unit of work `dos verify` can check (grammar: {grammar})"
        )
    if ship_shaped:
        return (
            f"verifiability        0 of {len(ship_shaped)} ship-shaped commit(s) match the "
            f"active grammar ({grammar}) — `verify` will resolve `via none`; reconcile [stamp]"
        )
    return (
        "verifiability        none of your last "
        f"{len(subjects)} commits name a unit of work — no referee can check agent claims yet"
    )


def _verifiability_facts(cfg: _config.SubstrateConfig) -> dict:
    """The machine form of `_verifiability_headline` for `dos doctor --json`.

    Detail: docs/CLI.md § _verifiability_facts.
    """
    import subprocess
    from dos import stamp as _stamp

    try:
        res = subprocess.run(
            ["git", "log", "--format=%s", "-50"],
            cwd=str(cfg.paths.root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=10,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
        subjects = res.stdout.splitlines() if res.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        subjects = []
    ship_shaped = [s for s in subjects if _stamp.ship_shaped_under_generic(s)]
    recognized = [s for s in ship_shaped if cfg.stamp.recognizes_direct_ship(s)]
    return {
        "commits_read": len(subjects),
        "ship_shaped": len(ship_shaped),
        "recognized": len(recognized),
        "grammar": _describe_stamp(cfg.stamp).split("  ", 1)[0],
    }


def _describe_stamp(conv: object) -> str:
    """One-line description of a `StampConvention` for `dos doctor` (SCV 3b).

    Detail: docs/CLI.md § _describe_stamp.
    """
    from dos.stamp import JOB_STAMP_CONVENTION

    dirs = tuple(getattr(conv, "subject_dirs", ()) or ())
    style = getattr(conv, "style", "grep")
    if dirs == JOB_STAMP_CONVENTION.subject_dirs:
        label = f"job ({'|'.join(dirs)})"
    elif not dirs:
        label = "generic (any/no dir prefix)"
    else:
        label = "|".join(dirs)
    # docs/289 — the trailer rung widens what this grammar recognizes; the
    # label must say so or the verifiability count reads as unexplained.
    if getattr(conv, "trailer_stamp", False):
        label += " + trailer"
    return f"{label}  [style={style}]"


def _home_list_projects():
    """Read the central project registry without creating DOS_HOME (doctor is
    read-only). Returns [] if the index doesn't exist yet."""
    from dos import home
    return home.list_projects()


# ---------------------------------------------------------------------------
# reindex  (rebuild the central store from the live .dos/ dirs — projection)
# ---------------------------------------------------------------------------
def cmd_reindex(args: argparse.Namespace) -> int:
    from dos import home
    summary = home.reindex(prune=args.prune)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        dropped = summary.get("throwaway", 0)
        extra = f", {dropped} throwaway dropped" if dropped else ""
        print(f"reindexed {summary['projects']} project(s): "
              f"{summary['active']} active, {summary['stale']} stale, "
              f"{summary['moved']} moved{extra}  ·  {summary['decisions']} decision(s)")
        for col in summary.get("id_collisions", []):
            print(f"warning: project_id collision {col['project_id']}: "
                  f"{col['roots']}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# reap  (bound .dos/ scratch to the [retention] caps — docs/106 §3.3/§3.4)
# ---------------------------------------------------------------------------
def cmd_reap(args: argparse.Namespace) -> int:
    """Reap per-project `.dos/` scratch to the workspace's `[retention]` caps.

    Detail: docs/CLI.md § cmd_reap.
    """
    _apply_workspace(args)
    from dos import home
    cfg = _config.active()
    if args.apply:
        _ensure_home_if_persisting()
    report = home.reap_scratch(cfg, apply=args.apply)

    journal_summary = None
    if args.journal:
        import time
        from dos import lane_journal, lane_lease, retention as _retention
        entries = lane_journal.read_all()
        if _retention.should_compact(entries, cfg.retention,
                                     now_ms=int(time.time() * 1000)):
            if args.apply:
                _ensure_home_if_persisting()
                res = lane_lease.compact_journal(cfg)
                journal_summary = {"compacted": True,
                                   "entries_before": res.entries_before,
                                   "entries_after": res.entries_after,
                                   "bytes_reclaimed": res.bytes_reclaimed}
            else:
                journal_summary = {"would_compact": True,
                                   "entries": len(entries),
                                   "threshold": cfg.retention.journal_max_entries}
        else:
            journal_summary = {"compacted": False, "entries": len(entries)}

    if args.json:
        out = dict(report)
        if journal_summary is not None:
            out["journal"] = journal_summary
        print(json.dumps(out, indent=2, default=str, sort_keys=True))
        return 0

    mode = "APPLIED" if args.apply else "DRY-RUN (nothing deleted — pass --apply)"
    print(f"# dos reap — {mode}")
    for key in ("audits", "verdicts", "runs"):
        cls = report.get(key)
        if cls is None:
            continue
        dc = cls.get("data_class", "")
        dc_tag = f"[{dc}] " if dc else ""
        if cls.get("unbounded"):
            print(f"  {key:<9} {dc_tag}cap=none (unbounded — kept all)")
            continue
        n_drop = len(cls["dropped"])
        note = "  [liveness-gate not yet wired — recency fallback]" if cls.get("liveness_unwired") else ""
        verb = "reaped" if args.apply else "would reap"
        print(f"  {key:<9} {dc_tag}cap={cls['cap']}  kept={cls['kept']}  {verb}={n_drop}{note}")
        for name in cls["dropped"][:10]:
            print(f"      - {name}")
        if n_drop > 10:
            print(f"      … and {n_drop - 10} more")
    if journal_summary is not None:
        if journal_summary.get("compacted"):
            print(f"  journal   compacted {journal_summary['entries_before']}"
                  f"→{journal_summary['entries_after']} lines "
                  f"({journal_summary['bytes_reclaimed']} bytes reclaimed)")
        elif journal_summary.get("would_compact"):
            print(f"  journal   would compact ({journal_summary['entries']} lines "
                  f"> {journal_summary['threshold']} threshold)")
        else:
            print(f"  journal   under threshold ({journal_summary['entries']} lines)")
    return 0


# ---------------------------------------------------------------------------
# projects  (the cross-project registry view — read-only)
# ---------------------------------------------------------------------------
def cmd_projects(args: argparse.Namespace) -> int:
    from dos import home
    rows = home.list_projects()
    if args.stale:
        rows = [r for r in rows if r.get("status") != "active"]
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("no projects indexed yet  ·  run a persisting `dos` command, "
              "then `dos reindex`")
        return 0
    print(f"  {'label':<20}  {'status':<7}  {'#runs':>5}  root")
    print("  " + "-" * 70)
    for r in rows:
        print(f"  {(r.get('label') or '?'):<20}  {(r.get('status') or '?'):<7}  "
              f"{r.get('run_count', 0):>5}  {r.get('root', '?')}")
    return 0


# ---------------------------------------------------------------------------
# learn  (cross-project aggregates over the resolved-decision log — read-only)
# ---------------------------------------------------------------------------
def cmd_learn(args: argparse.Namespace) -> int:
    from dos import home
    try:
        tally = home.learn(args.axis)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(tally, indent=2, default=str))
        return 0
    if not tally:
        print(f"no resolved decisions to aggregate for {args.axis!r}  ·  "
              "run `dos reindex` if the index is stale")
        return 0
    print(f"  {args.axis}")
    for row in tally:
        print(f"  {row['count']:>5}  {row['group']}")
    return 0


# ---------------------------------------------------------------------------
# gate  (the typed empty-packet verdict — gate_classify as a verb)
#   (full prose: docs/CLI.md § "gate  (the typed empty-packet verdict — gate_classify as a v")
_GATE_EXITS = ExitMap(
    {"LIVE": 0, "DRAIN": 3, "STALE-STAMP": 4, "BLOCKED": 5, "RACE": 6},
    unknown=7,  # a future kernel verdict the CLI hasn't caught up to — non-zero and
)               # distinct from every known verdict + contract_error, never read as LIVE.
_GATE_EXIT_CODES = _GATE_EXITS.codes
_GATE_EXIT_UNKNOWN = _GATE_EXITS.unknown
_GATE_EXIT_CONTRACT_ERROR = _GATE_EXITS.contract_error


def cmd_gate(args: argparse.Namespace) -> int:
    """Classify a /next-up packet into one typed gate verdict (the empty-packet gate).

    Detail: docs/CLI.md § cmd_gate.
    """
    _apply_workspace(args)
    from dos import gate_classify

    if (args.packet is None) == (args.picks_json is None):
        print("error: provide exactly one of PACKET (a .dispositions sidecar path) "
              "or --picks-json '<json-list>'", file=sys.stderr)
        return _GATE_EXIT_CONTRACT_ERROR

    try:
        if args.picks_json is not None:
            try:
                picks = json.loads(args.picks_json)
            except json.JSONDecodeError as e:
                print(f"error: --picks-json is not valid JSON: {e}", file=sys.stderr)
                return _GATE_EXIT_CONTRACT_ERROR
            if not isinstance(picks, list):
                print(f"error: --picks-json must be a JSON list of disposition "
                      f"dicts, got {type(picks).__name__}", file=sys.stderr)
                return _GATE_EXIT_CONTRACT_ERROR
            result = gate_classify.classify_packet(picks)
        else:
            result = gate_classify.classify_packet_file(args.packet)
    except gate_classify.StaleDispositionContract as e:
        # The sidecar is missing, unreadable, or a schema mismatch — the /next-up
        # that wrote it is out of contract with this gate. Fail loud (the typed
        # gate's whole point is to never produce a plausible-but-wrong verdict).
        print(f"error: {e}", file=sys.stderr)
        return _GATE_EXIT_CONTRACT_ERROR
    except gate_classify.MalformedDisposition as e:
        # A single pick dict was unusable (no phase/phase_id). Same posture.
        print(f"error: {e}", file=sys.stderr)
        return _GATE_EXIT_CONTRACT_ERROR

    verdict = result.verdict
    token = verdict.value  # the bare token, e.g. "STALE-STAMP" (alias-safe)
    payload = {
        "verdict": token,
        "reason": result.reason,
        "evidence": [
            {"series": d.series, "phase": d.phase, "live": d.live,
             "drop_reason": d.drop_reason}
            for d in result.evidence
        ],
    }
    # `--explain` (off by default): append a one-line next-action hint from the
    #   (full prose: docs/CLI.md § "`--explain` (off by default): append a one-line next-action")
    explain = getattr(args, "explain", False)
    interpretation = _interpret.gate(payload) if explain else None
    if args.json:
        if explain:
            payload["interpretation"] = interpretation
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{token}  {result.reason}")
        if explain:
            print(interpretation)
    return _GATE_EXIT_CODES.get(token, _GATE_EXIT_UNKNOWN)


# ---------------------------------------------------------------------------
# `dos pickable` (docs/207 Phase 1) — expose the shipped pre-dispatch gate.
#   (full prose: docs/CLI.md § "`dos pickable` (docs/207 Phase 1) — expose the shipped pre-d")
# ---------------------------------------------------------------------------
_PICKABLE_EXITS = ExitMap(
    {
        "OFFERABLE": 0,
        # Re-dispatch-INVARIANT holds (a re-dispatch cannot clear these) — 10..13.
        "DRAFT_CLASS": 10,
        "OPERATOR_GATED": 11,
        "SOAK_OPEN": 12,
        "DEPENDENCY_UNMET": 13,
        # Re-dispatch-CURABLE holds (clear on their own) — 20..25.
        "IN_FLIGHT": 20,
        "SOFT_CLAIMED_ELSEWHERE": 21,
        "STALE_CLAIM": 22,
        "COOLDOWN": 23,
        "UNPARSEABLE": 24,
        "SHIPPED": 25,
    },
    unknown=8,
)
_PICKABLE_EXIT_CODES = _PICKABLE_EXITS.codes
_PICKABLE_EXIT_UNKNOWN = _PICKABLE_EXITS.unknown
_PICKABLE_EXIT_CONTRACT_ERROR = _PICKABLE_EXITS.contract_error


def cmd_pickable(args: argparse.Namespace) -> int:
    """Decide whether a declared unit is offerable to a worker right now (docs/168 §2).

    Detail: docs/CLI.md § cmd_pickable.
    """
    _apply_workspace(args)
    from dos import pickable

    state: dict = {}
    if args.state is not None:
        try:
            state = json.loads(args.state)
        except json.JSONDecodeError as e:
            print(f"error: --state is not valid JSON: {e}", file=sys.stderr)
            return _PICKABLE_EXIT_CONTRACT_ERROR
        if not isinstance(state, dict):
            print(f"error: --state must be a JSON object, got {type(state).__name__}",
                  file=sys.stderr)
            return _PICKABLE_EXIT_CONTRACT_ERROR

    # `now_ms` is an input to the pure verdict (the `liveness.classify` discipline),
    # read from the wall ONCE here at the boundary — never inside `classify`.
    now_ms = args.now_ms
    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)

    verdict = pickable.classify(state, now_ms=now_ms)
    token = verdict.reason.value if verdict.held and verdict.reason else "OFFERABLE"
    payload = {
        "held": verdict.held,
        "reason": token if verdict.held else None,
        "evidence": verdict.evidence,
        "redispatch_invariant": verdict.is_redispatch_invariant,
        "unit": args.unit,
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        if verdict.held:
            inv = " (re-dispatch-invariant)" if verdict.is_redispatch_invariant else ""
            print(f"HELD({token}){inv}  {verdict.evidence}")
        else:
            print(f"OFFERABLE  {args.unit or 'unit'} may be picked up")
    return _PICKABLE_EXIT_CODES.get(token, _PICKABLE_EXIT_UNKNOWN)


# ---------------------------------------------------------------------------
# `dos enumerate` (docs/207 Phase 2) — the phase-list producer surface.
#   (full prose: docs/CLI.md § "`dos enumerate` (docs/207 Phase 2) — the phase-list producer")
_ENUMERATE_EXITS = ExitMap({"CLEAN": 0, "DRIFT": 3, "EMPTY": 4})
_ENUMERATE_EXIT_CLEAN = _ENUMERATE_EXITS["CLEAN"]
_ENUMERATE_EXIT_DRIFT = _ENUMERATE_EXITS["DRIFT"]
_ENUMERATE_EXIT_EMPTY = _ENUMERATE_EXITS["EMPTY"]
_ENUMERATE_EXIT_CONTRACT_ERROR = _ENUMERATE_EXITS.contract_error


def cmd_enumerate(args: argparse.Namespace) -> int:
    """Enumerate the unit ids a plan-doc declares, in document order (docs/168 §1).

    Detail: docs/CLI.md § cmd_enumerate.
    """
    _apply_workspace(args)
    from dos import enumerate as _enumerate

    try:
        body = Path(args.plan_doc).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"error: cannot read plan doc {args.plan_doc!r}: {e}", file=sys.stderr)
        return _ENUMERATE_EXIT_CONTRACT_ERROR

    meta_shipped = None
    if args.shipped is not None:
        try:
            meta_shipped = json.loads(args.shipped)
        except json.JSONDecodeError as e:
            print(f"error: --shipped is not valid JSON: {e}", file=sys.stderr)
            return _ENUMERATE_EXIT_CONTRACT_ERROR
        if not isinstance(meta_shipped, list):
            print(f"error: --shipped must be a JSON list, got {type(meta_shipped).__name__}",
                  file=sys.stderr)
            return _ENUMERATE_EXIT_CONTRACT_ERROR

    grammar = _config.active().enumerate_grammar
    if args.series:
        grammar = _enumerate.with_series(grammar, args.series)
    e = _enumerate.enumerate_units(body, grammar=grammar, meta_shipped=meta_shipped)

    if args.json:
        print(json.dumps(e.to_dict(), sort_keys=True))
    else:
        if not e.units:
            print("(empty) — no unit ids declared")
        else:
            print(f"series={e.series or '(generic)'}  "
                  f"units={len(e.units)}  shipped={len(e.shipped)}  remaining={len(e.remaining)}")
            for u in e.units:
                span = e.by_unit.get(u)
                mark = "·" if (span and span.shipped) else "→"
                via = f" via {span.shipped_by}" if span and span.shipped else ""
                print(f"  {mark} {u}{via}")
        for d in e.drift:
            print(f"  ⚠ drift[{d.kind}]: {d.detail}")

    if not e.units:
        return _ENUMERATE_EXIT_EMPTY
    if e.drift:
        return _ENUMERATE_EXIT_DRIFT
    return _ENUMERATE_EXIT_CLEAN


# ---------------------------------------------------------------------------
# `dos cooldown` (docs/207 Phase 3) — the anti-churn read surface.
#   (full prose: docs/CLI.md § "`dos cooldown` (docs/207 Phase 3) — the anti-churn read surf")
# ---------------------------------------------------------------------------
_COOLDOWN_EXITS = ExitMap({"CLEAR": 0, "RECENTLY_ATTEMPTED": 3}, unknown=5)
_COOLDOWN_EXIT_CODES = _COOLDOWN_EXITS.codes
_COOLDOWN_EXIT_UNKNOWN = _COOLDOWN_EXITS.unknown
_COOLDOWN_EXIT_CONTRACT_ERROR = _COOLDOWN_EXITS.contract_error


def cmd_cooldown(args: argparse.Namespace) -> int:
    """Decide whether a unit is in a per-pick cooldown window now (docs/207 §3).

    Detail: docs/CLI.md § cmd_cooldown.
    """
    _apply_workspace(args)
    from dos import cooldown as _cooldown
    from dos import lane_journal as _lj
    import datetime as _dt

    now_ms = args.now_ms
    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)

    attempts: list = []
    if args.attempts is not None:
        try:
            attempts = json.loads(args.attempts)
        except json.JSONDecodeError as e:
            print(f"error: --attempts is not valid JSON: {e}", file=sys.stderr)
            return _COOLDOWN_EXIT_CONTRACT_ERROR
        if not isinstance(attempts, list):
            print(f"error: --attempts must be a JSON list, got {type(attempts).__name__}",
                  file=sys.stderr)
            return _COOLDOWN_EXIT_CONTRACT_ERROR
    else:
        # Gather the ATTEMPT history from the lane journal at this boundary. Derive
        # each record's `attempted_at_ms` from its journal `ts` (the fold reads ms).
        for rec in _lj.read_all():
            if str(rec.get("op") or "") != "ATTEMPT":
                continue
            if "attempted_at_ms" not in rec:
                ts = str(rec.get("ts") or "")
                try:
                    dtv = _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=_dt.timezone.utc)
                    rec = {**rec, "attempted_at_ms": int(dtv.timestamp() * 1000)}
                except ValueError:
                    continue
            attempts.append(rec)

    cfg = _config.active()
    verdict = _cooldown.cooldown_verdict(
        args.unit, attempts, now_ms=now_ms, policy=cfg.cooldown
    )
    if args.json:
        print(json.dumps(verdict.to_dict(), sort_keys=True))
    else:
        print(f"{verdict.state.value}  {verdict.reason}")
    return _COOLDOWN_EXIT_CODES.get(verdict.state.value, _COOLDOWN_EXIT_UNKNOWN)


# ---------------------------------------------------------------------------
# `dos pick-priority` (docs/254) — the freshness sort-key read surface.
#   (full prose: docs/CLI.md § "`dos pick-priority` (docs/254) — the freshness sort-key read")
# ---------------------------------------------------------------------------
_PICK_PRIORITY_EXITS = ExitMap({"NEVER_ATTEMPTED": 0, "ATTEMPTED": 3}, unknown=5)
_PICK_PRIORITY_EXIT_CODES = _PICK_PRIORITY_EXITS.codes
_PICK_PRIORITY_EXIT_UNKNOWN = _PICK_PRIORITY_EXITS.unknown
_PICK_PRIORITY_EXIT_CONTRACT_ERROR = _PICK_PRIORITY_EXITS.contract_error


def cmd_pick_priority(args: argparse.Namespace) -> int:
    """Decide a unit's pick freshness and emit its sort_key (docs/254).

    Detail: docs/CLI.md § cmd_pick_priority.
    """
    _apply_workspace(args)
    from dos import pick_priority as _pp
    from dos import lane_journal as _lj
    import datetime as _dt

    unit = str(args.unit)

    attempts: list = []
    if args.attempts is not None:
        try:
            attempts = json.loads(args.attempts)
        except json.JSONDecodeError as e:
            print(f"error: --attempts is not valid JSON: {e}", file=sys.stderr)
            return _PICK_PRIORITY_EXIT_CONTRACT_ERROR
        if not isinstance(attempts, list):
            print(f"error: --attempts must be a JSON list, got {type(attempts).__name__}",
                  file=sys.stderr)
            return _PICK_PRIORITY_EXIT_CONTRACT_ERROR
    else:
        # Gather the ATTEMPT history from the lane journal at this boundary, exactly
        # as `cmd_cooldown` does (derive `attempted_at_ms` from the journal `ts`).
        for rec in _lj.read_all():
            if str(rec.get("op") or "") != "ATTEMPT":
                continue
            if "attempted_at_ms" not in rec:
                ts = str(rec.get("ts") or "")
                try:
                    dtv = _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=_dt.timezone.utc)
                    rec = {**rec, "attempted_at_ms": int(dtv.timestamp() * 1000)}
                except ValueError:
                    continue
            attempts.append(rec)

    # Reduce this unit's attempt rows to a summary: attempted iff ≥1 row matches the
    # unit; last_attempt_ms = the newest such row's stamp. Fail-open — an unreadable
    # stamp is skipped (never a refusal), and no matching row → never-attempted.
    last_ms: int | None = None
    for rec in attempts:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("unit_id") or "") != unit:
            continue
        raw = rec.get("attempted_at_ms")
        try:
            at = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            at = None
        if at is None:
            continue
        if last_ms is None or at > last_ms:
            last_ms = at
    summary = _pp.AttemptSummary.at(last_ms) if last_ms is not None else _pp.AttemptSummary.never()

    verdict = _pp.classify(unit, summary)
    if args.json:
        print(json.dumps(verdict.to_dict(), sort_keys=True))
    else:
        print(f"{verdict.freshness.value}  {verdict.reason}")
    return _PICK_PRIORITY_EXIT_CODES.get(verdict.freshness.value, _PICK_PRIORITY_EXIT_UNKNOWN)


# ---------------------------------------------------------------------------
# `dos reconcile` (docs/207 Phase 4) — the quiet-completion gate.
#   (full prose: docs/CLI.md § "`dos reconcile` (docs/207 Phase 4) — the quiet-completion ga")
# ---------------------------------------------------------------------------
_RECONCILE_EXITS = ExitMap(
    {"VERIFIED": 0, "QUIET_INCOMPLETE": 3, "HONEST_OPEN": 4}, unknown=5,
)
_RECONCILE_EXIT_CODES = _RECONCILE_EXITS.codes
_RECONCILE_EXIT_UNKNOWN = _RECONCILE_EXITS.unknown
_RECONCILE_EXIT_CONTRACT_ERROR = _RECONCILE_EXITS.contract_error


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Reconcile a unit's CLAIM against the ORACLE's ground-truth verdict (docs/168 §3).

    Detail: docs/CLI.md § cmd_reconcile.
    """
    _apply_workspace(args)
    from dos import reconcile as _reconcile

    # Resolve the oracle verdict: explicit flag wins (replay), else compute from
    # git ancestry via the real verify rung.
    if args.oracle_shipped is not None:
        oracle_shipped = args.oracle_shipped
    elif args.plan and args.phase:
        from dos import oracle as _oracle
        try:
            v = _oracle.is_shipped(args.plan, args.phase, cfg=_config.active())
        except Exception as e:  # pragma: no cover - defensive boundary
            print(f"error: could not resolve the oracle verdict: {e}", file=sys.stderr)
            return _RECONCILE_EXIT_CONTRACT_ERROR
        oracle_shipped = bool(getattr(v, "shipped", False))
    else:
        print("error: provide either --oracle-shipped, or both --plan and --phase "
              "(so the oracle verdict can be computed from git ancestry)",
              file=sys.stderr)
        return _RECONCILE_EXIT_CONTRACT_ERROR

    verdict = _reconcile.reconcile(
        args.unit, claimed_done=bool(args.claimed_done), oracle_shipped=oracle_shipped
    )
    if args.json:
        print(json.dumps(verdict.to_dict(), sort_keys=True))
    else:
        print(f"{verdict.state.value}  {verdict.reason}")
    return _RECONCILE_EXIT_CODES.get(verdict.state.value, _RECONCILE_EXIT_UNKNOWN)


def _add_workspace_flags(p: argparse.ArgumentParser, *, subcommand: bool = True) -> None:
    """Attach the workspace-selection flags (`--workspace`/`--driver`/`--job`).

    Detail: docs/CLI.md § _add_workspace_flags.
    """
    ws_default = argparse.SUPPRESS if subcommand else None
    job_default = argparse.SUPPRESS if subcommand else False
    p.add_argument("--workspace", default=ws_default,
                   help="workspace root the substrate serves (default: $DISPATCH_WORKSPACE or cwd)")
    p.add_argument("--driver", default=ws_default, metavar="NAME",
                   help="host policy driver: resolves dos.drivers.<NAME>.<NAME>_config "
                        "by convention (e.g. job, workshop); dos.toml tables still "
                        "layer over it")
    p.add_argument("--job", action="store_true", default=job_default,
                   help="back-compat alias for --driver job (the job-repo lane taxonomy)")


def _add_output_flag(p: argparse.ArgumentParser) -> None:
    """The RND `--output <name>` selector (default `text`).

    Detail: docs/CLI.md § _add_output_flag.
    """
    p.add_argument("--output", default=None, metavar="NAME",
                   help="output renderer: text (default), json, or a registered "
                        "dos.renderers plugin (e.g. terse)")


def _add_explain_flag(p: argparse.ArgumentParser) -> None:
    """The `--explain` agent affordance (off by default).

    Detail: docs/CLI.md § _add_explain_flag.
    """
    p.add_argument("--explain", action="store_true",
                   help="append a one-line next-action interpretation of the "
                        "verdict (same hint the MCP tools return); in machine "
                        "output (--json / --output json) it rides as an "
                        "`interpretation` field, in text as a trailing line")


# ---------------------------------------------------------------------------
# `USE THIS WHEN` help bodies (item 3). The MCP tool docstrings carry excellent
#   (full prose: docs/CLI.md § "`USE THIS WHEN` help bodies (item 3). The MCP tool docstring")
_HELP_VERIFY = """the truth syscall — did (plan, phase) actually ship?

USE THIS WHEN: another agent (or the user) CLAIMS a task/phase/feature is done,
and you want to confirm it from real evidence before relying on it or building on
top of it. This is the antidote to a self-narrating worker: it answers from
artifacts (a run-registry row, then a git-log grep over the ship-commit grammar),
never from anyone's word. Falls through to an honest `source=none` when there is
no positive evidence — and works against a plain git repo with NO plan and NO
registry, so you can point it at any repo.

THE RUNG that answered is the `(via …)` tag, strongest-first: registry (a
run-registry row cross-checked against git ancestry) › file-path (a distinctive
deliverable touched, via `git log -- <file>`) › direct (the ship-subject grep) ›
release/body mention › none (git history alone could not confirm). All of these
are GIT evidence. NON-git proof — "is the build green?" — is available as an
out-of-kernel driver oracle (the JUDGE/driver tier; it backs the stable-release
gate) and is not yet folded into this verdict's rung tag, so consult it alongside
`verify` when a passing build is the ground truth you need. See docs/HACKING.md
and the docs/84 evidence ladder.

Add --explain for a one-line gloss on what the verdict means for your next action
(the same hint the MCP `dos_verify` tool returns). Exit: 0 shipped, 1 not-shipped,
2 contract error."""

_HELP_ATTEST = """mint a portable, SIGNED receipt over an effect-witness verdict (docs/246).

USE THIS WHEN: you need to hand someone who was NOT present — an auditor, an
inspector general, a counterparty in an agent-to-agent transaction, an allied
partner — a certificate they can verify ON THEIR OWN that an effect really
happened (or really did not). `dos verify` returns a verdict to YOU (inside the
loop); this packages that verdict into a record a third party checks with the
shared key alone, without access to the agent, the operator, or the loop. The
DocuSign step applied to the kernel's notary engine: a private check → portable proof.

HOW: name the agent's CLAIM (--claim, an opaque effect key) and ONE independent
witness SURFACE the agent does not control —
  --accept-cmd CMD       run a command, the OS authors the exit code (OS_RECORDED);
  --before B --after A    diff two state snapshots the STORE wrote (OS_RECORDED, or
                          THIRD_PARTY with --third-party for a remote store).
The kernel re-reads the surface, joins it to the claim, and SIGNS the four-valued
verdict together with WHICH witness saw it and at WHAT tier (HMAC-SHA256; key from
--key-file or $DOS_ATTEST_KEY). The signed payload carries the witness author + tier,
not just the verdict token — a CONFIRMED at OS_RECORDED is a different evidentiary
object than one at THIRD_PARTY. REFUTED is the load-bearing adverse certificate (a
narrated success the world denies, made portable); UNWITNESSED stays a DISTINCT,
non-adverse "could-not-tell". It attests PRESENCE at a tier and a time, NEVER
correctness (the Wall §3 ceiling, inherited from the engine); evidentiary weight,
not legal.

Verify the result with `dos verify-receipt`. Exit IS the carried verdict:
0 CONFIRMED, 1 REFUTED, 3 UNWITNESSED/NO_CLAIM, 2 contract error."""

_HELP_VERIFY_RECEIPT = """check a portable receipt's signature — the third-party surface (docs/246).

USE THIS WHEN: someone hands you a `dos attest` receipt and you want to confirm it
is genuine and unaltered BEFORE relying on it. You need only the receipt (a JSON
file, or piped on stdin) and the shared/public half of the signing key — NOT the
agent, the operator, or the original loop. It re-derives the receipt's canonical
bytes and checks the signature, then shows the verdict the receipt carries WITH its
accountability tier.

The ONE place DOS fails LOUD: a tampered field, the wrong key, or a forged
signature makes the receipt INVALID (exit 1) — never a silent downgrade to
"unsigned but probably fine." A VALID receipt still carries a verdict you read:
REFUTED is flagged ADVERSE (an accountable witness saw the effect absent),
UNWITNESSED/NO_CLAIM as explicitly non-adverse (could-not-tell, not a finding of
absence). It checks the SIGNATURE and the TIER; it does not re-run the witness.

Phase 1 verifies HMAC-SHA256 receipts; an Ed25519 (asymmetric) receipt needs the
docs/246 Phase-2 [attest] extra and is reported as not-yet-verifiable here. Exit:
0 valid, 1 invalid, 2 contract error."""

_HELP_ARBITRATE = """the admission kernel — may a worker take this lane right now?

USE THIS WHEN: you are about to start work that touches a set of files (or
dispatch a sub-agent to), and other agents may be working in the same repo
concurrently. Call this FIRST to find out whether your file-tree collides with
work already in flight — it is the mechanism that stops two agents editing the
same files at once. State in, decision out: it admits a lane iff its file tree is
disjoint from every live lease (a bare `--lane ''` auto-picks a free, disjoint
lane). By default it loads the live lane-journal WAL so a lease a sibling holds is
seen; pass `--leases '[]'` to arbitrate against an empty world.

Add --explain for a one-line GO/STOP next-action gloss. Exit: 0 acquire, 1 refuse,
2 contract error."""

_HELP_LIVENESS = """the temporal verdict — is the run MOVING, or just spinning?

USE THIS WHEN: a run is in flight and you want to know whether it is making real
progress or stuck — without trusting its own "I'm making progress" self-report.
The in-flight sibling of `verify`: `verify` distrusts a finished claim, this
distrusts a live one. It reads ground truth (commits since --start-sha, and — with
--lane + --loop-ts — this lease's journal events + heartbeat age) and classifies
ADVANCING / SPINNING / STALLED. Works against a plain git repo with just a run-id
and a start SHA. Advisory: it reports, it never kills a process.

The verdict IS the exit code so a babysitter loop can branch without re-parsing:
0 ADVANCING, 3 SPINNING, 4 STALLED, 2 contract error (a bad --run-id)."""

_HELP_PRODUCTIVITY = """the loop-economics verdict — is the run still DOING work, or fading?

USE THIS WHEN: a run is in flight and committing, but you suspect it is grinding —
spending more and more to land less and less. `liveness` asks "did state move at
all?"; this asks "is the work-per-step RATE collapsing?" — a trend `liveness` (one
since-start count) and `loop_decide` (hard count caps) cannot see. Lifted from
Claude Code's own loop: a run is DIMINISHING when it has taken --min-steps steps AND
its last two per-step work deltas are BOTH under --floor (the multi-signal AND keeps
one quiet step from false-tripping it).

Feed it the per-step work deltas you measured, OLDEST first — the UNIT is yours
(tokens/commits/changed bytes; the kernel only compares magnitudes):
  dos productivity --deltas 800,600,300,40,12 --floor 100   →  DIMINISHING, exit 3

Needs nothing else — no git, no plan, no journal, no clock (productivity is
timeless). Advisory: it reports, it never kills a run. The verdict IS the exit code:
0 PRODUCTIVE, 3 DIMINISHING, 4 STALLED, 2 contract error (a bad --deltas)."""

_HELP_EFFICIENCY = """the token-effectiveness verdict — did the tokens this run spent buy work?

USE THIS WHEN: you want to understand how EFFECTIVELY a run is spending its tokens —
not "is it moving" (`liveness`) or "is its work-per-step rate fading" (`productivity`)
but "was the work worth what it cost?" A run can be ADVANCING and PRODUCTIVE and still
burn ten times the tokens its work was worth. This relates the work to its PRICE — a
ratio (work per token) the other verdicts cannot see.

Feed it two env-authored counts the runtime already has — the work the environment
witnessed (commits / changed bytes / passed tests — YOUR unit) and the tokens the
provider billed:
  dos efficiency --work 1200 --tokens 45000                    →  EFFICIENT, exit 0
  dos efficiency --work 0    --tokens 80000                    →  WASTEFUL, exit 4
  dos efficiency --work 3 --tokens 90000 --floor 0.0001        →  COSTLY,  exit 3
  dos efficiency --work 5 --usage-json usage.json --json       →  + the spend split

--usage-json (docs/300) reads the provider's usage record itself (a file, or `-` for
stdin), normalizes the two wire shapes (input-excludes-cached vs input-includes-cached)
into the typed five-way split (input / output / cache_read / cache_creation /
reasoning), derives the scalar total, and surfaces the diagnostics — cache-hit ratio,
decode share, reasoning share — in the JSON verdict. Pure legibility: the ladder is
unchanged; a richer breakdown can never flip a verdict toward EFFICIENT.

Both counts are bytes the AGENT did not author (a commit git wrote, the API's usage
record), so a run cannot narrate its way to EFFICIENT — the docs/138 invariant. The
always-free verdict is WASTEFUL (meaningful spend, 0 work — unit-independent); COSTLY
is opt-in (arm a --floor that means something for your work unit; the default floor is
disabled so a unit mismatch never manufactures a false COSTLY).

Needs nothing else — no git, no plan, no journal, no clock (efficiency is timeless, it
reads two numbers). Advisory: it reports, it never kills a run. The verdict IS the exit
code: 0 EFFICIENT, 3 COSTLY, 4 WASTEFUL, 2 contract error (a bad --work/--tokens)."""

_HELP_EFFICIENCY_TREND = """the cross-run fold — is this loop's work-per-token fading ACROSS runs?

USE THIS WHEN: single-run verdicts look fine but you suspect drift — each successive
run buying a little less work per token than the runs before it. `efficiency` prices
ONE run; this folds many (a self-improving loop's cycles, a lane's dispatches) and
answers the direction: IMPROVING / STEADY / DEGRADING. The token-cost regression
check, as a pure kernel verdict (docs/300).

Two evidence sources — give exactly one:
  dos efficiency-trend --samples "9:1000,8:1100,3:2000,2:2400"   →  DEGRADING, exit 3
  dos efficiency-trend --from-journal [--run RID] [--last N]     →  fold the fossils

--from-journal reads the verdict journal at this boundary: every `--observe`d
`dos efficiency` call fossilized its (work, tokens) evidence, and the fold reads those
counts back in append order — so a fleet gets a cross-run trend for free once it
records verdicts. The ladder mirrors `productivity`: withhold under --min-samples
runs; accuse only when the last TWO runs both fall more than --tolerance below the
median of the runs before them (sustained — one outlier run cannot trip it).

Advisory: it reports, it never stops a loop. The verdict IS the exit code:
0 IMPROVING/STEADY, 3 DEGRADING, 2 contract error (a bad --samples / both sources)."""

_HELP_WORK_ACCOUNT = """the work-kind account — what KINDS of work did this iteration land?

USE THIS WHEN: an iteration's stats would otherwise collapse to one bit ("did a
pick ship?") and you want the honest composition instead. A 0-pick iteration that
landed commits, reconciled stamps, raised decisions, or CAUGHT a false "done"
claim is not nothing — this names its dominant kind and renders the composed
headline ("1 pick shipped · 4 commits advanced"). The composition sibling of
`productivity` (a trend) and `efficiency` (a ratio) — docs/310.

Feed it the per-kind counts the WITNESSES recorded (never the worker's narration):
the oracle's verified-ship count, git's lane-commit count, the oracle-REFUTED
claim count, the decisions-queue delta:
  dos work-account --verified-ships 1 --advance-commits 4       →  SHIPPED, exit 0
  dos work-account --catches 1 --claimed-ships 2                →  CAUGHT,  exit 3
  dos work-account                                              →  IDLE,    exit 4

Claims alone classify IDLE — a self-reported ship with no oracle answer cannot
climb the ladder (the docs/138 invariant); the unadjudicated over-claim count is
echoed in the reason, visible but powerless. Two axes, two words: IDLE is about
THIS ITERATION's work; the backlog's word (DRAIN) belongs to `dos gate`.

Advisory and observability-only: it changes what the stats SAY, never what the
loop DOES. The verdict IS the exit code: 0 SHIPPED/ADVANCED/GROOMED/SURFACED,
3 CAUGHT (a worker over-claimed — actionable), 4 IDLE, 2 contract error."""

_HELP_IMPROVE = """the self-improving-loop keep-gate — may this loop KEEP this candidate?

USE THIS WHEN: you are running a self-improving work loop (propose → verify → measure →
keep-or-revert) and need to decide whether ONE candidate self-improvement is kept or
undone. The loop's fatal failure mode is grading its own homework — the agent that wrote
the change deciding it is better, so it learns to NARRATE improvement instead of making
it. This is the witness-gated keep-bit that closes that hole: `reward.admit` (docs/234)
re-aimed from "may a fine-tune TRAIN on this?" to "may this loop KEEP this commit?".

Feed it the facts the driver gathered — every one authored by the ENVIRONMENT, none by
the loop (the docs/138 invariant that makes the keep-bit non-forgeable):
  dos improve --suite-passed --truth-clean --work 43 --baseline-work 40   →  KEEP, exit 0
  dos improve --suite-passed --truth-clean --work 40 --baseline-work 40   →  REVERT, exit 3
  dos improve --work 40 --baseline-work 40                                →  REVERT (suite red), exit 3
  dos improve --truth-clean --work 9 --baseline-work 0 --consecutive-reverts 2 \\
              --max-reverts 3                                             →  ESCALATE, exit 4

KEEP requires the suite GREEN and the truth syscall CLEAN and a STRICT env-measured gain
(--work > --baseline-work) — a regression (red suite / dirty truth) is the non-negotiable
floor and is always REVERT, even with a huge claimed gain. A safe no-op (green but the
metric did not move) is also REVERT. After --max-reverts non-keeps in a row the breaker
ESCALATEs to a human (the RSI bottleneck — hand "what matters next" back).

NON-FORGEABILITY (docs/234): --narrated is carried for the operator and parsed for
NOTHING — it cannot move REVERT → KEEP. The only path to KEEP is an actual measured gain.
The two witnesses default to FALSE (a missing witness is a failing one — fail-safe).

Needs nothing else — no git, no plan, no journal, no clock (the verdict is pure; the
driver does the I/O). Advisory: it reports KEEP/REVERT/ESCALATE, it executes no merge or
checkout. The verdict IS the exit code: 0 KEEP, 3 REVERT, 4 ESCALATE, 2 contract error."""

_HELP_BREAKER = """the circuit breaker — this keeps failing; stop, escalate the rung.

USE THIS WHEN: some class of thing has failed repeatedly and you must decide whether
to keep trying or give up and escalate. A breaker counts failures and OPENS when too
many pile up. The generic facility behind loop_decide's hand-coded breakers — the
kernel knows nothing about WHAT failed (you hand it counts), so it works for any
failure class: a flaky tool, a stalled lane, a denied permission.

Two counters, from Claude Code's denialTracking: --consecutive (in a row — a
SUSTAINED outage, cleared by a success) and --total (cumulative — a FLAPPING failure
a streak misses). It OPENs on EITHER hitting its max. An OPEN breaker names where to
escalate (--on-trip none|judge|human — the ORACLE→JUDGE→HUMAN ladder):
  dos breaker --consecutive 3 --max-consecutive 3 --on-trip human   →  OPEN, exit 3

Needs nothing else — no git, no plan, no clock. Advisory: it reports OPEN, it never
kills a run. The verdict IS the exit code: 0 CLOSED, 3 OPEN, 2 contract error."""

_HELP_EXEC_CAPABILITY = """does this command grant arbitrary code execution? a SHAPE, not a word.

USE THIS WHEN: you are about to run (or allow) a command and want to know if it
hands the agent arbitrary code execution — `python -c …`, `bash -c …`, `npx …`,
`ssh host …`, `sudo …` — each of which escapes a narrower per-command gate. Lifted
from Claude Code's dangerousPatterns. It matches the INVOKED PROGRAM (the first
token, after stripping env/sudo wrappers + VAR=value), never a substring — so
`cat python.txt` is BOUNDED (it runs `cat`), not a false python hit:
  dos exec-capability --command "python -c 'import os'"   →  GRANTS_ARBITRARY_EXEC, exit 3
  dos exec-capability --command "ls -la"                  →  BOUNDED, exit 0

The set defaults to the cross-platform interpreters/shells/runners; `--extra git,curl`
adds a host's own. ADVISORY — it reports the capability, it never denies on its own;
a consumer (the PRE hook) decides. BOUNDED is "not in the declared set," NOT a safety
guarantee. The verdict IS the exit code: 0 BOUNDED/EMPTY, 3 GRANTS_ARBITRARY_EXEC."""

_HELP_HOOK_EXIT = """map a plain shell hook's exit code → an intervention verb.

USE THIS WHEN: you have a shell script (a linter, a policy probe, a smoke test) that
signals its result with an exit code, and you want that to ride the DOS intervention
ladder WITHOUT writing JSON. Lifts Claude Code's hook convention: exit 0 = proceed,
exit 2 = blocking error (BLOCK), any other non-zero = a non-blocking warning (WARN):
  dos hook-exit --code 2            →  BLOCK, exit 3
  dos hook-exit --code 0            →  PASS, exit 0
  dos hook-exit --code 3 --map 3=DEFER   →  DEFER, exit 5

An unanticipated non-zero code falls to WARN — the fail-safe direction (inform, never
silently pass, never spuriously block). The map is declarable in `dos.toml
[hook_exit]`. The script's exit code is authored by the SCRIPT (a deterministic
JUDGE), not the agent. ADVISORY — it RECOMMENDS; the host decides. The verb's OWN
exit reflects the rung: PASS 0, BLOCK 3, WARN 4, DEFER 5, OBSERVE 6, contract err 2."""

_HELP_REWARD = """may a fine-tune TRAIN on this trajectory? the reward-set admission verdict.

USE THIS WHEN: you are building a reward set for an RL/SFT/DPO run and want DOS's
deterministic floor INSIDE the loop. A self-judged sampler banks every "resolved/done"
trajectory as a positive — which trains the policy to over-claim more (it is rewarded
for confidently narrating a success it did not achieve). This is the witness-gated
filter: a claim enters the positive set ONLY if a NON-FORGEABLE witness confirms it.
  dos reward --claim --witness confirm            →  ACCEPT, exit 0   (preferred positive)
  dos reward --claim --witness refute             →  REJECT_POISON, exit 3 (purged + dispreferred)
  dos reward --claim --witness none               →  ABSTAIN, exit 4   (never mint a positive)
  dos reward --no-claim                           →  NO_CLAIM, exit 5   (not a candidate)

THE NON-DISTILLABLE LABEL (docs/234): the accept bit is a pure function of the witness
the agent authors zero bytes of — so no answer text can move it reject→accept. The
floor, runnable at $0:
  dos reward --claim --witness confirm --forgeable →  ABSTAIN, exit 4  (a self-report is IGNORED)
A forgeable (AGENT_AUTHORED) read-back — the agent re-reading its OWN surface — is
recorded but structurally filtered from the belief decision; only OS_RECORDED /
THIRD_PARTY moves the bit. YOUR extractor supplies --claim (the kernel never parses
domain text); your env/OS/ledger witness supplies --witness. PURE, no-plan, ADVISORY.
The verdict IS the exit code: 0 ACCEPT, 3 REJECT_POISON, 4 ABSTAIN, 5 NO_CLAIM."""

_HELP_TEST_WITNESS = """does this NEW test actually witness this change? reverse-classical testing.

USE THIS WHEN: an agent (or a PR) claims "I added a test for this" and you want the
deterministic floor under that claim. A test that passes on BOTH trees keeps the suite
green while witnessing nothing — the false-positive a "tests added ✓" review banks
(`improve()`'s suite-green floor cannot tell a witnessing test from a decorative one).
The rule (reverse-classical testing, docs/288): run the new test against the tree
WITHOUT the change; it must fail. Red->green across the change, or no witness.
  dos test-witness --baseline fail  --candidate pass  →  DISCRIMINATES, exit 0 (the witness)
  dos test-witness --baseline pass  --candidate pass  →  VACUOUS, exit 3  (witnesses NOTHING)
  dos test-witness --baseline fail  --candidate fail  →  UNSATISFIED, exit 4 (no green half)
  dos test-witness --baseline pass  --candidate fail  →  REGRESSIVE, exit 5 (breaks the test)
  dos test-witness --baseline error --candidate pass  →  DISCRIMINATES, exit 0 (structural —
                                                         assert_level=false, the weaker form)

THE FLOOR, runnable at $0: outcomes are believed only on a non-forgeable rung —
  dos test-witness --baseline fail --candidate pass --forgeable  →  ABSTAIN, exit 6
A NARRATED red->green (the agent's own "it failed before, passes now") is structurally
ignored; the only path to DISCRIMINATES is a test that actually fails on the tree it
didn't get to touch. YOUR boundary (a CI step, the self-improve worktree flow) runs the
test on both trees and supplies the outcomes; the kernel never runs pytest and never
reads the test's content. PURE, no-plan, ADVISORY — DISCRIMINATES proves the test
discriminates the TREES, not that it asserts the intended BEHAVIOR (that residue goes up
the ladder to a JUDGE/HUMAN). The verdict IS the exit code: 0 DISCRIMINATES, 3 VACUOUS,
4 UNSATISFIED, 5 REGRESSIVE, 6 ABSTAIN."""

_HELP_ANSWER_SHAPE = """is this output an ANSWER, or a structural non-answer? a SHAPE, not correctness.

USE THIS WHEN: an assembly/RAG policy is about to ship a drafted output and you want a
pre-ship floor that catches the grounded-but-not-an-answer case a numeric grounding
gate misses — an empty stub, a leaked chain-of-thought log, a tool-call dump, a bare
"I cannot" pasted as content. The gap a real adoption hit: every shipped number was
witnessed, yet the app shipped a 5,780-char reasoning log as its answer. The rule is
  ship  ⟺  grounded  AND  answer_shape ≠ NON_ANSWER
  dos answer-shape --text "The capital is Paris."        →  ANSWER_SHAPED, exit 0
  dos answer-shape --text "<thinking>let me reason…"     →  NON_ANSWER, exit 3
  echo "$draft" | dos answer-shape --text -              →  reads the candidate from stdin

THE BOUNDARY: this judges SHAPE, never CORRECTNESS. ANSWER_SHAPED means "shaped like an
answer," NOT "a right answer" — the semantic question is a JUDGE/HUMAN's, and shape
returns INDETERMINATE (the abstain floor) on anything it cannot decide. The markers are
policy: `--non-answer RE,…` adds host disqualifiers ON TOP of the generic default,
`--min-chars N` sets the viability floor, `--markers RE,…` requires a positive answer
signature. PURE (no I/O, never raises). ADVISORY — it reports a shape; the consumer
decides. The verdict IS the exit code: 0 ANSWER_SHAPED, 3 NON_ANSWER, 4 INDETERMINATE."""

_HELP_GATE = """classify a /next-up packet into one typed gate verdict.

USE THIS WHEN: a dispatch loop renders an empty (or thin) packet and you must
decide whether the backlog is GENUINELY drained or only looks that way. Instead of
a brittle "0 picks → stop", this returns a typed verdict that distinguishes a true
DRAIN from STALE-STAMP (shipped-but-unstamped, self-heal), BLOCKED (a sibling
claim / quota), and RACE (you lost a candidates-cache lock — retry once). Feed it a
`.dispositions-<tag>.json` sidecar path, or inline `--picks-json`.

The verdict IS the exit code: 0 LIVE, 3 DRAIN, 4 STALE-STAMP, 5 BLOCKED, 6 RACE,
2 contract error. Add --explain for the loop-routing gloss (CONTINUE / STOP /
SELF-HEAL / RETRY)."""

_HELP_PICKABLE = """decide whether a declared unit is offerable to a worker right now.

USE THIS WHEN: before a dispatch loop offers a unit, you must know if a worker
could ACTUALLY pick it up — and if not, WHY NOT, precisely enough to route. This is
the pre-dispatch twin of `dos gate` (the post-render gate): it answers OFFERABLE,
or HELD by exactly one typed reason (DRAFT_CLASS / OPERATOR_GATED / SOAK_OPEN /
DEPENDENCY_UNMET — a re-dispatch CANNOT clear these; IN_FLIGHT / SOFT_CLAIMED /
STALE_CLAIM / COOLDOWN / UNPARSEABLE / SHIPPED — these clear on their own).

Pass the unit's declared state as a JSON object the host pre-gathers:
  dos pickable AUTH3 --state '{"plan_class":"DRAFT"}'  →  HELD(DRAFT_CLASS), exit 10

The verdict IS the exit code: OFFERABLE=0, invariant holds 10..13, curable holds
20..25, contract error 2 — so a skill branches on WHICH hold without parsing
(`/dos-promote` routes a DRAFT_CLASS to promote-to-active, an OPERATOR_GATED to a
decision, a SOAK_OPEN to wait)."""

_HELP_ENUMERATE = """enumerate the unit ids a plan-doc declares, in document order.

USE THIS WHEN: you need the machine-readable phase list a plan-doc declares — the
`declared` set `dos complete` measures the residual against, and the universe the
picker offers from. This is the producer that closes the picker-invisibility gap:
a plan with a rich phase table but no cached list is no longer silently dropped —
its units are enumerated from the doc, with a typed DriftNote where the doc
disagrees with itself, never a silent empty result.

  dos enumerate planning/auth-plan.md --series AUTH
  dos enumerate docs/42_plan.md --json     # generic markdown headings

The grammar comes from the workspace `[enumerate]` table (heading levels / table
scan / fallback / rollup); `--series` layers the per-plan series-anchored scan.
Exit STATUS: clean=0, a DriftNote present=3 (the doc disagrees with itself),
empty=4, contract error 2."""

_HELP_COOLDOWN = """decide whether a unit is in a per-pick cooldown window right now.

USE THIS WHEN: a dispatch loop's pick-selection must avoid re-picking a unit it
ALREADY tried-and-didn't-move — the re-pick storm that made a bare loop ship only
~5% of the time (it re-confirmed a known drain every iteration once the claim TTL
lapsed). This folds the unit's recorded pick attempts (the lane-journal OP_ATTEMPT
events) into CLEAR or RECENTLY_ATTEMPTED. Outcome-aware: a genuine SHIPPED is moot
(it left the residual); a DRAINED/BLOCKED inside the window holds; an ERROR earns
a shorter fast-retry window.

  dos cooldown AUTH3                # reads the journal; CLEAR=0 / RECENTLY_ATTEMPTED=3
  dos cooldown AUTH3 --json

The verdict IS the exit code (CLEAR=0, RECENTLY_ATTEMPTED=3) so pick-selection
skips a cooled unit without parsing. The window is declared in `[cooldown]`
(default 6h / 30m). The cooldown is a HINT — a fail-open read can only DELAY a
re-pick, never wedge a clean unit."""

_HELP_PICK_PRIORITY = """rank a unit by FRESHNESS so the picker prefers new work over churn.

USE THIS WHEN: a dispatch loop must choose among offerable units and you want it to
pick NEW, not-started work before re-attempting something it already tried that
didn't move. `cooldown` GATES a unit after its window; this ORDERS what's left — it
folds the same OP_ATTEMPT history into a sort_key a picker appends AFTER its own
(priority, status, …) key, so freshness breaks ties WITHIN a tier and never across
one. Two signals: never-attempted sorts first; among attempted, least-recently-tried
sorts first (LRU). A P1 unit always outranks a P2 unit — freshness can only reorder,
never starve work or override priority.

  dos pick-priority AUTH3              # reads the journal; NEVER_ATTEMPTED=0 / ATTEMPTED=3
  dos pick-priority AUTH3 --json       # {freshness, last_attempt_ms, sort_key, reason}

The verdict IS the exit code (NEVER_ATTEMPTED=0, ATTEMPTED=3) so `dos pick-priority
U && pick U` reads naturally. Fail-open: a unit with no readable attempt is
NEVER_ATTEMPTED (sorts first) — the pre-fix behaviour, never a refusal."""

_HELP_RECONCILE = """reconcile a unit's CLAIM against the ORACLE's ground-truth verdict.

USE THIS WHEN: a dispatch loop's archive step must decide whether a unit the agent
CLAIMED done actually shipped — before it drops the unit from the residual. This is
the picker-boundary closure of the quiet-failure line: it joins the agent's claim
against `oracle.is_shipped` (the same verify rung, from git ancestry) and is
FAIL-CLOSED ON THE CLAIM — the agent's word never removes work, only ground truth
does. A claim the oracle refutes is QUIET_INCOMPLETE: kept in the residual, flagged
for routing (the FQ-336 touch-counts-as-ship false-DRAIN would have been caught).

  dos reconcile AUTH3 --claimed-done --plan AUTH --phase AUTH3   # oracle from git
  dos reconcile AUTH3 --claimed-done --no-oracle-shipped         # replay/test

The verdict IS the exit code: VERIFIED=0 (oracle confirms — leaves the residual),
QUIET_INCOMPLETE=3 (claimed but the oracle says not — KEPT + flagged), HONEST_OPEN=4
(not claimed + not shipped — honest open work). DETECT-and-KEEP, never a mutation —
the host owns the correction."""

_HELP_INIT = """scaffold a DOS workspace — the dos.toml, and optionally skills + hooks.

USE THIS WHEN: a stranger adopts DOS in a repo. `dos init` writes a `dos.toml`
(lanes auto-derived from the top-level dirs). Add `--skills` and it ALSO copies the
generic SKILL.md screenplays into `.claude/skills/` as EDITABLE LOCAL FILES; add
`--hooks <runtime>` and it ALSO wires the three DOS hooks into THAT runtime's own
config file — so the adoption path is one command, not a manual copy out of the
wheel or a hand-edited settings file:

  dos init --skills /tmp/svc            # dos.toml + the core skills (next-up/dispatch/loop/replan)
  dos init --skill dos-promote /tmp/svc # dos.toml + just the named skill(s) (repeatable)
  dos init --all .                      # the FULL pack into the current workspace
  dos init --hooks auto .               # DETECT the runtime(s) this repo already uses
                                        #    and wire them all — the zero-decision path
  dos init --hooks claude-code .        # dos.toml + bind the verdict to a Claude Code launch
  dos init --hooks cursor .             # …or Cursor      (writes .cursor/hooks.json)
  dos init --hooks codex .              # …or Codex CLI   (writes .codex/config.toml)
  dos init --hooks gemini .             # …or Gemini CLI  (writes .gemini/settings.json)
  dos init --hooks antigravity .        # …or Antigravity (writes .agents/hooks.json)
  dos init --hooks claude-cowork .      # …or Claude Cowork (the SAME .claude/settings.json
                                        #    Claude Code reads — shared harness, docs/298)

`--hooks <runtime>` (docs/221) is CROSS-VENDOR: it wires three SHIPPED hooks (the
verdict bound to the runtime, docs/134 §6 / docs/217) into the host you actually run
— Claude Code, Cursor, Codex CLI, Gemini CLI, Google Antigravity, or Claude Cowork —
each in its own file/format, with the matching `--dialect` so the host parses the
verdict it honors:
  Stop/AfterAgent      → `dos hook stop`     (refuse to stop on an unverified claim),
  PreToolUse/BeforeTool/beforeShellExecution → `dos hook pretool` (deny a refused call),
  PostToolUse/AfterTool/afterFileEdit        → `dos hook posttool` (re-surface a stalled stream).
The block is MERGED into any existing config — your other hooks/keys are preserved.
`--with-hooks` is the back-compat alias for `--hooks claude-code` (byte-identical).
`--hooks auto` (docs/303) names the host FOR you: it probes which config dirs
already exist here (.claude/, .cursor/, .codex/, .gemini/, .agents/) plus the env
of the shell it runs in, wires every runtime it finds (a shared config file is
wired once), and FAILS LOUD with this list when nothing is detected — never a
guessed default.

The copied skills/hooks are ordinary files you edit; the package-data is the SEED,
not a runtime binding. Re-running is idempotent — a diverged local skill copy or an
already-wired DOS hook is NOT clobbered without `--force` (which REPAIRS it).
`--skills` / `--hooks` work on an already-init'd workspace too (the dos.toml is left
alone). The advisory MCP path (the agent CALLS `dos_verify`) is wired separately —
`dos-mcp` in the host config; see src/dos_mcp/README.md. Hooks ENFORCE (deny); MCP
ADVISES (the agent asks). Use both."""

_HELP_MAN = """the self-describing manual over this workspace's registries.

USE THIS WHEN: you need to know what a refusal token MEANS, or which lanes exist,
for THIS workspace — including any the repo declared in dos.toml. `man wedge`
lists/explains the closed refusal vocabulary (so you emit a real, verifiable
reason instead of free-text prose); `man lane` lists/explains the lane taxonomy
`arbitrate` admits over. The page is rendered from the live registry, never a
hand-authored doc, so a custom reason/lane gets a real entry through the same verb.

Add --explain (on `man wedge <ID>`) for a one-line safe-to-emit / blocks-or-advisory
gloss, the same hint the MCP `dos_check_reason` tool returns."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dos", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # GLOBAL workspace flags — accepted BEFORE the subcommand too, so
    #   (full prose: docs/CLI.md § "GLOBAL workspace flags — accepted BEFORE the subcommand too,")
    _add_workspace_flags(p, subcommand=False)
    # `metavar` collapses argparse's auto-generated {init,verify,…} wall (in the
    #   (full prose: docs/CLI.md § "`metavar` collapses argparse's auto-generated {init,verify,…")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    pi = sub.add_parser("init", help="scaffold a DOS workspace config (and, with "
                                     "--skills / --hooks <runtime>, the skills + "
                                     "cross-vendor hooks)",
                        description=_HELP_INIT,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pi.add_argument("dir", nargs="?", default=".")
    pi.add_argument("--force", action="store_true",
                    help="overwrite an existing dos.toml / a diverged local skill "
                         "copy; repair an existing DOS hooks block")
    pi.add_argument("--example", metavar="NAME", default=None,
                    help="scaffold dos.toml from a named reference driver pack "
                         "(e.g. workshop) instead of auto-deriving lanes from your "
                         "top-level dirs — gives you a worked concurrent/exclusive "
                         "lane taxonomy to adapt. See `dos quickstart --driver NAME` "
                         "for a live demo of what it does.")
    # docs/207 Phase 7 — copy the generic SKILL.md screenplays into the workspace's
    #   (full prose: docs/CLI.md § "docs/207 Phase 7 — copy the generic SKILL.md screenplays int")
    pi.add_argument("--skills", action="store_true",
                    help="copy the CORE generic skills into .claude/skills/ ("
                         + ", ".join(_CORE_SKILLS) + ")")
    pi.add_argument("--skill", action="append", default=None, metavar="NAME",
                    help="copy a specific generic skill (repeatable); implies --skills")
    pi.add_argument("--all", action="store_true",
                    help="copy the FULL skill pack into .claude/skills/")
    # docs/134 §6 / docs/165 / docs/221 — bind the verdict to an agent runtime by
    #   (full prose: docs/CLI.md § "docs/134 §6 / docs/165 / docs/221 — bind the verdict to an a")
    from dos import hook_install as _hi_choices
    pi.add_argument("--hooks", metavar="HOST",
                    choices=[_hi_choices.AUTO_HOST, *_hi_choices.host_names()],
                    help="wire the DOS hooks into a runtime's config file. 'auto' "
                         "detects the runtime(s) this repo already uses and wires "
                         "them all (docs/303 — the zero-decision path); or name "
                         "one of: " + ", ".join(_hi_choices.host_names())
                         + " (cursor → .cursor/hooks.json, codex → .codex/config.toml, "
                         "gemini → .gemini/settings.json, claude-code → "
                         ".claude/settings.json). pretool denies refused calls, "
                         "posttool re-surfaces a stalled stream, stop refuses an "
                         "unverified done.")
    pi.add_argument("--with-hooks", action="store_true", dest="with_hooks",
                    help="alias for --hooks claude-code (wire the DOS hooks into "
                         ".claude/settings.json)")
    pi.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="with --hooks: PREVIEW the config merge (print what would "
                         "be written, write nothing) — the dress rehearsal before "
                         "wiring a runtime that already has its own config")
    pi.set_defaults(func=cmd_init)

    pqs = sub.add_parser(
        "quickstart",
        help="run the caught-lie + collision demo in a throwaway repo "
             "(60-second taste)",
        description=(
            "Run the DOS money-moment in one command: an agent claims it shipped "
            "two things; a fresh workspace gets the one real commit (AUTH1), then "
            "the truth syscall rules on both claims — backed by git → SHIPPED, "
            "nothing landed → NOT_SHIPPED. The contrast is DOS: a claim is believed "
            "only when the artifacts back it. "
            "Part two is the multi-writer act (a fleet, or just two agent tabs on "
            "one repo): three arbitrate calls through the real kernel admit agent A "
            "onto src, redirect agent B off the busy region onto the disjoint docs "
            "lane, and refuse agent C when every lane is held — the collision that "
            "never reached your files. It closes with the adoption router: hooks / "
            "MCP / CI / fleet, one line each. "
            "--spinning runs the SIBLING scene instead — the false \"still "
            "working\": a loop narrates progress while landing zero commits, and "
            "`dos liveness` catches the spin from the git delta, never the prose. "
            "By default it runs in a temp dir and cleans up; --keep DIR leaves it."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pqs.add_argument("--keep", metavar="DIR", default=None,
                     help="scaffold the demo repo in DIR and leave it (default: a "
                          "temp dir, removed on exit)")
    pqs.add_argument("--spinning", action="store_true",
                     help="run the spinning-loop scene instead: an agent narrates "
                          "'making progress' for four steps while committing "
                          "nothing — `dos liveness` rules SPINNING off the git "
                          "delta + heartbeat (never the narration), `dos "
                          "efficiency` prices the waste, and one honest commit "
                          "flips the verdict to ADVANCING. The false-'still "
                          "working' sibling of the default caught-lie scene")
    pqs.add_argument("--driver", metavar="NAME", default=None,
                     help="scaffold the throwaway repo under a named driver pack "
                          "(e.g. workshop) instead of the auto-derived generic "
                          "config, and show its concurrent-lane arbitration. The "
                          "throwaway repo has no competing dos.toml, so this is the "
                          "one place a stranger can SEE a reference driver without "
                          "it being shadowed by a workspace's own [lanes].")
    pqs.add_argument("--output", default=None,
                     help="renderer for the verdict lines (text/json/plain or a "
                          "dos.renderers plugin); default text")
    pqs.set_defaults(func=cmd_quickstart)

    pec = sub.add_parser(
        "exit-codes",
        help="print the verdict-IS-the-exit-code table (all verbs or one)",
        description=(
            "Print the exit-code contract every verdict-bearing verb follows — the "
            "same table `dos doctor --json` carries under `exit_codes`, surfaced as a "
            "CLI so a shell/CI author can look it up without parsing JSON. "
            "`dos exit-codes VERB` filters to one verb; --json emits the machine form."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pec.add_argument("verb", nargs="?", default=None,
                     help="filter to one verb (e.g. verify, liveness); omit for all")
    pec.add_argument("--json", action="store_true",
                     help="emit the contract as JSON (the doctor --json shape)")
    pec.set_defaults(func=cmd_exit_codes)

    pv = sub.add_parser("verify", help="the truth syscall: did (plan,phase) ship?",
                        description=_HELP_VERIFY,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pv)
    pv.add_argument("plan")
    pv.add_argument("phase")
    pv.add_argument("--json", action="store_true")
    # docs/265 — skip the non-git evidence rung (the CI/checks probe) for this one
    # call, even when the workspace wired `[verify] non_git_oracle`. A fast path when
    # the operator wants the git answer without the network round-trip; a no-op in a
    # workspace that wired no oracle (the rung is already off there).
    pv.add_argument("--no-ci", action="store_true",
                    help="skip the non-git evidence rung (CI/checks); git-only verdict")
    _add_output_flag(pv)
    _add_explain_flag(pv)
    pv.set_defaults(func=cmd_verify)

    # attest (docs/246 Phase 1) — the portable, SIGNED receipt over an
    #   (full prose: docs/CLI.md § "attest (docs/246 Phase 1) — the portable, SIGNED receipt ove")
    pat = sub.add_parser(
        "attest",
        help="mint a portable, SIGNED receipt over an effect-witness verdict so a "
             "third party (auditor / counterparty) can verify it without the loop",
        description=_HELP_ATTEST,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pat)
    pat.add_argument("--claim", required=True, metavar="KEY",
                     help="the opaque effect key the agent asserted (e.g. "
                          "'orders:row:42', 'quiz:Classic-Art-History') — the thing "
                          "to witness, never itself evidence")
    pat.add_argument("--narrated", default="", metavar="TEXT",
                     help="the agent's original phrasing of the claim (shown on the "
                          "receipt, never parsed for truth)")
    pat.add_argument("--accept-cmd", default=None, metavar="CMD",
                     help="witness via the OS exit code of this command (OS_RECORDED) "
                          "— the cheapest non-forgeable witness; mutually exclusive "
                          "with --before/--after")
    pat.add_argument("--before", default=None, metavar="PATH",
                     help="witness via a state-snapshot diff: path to the BEFORE "
                          "snapshot (a {key:value} JSON object the STORE wrote)")
    pat.add_argument("--after", default=None, metavar="PATH",
                     help="the AFTER state snapshot (paired with --before)")
    pat.add_argument("--third-party", action="store_true",
                     help="tag the state-diff snapshot rung THIRD_PARTY (a remote "
                          "store) instead of OS_RECORDED")
    pat.add_argument("--key-file", default=None, metavar="PATH",
                     help="the HMAC signing key (raw bytes); else read from "
                          "$DOS_ATTEST_KEY. A signed receipt needs a key")
    pat.add_argument("--timestamp", default="", metavar="RFC3339",
                     help="the attestation time (default: now, UTC). Injectable for "
                          "deterministic/replay runs")
    pat.add_argument("--json", action="store_true",
                     help="emit the full signed receipt as JSON (what a third party "
                          "receives + verifies)")
    pat.set_defaults(func=cmd_attest)

    # verify-receipt (docs/246) — the third-party check, NO loop access. Re-derives
    #   (full prose: docs/CLI.md § "verify-receipt (docs/246) — the third-party check, NO loop a")
    pvrc = sub.add_parser(
        "verify-receipt",
        help="check a portable `dos attest` receipt's signature with the shared key "
             "alone (fails LOUD on a tamper/forge) — the third-party surface",
        description=_HELP_VERIFY_RECEIPT,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pvrc)
    pvrc.add_argument("--receipt", default=None, metavar="PATH",
                      help="path to the receipt JSON to verify; else read it from stdin")
    pvrc.add_argument("--key-file", default=None, metavar="PATH",
                      help="the shared/public half (raw bytes) to check the signature "
                           "against; else read from $DOS_ATTEST_KEY")
    pvrc.add_argument("--json", action="store_true",
                      help="machine-readable {valid, reason, receipt, adverse}")
    pvrc.set_defaults(func=cmd_verify_receipt)

    pca = sub.add_parser(
        "commit-audit",
        help="does a commit's CLAIM match its DIFF? author-neutral (human or "
             "agent), plan-free — the universal byte-author≠claimant floor",
        description=(
            "Grade whether a commit's SUBJECT claim is witnessed by its own DIFF. "
            "The kernel's whole thesis is byte-author ≠ claimant: a commit subject "
            "is forgeable (whoever wrote the message authored it), the files it "
            "touched are not (the commit machinery did). That split is "
            "AUTHOR-NEUTRAL — a human's `fix: resolve the race` touching only a "
            "README is the same unwitnessed claim as an agent's `--allow-empty "
            "\"shipped\"`. Needs NO plan, NO phase, NO DOS vocabulary: it reads one "
            "commit (or a `A..B` range) and reports, per commit, whether the claim "
            "is `diff-witnessed` (non-forgeable) or rests on the `subject-only` "
            "(forgeable) message text. Fires CLAIM_UNWITNESSED only where a "
            "concrete code/test claim and a contradicting diff coexist; ABSTAINs on "
            "the rest. Grades did-the-diff-do-the-KIND-of-thing-claimed, never "
            "was-it-CORRECT (Wall 3). Drops into a pre-commit hook / CI gate: the "
            "exit code is the verdict (0 clean / 1 an unwitnessed claim found / 2 "
            "unreadable ref)."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pca)
    pca.add_argument("ref", nargs="?", default="HEAD",
                     help="a commit ref (default HEAD) or a `A..B` range to audit")
    pca.add_argument("--warn-only", action="store_true",
                     help="advisory: print findings but always exit 0 (never fail "
                          "a build) — for a non-blocking pre-commit warning")
    pca.add_argument("--docs-ok", action="store_true",
                     help="accept a doc-only diff as witnessing a code-effect claim "
                          "(silences EMPTY_CLAIM on repos that commit fixes as docs)")
    pca.add_argument("--sweep", action="store_true",
                     help="over a RANGE, print the aggregate DRIFT RATE "
                          "(unwitnessed/checkable) + a by-claim-kind grid instead of "
                          "the per-commit firehose — 'how honest are this repo's "
                          "commit messages?'")
    pca.add_argument("--json", action="store_true")
    pca.set_defaults(func=cmd_commit_audit)

    pvr = sub.add_parser(
        "verify-result",
        help="the fold-site result-state witness: did a subagent's terminal record "
             "DIE (a harness-synthesized rate-limit/quota/error), or is it a real "
             "result? (docs/197 §7 keystone)",
        description=(
            "Classify a subagent TRANSCRIPT's terminal assistant record. An ultracode "
            "Workflow folds agent()'s self-authored return as ground truth at the "
            "${result} interpolation, and ~32% of real subagents return a "
            "HARNESS-synthesized terminal-error string there (a rate-limit / quota / "
            "auth / server error) that survives .filter(Boolean) and is banked as a "
            "finished finding. This verb keys on message.model=='<synthetic>' (the "
            "unforgeable harness-authorship marker — the CC harness, not the "
            "subagent's model, authored the bytes), corroborated by top-level "
            "isApiErrorMessage + stop_reason=='stop_sequence', and REFUSES to believe "
            "a harness-authored death — grounding, not consistency (docs/138). Reads "
            "--transcript PATH, or a hook event with transcript_path on STDIN. The "
            "EXIT CODE is the branch signal at the fold: 3 = DEAD (route to a DEAD "
            "bucket, count in the denominator, do NOT fold), 0 = HEALTHY (or "
            "UNREADABLE — the fail-safe floor: a read fault never fabricates a "
            "death), 2 = contract error. --json emits the full verdict + refusal "
            "envelope. ADVISORY: it reports, it never re-runs a worker (docs/197 §6.5)."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pvr)
    pvr.add_argument("--transcript", default=None, metavar="PATH",
                     help="the subagent transcript JSONL to witness (default: the "
                          "transcript_path on a hook event read from STDIN)")
    pvr.add_argument("--json", action="store_true",
                     help="emit the full verdict object {state, dead, class, "
                          "api_status, reason, envelope} instead of the text line")
    pvr.set_defaults(func=cmd_verify_result)

    pcov = sub.add_parser(
        "coverage",
        help="the cheap, non-git fan-out coverage fold: how many of N declared "
             "workers REALLY returned a result vs died? (docs/197 §7 follow-up)",
        description=(
            "Fold a fan-out's per-worker terminal-states against the workflow-DECLARED "
            "count N into a coverage verdict {FULL, UNDERFILLED, STARVED, OVERFILLED, "
            "EMPTY}. The follow-up to verify-result: a Workflow logs() coverage and "
            "throws it away, so a 4-of-7 fan-out is laundered as 7/7 (failed = N − "
            "survivors.length cannot tell a harness death from a real negative). This "
            "verb's --json carries a `prompt_line` — the legible sentence a workflow "
            "feeds INTO the synthesis prompt so a sub-quorum can't pass as full. An "
            "HONEST AGGREGATOR (folds already-adjudicated result_state verdicts, 0 new "
            "labels — the fleet_roll posture, docs/179); the win is that --declared is "
            "INDEPENDENT of the survivor list (laundering is then structurally "
            "impossible) and it surfaces the discarded `unaccounted` count. Two input "
            "modes: --transcript/--transcripts-glob is HARNESS-GROUNDED (coverage runs "
            "verify-result itself; --json stamps grounded:true); --states "
            "HEALTHY,SYNTHETIC,… is CALLER-ASSERTED/provenance-degraded (grounded:false). "
            "--declared N is REQUIRED, never inferred. EXIT: 0 = FULL/EMPTY (fold), 3 = "
            "degraded (inject the caveat, count the gap), 2 = contract error. ADVISORY: "
            "it never re-runs a worker or judges a healthy return's correctness (that is "
            "effect_witness, the witness-routing rung)."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pcov)
    pcov.add_argument("--declared", type=int, default=None, metavar="N",
                      help="the EXPECTED fan-out width (the denominator) — REQUIRED, "
                           "never inferred from the input length (that independence is "
                           "the laundering fix)")
    pcov.add_argument("--transcript", action="append", default=None, metavar="PATH",
                      help="a subagent transcript JSONL to witness (repeatable). "
                           "HARNESS-GROUNDED: coverage runs verify-result per path")
    pcov.add_argument("--transcripts-glob", default=None, metavar="GLOB",
                      help="a glob of subagent transcript JSONLs to witness "
                           "(HARNESS-GROUNDED). Combinable with --transcript")
    pcov.add_argument("--states", default=None, metavar="S1,S2,…",
                      help="a comma list of per-worker TerminalStates "
                           "(HEALTHY/SYNTHETIC/EMPTY/UNREADABLE) the CALLER already "
                           "witnessed via verify-result. PROVENANCE-DEGRADED "
                           "(caller-asserted; coverage cannot re-ground them → "
                           "grounded:false)")
    pcov.add_argument("--min-quorum", type=float, default=None, metavar="F",
                      help="LEGIBILITY-only: stamp quorum_met = healthy/declared >= F. "
                           "Never changes the verdict (FULL stays strict equality)")
    pcov.add_argument("--json", action="store_true",
                      help="emit the full verdict object {state, declared, healthy, "
                           "dead, unreadable, unaccounted, fraction, prompt_line, "
                           "grounded, …} instead of the text line")
    pcov.set_defaults(func=cmd_coverage)

    pmh = sub.add_parser(
        "model-health",
        help="the per-MODEL fleet death rollup: WHICH model is down across the "
             "children and grandchildren — and what to reroute",
        description=(
            "Fold a set of descendant transcripts into a per-model tally of "
            "MODEL_UNAVAILABLE deaths, so a model that is down on a child or "
            "grandchild (\"<model> is currently unavailable\" — the worker never ran, "
            "returning success+is_error+1-turn+$0) is visible at a glance instead of "
            "buried in one transcript. The observability sibling of verify-result and "
            "coverage: verify-result classifies ONE death, coverage folds N deaths "
            "into a quorum count, this groups the model-down deaths by the MODEL each "
            "died on and surfaces the reroute targets. HARNESS-GROUNDED (runs "
            "result_state itself, so the counts cannot be forged by a self-reporting "
            "fleet); an HONEST AGGREGATOR (0 new labels — every death was already "
            "adjudicated by result_state). --json carries {any_model_down, "
            "reroute_targets, tallies, headline, …}. EXIT: 0 = all models healthy, "
            "5 = a model is DOWN (a shell conductor branches into a reroute on 5 "
            "without parsing JSON — the auto-routing trigger), 2 = contract error. "
            "ADVISORY: it REPORTS which models are down + the reroute heal; it never "
            "re-dispatches a worker (the live-model roster is host/driver policy)."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pmh)
    pmh.add_argument("--session", default=None, metavar="SESSION.jsonl",
                     help="a session transcript JSONL — AUTO-DISCOVER its descendant "
                          "agents (child→grandchild→…) by the parentUuid tree and fold "
                          "each with its depth tag. No need to know where the descendant "
                          "transcripts live (the 'down on a child or grandchild etc' "
                          "axis). Mutually exclusive with --transcript/--transcripts-glob")
    pmh.add_argument("--transcript", action="append", default=None, metavar="PATH",
                     help="a descendant transcript JSONL to witness (repeatable)")
    pmh.add_argument("--transcripts-glob", default=None, metavar="GLOB",
                     help="a glob of descendant transcript JSONLs to witness "
                          "(combinable with --transcript)")
    pmh.add_argument("--json", action="store_true",
                     help="emit the full rollup object {any_model_down, "
                          "reroute_targets, model_unavailable, tallies, headline, …} "
                          "instead of the text lines")
    pmh.set_defaults(func=cmd_model_health)

    pmr = sub.add_parser(
        "model-reroute",
        help="the auto-HEALING actuator: from a model-health verdict + a host "
             "roster, PROPOSE a re-dispatch of each down model's units on a sibling",
        description=(
            "The other half of model-health, the auto-healing word: model-health "
            "says WHICH model is down across the fleet; this says WHAT to route to "
            "(the first non-down model in --roster, preference = roster order) and "
            "carries the re-dispatch command with the sibling substituted — one paste "
            "away. PROPOSE-NOT-ENACT (the watchdog boundary): it launches NOTHING; "
            "the spawn is the operator's paste or a further host driver. Input is the "
            "same as model-health (--session auto-discovers descendants, or "
            "--transcript/--transcripts-glob). FAIL-CLOSED: if every roster model is "
            "down, or a down model is unnamed, the proposal is an ESCALATE (an "
            "operator must act), never a silent drop. The roster is HOST policy — the "
            "kernel names no model; you pass it in. EXIT: 0 = nothing down or every "
            "down model got a REROUTE, 5 = an ESCALATE is needed, 2 = contract error."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pmr)
    pmr.add_argument("--roster", default="", metavar="M1,M2,…",
                     help="the host's model roster, best-first (REQUIRED). The first "
                          "model that is not itself down is the reroute target. The "
                          "kernel names no model — this is where the roster lives")
    pmr.add_argument("--session", default=None, metavar="SESSION.jsonl",
                     help="a session transcript — auto-discover descendants (as in "
                          "model-health). Mutually exclusive with --transcript/-glob")
    pmr.add_argument("--transcript", action="append", default=None, metavar="PATH",
                     help="a descendant transcript JSONL (repeatable)")
    pmr.add_argument("--transcripts-glob", default=None, metavar="GLOB",
                     help="a glob of descendant transcript JSONLs")
    pmr.add_argument("--command-template", default="", metavar="TMPL",
                     help="a re-dispatch command template; '{model}' is replaced with "
                          "the chosen sibling (e.g. 'claude -p --model {model} …'). "
                          "Carried in the proposal, NEVER run")
    pmr.add_argument("--json", action="store_true",
                     help="emit the proposals as a JSON array [{action, down_model, "
                          "sibling, units, command, reason}] instead of the text plan")
    pmr.set_defaults(func=cmd_model_reroute)

    pln = sub.add_parser(
        "liveness",
        help="the temporal verdict: is the run moving (ADVANCING/SPINNING/STALLED)?",
        description=_HELP_LIVENESS,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pln)
    pln.add_argument("--run-id", required=True, metavar="RID",
                     help="the run-id (RID-…) whose start-ms is decoded from the token")
    pln.add_argument("--start-sha", default="", metavar="SHA",
                     help="the run's start commit; commits in SHA..HEAD are the forward delta")
    pln.add_argument("--now-ms", type=int, default=None, metavar="MS",
                     help="wall-clock epoch-ms (default: now). Injectable for deterministic runs")
    pln.add_argument("--last-heartbeat-age-ms", type=int, default=None, metavar="MS",
                     help="OVERRIDE the journal-derived heartbeat age (ms). When given "
                          "it wins over the lane journal (a non-journal source, e.g. the "
                          "live registry). Absent ⇒ use the journal-derived age (or none)")
    pln.add_argument("--lane", default="", metavar="LANE",
                     help="this run's lease lane — with --loop-ts, scopes the journal "
                          "rungs to THIS lease (events count toward ADVANCING, heartbeat "
                          "derived). Absent ⇒ journal rungs silent (commit rung only)")
    pln.add_argument("--loop-ts", default="", metavar="TS",
                     help="this run's lease loop-ts (the (loop_ts, lane) identity). "
                          "Required alongside --lane to engage the journal rungs")
    pln.add_argument("--pid", type=int, default=None, metavar="PID",
                     help="the run's OS pid — engages the unforgeable proc-liveness "
                          "rung (docs/95): if the OS reports the pid is GONE, an "
                          "otherwise-SPINNING verdict demotes to STALLED (a fresh "
                          "heartbeat on a dead process). Absent ⇒ rung silent")
    pln.add_argument("--host-id", default="", metavar="HOST",
                     help="the host the pid was recorded on; if it differs from this "
                          "host the proc rung stays silent (a pid is host-local)")
    pln.add_argument("--no-proc", action="store_true",
                     help="disable the OS proc-liveness probe even when --pid is given")
    pln.add_argument("--usage-json", dest="usage_json", default=None, metavar="PATH",
                     help="a provider usage record (file, or `-` for stdin; docs/300) — "
                          "its normalized token total feeds the OPTIONAL waste signal "
                          "`tokens_spent_since`, so a SPINNING verdict can SAY how many "
                          "tokens burned with no commit. NEVER a verdict input (docs/219: "
                          "tokens never decide ADVANCING/SPINNING/STALLED); absent ⇒ "
                          "byte-identical to before")
    pln.add_argument("--json", action="store_true",
                     help="machine-readable verdict {verdict, reason, evidence}")
    _add_output_flag(pln)
    pln.set_defaults(func=cmd_liveness)

    # productivity (docs/218) — liveness's lateral sibling. Where liveness reads a
    #   (full prose: docs/CLI.md § "productivity (docs/218) — liveness's lateral sibling. Where")
    pprd = sub.add_parser(
        "productivity",
        help="the loop-economics verdict: is the run still doing work "
             "(PRODUCTIVE/DIMINISHING/STALLED)?",
        description=_HELP_PRODUCTIVITY,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pprd)
    pprd.add_argument("--deltas", default=None, metavar="D1,D2,…",
                      help="the per-step work deltas, OLDEST first — a comma list of "
                           "non-negative integers (work units the runtime measured "
                           "each step: tokens / commits / changed bytes). The last two "
                           "drive the verdict. Empty ⇒ PRODUCTIVE (nothing to judge)")
    pprd.add_argument("--min-steps", type=int, default=None, metavar="N",
                      help="minimum steps before a run can be called DIMINISHING "
                           "(default 3 — the CC continuationCount>=3). Below it there "
                           "is too little trend to judge a fading rate")
    pprd.add_argument("--floor", type=int, default=None, metavar="UNITS",
                      help="the per-step work-unit floor (default 500 — the CC "
                           "DIMINISHING_THRESHOLD). A run is DIMINISHING only when its "
                           "last two deltas are BOTH under this (a sustained low rate)")
    pprd.add_argument("--json", action="store_true",
                      help="machine-readable verdict {verdict, reason, history}")
    _add_output_flag(pprd)
    pprd.set_defaults(func=cmd_productivity)

    # efficiency (docs/263) — productivity's lateral sibling. Where productivity reads
    #   (full prose: docs/CLI.md § "efficiency (docs/263) — productivity's lateral sibling. Wher")
    peff = sub.add_parser(
        "efficiency",
        help="the token-effectiveness verdict: did the tokens buy work "
             "(EFFICIENT/COSTLY/WASTEFUL)?",
        description=_HELP_EFFICIENCY,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(peff)
    peff.add_argument("--work", type=int, default=0, metavar="UNITS",
                      help="the work the environment WITNESSED for this run — a "
                           "non-negative count in YOUR unit (commits / changed bytes / "
                           "passed tests). 0 with meaningful --tokens ⇒ WASTEFUL")
    peff.add_argument("--tokens", type=int, default=0, metavar="N",
                      help="the tokens this run SPENT (the provider usage record) — a "
                           "non-negative count. Below --min-tokens ⇒ EFFICIENT (too "
                           "little spend to judge)")
    peff.add_argument("--min-tokens", type=int, default=None, metavar="N",
                      help="minimum tokens spent before a run can be called "
                           "COSTLY/WASTEFUL (default 1000). Below it there is too little "
                           "spend to judge a ratio (the productivity --min-steps analogue)")
    peff.add_argument("--floor", type=float, default=None, metavar="RATIO",
                      help="the work-per-token floor: the minimum work/token a run must "
                           "clear to be EFFICIENT (default 0.0 = DISABLED, so only "
                           "WASTEFUL fires). Arm it with a ratio that means something for "
                           "YOUR work unit; under it (with nonzero work) ⇒ COSTLY")
    peff.add_argument("--usage-json", dest="usage_json", default=None, metavar="PATH",
                      help="read the provider usage record itself (a JSON file, or "
                           "`-` for stdin) — docs/300: normalizes the wire shape into "
                           "the typed five-way split, derives --tokens from its total "
                           "(or cross-checks an explicit --tokens), and surfaces the "
                           "cache-hit / decode / reasoning diagnostics in the verdict")
    peff.add_argument("--json", action="store_true",
                      help="machine-readable verdict {verdict, reason, evidence}")
    _add_output_flag(peff)
    peff.set_defaults(func=cmd_efficiency)

    # efficiency-trend (docs/300) — the cross-run fold over the same two counts:
    # is work-per-token fading ACROSS runs? Evidence comes caller-assembled
    # (--samples) or from the verdict journal's fossils (--from-journal).
    ptrd = sub.add_parser(
        "efficiency-trend",
        help="the cross-run token-effectiveness trend: is work-per-token fading "
             "across runs (IMPROVING/STEADY/DEGRADING)?",
        description=_HELP_EFFICIENCY_TREND,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(ptrd)
    ptrd.add_argument("--samples", default=None, metavar="W:T,W:T,…",
                      help="the per-run (work:tokens) pairs, OLDEST first — the same "
                           "two env-authored counts `dos efficiency` reads, one pair "
                           "per run. Mutually exclusive with --from-journal")
    ptrd.add_argument("--from-journal", dest="from_journal", action="store_true",
                      help="fold the verdict journal's recorded efficiency evidence "
                           "instead (every --observe'd `dos efficiency` call recorded "
                           "its work/tokens). Read-only; oldest first")
    ptrd.add_argument("--run", default=None, metavar="RID",
                      help="with --from-journal: only this run-id's efficiency events")
    ptrd.add_argument("--last", type=int, default=None, metavar="N",
                      help="with --from-journal: fold only the most recent N samples")
    ptrd.add_argument("--min-samples", dest="min_samples", type=int, default=None,
                      metavar="N",
                      help="minimum runs before the trend will judge a direction "
                           "(default 3; never below 3 — two recent runs + one "
                           "baseline run is the smallest readable shape)")
    ptrd.add_argument("--tolerance", type=float, default=None, metavar="FRAC",
                      help="the fractional band around the prior-median ratio "
                           "(default 0.25): DEGRADING/IMPROVING need the last TWO "
                           "runs both outside it (sustained, one outlier can't trip)")
    ptrd.add_argument("--json", action="store_true",
                      help="machine-readable verdict {verdict, reason, history}")
    _add_output_flag(ptrd)
    ptrd.set_defaults(func=cmd_efficiency_trend)

    # work-account (docs/310) — the composition sibling: where productivity reads a
    # trend and efficiency a ratio, this reads a typed account of one iteration's
    # work BY KIND and names the dominant kind + the composed headline.
    pwka = sub.add_parser(
        "work-account",
        help="the work-kind account: what KINDS of work did this iteration land "
             "(SHIPPED/CAUGHT/ADVANCED/GROOMED/SURFACED/IDLE)?",
        description=_HELP_WORK_ACCOUNT,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pwka)
    pwka.add_argument("--verified-ships", dest="verified_ships", type=int, default=0,
                      metavar="N",
                      help="phases the ORACLE confirmed closed (dos verify / "
                           "dos reconcile VERIFIED) — never the claim")
    pwka.add_argument("--claimed-ships", dest="claimed_ships", type=int, default=0,
                      metavar="N",
                      help="phases the iteration SAID it closed — the self-report, "
                           "carried so the over-claim gap stays visible; alone it "
                           "classifies IDLE (claims cannot climb the ladder)")
    pwka.add_argument("--catches", type=int, default=0, metavar="N",
                      help="claims the oracle REFUTED (reconcile QUIET_INCOMPLETE, a "
                           "commit-audit drift) — a caught lie is counted work, exit 3")
    pwka.add_argument("--advance-commits", dest="advance_commits", type=int, default=0,
                      metavar="N",
                      help="commits git recorded on the leased lane that closed no "
                           "phase — partial progress, no longer graded zero")
    pwka.add_argument("--grooms", type=int, default=0, metavar="N",
                      help="durable plan-state bookkeeping (stamps reconciled, "
                           "findings closed/added, inbox promotions)")
    pwka.add_argument("--unblocks", type=int, default=0, metavar="N",
                      help="units that flipped HELD/BLOCKED → OFFERABLE this iteration")
    pwka.add_argument("--surfaced", type=int, default=0, metavar="N",
                      help="operator-decision entries raised")
    pwka.add_argument("--json", action="store_true",
                      help="machine-readable verdict {verdict, reason, account}")
    _add_output_flag(pwka)
    pwka.set_defaults(func=cmd_work_account)

    # improve (docs/280) — the keep-gate of the first self-improving work loop for
    #   (full prose: docs/CLI.md § "improve (docs/280) — the keep-gate of the first self-improvi")
    pimp = sub.add_parser(
        "improve",
        help="the self-improving-loop keep-gate: may this loop KEEP this candidate "
             "(KEEP/REVERT/ESCALATE)?",
        description=_HELP_IMPROVE,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pimp)
    # The two boolean WITNESSES — env-authored, the non-negotiable floor. Default
    # False (a missing witness is a failing witness — fail-safe).
    pimp.add_argument("--suite-passed", dest="suite_passed", action="store_true",
                      help="the test suite passed on the candidate-only tree (the "
                           "runner's exit 0). MISSING ⇒ treated as red (fail-safe)")
    pimp.add_argument("--truth-clean", dest="truth_clean", action="store_true",
                      help="the truth syscall is clean (dos verify / commit-audit over "
                           "git ancestry agreed). MISSING ⇒ treated as dirty (fail-safe)")
    pimp.add_argument("--work", type=int, default=0, metavar="UNITS",
                      help="the env-measured improvement metric AFTER the candidate — a "
                           "non-negative count in YOUR unit (passing tests / 1000 minus "
                           "lint findings / a budget). KEEP needs this > --baseline-work")
    pimp.add_argument("--baseline-work", dest="baseline_work", type=int, default=0,
                      metavar="UNITS",
                      help="the SAME metric BEFORE the candidate (measured on the green "
                           "baseline tree). A strict gain (work > baseline) is required "
                           "for KEEP — an env-measured gain, never a claimed one")
    pimp.add_argument("--tokens", type=int, default=0, metavar="N",
                      help="tokens the candidate's proposing agent spent (for the "
                           "efficiency rung; only matters under an armed --efficiency-floor)")
    pimp.add_argument("--consecutive-reverts", dest="consecutive_reverts", type=int,
                      default=0, metavar="N",
                      help="the carried breaker count: prior candidates in a row that did "
                           "not KEEP. The Nth reaching --max-reverts ⇒ ESCALATE")
    pimp.add_argument("--max-reverts", dest="max_reverts", type=int, default=None,
                      metavar="N",
                      help="ESCALATE to a human after this many non-keeps in a row "
                           "(default 3). The RSI bottleneck — hand the judgment back "
                           "rather than propose another candidate nothing accepts")
    pimp.add_argument("--efficiency-floor", dest="efficiency_floor", type=float,
                      default=None, metavar="RATIO",
                      help="arm the WASTEFUL revert: a metric-improving candidate whose "
                           "work/token ratio falls under this floor is reverted as "
                           "overpriced (default 0.0 = DISABLED; every improvement is kept)")
    pimp.add_argument("--min-tokens", type=int, default=None, metavar="N",
                      help="min spend before the efficiency rung accuses (default 1000); "
                           "passed through to efficiency.classify")
    pimp.add_argument("--narrated", type=str, default=None, metavar="TEXT",
                      help="the candidate's own description — carried for the operator "
                           "surface and parsed for NOTHING (it cannot move REVERT→KEEP; "
                           "docs/234)")
    pimp.add_argument("--usage-json", dest="usage_json", default=None, metavar="PATH",
                      help="read the proposing agent's provider usage record (a JSON "
                           "file, or `-` for stdin) — docs/300: derives --tokens from "
                           "the typed five-way split and carries the cache-hit / "
                           "decode / reasoning diagnostics into the keep/revert record")
    pimp.add_argument("--json", action="store_true",
                      help="machine-readable verdict {verdict, revert_cause, escalation, "
                           "next_consecutive_reverts, reason, evidence}")
    _add_output_flag(pimp)
    pimp.set_defaults(func=cmd_improve)

    # breaker (docs/223) — the generic circuit-breaker extracted from loop_decide's
    #   (full prose: docs/CLI.md § "breaker (docs/223) — the generic circuit-breaker extracted f")
    pbrk = sub.add_parser(
        "breaker",
        help="the circuit breaker: has this failure class tripped "
             "(CLOSED/OPEN)? escalate the rung on a trip",
        description=_HELP_BREAKER,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pbrk)
    pbrk.add_argument("--consecutive", type=int, default=None, metavar="N",
                      help="failures IN A ROW so far (a sustained outage; reset by a "
                           "success). Trips when >= --max-consecutive")
    pbrk.add_argument("--total", type=int, default=None, metavar="N",
                      help="CUMULATIVE failures so far (a flapping failure a streak "
                           "misses; never reset). Trips when >= --max-total")
    pbrk.add_argument("--max-consecutive", type=int, default=None, metavar="N",
                      help="the consecutive-failure limit (default 3 — the CC "
                           "maxConsecutive). 0 disables this rung")
    pbrk.add_argument("--max-total", type=int, default=None, metavar="N",
                      help="the total-failure limit (default 20 — the CC maxTotal). "
                           "0 disables this rung. At least one rung must be enabled")
    pbrk.add_argument("--on-trip", default=None, choices=("none", "judge", "human"),
                      help="where an OPEN breaker escalates: none (advisory, default) "
                           "/ judge (a dos.judges adjudicator) / human (the decisions "
                           "queue) — the ORACLE→JUDGE→HUMAN trust ladder")
    pbrk.add_argument("--json", action="store_true",
                      help="machine-readable verdict {state, escalation, reason, tripped_on}")
    _add_output_flag(pbrk)
    pbrk.set_defaults(func=cmd_breaker)

    # exec-capability (docs/224) — idea B1: the arbitrary-exec capability classifier.
    # CC's dangerousPatterns lifted as the docs/158 "a SHAPE not a word" law applied to
    # command auditing. PURE, advisory, no-plan — matches the invoked PROGRAM, never a
    # substring. The consumer (dos hook pretool) decides what to do with the verdict.
    pxc = sub.add_parser(
        "exec-capability",
        help="does this command grant arbitrary code execution "
             "(GRANTS_ARBITRARY_EXEC/BOUNDED)? a SHAPE, not a word",
        description=_HELP_EXEC_CAPABILITY,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pxc)
    pxc.add_argument("--command", default=None, metavar="CMD",
                     help="the command string to classify (the invoked program is "
                          "matched against the arbitrary-exec set; a substring is NOT)")
    pxc.add_argument("--extra", default=None, metavar="P1,P2,…",
                     help="extra program tokens to treat as arbitrary-exec entry points "
                          "(a host's own interpreters; comma list). Added to the default "
                          "cross-platform set, never replacing it")
    pxc.add_argument("--json", action="store_true",
                     help="machine-readable verdict {capability, program, reason}")
    _add_output_flag(pxc)
    pxc.set_defaults(func=cmd_exec_capability)

    # hook-exit (docs/226) — idea C3: map a plain shell hook's exit code to an
    # intervention verb (CC's 0/2/other convention). PURE, no-plan — the cheapest
    # integration surface for a script too simple to emit JSON. The verb's own exit
    # reflects the rung so a shell wrapper can branch.
    phe = sub.add_parser(
        "hook-exit",
        help="map a shell hook's exit code → an intervention verb "
             "(0 pass / 2 BLOCK / other WARN)",
        description=_HELP_HOOK_EXIT,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(phe)
    phe.add_argument("--code", type=int, required=True, metavar="N",
                     help="the exit code the hook script returned (the integer from $?)")
    phe.add_argument("--map", default=None, metavar="C=VERB,…",
                     help="declare custom code→verb mappings (comma list, e.g. "
                          "'3=DEFER,4=OBSERVE'). Merged over the default CC convention; "
                          "VERB is one of OBSERVE/WARN/BLOCK/DEFER")
    phe.add_argument("--json", action="store_true",
                     help="machine-readable verdict {code, intervention, reason, matched}")
    _add_output_flag(phe)
    phe.set_defaults(func=cmd_hook_exit)

    # answer-shape (docs/156 §4) — the grounded-but-not-an-answer verdict. Catches a
    #   (full prose: docs/CLI.md § "answer-shape (docs/156 §4) — the grounded-but-not-an-answer")
    pash = sub.add_parser(
        "answer-shape",
        help="is this output an ANSWER or a structural non-answer "
             "(ANSWER_SHAPED/NON_ANSWER/INDETERMINATE)? a SHAPE, not correctness",
        description=_HELP_ANSWER_SHAPE,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pash)
    pash.add_argument("--text", default=None, metavar="TEXT",
                      help="the candidate output to classify; '-' reads stdin (the "
                           "natural way to pipe a large drafted answer in)")
    pash.add_argument("--file", default=None, metavar="PATH",
                      help="read the candidate output from a file; '-' reads stdin. "
                           "Wins over --text if both are given")
    pash.add_argument("--min-chars", type=int, default=None, metavar="N",
                      help="the viability floor (default 1 — only empty/whitespace "
                           "disqualifies on length). A non-empty output below it is a "
                           "NON_ANSWER stub. Length catches the empty end; the markers "
                           "catch the leaked-CoT end")
    pash.add_argument("--non-answer", default=None, metavar="RE,…",
                      help="extra disqualifying regexes (comma list) ADDED to the "
                           "generic cross-domain default, never replacing it. A hit "
                           "(case-insensitive, anywhere) ⇒ NON_ANSWER. A bad regex is "
                           "skipped, never raised (the fail-safe under-disqualify)")
    pash.add_argument("--markers", default=None, metavar="RE,…",
                      help="positive answer signatures (comma list) — strict mode: when "
                           "set, a non-trivial text matching NONE of them is "
                           "INDETERMINATE (the host required positive evidence and "
                           "found none), not ANSWER_SHAPED")
    pash.add_argument("--json", action="store_true",
                      help="machine-readable verdict {state, length, matched, "
                           "is_shippable, is_disqualified, reason}")
    _add_output_flag(pash)
    pash.set_defaults(func=cmd_answer_shape)

    # reward (docs/230/234) — the lab on-ramp: may a fine-tune TRAIN on this
    #   (full prose: docs/CLI.md § "reward (docs/230/234) — the lab on-ramp: may a fine-tune TRA")
    prw = sub.add_parser(
        "reward",
        help="may a fine-tune TRAIN on this trajectory? the reward-set admission "
             "verdict (ACCEPT/REJECT_POISON/ABSTAIN/NO_CLAIM) — the non-distillable label",
        description=_HELP_REWARD,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(prw)
    prw.add_argument("--claim", action="store_true",
                     help="this trajectory made a checkable claim (the default intent). "
                          "YOUR extractor decides this at the boundary; the kernel never "
                          "parses domain text")
    prw.add_argument("--no-claim", action="store_true",
                     help="no checkable claim — NO_CLAIM (not a positive candidate)")
    prw.add_argument("--witness", default="none", metavar="{confirm,refute,none}",
                     choices=["confirm", "refute", "none"],
                     help="what a NON-FORGEABLE read-back (env DB-hash / OS exit / ledger) "
                          "saw: confirm (effect present) / refute (absent) / none (no "
                          "accountable witness reached). Default none")
    prw.add_argument("--forgeable", action="store_true",
                     help="put the witness on the AGENT_AUTHORED rung (the agent re-read "
                          "its OWN surface) — it is STRUCTURALLY ignored, so even "
                          "'--witness confirm --forgeable' cannot ACCEPT. The floor, demoed")
    prw.add_argument("--narrated", default=None, metavar="TEXT",
                     help="the agent's own phrasing, carried for the operator surface and "
                          "NEVER parsed for truth (the verdict is invariant under it)")
    prw.add_argument("--json", action="store_true",
                     help="machine-readable label {verdict, accept, poison, dispreferred, "
                          "claim_present, witness, accountability, reason} (the JSONL row)")
    _add_output_flag(prw)
    prw.set_defaults(func=cmd_reward)

    # test-witness (docs/288, TWV) — reverse-classical testing as a kernel rung:
    # does a NEW test actually witness the change it ships with? A pass/pass test
    # is VACUOUS (witnesses nothing); only env-recorded red->green DISCRIMINATES.
    # PURE, no-plan. The verdict IS the exit code so a CI/keep-gate can branch.
    ptw = sub.add_parser(
        "test-witness",
        help="does a NEW test actually witness the change? reverse-classical "
             "testing (DISCRIMINATES/VACUOUS/UNSATISFIED/REGRESSIVE/ABSTAIN)",
        description=_HELP_TEST_WITNESS,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(ptw)
    ptw.add_argument("--baseline", default="not-run",
                     metavar="{pass,fail,error,not-run}",
                     choices=["pass", "fail", "error", "not-run"],
                     help="the runner's outcome for THE TEST on the tree WITHOUT the "
                          "change (fail = ran and rejected the old behavior; error = "
                          "could not even load). Default not-run (-> ABSTAIN: half a "
                          "join is not a join)")
    ptw.add_argument("--candidate", default="not-run",
                     metavar="{pass,fail,error,not-run}",
                     choices=["pass", "fail", "error", "not-run"],
                     help="the runner's outcome for THE TEST on the tree WITH the "
                          "change. Default not-run (-> ABSTAIN)")
    ptw.add_argument("--forgeable", action="store_true",
                     help="the outcomes are the agent's OWN narration (AGENT_AUTHORED) "
                          "— the verdict ABSTAINS, so a narrated red->green can never "
                          "mint a witness. The floor, demoed")
    ptw.add_argument("--json", action="store_true",
                     help="machine-readable verdict {verdict, witnesses, assert_level, "
                          "reason, evidence:{baseline, candidate, rung}}")
    _add_output_flag(ptw)
    ptw.set_defaults(func=cmd_test_witness)

    # resume (docs/107) — the third ARIES phase. Replay a run's intent ledger,
    #   (full prose: docs/CLI.md § "resume (docs/107) — the third ARIES phase. Replay a run's in")
    prz = sub.add_parser(
        "resume",
        help="the resume verdict: replay a run's intent ledger, re-verify "
             "progress against git, and PROPOSE the continuation (never executes)")
    _add_workspace_flags(prz)
    prz.add_argument("--run-id", required=True, metavar="RID",
                     help="the run-id whose intent ledger to replay & resume")
    prz.add_argument("--diverged", action="store_true",
                     help="assert ground truth advanced past the resume point on "
                          "this run's lane → DIVERGED (refuse + raise a decision). "
                          "A richer driver computes this from the lane tree; the "
                          "bare CLI lets the operator assert it")
    prz.add_argument("--no-record", action="store_true",
                     help="inspect-only: do NOT append a RESUME_PROPOSED record "
                          "(by default a RESUMABLE verdict records one, idempotently)")
    prz.add_argument("--json", action="store_true",
                     help="machine-readable {verdict, resume_sha, residual, …}")
    _add_output_flag(prz)
    prz.set_defaults(func=cmd_resume)

    # rewind (docs/164 F1.5) — resume's CONVERSATION-axis sibling. Replays the run's
    #   (full prose: docs/CLI.md § "rewind (docs/164 F1.5) — resume's CONVERSATION-axis sibling.")
    prw = sub.add_parser(
        "rewind",
        help="the conversation-rewind verdict: replay a run's ledger for the minted "
             "checkpoint, read its transcript, and PROPOSE excising the dead-end turns "
             "(never truncates — the host owns the transcript)")
    _add_workspace_flags(prw)
    prw.add_argument("--run-id", required=True, metavar="RID",
                     help="the run-id whose ledger + transcript to replay & rewind")
    prw.add_argument("--fire", metavar="SIGNAL", default="",
                     choices=["", "DIVERGED", "THRASHING", "STARVED"],
                     help="the ground-truth stop signal the boundary observed "
                          "(DIVERGED / THRASHING / STARVED). A richer driver computes "
                          "it from resume/convergence; the bare CLI lets the operator "
                          "assert it. Omitted → NO_REWIND (the loop continues)")
    prw.add_argument("--json", action="store_true",
                     help="machine-readable {verdict, rewind_to_turn, dropped_turns, "
                          "no_good_note, …}")
    _add_output_flag(prw)
    prw.set_defaults(func=cmd_rewind)

    # complete (docs/117) — the live completion verdict, the forward dual of resume.
    #   (full prose: docs/CLI.md § "complete (docs/117) — the live completion verdict, the forwa")
    pcm = sub.add_parser(
        "complete",
        help="the completion verdict: is the whole declared job verifiably done? "
             "(residual = declared − verified, asked forward; read-only)")
    _add_workspace_flags(pcm)
    pcm.add_argument("--run-id", required=True, metavar="RID",
                     help="the run-id whose intent ledger to adjudicate completion for")
    pcm.add_argument("--diverged", action="store_true",
                     help="assert ground truth advanced past the resume point on this "
                          "run's lane (carried into the INCOMPLETE reason; completion "
                          "stays 'not done' either way)")
    pcm.add_argument("--json", action="store_true",
                     help="machine-readable {state, residual, verified, declared, …}")
    _add_output_flag(pcm)
    pcm.set_defaults(func=cmd_complete)

    # docs/143 R1 — the argument-provenance verdict (did the model MINT an id, or RESOLVE
    # it?). A pure fold demoable from the operator surface: hand it a mutating call's args
    # + the env-authored bytes the agent saw, get believe/UNSUPPORTED.
    pap = sub.add_parser(
        "arg-provenance",
        help="did the model MINT this id/FK arg, or RESOLVE it from env-authored bytes? "
             "(docs/143 R1; pure; exit 0=believe, 3=UNSUPPORTED)")
    pap.add_argument("--tool", required=True, help="the tool name (its write-verb stem "
                     "decides mutating vs read)")
    pap.add_argument("--args", required=True, metavar="JSON",
                     help="the call's arguments as a JSON object")
    pap.add_argument("--prior", action="append", metavar="BLOB",
                     help="a prior tool RESULT (JSON/text). Repeatable — each is one "
                          "env-authored TOOL_RESULT blob")
    pap.add_argument("--prior-file", default=None,
                     help="read newline-delimited prior-result blobs from a file")
    pap.add_argument("--task-text", default="", help="the task prompt bytes (one TASK_TEXT "
                     "env blob — an id the task names is env-authored provenance)")
    pap.add_argument("--new-key", action="append", metavar="ARG",
                     help="an arg holding the NEW object's OWN identity (a create's own "
                          "key) — never nudged. Repeatable")
    pap.add_argument("--read", action="store_true",
                     help="mark the call non-mutating (a read is never gated; it sources "
                          "provenance)")
    pap.add_argument("--json", action="store_true",
                     help="machine-readable {believe, unsupported, args, reason}")
    pap.set_defaults(func=cmd_arg_provenance)

    # status (docs/120 Phase 2) — `dos status <run_id>`: the FOLDED FACT. One
    #   (full prose: docs/CLI.md § "status (docs/120 Phase 2) — `dos status <run_id>`: the FOLDE")
    pst = sub.add_parser(
        "status",
        help="the folded fact: one fail-closed digest of a run — liveness + "
             "ledger-verified progress + held-lease region + resume (once stopped). "
             "Never surfaces a self-report (no `claimed` field)")
    _add_workspace_flags(pst)
    pst.add_argument("run_id", metavar="RUN_ID",
                     help="the run-id (RID-…) the digest is keyed on")
    pst.add_argument("--start-sha", default="", metavar="SHA",
                     help="the run's start commit (commits in SHA..HEAD are the liveness "
                          "forward delta). Default: the run's declared start_sha off its "
                          "intent ledger, else empty (a conservative 0-commit floor)")
    pst.add_argument("--now-ms", type=int, default=None, metavar="MS",
                     help="wall-clock epoch-ms (default: now). Injectable for deterministic runs")
    pst.add_argument("--last-heartbeat-age-ms", type=int, default=None, metavar="MS",
                     help="OVERRIDE the journal-derived heartbeat age (ms) for the "
                          "liveness rung (a non-journal source, e.g. the live registry)")
    pst.add_argument("--lane", default="", metavar="LANE",
                     help="this run's lease lane — with --loop-ts, scopes the journal "
                          "liveness rungs to THIS lease (events toward ADVANCING, heartbeat)")
    pst.add_argument("--loop-ts", default="", metavar="TS",
                     help="this run's lease loop-ts (the (loop_ts, lane) identity); "
                          "required alongside --lane to engage the journal rungs")
    pst.add_argument("--stopped", action="store_true",
                     help="force the resume read: treat the run as STOPPED (compute the "
                          "resume verdict). Default: auto — stopped iff the ledger "
                          "SUSPENDed or liveness is STALLED")
    pst.add_argument("--live", action="store_true",
                     help="force-skip the resume read: treat the run as LIVE (resume "
                          "stays null, the expensive ancestry re-adjudication is skipped)")
    pst.add_argument("--json", action="store_true",
                     help="the A2A digest shape {schema, run_id, liveness, progress, "
                          "region, resume} — and NO `claimed` key (the fail-closed contract)")
    _add_output_flag(pst)
    pst.set_defaults(func=cmd_status)

    # SUP (docs/99) — `dos loop`: the supervisor. Count the held lane leases
    #   (full prose: docs/CLI.md § "SUP (docs/99) — `dos loop`: the supervisor. Count the held l")
    plp = sub.add_parser(
        "loop",
        help="the supervisor: keep N dispatch-loops alive across the lane roster")
    _add_workspace_flags(plp)
    plp.add_argument("--target", type=int, default=None, metavar="N",
                     help="desired live-worker population; overrides the standing "
                          "[supervise] target in dos.toml for this run (default: the "
                          "config target, or 1 if undeclared)")
    plp.add_argument("--max-concurrency", type=int, default=None, metavar="N",
                     help="the DERIVED-CLAIM concurrency budget (docs/283): let the "
                          "supervisor keep up to N workers alive on a fungible "
                          "auto-pick lane WITHOUT pre-declaring N disjoint trees — "
                          "the arbiter narrows each worker's per-pick claim at "
                          "acquire time. Overrides [supervise].max_concurrency for "
                          "this run. With this set on a dynamic-claim workspace "
                          "(concurrent=[]), a bare auto-pick handle is synthesised so "
                          "--target above the static disjoint-lane count is reachable")
    plp.add_argument("--watch", action="store_true",
                     help="re-emit the plan every --interval seconds (Ctrl-C to stop)")
    plp.add_argument("--interval", type=float, default=30.0, metavar="SECONDS",
                     help="--watch re-emit cadence in seconds (default 30; the "
                          "long-lived supervisor DRIVER uses a much longer one)")
    plp.add_argument("--now-ms", type=int, default=None, metavar="MS",
                     help="wall-clock epoch-ms (default: now). Injectable for "
                          "deterministic runs/tests, the `dos liveness` idiom")
    plp.add_argument("--json", action="store_true",
                     help="machine-readable plan {verdict, alive, admissible, "
                          "target, spawn/reap/flag, evidence}")
    _add_output_flag(plp)
    plp.set_defaults(func=cmd_loop)

    # watch — the push-model watchdog (docs/101): poll liveness per tracked run on
    # a cadence + propose halts on SPINNING / hung-past-budget. The per-run-health
    # sibling of `dos loop`'s population axis; the build that answers the §2.1
    # budget-late incident. Exit 0 (an effect record, not a verdict-as-exit-code).
    pw = sub.add_parser(
        "watch",
        help="the watchdog: poll liveness for tracked runs + propose halts on spin/hang")
    _add_workspace_flags(pw)
    pw.add_argument("--track", action="append", metavar="SPEC",
                    help="a run to watch: run_id[:start_sha[:lane[:loop_ts[:handle]]]] "
                         "(repeatable; run_id required, later fields optional)")
    pw.add_argument("--discover", action="store_true",
                    help="also watch every live lease (folded into tracked runs; "
                         "judged on the journal rung — a lease records no start SHA)")
    pw.add_argument("--budget-ms", type=int, default=None, metavar="MS",
                    help="wall-clock budget: a STALLED run past it is halted "
                         "(default: none — any STALLED run is treated as hung)")
    pw.add_argument("--command", default="",
                    help="the host-supplied stop command echoed in each halt proposal "
                         "(the paste-to-stop; the kernel NEVER runs it)")
    pw.add_argument("--repropose-ms", type=int, default=None, metavar="MS",
                    help="min interval between repeated halt proposals for one run "
                         "(default 30m; one proposal per genuine spin episode)")
    pw.add_argument("--watch", action="store_true",
                    help="re-poll every --interval seconds forever (Ctrl-C to stop)")
    pw.add_argument("--max-ticks", type=int, default=None, metavar="N",
                    help="re-poll N times then stop (bounded cadence; for scripts/tests)")
    pw.add_argument("--interval", type=float, default=300.0, metavar="SECONDS",
                    help="re-poll cadence in seconds (default 300 — a watchdog, "
                         "not a busy-poll)")
    pw.add_argument("--now-ms", type=int, default=None, metavar="MS",
                    help="wall-clock epoch-ms (default: now). Injectable for "
                         "deterministic runs/tests, the `dos liveness` idiom")
    pw.add_argument("--json", action="store_true",
                    help="machine-readable {watched, verdicts, proposed_halts, …}")
    _add_output_flag(pw)
    pw.set_defaults(func=cmd_watch)

    pa = sub.add_parser("arbitrate", help="the admission kernel: may a loop start?",
                        description=_HELP_ARBITRATE,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pa)
    pa.add_argument("--lane", default="", help="requested lane ('' = bare auto-pick)")
    pa.add_argument("--kind", default="", help="cluster|keyword|global|''")
    pa.add_argument("--tree", nargs="*", help="requested file tree (globs)")
    pa.add_argument("--leases", default="",
                    help="JSON list of live lease dicts; OVERRIDES the default "
                         "(which loads the live lane-journal WAL set, so a lease a "
                         "sibling holds is seen). Pass '[]' to arbitrate against an "
                         "empty world (the pure/testing path).")
    pa.add_argument("--force", action="store_true")
    pa.add_argument("--class-budget", action="append", default=None, metavar="KIND=N",
                    help="cap concurrent leases of a lane-kind at N (docs/97). "
                         "Repeatable. OVERLAYS the [[concurrency_class]] dos.toml "
                         "budgets (an explicit flag wins over the declared default). "
                         "The arbiter skips at-budget candidates on the auto-pick "
                         "walk and refuses CLASS_BUDGET_EXHAUSTED when it's the sole "
                         "blocker.")
    pa.add_argument("--pretty", action="store_true")
    _add_output_flag(pa)
    _add_explain_flag(pa)
    pa.set_defaults(func=cmd_arbitrate)

    # scope-gate (docs/102 §5) — the BINDING pre-effect scope gate. Asks the same
    #   (full prose: docs/CLI.md § "scope-gate (docs/102 §5) — the BINDING pre-effect scope gate")
    psg = sub.add_parser(
        "scope-gate",
        help="binding pre-effect scope gate: may this PROPOSED write land in its lane?")
    _add_workspace_flags(psg)
    psg.add_argument("--lane", default="",
                     help="the lane the proposed write claims (a key in [lanes]); "
                          "'' uses the generic **/* tree (the no-plan floor)")
    psg.add_argument("--file", action="append", metavar="PATH",
                     help="an explicit proposed-write path (repeatable); when given, "
                          "OVERRIDES the git gather — a caller that already computed "
                          "its diff's footprint (e.g. a commit broker) passes them here")
    psg.add_argument("--staged", action="store_true",
                     help="gather the proposed write-set from `git diff --cached "
                          "--name-only` (the edit-time footprint about to be committed)")
    psg.add_argument("--base", default="HEAD",
                     help="base ref when gathering from a range (default HEAD)")
    psg.add_argument("--head", default="HEAD",
                     help="head ref when gathering from a range (default HEAD)")
    psg.add_argument("--json", action="store_true",
                     help="machine-readable {allowed, verdict, refused_files, scope}")
    psg.add_argument("--pretty", action="store_true")
    _add_output_flag(psg)
    psg.set_defaults(func=cmd_scope_gate)

    pl = sub.add_parser("lease", help="cross-process archive lock")
    _add_workspace_flags(pl)
    lsub = pl.add_subparsers(dest="lease_cmd", required=True)
    la = lsub.add_parser("acquire")
    la.add_argument("owner")
    la.add_argument("--retries", type=int, default=5)
    la.add_argument("--retry-interval", type=float, default=2.0)
    la.add_argument("--ttl-seconds", type=int, default=300)
    lr = lsub.add_parser("release")
    lr.add_argument("owner")
    lr.add_argument("--force", action="store_true")
    lst = lsub.add_parser("status")
    lst.add_argument("--ttl-seconds", type=int, default=300)
    pl.set_defaults(func=cmd_lease)

    # lease-lane — the lane-lease WRITE-BACK over the pure arbiter (docs/96). The
    # durable, cross-process surface an ephemeral orchestrator (harness Workflow)
    # needs so parallel branches see each other's grants and collisions are
    # PREVENTED at contention, not detected after. The verdict IS the exit code.
    pll = sub.add_parser(
        "lease-lane",
        help="durable lane lease over the pure arbiter (write-back to the WAL)")
    _add_workspace_flags(pll)
    llsub = pll.add_subparsers(dest="lease_lane_cmd", required=True)

    lla = llsub.add_parser(
        "acquire", help="arbitrate a lane and, on acquire, journal the grant")
    lla.add_argument("--lane", default="", help="requested lane ('' = auto-pick)")
    lla.add_argument("--kind", default="", help="cluster|keyword|global|''")
    lla.add_argument("--tree", nargs="*", help="requested file tree (globs)")
    lla.add_argument("--owner", required=True,
                     help="lease holder tag (e.g. the workflow branch id)")
    lla.add_argument("--run-id", default="",
                     help="CID spine id (RID-…) to stamp on the lease — the "
                          "WAL↔spine join key, so `dos trace` can walk a held lane "
                          "back to its run (docs/137). Default: $CID_RUN_ID / "
                          "$DISPATCH_RUN_ID if set, else none")
    lla.add_argument("--loop-ts", default="",
                     help="the (loop_ts, lane) identity (default: a UTC stamp)")
    lla.add_argument("--leases", default="",
                     help="extra live leases (JSON array) to union with the WAL's")
    lla.add_argument("--retries", type=int, default=5,
                     help="lock-acquire retries before reporting lock-busy")
    lla.add_argument("--retry-interval", type=float, default=0.2)
    lla.add_argument("--pretty", action="store_true")

    llr = llsub.add_parser("release", help="release a held lane lease (RELEASE to the WAL)")
    llr.add_argument("--lane", required=True)
    llr.add_argument("--owner", required=True)
    llr.add_argument("--loop-ts", default="",
                     help="release this specific lease; omit for the newest on the lane")

    llh = llsub.add_parser(
        "heartbeat",
        help="refresh a held lane lease (HEARTBEAT to the WAL) — makes liveness "
             "SPINNING reachable from real evidence")
    llh.add_argument("--lane", required=True)
    llh.add_argument("--owner", required=True)
    llh.add_argument("--loop-ts", default="",
                     help="beat this specific lease; omit for the newest on the "
                          "lane held by --owner. Pass the SAME --loop-ts the "
                          "acquire used, or the beat folds as a no-op")
    llh.add_argument("--coalesce-within-s", type=float, default=0.0,
                     help="WAL-drain brake (docs/106): elide this beat if the "
                          "lease's current beat is younger than this many seconds "
                          "(still returns beat=true). Verdict-preserving when set "
                          "well under liveness spin_ms. Default 0 = write every beat")

    lls = llsub.add_parser(
        "spawn",
        help="record an INTENT to take a lane (SPAWN to the WAL) — the FIRST launch "
             "step, before preflight, so `dos top` sees the loop the instant it "
             "commits to a lane (the SPAWN→ACQUIRE window). Grants NO lease")
    lls.add_argument("--lane", required=True,
                     help="the lane the launcher is committing to")
    lls.add_argument("--owner", default="",
                     help="launcher tag (default: host:pid)")
    lls.add_argument("--loop-ts", default="",
                     help="the (loop_ts, lane) identity to carry onto the eventual "
                          "ACQUIRE (default: a UTC stamp)")
    lls.add_argument("--run-id", default="",
                     help="CID spine id for the SPAWN→ACQUIRE join (default: "
                          "$CID_RUN_ID / $DISPATCH_RUN_ID if set, else none)")
    lls.add_argument("--reason", default="",
                     help="free-text launch context for the operator")

    lll = llsub.add_parser(
        "live", help="the live-lease set reconstructed from the WAL (feeds --leases)")
    lll.add_argument("--pretty", action="store_true")
    pll.set_defaults(func=cmd_lease_lane)

    # override — the operator's SELF_MODIFY override window (docs/296): report
    # or disarm. Deliberately NO `arm` subcommand — arming is the operator's
    # hand on `.dos/override/self-modify.toml`, never a verb an agent can call.
    pov = sub.add_parser(
        "override",
        help="the operator's SELF_MODIFY override window: status / disarm "
             "(arming is by hand — docs/296)")
    _add_workspace_flags(pov)
    ovsub = pov.add_subparsers(dest="override_cmd", required=True)
    ovsub.add_parser(
        "status",
        help="report the armed window (exit 0 armed / 1 disarmed-or-expired)")
    ovsub.add_parser(
        "disarm",
        help="delete the arm file — always safe, for anyone (restores the deny)")
    pov.set_defaults(func=cmd_override)

    # halt — record a STOP DECISION for an in-flight run + propose the command
    #   (full prose: docs/CLI.md § "halt — record a STOP DECISION for an in-flight run + propose")
    phl = sub.add_parser(
        "halt",
        help="record a stop decision for an in-flight run (HALT to the WAL) + "
             "propose the stop command — never signals")
    _add_workspace_flags(phl)
    phl.add_argument("--handle", required=True,
                     help="OPAQUE identifier of the run to stop (a pid, container "
                          "id, remote-task token, …); recorded verbatim, never "
                          "interpreted")
    phl.add_argument("--lane", default="",
                     help="lane to correlate the HALT to (forensic only)")
    phl.add_argument("--loop-ts", default="",
                     help="(loop_ts, lane) identity to correlate to (forensic only)")
    phl.add_argument("--owner", default="",
                     help="who is requesting the halt (tags the reason)")
    phl.add_argument("--reason", default="", help="why this run is being stopped")
    phl.add_argument("--run-id", default="",
                     help="the run-id being stopped (recorded for the spine)")
    phl.add_argument("--command", default="",
                     help="the host-supplied stop command to echo back for a "
                          "driver/operator to run (the kernel NEVER runs it)")
    phl.add_argument("--resumable", action="store_true",
                     help="stop the run RESUMABLY (docs/107 §4): also append a "
                          "SUSPEND to the run's intent ledger so it is parked & "
                          "scavenge-immune, not hard-killed. Needs --run-id")
    phl.add_argument("--resume-sha", default="",
                     help="with --resumable: the recorded resume-point SHA to carry "
                          "on the SUSPEND (a hint, still re-verified at resume)")
    phl.add_argument("--pretty", action="store_true")
    phl.set_defaults(func=cmd_halt)

    # health — pre-dispatch lane-health gate (query startability BEFORE a child)
    ph = sub.add_parser(
        "health",
        help="pre-dispatch lane-health gate (overlap + recurring-blocker → route)",
    )
    ph.add_argument("--lane", required=True)
    ph.add_argument("--tree", default="",
                    help="comma-separated file-glob tree for the lane")
    ph.add_argument("--leases-json", default="",
                    help="JSON array of live leases [{lane,lane_kind,tree,loop_ts}]")
    ph.add_argument("--own-lease-ts", default="")
    ph.add_argument("--window", type=int, default=12)
    ph.add_argument("--git-log-file", default="")
    ph.set_defaults(func=cmd_health)

    # scout — pre-dispatch CHOOSER (pick the next activity BEFORE leasing a lane)
    ps = sub.add_parser(
        "scout",
        help="pre-dispatch CHOOSER — pick the next activity BEFORE leasing a lane",
    )
    ps.add_argument("--state-json", default="",
                    help="JSON ScoutState (see dos.scout)")
    ps.set_defaults(func=cmd_scout)

    pr = sub.add_parser("run-id", help="mint a correlation run-id")
    rsub = pr.add_subparsers(dest="run_id_cmd", required=True)
    rm = rsub.add_parser("mint")
    rm.add_argument("process")
    rm.add_argument("--parent", default=None)
    rm.add_argument("--root", default=None)
    pr.set_defaults(func=cmd_run_id)

    pgd = sub.add_parser(
        "guard",
        help="wrap a headless agent launch: inject the DOS MCP server "
             "(+ optional verify-on-stop hook), then exec the host",
        description=(
            "Frame a non-interactive agent launch with the DOS wiring, then exec "
            "the host command. Everything after `--` is the host command, passed "
            "through verbatim; DOS appends `--mcp-config` (the dos-mcp server, so "
            "the agent CAN call dos_verify) and — with --verify-on-stop — a "
            "`--settings` Stop hook (so the runtime INSISTS on it). There is no "
            "host `--hooks` flag; hooks ride inside --settings. The MCP mount "
            "works today; the Stop hook targets `dos hook stop` (docs/134, not "
            "yet built) so it is opt-in. Use --print-config to see the exact JSON "
            "without launching anything.\n\n"
            "Example:\n"
            "  dos guard -- claude -p \"implement AUTH2\" --output-format json"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pgd.add_argument("--no-mcp", action="store_true",
                     help="do not inject the DOS MCP server")
    pgd.add_argument("--verify-on-stop", action="store_true",
                     help="inject a Stop hook running `dos hook stop` so the "
                          "agent can't stop on an unverified claim (docs/134 §2)")
    pgd.add_argument("--claim-prompt", action="store_true",
                     help="append a system-prompt instruction asking the agent "
                          "to end completed work with a `DOS-CLAIM: <plan> "
                          "<phase>` line (the docs/134 §2.1 marker rung)")
    pgd.add_argument("--strict-mcp", action="store_true",
                     help="pass --strict-mcp-config so the host uses ONLY the "
                          "injected server (the CI-honest form)")
    pgd.add_argument("--print-config", action="store_true",
                     help="print the launch plan (injected JSON + final argv) "
                          "and exit; launch nothing")
    pgd.add_argument("--json", action="store_true",
                     help="with --print-config, emit the plan as JSON")
    pgd.add_argument("host_command", nargs=argparse.REMAINDER,
                     help="the host launch command, after `--` "
                          "(e.g. -- claude -p \"...\"). Everything here is "
                          "passed through verbatim, including its own flags.")
    pgd.set_defaults(func=cmd_guard)

    ph = sub.add_parser(
        "hook",
        help="hook entrypoints for an agent host (e.g. a verify-on-stop Stop hook)")
    hsub = ph.add_subparsers(dest="hook_cmd", required=True)
    phs = hsub.add_parser(
        "stop",
        help="a Stop/SubagentStop hook: verify what the agent CLAIMED it shipped "
             "and refuse to let it stop on a false done (docs/134 §2)",
        description=(
            "Reads the host hook event JSON on STDIN ({transcript_path, cwd, "
            "stop_hook_active, …}), extracts the (plan, phase) the agent claimed "
            "shipped, verifies each against git, and on a NOT_SHIPPED confident "
            "claim prints the EXACT Claude-Code Stop dialect "
            "{\"decision\": \"block\", \"reason\": …} so CC declines to stop and "
            "feeds the reason back to the model (the load-bearing fix vs the old "
            "{\"ok\": …} no-op CC silently ignored, docs/165 §2). Exits 0 with no "
            "output when there is nothing to block (no claim, every claim verified, "
            "or stop_hook_active — the one-push-back anti-loop guard). The rich "
            "{\"ok\": …, \"results\": …} object is available behind --json (a "
            "machine-readable surface, not the bytes CC reads). Wire it via "
            "`.claude/settings.json` or `dos guard --verify-on-stop`."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(phs)
    phs.add_argument("--transcript", default=None,
                     help="transcript JSONL path (default: the event's "
                          "transcript_path on stdin)")
    phs.add_argument("--plan", default=None,
                     help="frontmatter-bound claim: the plan a firing skill "
                          "declares (with --phase)")
    phs.add_argument("--phase", default=None,
                     help="frontmatter-bound claim: the phase a firing skill "
                          "declares (with --plan)")
    phs.add_argument("--last-turns", type=int, default=1,
                     help="how many trailing assistant turns to scan for claims "
                          "(default 1: the turn that is stopping)")
    phs.add_argument("--strict", action="store_true",
                     help="also act on the weak HEURISTIC rung (default: block "
                          "only on confident marker/frontmatter claims)")
    phs.add_argument("--force", action="store_true",
                     help="ignore the stop_hook_active anti-loop guard (for a host "
                          "that manages the continuation loop itself)")
    phs.add_argument("--json", action="store_true",
                     help="emit the full result object ({ok, reason?, results}) for "
                          "tooling/non-CC hosts, instead of the Claude-Code Stop "
                          "dialect ({\"decision\":\"block\",…}) the default emits")
    phs.add_argument("--dialect", default=None,
                     help="render the stop refusal in a host's shape instead of the "
                          "default Claude-Code {\"decision\":\"block\",…}: "
                          "{gemini,cursor,codex,antigravity,claude-cowork} (docs/217/268). Same "
                          "verdict, different envelope (Gemini's {\"decision\":\"deny\",…}, "
                          "Cursor's {\"permission\":\"deny\",…}). Wired by "
                          "`dos init --hooks <host>` so a non-CC host's stop hook is "
                          "honored, not discarded as a foreign shape.")
    phs.set_defaults(func=cmd_hook_stop)

    pmk = hsub.add_parser(
        "marker",
        help="a Stop hook: refuse a keep-alive wait-marker once its per-session "
             "budget is spent — the runtime lever vs poll-loop cache-replay waste "
             "(loop_decide §wait-marker)",
        description=(
            "Reads the host Stop event JSON on STDIN ({session_id, cwd, …}), reads "
            "the session's running keep-alive-marker count (the durable "
            ".dos/markers/<sid>.jsonl tally — NOT a flag the model threads through), "
            "and asks the PURE loop_decide.wait_marker_budget. POLARITY is the "
            "INVERSE of `dos hook stop`: a keep-alive marker is the loop CHOOSING NOT "
            "TO STOP, so while the budget REMAINS this records the marker and prints "
            "{\"decision\": \"block\", \"reason\": …} to HOLD THE TURN OPEN one more "
            "marker; once the budget is EXHAUSTED it prints NOTHING (CC's 'allow "
            "stop') so the loop ends its turn and waits on the real Bash "
            "task-notification (which fires on the child's true exit regardless). "
            "Closes the poll-loop antipattern where a /loop run burns a full "
            "cache-replay turn per keep-alive marker (session 4b4ff97c: 252 markers "
            "/ ~$7.80). ⚠ ARMS ONLY INSIDE A LOOP (docs/274): a Stop hook fires on "
            "EVERY finished turn, not only on a keep-alive poll, so the budget blocks "
            "only when --loop is passed or a loop-scoping env (DOS_LOOP / CID_RUN_ID) "
            "is set; on any other turn it emits nothing (allow stop). It also never "
            "re-blocks a stop already continued by a prior hook (stop_hook_active). "
            "`dos hook stop` blocks a FALSE DONE; this blocks a PREMATURE "
            "STOP only while the marker budget is unspent — they compose (wire both). "
            "Every failure mode (no stdin, bad JSON, no session_id, an I/O error) "
            "degrades to 'emit nothing' = let the agent stop. The rich allow/refuse "
            "object is available behind --json (a machine surface, not the bytes CC "
            "reads). With --reset, instead appends a forward-delta RESET to the tally "
            "(zeroing the no-op count so a re-entered wait phase starts fresh, docs/259 "
            "§Follow-up 2) and emits nothing. Wire it via `.claude/settings.json` Stop "
            "hooks (and the --reset form on a SessionStart/UserPromptSubmit hook)."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pmk)
    pmk.add_argument("--session-id", default=None,
                     help="session key the marker tally accumulates under (default: "
                          "the event's session_id on stdin)")
    pmk.add_argument("--reset", action="store_true",
                     help="append a forward-delta RESET that zeroes the session's no-op "
                          "tally (the `tool_stream` ADVANCING analogue) and emit "
                          "nothing — wire on a forward-progress hook so a re-entered "
                          "wait phase starts with a fresh budget (docs/259 §Follow-up 2)")
    pmk.add_argument("--loop", action="store_true",
                     help="ARM the budget: assert this Stop is a keep-alive poll inside a "
                          "headless loop (docs/274). WITHOUT this (and without the "
                          "DOS_LOOP / CID_RUN_ID env), the hook treats the Stop as an "
                          "ordinary finished turn and ALLOWS it — because a bare Stop "
                          "hook fires on every turn, not only on a poll, so an unscoped "
                          "budget would force keep-alive turns on every interactive turn. "
                          "Pass this (or set the env) only on a loop-local binding.")
    pmk.add_argument("--max-markers", type=int, default=None,
                     help="the per-session keep-alive-marker budget. Precedence: this "
                          "flag › `dos.toml [marker] max_streak` › the generic default "
                          "(4 — the /dispatch-loop SKILL's per-run cap; the runtime "
                          "refusal fires one marker before the keepalive_poll telemetry "
                          "flag at >=5). Pass it to override the workspace's configured cap.")
    pmk.add_argument("--json", action="store_true",
                     help="emit the full result object ({allow_marker, "
                          "markers_emitted, max_markers, reason}) for tooling/non-CC "
                          "hosts, instead of the Claude-Code Stop dialect the default "
                          "emits")
    pmk.add_argument("--debug", action="store_true",
                     help="print diagnostics to STDERR (the stdout contract stays "
                          "EXCLUSIVELY the host dialect or empty)")
    pmk.set_defaults(func=cmd_hook_marker)

    php = hsub.add_parser(
        "posttool",
        help="a PostToolUse hook: re-surface a repeated env value as non-blocking "
             "additionalContext when the tool stream stalls (docs/173 §4)",
        description=(
            "Reads the host PostToolUse event JSON on STDIN ({session_id, "
            "tool_name, tool_input, tool_response/tool_output, cwd, …}), accumulates "
            "the session's tool-result stream, and classifies its trailing run with "
            "dos.tool_stream. When the SAME (tool, args, result) triple repeats — the "
            "env returning byte-identical results so no new information enters the "
            "loop — it prints the Claude-Code WARN dialect "
            "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", "
            "\"additionalContext\": …}} so CC re-surfaces the unchanged value into "
            "the next turn. PostToolUse fires AFTER the tool ran, so it CANNOT block: "
            "this is advisory-only (docs/99) — it re-surfaces, never cuts (a poll of "
            "a not-yet-complete background task is a legitimate repeat). Emits nothing "
            "and exits 0 on ADVANCING or any failure (no stdin, bad JSON, no result, "
            "no session_id). Wire it via `.claude/settings.json` PostToolUse hooks."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(php)
    php.add_argument("--session-id", default=None,
                     help="session key the stream accumulates under (default: the "
                          "event's session_id on stdin)")
    php.add_argument("--debug", action="store_true",
                     help="print diagnostics to STDERR (the stdout contract stays "
                          "EXCLUSIVELY the host dialect or empty)")
    php.add_argument("--dialect", default="claude-code",
                     help="the host runtime whose hook envelope to emit "
                          "(claude-code [default] / codex / gemini / cursor / "
                          "antigravity / claude-cowork, or a dos.hook_dialects plugin). "
                          "An unknown name fails loud and emits nothing (docs/217)")
    php.set_defaults(func=cmd_hook_posttool)

    ppt = hsub.add_parser(
        "pretool",
        help="a PreToolUse hook: DENY a structurally-refused tool call BEFORE it "
             "runs — the one moment a DOS verdict can prevent an effect (docs/191)",
        description=(
            "Reads the host PreToolUse event JSON on STDIN ({session_id, tool_name, "
            "tool_input, cwd, …} — and CRUCIALLY no tool_response, the structural PRE "
            "marker), runs the two PRE-SOUND rungs and emits the EXACT Claude-Code "
            "PreToolUse dialect. RUNG A (structural admission): a Write/Edit/Bash whose "
            "path tree hits the kernel's own running code (SELF_MODIFY) or collides a "
            "live lane lease → {\"hookSpecificOutput\": {\"hookEventName\": "
            "\"PreToolUse\", \"permissionDecision\": \"deny\", "
            "\"permissionDecisionReason\": …}} so CC withholds the write before it "
            "happens (the mediated-write moment, docs/126). RUNG B (behavioral "
            "provenance, confidence-gated): a HIGH-confidence MINTED id argument → deny "
            "with a turn-preserving synthetic corrective; a LOW/composite mint → "
            "additionalContext only (passthrough). DOS stays a PDP — the deny is a "
            "PROPOSAL the runtime (the PEP) consumes; the default --handler 'observe' "
            "emits ZERO behavioral deny (a deny there needs a wired ruling handler). "
            "Emits nothing and exits 0 on passthrough or any failure (no stdin, bad "
            "JSON, no tool_name, a result key present = mis-routed PostToolUse event). "
            "Wire it via `.claude/settings.json` PreToolUse hooks."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(ppt)
    ppt.add_argument("--handler", default="observe",
                     help="the dos.enforce_handlers handler that consumes a Rung-B "
                          "intervention decision (default 'observe' = the PDP-only "
                          "floor, zero behavioral deny; a ruling handler is a driver)")
    ppt.add_argument("--debug", action="store_true",
                     help="print diagnostics to STDERR (the stdout contract stays "
                          "EXCLUSIVELY the host dialect or empty)")
    ppt.add_argument("--dialect", default="claude-code",
                     help="the host runtime whose hook envelope to emit "
                          "(claude-code [default] / codex / gemini / cursor / "
                          "antigravity / claude-cowork, or a dos.hook_dialects plugin). "
                          "DENY is honored by every host; an unknown name fails loud "
                          "and emits nothing (docs/217)")
    ppt.set_defaults(func=cmd_hook_pretool)

    pia = sub.add_parser(
        "id-alloc",
        help="atomically allocate a never-reused, monotonic id for a scope")
    _add_workspace_flags(pia)
    iasub = pia.add_subparsers(dest="id_alloc_cmd", required=True)
    iaa = iasub.add_parser(
        "allocate", help="claim the next id for SCOPE (atomic compare-and-increment)")
    iaa.add_argument("scope", help="the opaque series key the id belongs to")
    iaa.add_argument("--start", type=int, default=id_alloc.DEFAULT_START,
                     help="first id for a never-before-seen scope (default 1)")
    iap = iasub.add_parser(
        "peek", help="the current high-water id for SCOPE without claiming one")
    iap.add_argument("scope")
    pia.set_defaults(func=cmd_id_alloc)

    pj = sub.add_parser("journal", help="the lane write-ahead log")
    _add_workspace_flags(pj)
    jsub = pj.add_subparsers(dest="journal_cmd", required=True)
    jt = jsub.add_parser("tail")
    jt.add_argument("n", nargs="?", type=int, default=20)
    jsub.add_parser("replay")
    jsub.add_parser("seq")
    jsub.add_parser(
        "compact",
        help="fold the WAL to a single CHECKPOINT snapshot of the live set and "
             "rewrite it (bounds an unbounded journal; verdict-preserving)")
    pj.set_defaults(func=cmd_journal)

    pm = sub.add_parser("man", help="the self-describing manual over the registries",
                        description=_HELP_MAN,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pm)
    pm.add_argument("section", choices=["wedge", "lane"])
    pm.add_argument("id", nargs="?", default="")
    _add_output_flag(pm)
    _add_explain_flag(pm)
    pm.set_defaults(func=cmd_man)

    # hosts — the self-describing host-support matrix from the registries (docs #93).
    # Read-only; no workspace needed (the registry is package-global), but accept the
    # flags for uniformity with the other self-describing verbs.
    ph = sub.add_parser(
        "hosts",
        help="the host-support matrix from the registries (which runtimes DOS wires)")
    _add_workspace_flags(ph)
    ph.add_argument("--json", action="store_true",
                    help="machine-readable matrix (host rows + dialect set)")
    ph.set_defaults(func=cmd_hosts)

    pju = sub.add_parser("judge",
                         help="adjudicate a no-pick verdict (deterministic; picker_oracle)")
    _add_workspace_flags(pju)
    pju.add_argument("section", choices=["wedge"],
                     help="what to adjudicate (currently: wedge — a no-pick verdict)")
    pju.add_argument("run_ts", help="the run timestamp dir (e.g. 20260531T010000Z)")
    pju.add_argument("--json", action="store_true", help="machine-readable verdict")
    pju.set_defaults(func=cmd_judge)

    # judge-eval — score a JUDGE-rung adjudicator against labelled claims (Axis 6).
    # The bring-your-own-adjudicator research instrument: confusion grid +
    # false-clear rate + rung context. Exit 1 if the judge false-cleared anything.
    pje = sub.add_parser(
        "judge-eval",
        help="score a judge against labelled claims (false-clear rate, accuracy)")
    _add_workspace_flags(pje)
    pje.add_argument("--judge", default="abstain", metavar="NAME",
                     help="the judge to score (built-in `abstain`, shipped `llm`, "
                          "or any registered `dos.judges` plugin; default: abstain)")
    pje.add_argument("--cases", required=True, metavar="FILE.jsonl",
                     help="labelled cases: one JSON object/line with claim_text, "
                          "truth (bool), and optional stated_reason/evidence")
    pje.add_argument("--json", action="store_true", help="machine-readable report")
    pje.set_defaults(func=cmd_judge_eval)

    # overlap-eval — score a disjointness SCORER against labelled concurrent-pair
    # outcomes (Axis 7, docs/113). The bring-your-own-scorer research instrument:
    # confusion grid + false-admit rate (the dangerous cell) + safe-forgone cost.
    # Exit 1 if the scorer admitted any pair that actually collided.
    poe = sub.add_parser(
        "overlap-eval",
        help="score an overlap scorer against labelled pairs (false-admit rate)")
    _add_workspace_flags(poe)
    poe.add_argument("--policy", default="prefix", metavar="NAME",
                     help="the overlap policy to score (built-in `prefix`, or any "
                          "registered `dos.overlap_policies` plugin; default: prefix)")
    poe.add_argument("--cases", required=True, metavar="FILE.jsonl",
                     help="labelled cases: one JSON object/line with tree_a, tree_b "
                          "(glob lists), collided (bool), and optional label")
    poe.add_argument("--json", action="store_true", help="machine-readable report")
    poe.set_defaults(func=cmd_overlap_eval)

    # intervention-eval — score an actuation POLICY by net task delta (docs/143 §13.2).
    #   (full prose: docs/CLI.md § "intervention-eval — score an actuation POLICY by net task de")
    pie = sub.add_parser(
        "intervention-eval",
        help="score an intervention policy by NET TASK DELTA, not verdict accuracy "
             "(docs/143 §13; exit 1 if net-harmful)")
    _add_workspace_flags(pie)
    pie.add_argument("--cases", required=True, metavar="FILE.jsonl",
                     help="labelled cases: one JSON object/line with a compact "
                          "`confidence` (HIGH/LOW/NONE) + `unsupported` names (a verdict is "
                          "SYNTHESIZED) OR a full `verdict` (ProvenanceVerdict.to_dict()), "
                          "plus truly_minted, mattered_to_score, recovered_if_blocked, "
                          "recovered_if_deferred (bools — GROUND TRUTH from executed replay "
                          "arms), optional label")
    pie.add_argument("--high", default=None, metavar="RUNG",
                     help="intervention for a HIGH-confidence (whole-value-absent) mint "
                          "(default: the --ceiling rung, i.e. BLOCK — so `--ceiling DEFER` "
                          "alone enables the turn-spending rung; pass --high to override)")
    pie.add_argument("--low", default="WARN", metavar="RUNG",
                     help="intervention for a LOW-confidence (composite/partial) mint "
                          "(default: WARN — inform, still dispatch)")
    pie.add_argument("--ceiling", default="BLOCK", metavar="RUNG",
                     help="most-disruptive rung an escalation may reach (default: BLOCK; "
                          "raise to DEFER to enable the turn-spending rung)")
    pie.add_argument("--json", action="store_true", help="machine-readable report")
    pie.set_defaults(func=cmd_intervention_eval)

    # tool-stream-eval — score a stall-reader POLICY by net recovery (docs/145 §9).
    # The loop-economics twin of intervention-eval: does firing a re-surface on a REPEATING
    # stream recover stuck tasks more often than it false-fires on a legitimate poller?
    # Exit 0 iff net-positive (recovered > false-resurfaced); the friendly-direction CI gate.
    ptse = sub.add_parser(
        "tool-stream-eval",
        help="score a stall-reader policy by NET RECOVERY, not detection accuracy "
             "(docs/145 §9; exit 0 iff net-positive)")
    _add_workspace_flags(ptse)
    ptse.add_argument("--cases", required=True, metavar="FILE.jsonl",
                      help="labelled cases: one JSON object/line with a compact `repeat` (N "
                           "identical steps) OR a full `steps` list ([tool, args, result] each), "
                           "plus actually_stuck, legit_polling, recovered_if_fired (bools — "
                           "GROUND TRUTH from replay), optional label")
    ptse.add_argument("--repeat-n", type=int, default=None, metavar="N",
                      help="run-length at which the loop is REPEATING (default: the active "
                           "[tool_stream] config, generic 3) — sweep to calibrate from data")
    ptse.add_argument("--stall-n", type=int, default=None, metavar="N",
                      help="run-length at which REPEATING hardens to STALLED (default: config, "
                           "generic 5; must be >= repeat_n)")
    ptse.add_argument("--ignore-tools", default=None, metavar="t1,t2",
                      help="comma-separated known-poller tool names exempted from the reader "
                           "(overrides the config allow-list)")
    ptse.add_argument("--json", action="store_true", help="machine-readable report")
    ptse.set_defaults(func=cmd_tool_stream_eval)

    # precursor-gate-eval — score a precursor GRAMMAR by recall vs waste (docs/147 §9.2).
    # The policy/refusal twin of tool-stream-eval: does the declared mandated-precursor map
    # catch the real prerequisite-skips without false-REFUTING on a precursor that fired under
    # an unlisted alias? Exit 0 iff net-positive (real-skips caught > false-REFUTEs).
    ppge = sub.add_parser(
        "precursor-gate-eval",
        help="score a precursor grammar by RECALL vs false-refute waste "
             "(docs/147 §9.2; exit 0 iff net-positive)")
    _add_workspace_flags(ppge)
    ppge.add_argument("--cases", required=True, metavar="FILE.jsonl",
                      help="labelled cases: one JSON object/line with `tool` (the mutating call), "
                           "`prior` (a list of prior tool names = the call stream), plus "
                           "precursor_required + precursor_actually_fired (bools — GROUND TRUTH "
                           "from the policy prose + replay), optional mattered_to_score, label")
    ppge.add_argument("--json", action="store_true", help="machine-readable report")
    ppge.set_defaults(func=cmd_precursor_gate_eval)

    pdec = sub.add_parser("decisions",
                          help="the operator-decision queue (list + drill-in TUI)")
    _add_workspace_flags(pdec)
    # Accept either `dos decisions N` or `dos decisions show N` (the literal
    # `show` word is optional sugar). Parsed in `cmd_decisions` so both read.
    pdec.add_argument("target", nargs="*", default=None, metavar="[show] N",
                      help="drill into decision N (1-based) non-interactively")
    pdec.add_argument("--all", action="store_true",
                      help="show ORACLE/JUDGE-resolvable rows too (default: HUMAN-only)")
    pdec.add_argument("--no-tui", action="store_true",
                      help="force the plain list instead of the interactive TUI")
    pdec.add_argument("--json", action="store_true", help="machine-readable output")
    _add_output_flag(pdec)
    pdec.set_defaults(func=cmd_decisions)

    # notify — the notification spine (docs/225): push the decisions / top
    # projection to a transport. A driver (`slack`) is discovered by name; the
    # default `null` notifier renders + sends nothing (safe, outward-facing-aware).
    pnotify = sub.add_parser(
        "notify",
        help="push the decisions / fleet-status projection to a transport "
             "(Slack etc.); default null = render only")
    nsub = pnotify.add_subparsers(dest="notify_cmd", required=True)

    def _add_notify_common(p):
        _add_workspace_flags(p)
        p.add_argument("--notifier", default="null", metavar="NAME",
                       help="transport: built-in `null` (render only, default), or a "
                            "registered `dos.notifiers` plugin (e.g. `slack`)")
        p.add_argument("--channel", default="", metavar="NAME|ID",
                       help="target channel: a logical name in slack_config.json, or "
                            "a raw id (C0…). Required for the `slack` transport.")
        p.add_argument("--url", default="", metavar="URL",
                       help="target endpoint for the `webhook` transport (or set "
                            "$DOS_WEBHOOK_URL / the workspace .env). Ignored by `slack`.")
        p.add_argument("--token", default="", metavar="SECRET",
                       help="optional bearer token for `webhook` (or $DOS_WEBHOOK_TOKEN "
                            "/ .env); slack uses $SLACK_BOT_TOKEN instead.")
        p.add_argument("--dry-run", action="store_true",
                       help="render + report what WOULD be sent; send nothing")
        p.add_argument("--json", action="store_true",
                       help="machine-readable {notification, result, notifier}")

    pnd = nsub.add_parser(
        "decisions", help="push the operator-decision queue (the TOP decisions)")
    _add_notify_common(pnd)
    pnd.add_argument("--all", action="store_true",
                     help="include ORACLE/JUDGE-resolvable rows (default: HUMAN-only)")
    pnd.add_argument("--top", type=int, default=5, metavar="K",
                     help="how many top-ranked decisions to surface as fields (default: 5)")
    pnd.set_defaults(func=cmd_notify)

    pnt = nsub.add_parser(
        "top", help="push the live fleet status (lanes/leases/verdicts), edit-in-place")
    _add_notify_common(pnt)
    pnt.set_defaults(func=cmd_notify)

    # trace — the cross-surface join (docs/137): walk one run across the spine,
    # the intent ledger, the WAL, and git, joined by its run_id. A read-only
    # projection (the `decisions`/`top`/`plan` posture); no new verdict.
    ptr = sub.add_parser(
        "trace",
        help="walk one run across spine + intent ledger + WAL + git, joined by run_id")
    _add_workspace_flags(ptr)
    ptr.add_argument("run_id", metavar="RID",
                     help="the run-id (RID-…) to trace across every surface")
    ptr.add_argument("--json", action="store_true",
                     help="machine-readable {spine, intent, wal, git} join")
    ptr.set_defaults(func=cmd_trace)

    # observe — the verdict-journal projection (docs/262): fold every adjudication
    # the kernel recorded (verify/liveness/efficiency/…) into per-dimension counts,
    # or one run's verdict history. A read-only projection (the trace/decisions/top
    # posture); the verdicts were minted by the syscalls, this only folds + renders.
    pobs = sub.add_parser(
        "observe",
        help="project the verdict journal — every kernel adjudication, folded by run/syscall")
    _add_workspace_flags(pobs)
    pobs.add_argument("--run", metavar="RID", default="",
                      help="filter to one run-id and show its verdict history (the trace join)")
    pobs.add_argument("--syscall", metavar="NAME", default="",
                      help="filter to one verdict dimension (verify/liveness/efficiency/…)")
    pobs.add_argument("--by", metavar="DIM", default="syscall",
                      choices=["syscall", "verdict", "run_id", "lane", "source"],
                      help="fold the rollup on this dimension (default: syscall)")
    pobs.add_argument("--tail", metavar="N", type=int, default=0,
                      help="show the last N raw events instead of the rollup")
    pobs.add_argument("--json", action="store_true",
                      help="machine-readable {rollup, events} (the trajectory-audit's source)")
    pobs.set_defaults(func=cmd_observe)

    # census — the verdict-USAGE census (issue #20): fold the verdict journal AND
    # the hook observation log into per-verb invocation counts over the derived
    # CLI verb universe, surfacing the never-fired orphan list (verdict-bearing
    # surfaces consumed by nothing). A read-only projection (the observe posture);
    # it folds two logs, mints no verdict.
    pcen = sub.add_parser(
        "census",
        help="per-verb invocation counts + the never-fired orphan list, folded over both telemetry logs")
    _add_workspace_flags(pcen)
    pcen.add_argument("--json", action="store_true",
                      help="machine-readable {rows, never_fired, orphans}")
    pcen.set_defaults(func=cmd_census)

    # headline — the quotable, receipt-linked one-liner over the per-call hook
    # observation log (issue #71): "N tool calls adjudicated — M false done's
    # refused at stop, …", honest zeros + a coverage clause, --receipts expands
    # each count to its regenerating command. A read-only projection (the observe
    # posture) over docs/297, NOT the lane journal (the like-for-like rule).
    phl = sub.add_parser(
        "headline",
        help="a quotable, receipt-linked one-liner over the hook observation log")
    _add_workspace_flags(phl)
    phl.add_argument("--since", metavar="TS", default="",
                     help="keep only observations at/after this ISO-8601 stamp (the window)")
    phl.add_argument("--receipts", action="store_true",
                     help="expand each nonzero count to its records + the regenerating command")
    phl.add_argument("--json", action="store_true",
                     help="machine-readable {adjudicated, per-class counts, receipts}")
    phl.set_defaults(func=cmd_headline)

    # helped — the operator-facing "what did DOS catch for me?" rollup (help_summary):
    # fold the BLOCK/WARN/DEFER enforcement records the lane WAL already carries into a
    # "DOS has caught N things" summary, by reason class + tool. The last observability
    # rung — from the journal out to the human. Read-only (the observe/decisions posture).
    phlp = sub.add_parser(
        "helped",
        help="show how many things DOS productively caught for you (the operator rollup)")
    _add_workspace_flags(phlp)
    phlp.add_argument("--session", metavar="SID", default="",
                      help="scope to one session's helps (the OP_ENFORCE holder/session id)")
    phlp.add_argument("--since", metavar="TS", default="",
                      help="keep only enforcements at/after this ISO timestamp")
    phlp.add_argument("--explain", action="store_true",
                      help="drill down: per reason class, its plain-English meaning + "
                           "concrete examples (which file, why)")
    phlp.add_argument("--json", action="store_true",
                      help="machine-readable {total, blocked, warned, by_reason, by_tool, "
                           "examples, glossary, …}")
    phlp.set_defaults(func=cmd_helped)

    # export — the verdict-journal DRAIN (docs/266): ship the stream outward to an
    #   (full prose: docs/CLI.md § "export — the verdict-journal DRAIN (docs/266): ship the stre")
    pexp = sub.add_parser(
        "export",
        help="drain the verdict journal to an observability backend "
             "(file/statsd/otlp); default null = report only")
    _add_workspace_flags(pexp)
    pexp.add_argument("--to", default="null", metavar="NAME",
                      help="transport: built-in `null` (report only, default), or a "
                           "registered `dos.exporters` plugin (`file`/`statsd`/`otlp`)")
    pexp.add_argument("--path", default="", metavar="PATH",
                      help="target file for the `file` transport (or set $DOS_EXPORT_FILE "
                           "/ the workspace .env). Ignored by statsd/otlp.")
    pexp.add_argument("--host", default="", metavar="HOST",
                      help="target host for the `statsd` transport (default 127.0.0.1). "
                           "Ignored by file/otlp.")
    pexp.add_argument("--port", type=int, default=0, metavar="PORT",
                      help="target port for the `statsd` transport (default 8125). "
                           "Ignored by file/otlp.")
    pexp.add_argument("--endpoint", default="", metavar="URL",
                      help="target endpoint for the `otlp` transport "
                           "(e.g. http://localhost:4318). Ignored by file/statsd.")
    pexp.add_argument("--since", default="", metavar="SEQ|auto",
                      help="drain only events AFTER this journal seq cursor (resumable "
                           "forward offset). `auto` reads/writes the persisted cursor at "
                           ".dos/export-cursor.<transport> so repeated drains self-thread")
    pexp.add_argument("--tail", type=int, default=0, metavar="N",
                      help="cap to the last N journal events before the --since slice")
    pexp.add_argument("--follow", action="store_true",
                      help="stay in a BOUNDED foreground loop: poll → drain → persist the "
                           "cursor → sleep (NOT a daemon; ^C or --follow-max ends it)")
    pexp.add_argument("--follow-interval", type=float, default=2.0, metavar="SECS",
                      help="seconds between --follow polls (default 2.0)")
    pexp.add_argument("--follow-max", type=int, default=0, metavar="N",
                      help="stop --follow after N iterations (default 0 = until ^C)")
    pexp.add_argument("--dry-run", action="store_true",
                      help="report what WOULD ship; send nothing")
    pexp.add_argument("--json", action="store_true",
                      help="machine-readable {result, exporter, shipped, since, persist}")
    pexp.set_defaults(func=cmd_export)

    # memory — the agent-memory gates: recall re-verification (docs/103) and
    # the candidate write gate (docs/314), both over the recall driver.
    pmem = sub.add_parser(
        "memory",
        help="adjudicate agent memory: re-verify at recall (FRESH/STALE/UNVERIFIABLE), "
             "gate a candidate at write (`admit`)")
    msub = pmem.add_subparsers(dest="memory_cmd", required=True)
    mrec = msub.add_parser("recall",
                           help="re-verify ONE memory by name/slug → its recall verdict")
    _add_workspace_flags(mrec)
    mrec.add_argument("name", help="the memory's frontmatter name / slug, or a path")
    mrec.add_argument("--store", default="",
                      help="the store ARG: the agent-memory dir for the `file` kind "
                           "(default: the documented ~/.claude/projects/<ws>/memory "
                           "layout); a provider selector for a driver kind")
    mrec.add_argument("--store-kind", default="file",
                      help="which memory store to read (docs/314): `file` built-in; "
                           "provider drivers register via the dos.memory_stores "
                           "entry-point group (e.g. mem0)")
    mrec.add_argument("--explain", action="store_true",
                      help="append the agent-facing interpretation line")
    mrec.add_argument("--json", action="store_true", help="machine-readable verdict")
    _add_output_flag(mrec)
    madm = msub.add_parser(
        "admit",
        help="the WRITE gate (docs/314): adjudicate a CANDIDATE memory before it "
             "enters any store (ADMIT_WITNESSED/AS_CLAIM/OPINION/REJECT_POISON)")
    _add_workspace_flags(madm)
    madm.add_argument("--text-file", default="",
                      help="the candidate memory file to adjudicate (default: stdin)")
    madm.add_argument("--name", default="",
                      help="display name for a frontmatter-less candidate")
    madm.add_argument("--explain", action="store_true",
                      help="append the writer-facing interpretation line")
    madm.add_argument("--json", action="store_true", help="machine-readable verdict")
    _add_output_flag(madm)
    mver = msub.add_parser("verify",
                           help="sweep the WHOLE memory store (STALE first)")
    _add_workspace_flags(mver)
    mver.add_argument("--store", default="", help="the store ARG (see `recall`)")
    mver.add_argument("--store-kind", default="file",
                      help="which memory store to sweep (see `recall`)")
    mver.add_argument("--reprobe", action="store_true",
                      help="ignore verification-memory fossils (docs/314 P4): re-probe "
                           "every memory even if a byte-unchanged STALE verdict is "
                           "already journaled")
    mver.add_argument("--route", action="store_true",
                      help="cross-post non-FRESH verdicts to `dos decisions` via "
                           "OP_REFUSE (needs RECALL_* declared in dos.toml [reasons])")
    mver.add_argument("--json", action="store_true", help="machine-readable list")
    _add_output_flag(mver)
    pmem.set_defaults(func=cmd_memory)

    ptop = sub.add_parser(
        "top",
        help="live fleet watchdog: lanes, leases, recent verdicts, commits (read-only)")
    _add_workspace_flags(ptop)
    ptop.add_argument("--once", action="store_true",
                      help="render one frame and exit (CI / pipe) instead of the live loop")
    ptop.add_argument("--json", action="store_true",
                      help="emit the machine-readable snapshot and exit")
    ptop.add_argument("--interval", type=float, default=5.0, metavar="SEC",
                      help="live-refresh cadence in seconds (default: 5)")
    ptop.set_defaults(func=cmd_dispatch_top)

    pplan = sub.add_parser(
        "plan",
        help="work-terrain board: every phase, the plan's claim vs the oracle (read-only)")
    _add_workspace_flags(pplan)
    pplan.add_argument("phases", nargs="*", metavar="PLAN PHASE",
                       help="explicit (plan, phase) pairs to fan the oracle over "
                            "(no plan doc needed); omit to read the declared plan source")
    pplan.add_argument("--source", default=None, metavar="NAME",
                       help="plan source: markdown (default) or a registered "
                            "dos.plan_sources plugin")
    pplan.add_argument("--once", action="store_true",
                       help="render one frame and exit (CI / pipe) instead of the live loop")
    pplan.add_argument("--json", action="store_true",
                       help="emit the machine-readable snapshot and exit")
    pplan.add_argument("--interval", type=float, default=5.0, metavar="SEC",
                       help="live-refresh cadence in seconds (default: 5)")
    pplan.set_defaults(func=cmd_plan)

    pd = sub.add_parser("doctor", help="report the active workspace + taxonomy")
    _add_workspace_flags(pd)
    pd.add_argument("--check", action="store_true",
                    help="run completeness checks (a declared [stamp] that matches "
                         "none of this repo's ship-shaped commits; a lane declared "
                         "without a [lanes.trees] entry); exit 1 if any finding fires")
    pd.add_argument("--json", action="store_true",
                    help="machine-readable workspace report (paths/lanes/stamp/git) "
                         "— what a generic skill reads to discover its layout (SKP)")
    pd.set_defaults(func=cmd_doctor)

    # docs/227 (G1 from docs/189) — the config-integrity linter as a focused verb:
    #   (full prose: docs/CLI.md § "docs/227 (G1 from docs/189) — the config-integrity linter as")
    pl = sub.add_parser("lint",
                        help="check dos.toml for dead policy (unreachable lanes, "
                             "contradictions, dangling refs); exit 1 on any "
                             "error/warn finding")
    _add_workspace_flags(pl)
    pl.add_argument("--json", action="store_true",
                    help="machine-readable findings (kind/severity/subject/detail/fix) "
                         "+ per-severity counts")
    pl.add_argument("--strict", action="store_true",
                    help="gate on error findings only (default gates on error OR warn; "
                         "info never gates)")
    pl.set_defaults(func=cmd_lint)

    prx = sub.add_parser("reindex",
                         help="rebuild the central store from the live .dos/ dirs")
    prx.add_argument("--prune", action="store_true",
                     help="compact the registry: drop projects whose .dos/ is gone "
                          "plus throwaway OS-temp-rooted rows, and rewrite roots.log "
                          "to the kept roots (default: mark stale, rewrite nothing)")
    prx.add_argument("--json", action="store_true", help="machine-readable summary")
    prx.set_defaults(func=cmd_reindex)

    pp = sub.add_parser("projects",
                        help="the cross-project registry DOS has indexed (read-only)")
    pp.add_argument("--stale", action="store_true",
                    help="show only stale/moved projects")
    pp.add_argument("--json", action="store_true", help="machine-readable output")
    pp.set_defaults(func=cmd_projects)

    plr = sub.add_parser("learn",
                         help="cross-project aggregates over resolved decisions (read-only)")
    plr.add_argument("axis", choices=["wedge-hotspots", "lane-refusals",
                                      "oracle-calibration"],
                     help="which aggregate to compute")
    plr.add_argument("--json", action="store_true", help="machine-readable output")
    plr.set_defaults(func=cmd_learn)

    prp = sub.add_parser(
        "reap",
        help="bound .dos/ scratch to the [retention] caps (audits/verdicts/runs "
             "by recency; WAL by threshold) — DRY-RUN unless --apply")
    prp.add_argument("--apply", action="store_true",
                     help="actually delete (default: preview what would be reaped)")
    prp.add_argument("--journal", action="store_true",
                     help="also compact the WAL when it exceeds the [retention] "
                          "size/age threshold (should_compact)")
    prp.add_argument("--json", action="store_true", help="machine-readable summary")
    prp.set_defaults(func=cmd_reap)

    # SKP Phase 1b — `dos gate`: the typed empty-packet verdict over
    # `gate_classify`, exposed as a verb so a generic skill gates its empty case
    # through the CLI instead of re-implementing the classifier inline.
    pg = sub.add_parser("gate",
                        help="classify a /next-up packet into one typed gate verdict "
                             "(LIVE/DRAIN/STALE-STAMP/BLOCKED/RACE)",
                        description=_HELP_GATE,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pg)
    pg.add_argument("packet", nargs="?", default=None,
                    metavar="PACKET",
                    help="path to the .dispositions-<tag>.json sidecar to classify "
                         "(file mode; honours a sibling .race-<tag>.json)")
    pg.add_argument("--picks-json", default=None, metavar="JSON",
                    help="inline JSON list of per-pick disposition dicts to classify "
                         "(in-memory mode; mutually exclusive with PACKET)")
    pg.add_argument("--json", action="store_true",
                    help="machine-readable verdict {verdict, reason, evidence}")
    _add_explain_flag(pg)
    pg.set_defaults(func=cmd_gate)

    # docs/207 Phase 1 — `dos pickable`: the pre-dispatch gate verdict. Given a
    # unit's declared state, is it OFFERABLE or HELD(reason)? The exit code IS the
    # verdict, with a PER-HoldReason code so a skill branches on which hold.
    pp_ = sub.add_parser("pickable",
                         help="decide whether a declared unit is offerable to a worker "
                              "(OFFERABLE / HELD(reason)); the exit code is the verdict",
                         description=_HELP_PICKABLE,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pp_)
    pp_.add_argument("unit", nargs="?", default=None, metavar="UNIT",
                     help="the unit id being gated (label only; echoed in the output)")
    pp_.add_argument("--state", default=None, metavar="JSON",
                     help="JSON object of the unit's declared state, host-gathered "
                          "(keys: shipped, in_flight, soft_claimed_elsewhere, "
                          "stale_claim, plan_class, operator_gated, soak_open, "
                          "dependency_unmet, cooldown_until_ms, unparseable)")
    pp_.add_argument("--now-ms", type=int, default=None, metavar="MS",
                     help="the clock (ms) the COOLDOWN check reads; defaults to now")
    pp_.add_argument("--json", action="store_true",
                     help="machine-readable {held, reason, evidence, redispatch_invariant}")
    pp_.set_defaults(func=cmd_pickable)

    # docs/207 Phase 2 — `dos enumerate`: the phase-list producer. Emit the unit
    # ids a plan-doc declares, in doc order, with the shipped/remaining partition
    # and typed DriftNotes. Exit status: clean=0, drift=3, empty=4.
    pe_ = sub.add_parser("enumerate",
                         help="enumerate the unit ids a plan-doc declares (the "
                              "phase-list producer); exit clean=0/drift=3/empty=4",
                         description=_HELP_ENUMERATE,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pe_)
    pe_.add_argument("plan_doc", metavar="PLAN_DOC",
                     help="path to the plan-doc to enumerate")
    pe_.add_argument("--series", default=None, metavar="ID",
                     help="the per-plan series prefix (plan-meta id/phase_prefix); "
                          "layers a series-anchored scan onto the [enumerate] grammar")
    pe_.add_argument("--shipped", default=None, metavar="JSON",
                     help="JSON list of plan-meta shipped:[] ids (an authoritative "
                          "positive cache; drives the list↔table drift note)")
    pe_.add_argument("--json", action="store_true",
                     help="machine-readable {series, units, remaining, shipped, by_unit, drift}")
    pe_.set_defaults(func=cmd_enumerate)

    # docs/207 Phase 3 — `dos cooldown`: the anti-churn read surface. Folds a
    # unit's OP_ATTEMPT history into CLEAR / RECENTLY_ATTEMPTED. Exit IS the verdict.
    pc_ = sub.add_parser("cooldown",
                         help="decide whether a unit is in a per-pick cooldown window "
                              "(CLEAR / RECENTLY_ATTEMPTED); the exit code is the verdict",
                         description=_HELP_COOLDOWN,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pc_)
    pc_.add_argument("unit", metavar="UNIT", help="the unit id to check")
    pc_.add_argument("--now-ms", type=int, default=None, metavar="MS",
                     help="the clock (ms) the window check reads; defaults to now")
    pc_.add_argument("--attempts", default=None, metavar="JSON",
                     help="inline JSON list of attempt records (replay/test mode; "
                          "bypasses the lane-journal read)")
    pc_.add_argument("--json", action="store_true",
                     help="machine-readable {state, unit_id, last_ts, count, until_ms, reason}")
    pc_.set_defaults(func=cmd_cooldown)

    # docs/254 — `dos pick-priority`: the freshness sort-key producer. Folds the same
    # OP_ATTEMPT history into NEVER_ATTEMPTED / ATTEMPTED + a sort_key the picker
    # appends to its (priority, status, …) key so fresh work sorts first. Exit IS the
    # verdict (NEVER_ATTEMPTED=0, ATTEMPTED=3).
    ppr_ = sub.add_parser("pick-priority",
                          help="rank a unit by freshness so the picker prefers new work "
                               "(NEVER_ATTEMPTED / ATTEMPTED); the exit code is the verdict",
                          description=_HELP_PICK_PRIORITY,
                          formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(ppr_)
    ppr_.add_argument("unit", metavar="UNIT", help="the unit id to rank")
    ppr_.add_argument("--attempts", default=None, metavar="JSON",
                      help="inline JSON list of attempt records (replay/test mode; "
                           "bypasses the lane-journal read)")
    ppr_.add_argument("--json", action="store_true",
                      help="machine-readable {unit_id, freshness, last_attempt_ms, sort_key, reason}")
    ppr_.set_defaults(func=cmd_pick_priority)

    # docs/207 Phase 4 — `dos reconcile`: the quiet-completion gate. Joins a unit's
    # CLAIM against the ORACLE verdict. Exit IS the verdict (VERIFIED=0,
    # QUIET_INCOMPLETE=3, HONEST_OPEN=4).
    pr_ = sub.add_parser("reconcile",
                         help="reconcile a unit's CLAIM against the ORACLE verdict "
                              "(VERIFIED / QUIET_INCOMPLETE / HONEST_OPEN); exit IS the verdict",
                         description=_HELP_RECONCILE,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_workspace_flags(pr_)
    pr_.add_argument("unit", metavar="UNIT", help="the unit id being reconciled")
    pr_.add_argument("--claimed-done", action="store_true",
                     help="the agent CLAIMED this unit done (its self-report)")
    pr_.add_argument("--oracle-shipped", dest="oracle_shipped", default=None,
                     action=argparse.BooleanOptionalAction,
                     help="supply the oracle verdict directly for replay/test "
                          "(--oracle-shipped / --no-oracle-shipped); else compute it "
                          "from git ancestry via --plan + --phase")
    pr_.add_argument("--plan", default=None, metavar="PLAN",
                     help="the plan id (with --phase) to compute the oracle verdict from git")
    pr_.add_argument("--phase", default=None, metavar="PHASE",
                     help="the phase id (with --plan) to compute the oracle verdict from git")
    pr_.add_argument("--json", action="store_true",
                     help="machine-readable {state, unit, claimed, oracle_shipped, flag, reason}")
    pr_.set_defaults(func=cmd_reconcile)

    # LVN (docs/82) — `dos liveness`: the temporal verdict. Did ground-truth
    # state advance since RUN_ID started, or is the agent spinning? The exit code
    # IS the verdict (ADVANCING=0, SPINNING=3, STALLED=4) so a babysitter loop can
    # branch on a run without reading the agent's self-report.
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
