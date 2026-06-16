"""`dos plugins` — the extension-surface manifest (the legible plugin registry).

DOS is extended through Python entry-point groups: a third party ships an occupant in
their OWN pip package, registers it under ``[project.entry-points."dos.<seam>"]``, and the
kernel discovers it BY NAME at the call boundary — built-ins first (unshadowable), then the
group. Many such seams exist (judges, exporters, notifiers, predicates, host-policy
drivers, …). The mechanism is mature; what was missing is a way to SEE it — to ask "what
seams exist, what's the built-in floor, and what third-party plugins are installed right
now?" without reading kernel docstrings.

This module is that manifest. It is a PURE FOLD: a static descriptor table (the
human-facing metadata per seam — Protocol, safety invariant, can-it-do-I/O, purpose) +
a uniform ``entry_points(group=...)`` scan for the DISCOVERED (third-party) occupants of
each group. It takes no lease, launches nothing, mutates nothing — it reads the installed
entry-point metadata the same way each seam's own resolver does. ``dos plugins`` renders
it; ``dos plugin new`` (the scaffolder) reuses the SAME table so a generated stub always
matches a real seam.

The descriptor table is the single source of truth shared by the manifest and the
scaffolder — they cannot drift, because there is only one table.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# The per-seam descriptor — the metadata `dos plugins` shows and `dos plugin new`
# scaffolds from. Kept as DATA (not derived from each seam module) so the manifest
# never imports a dozen modules just to list them, and the safety-invariant wording is
# stated once, here, matching docs/STABILITY.md.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeamDescriptor:
    """One pluggable seam: its entry-point group + the contract an author must honor."""
    group: str                 # the entry-point group string, e.g. "dos.judges"
    axis: str                  # the hackability Axis label (HACKING.md)
    protocol: str              # the Protocol/contract an occupant implements
    invariant: str             # the safety invariant that keeps an OPEN set honest
    does_io: bool              # may an occupant do provider/network/disk I/O?
    builtins: tuple[str, ...]  # the unshadowable built-in floor (resolves FIRST)
    purpose: str               # one line: what plugging in here lets you do
    example: str = ""          # a reference occupant (driver or examples/dos_ext)


# Order: the host-policy driver first (the trunk), then the behavior seams grouped
# roughly by the trust ladder + the operator surfaces. Mirrors docs/STABILITY.md.
SEAMS: tuple[SeamDescriptor, ...] = (
    SeamDescriptor(
        group="dos.drivers", axis="1 — host policy",
        protocol="<name>_config(workspace) -> SubstrateConfig",
        invariant="selector → fail-loud on unknown; built-in packs unshadowable; "
                  "kernel attaches workspace facts (SELF_MODIFY scope stays kernel-owned)",
        does_io=False, builtins=("workshop", "job"),
        purpose="ship a whole host policy pack (its lanes/paths/facts) in your own package",
        example="dos.drivers.workshop:workshop_config",
    ),
    SeamDescriptor(
        group="dos.judges", axis="6 — adjudicators",
        protocol="Judge.rule(claim, config) -> JudgeVerdict",
        invariant="advisory-only; FAILS TO ABSTAIN (never auto-AGREE); built-in `abstain` "
                  "unshadowable — registering one can never loosen distrust",
        does_io=True, builtins=("abstain",),
        purpose="rule on the residue the deterministic oracle abstained on (a model/heuristic/debate)",
        example="dos.drivers.llm_judge:LlmJudge",
    ),
    SeamDescriptor(
        group="dos.predicates", axis="3 — admission",
        protocol="AdmissionPredicate.check(request, config) -> AdmissionVerdict",
        invariant="CONJUNCTIVE-ONLY — AND-ed after the built-in disjointness/self-modify "
                  "guards; can only REFUSE, never force-admit",
        does_io=False, builtins=(),
        purpose="add an extra admission gate (budget, quota, calendar) over a lease request",
        example="dos_ext.predicates:BudgetGuard",
    ),
    SeamDescriptor(
        group="dos.overlap_policies", axis="7 — disjointness",
        protocol="OverlapPolicy.overlap(tree_a, tree_b) -> float",
        invariant="AND-ed UNDER the deterministic prefix floor — can only make admission "
                  "STRICTER, never admit a path-colliding pair",
        does_io=False, builtins=("prefix",),
        purpose="catch semantic collisions the path-prefix rule misses",
        example="dos_ext.overlap:SemanticGroupPolicy",
    ),
    SeamDescriptor(
        group="dos.renderers", axis="4 — presentation",
        protocol="Renderer.render(verdict) -> str",
        invariant="PURE presentation — handed an already-decided object, mutates nothing, "
                  "decides nothing; built-in floor unshadowable",
        does_io=False, builtins=("text", "json", "plain"),
        purpose="render a verdict for a different audience (a status bar, a product's words)",
        example="dos_ext.renderer:TerseRenderer",
    ),
    SeamDescriptor(
        group="dos.exporters", axis="—  observability",
        protocol="Exporter.export(events) -> ExportResult",
        invariant="FAIL-SOFT — a raising/missing exporter drops to a 0-count result, "
                  "never crashes the observed verb; built-in `null` unshadowable",
        does_io=True, builtins=("null",),
        purpose="ship verdict events to a sink (file, statsd, OTLP) for monitoring",
        example="dos.drivers.export_otlp:OtlpExporter",
    ),
    SeamDescriptor(
        group="dos.notifiers", axis="—  notification",
        protocol="Notifier.send(note) -> NotifyResult",
        invariant="FAIL-SOFT — a raising/missing transport returns a not-delivered result, "
                  "never crashes; built-in `null` unshadowable",
        does_io=True, builtins=("null",),
        purpose="deliver an operator notification (Slack, webhook) on a wedge/decision",
        example="dos.drivers.notify_slack:SlackNotifier",
    ),
    SeamDescriptor(
        group="dos.evidence_sources", axis="—  witness",
        protocol="EvidenceSource.gather(subject, config) -> EvidenceFacts",
        invariant="believe-UNDER-floor — its facts are read but the git verdict is the "
                  "floor; FAIL-SAFE to NO_SIGNAL (never fabricates a ship)",
        does_io=True, builtins=("null",),
        purpose="add a non-git witness (CI status, an acceptance command, a paste log)",
        example="dos.drivers.os_acceptance:OsAcceptanceEvidenceSource",
    ),
    SeamDescriptor(
        group="dos.enforce_handlers", axis="—  actuation",
        protocol="EnforcementHandler.handle(event) -> None",
        invariant="consumes a hook event; advisory side-effects only — never overrides the "
                  "kernel's allow/deny verdict",
        does_io=True, builtins=("observe",),
        purpose="react to a hook event (log/observe) as the kernel adjudicates a tool call",
        example="(built-in `observe`)",
    ),
    SeamDescriptor(
        group="dos.hook_dialects", axis="—  host output",
        protocol="HookDialect.render(verdict) -> dict",
        invariant="OUTPUT downstream of the verdict — a dialect formats, never decides; "
                  "built-in `claude-code` (the neutral lingua franca) unshadowable",
        does_io=False, builtins=("claude-code",),
        purpose="render a hook decision in another host's wire shape (gemini, codex, cursor)",
        example="dos.drivers.hook_dialects:CodexDialect",
    ),
    SeamDescriptor(
        group="dos.hook_installs", axis="—  host wiring",
        protocol="HostHookSpec — where/how to wire hooks for a runtime",
        invariant="install-spec data; built-in `claude-code` default unshadowable",
        does_io=False, builtins=("claude-code",),
        purpose="teach `dos init --hooks <runtime>` how to wire a new host runtime",
        example="dos.drivers.hook_dialects (install specs)",
    ),
    SeamDescriptor(
        group="dos.plan_sources", axis="—  planning",
        protocol="PlanSource.plans() -> list",
        invariant="FAIL-TO-EMPTY — a broken source yields no plans, never crashes a sweep",
        does_io=True, builtins=("markdown",),
        purpose="produce the plan portfolio from a non-markdown source (issues, a DB)",
        example="(built-in `markdown`)",
    ),
    SeamDescriptor(
        group="dos.stop_policies", axis="—  loop halt",
        protocol="StopPolicy.decide(...) -> StopVerdict",
        invariant="AND-ed UNDER the kernel `resource_blocked` floor — can only ADD a halt, "
                  "never remove one; built-in `never` unshadowable",
        does_io=True, builtins=("never",),
        purpose="opt a loop into halting on an urgent decision class (e.g. LIVENESS)",
        example="dos.drivers.decision_stop:DecisionClassStopPolicy",
    ),
    SeamDescriptor(
        group="dos.log_sources", axis="—  log routing",
        protocol="LogSource — by-accountability routing",
        invariant="pure routing by accountability; built-in floor unshadowable",
        does_io=True, builtins=("null",),
        purpose="route a worker's logs by accountability for the liveness fold",
        example="(built-in floor)",
    ),
    SeamDescriptor(
        group="dos.scope_sources", axis="—  completion mode",
        protocol="ScopeSource — completion-mode selector",
        invariant="pure selection; built-in floor unshadowable",
        does_io=False, builtins=(),
        purpose="select the completion-mode scope for a verify/gate sweep",
        example="(built-in floor)",
    ),
    SeamDescriptor(
        group="dos.memory_stores", axis="—  memory",
        protocol="MemoryStore — read-only, by-name resolver",
        invariant="READ-ONLY; built-in `file` store unshadowable",
        does_io=True, builtins=("file",),
        purpose="sweep a hosted memory store (Mem0) through the same recall/admit gates",
        example="dos.drivers.memory_mem0 (file built-in)",
    ),
    SeamDescriptor(
        group="dos.vcs", axis="—  version control",
        protocol="VcsBackend(root) — ancestry/log/diff backend",
        invariant="built-ins `git`/`null` unshadowable; a backend only READS history",
        does_io=True, builtins=("git", "null"),
        purpose="back the truth oracle with a non-git VCS (hg, jj) — same ancestry checks",
        example="(built-in `git`)",
    ),
    SeamDescriptor(
        group="dos.mcp_tools", axis="—  MCP surface",
        protocol="register(mcp) -> None  (or a bare tool callable)",
        invariant="ADDITIVE to the curated syscall ABI — a plugin tool adds a verb, never "
                  "replaces a built-in one; a broken plugin is skipped, never crashes the server",
        does_io=True, builtins=(),
        purpose="add your own MCP tool/verb to the `dos` server a host talks to",
        example="dos_ext.mcp_tools:register",
    ),
)


_BY_GROUP = {s.group: s for s in SEAMS}

# A user typing `dos plugin new judge` means the `dos.judges` group — accept the natural
# singular as an alias for the plural group token (and a couple of common shorthands), so
# the scaffolder is forgiving where it can be unambiguous.
_SEAM_ALIASES = {
    "judge": "judges", "predicate": "predicates", "renderer": "renderers",
    "exporter": "exporters", "notifier": "notifiers", "driver": "drivers",
    "overlap": "overlap_policies", "overlap_policy": "overlap_policies",
    "evidence": "evidence_sources", "evidence_source": "evidence_sources",
    "enforce_handler": "enforce_handlers", "handler": "enforce_handlers",
    "hook_dialect": "hook_dialects", "dialect": "hook_dialects",
    "hook_install": "hook_installs", "plan_source": "plan_sources",
    "stop_policy": "stop_policies", "log_source": "log_sources",
    "scope_source": "scope_sources", "memory_store": "memory_stores",
    "memory": "memory_stores", "mcp_tool": "mcp_tools", "mcp": "mcp_tools",
}


def seam_for(group: str) -> "SeamDescriptor | None":
    """The descriptor for an entry-point group, or None.

    Accepts the full ``dos.x`` group, the bare ``x`` token, or a natural singular alias
    (``judge`` → ``dos.judges``) — whatever an operator is likely to type."""
    token = group[4:] if group.startswith("dos.") else group
    token = _SEAM_ALIASES.get(token, token)
    return _BY_GROUP.get(f"dos.{token}")


def _discovered_names(group: str, *, _stderr=None) -> list[str]:
    """Third-party occupant names registered under ``group``, sorted. Discovery I/O.

    The uniform scan every seam's own resolver does — used here so the manifest reads
    exactly what the resolvers read. A discovery fault degrades to an empty list (the
    seam-resolver posture), never crashing ``dos plugins``."""
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return []
    try:
        eps = entry_points(group=group)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(group, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive
        return []
    return sorted(ep.name for ep in eps)


@dataclass(frozen=True)
class SeamReport:
    """One seam folded with its live occupants — what `dos plugins` renders per group."""
    descriptor: SeamDescriptor
    builtins: tuple[str, ...]
    third_party: tuple[str, ...]  # discovered names NOT shadowing a built-in

    def to_dict(self) -> dict:
        d = self.descriptor
        return {
            "group": d.group,
            "axis": d.axis,
            "protocol": d.protocol,
            "invariant": d.invariant,
            "does_io": d.does_io,
            "builtins": list(self.builtins),
            "third_party": list(self.third_party),
            "purpose": d.purpose,
            "example": d.example,
        }


def fold(*, _stderr=None) -> list[SeamReport]:
    """Every seam folded with its live built-in floor + discovered third-party occupants.

    A third-party name that COLLIDES with a built-in is still listed under ``third_party``
    (so an operator can see a shadow attempt), but the built-in is what resolves — the
    unshadowability guarantee. Pure read: entry-point metadata only."""
    out: list[SeamReport] = []
    for s in SEAMS:
        discovered = _discovered_names(s.group, _stderr=_stderr)
        third_party = tuple(n for n in discovered if n not in s.builtins)
        out.append(SeamReport(descriptor=s, builtins=s.builtins, third_party=third_party))
    return out


def render_text(reports: list[SeamReport]) -> str:
    """The human table: one block per seam, third-party occupants called out."""
    lines: list[str] = []
    n_third = sum(len(r.third_party) for r in reports)
    lines.append(
        f"DOS extension surface — {len(reports)} pluggable seams, "
        f"{n_third} third-party plugin(s) installed")
    lines.append("")
    for r in reports:
        d = r.descriptor
        io = "I/O" if d.does_io else "pure"
        lines.append(f"  {d.group}   [Axis {d.axis}]  ({io})")
        lines.append(f"      {d.purpose}")
        lines.append(f"      protocol:  {d.protocol}")
        lines.append(f"      invariant: {d.invariant}")
        floor = ", ".join(d.builtins) if d.builtins else "(none)"
        lines.append(f"      built-in:  {floor}  (unshadowable)")
        if r.third_party:
            lines.append(f"      installed: {', '.join(r.third_party)}  <- third-party")
        lines.append("")
    lines.append(
        "  scaffold a plugin:  dos plugin new <seam> --name <yourname>   "
        "(e.g. dos plugin new judge --name acme)")
    return "\n".join(lines)


def render_json(reports: list[SeamReport]) -> str:
    import json

    return json.dumps(
        {
            "seam_count": len(reports),
            "third_party_count": sum(len(r.third_party) for r in reports),
            "seams": [r.to_dict() for r in reports],
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# The scaffolder — `dos plugin new <seam> --name <n>` emits a minimal, installable
# package that registers ONE occupant under the named seam. Same descriptor table as
# the manifest, so a generated stub always matches a real seam's contract.
# ---------------------------------------------------------------------------

def _stub_symbol(seam: SeamDescriptor, name: str) -> str:
    """The symbol name the entry point points at, per seam shape."""
    if seam.group == "dos.drivers":
        return f"{name.replace('-', '_')}_config"
    if seam.group == "dos.mcp_tools":
        return "register"
    # Class-style occupants (judges, predicates, renderers, …) — a CamelCase class.
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_")) + "Plugin"


def _pyproject_for(seam: SeamDescriptor, name: str, pkg: str) -> str:
    """The `pyproject.toml` registering `name` under `seam.group`, pointing at the stub."""
    floor = ", ".join(seam.builtins) or "(none)"
    return f'''# A DOS extension package scaffolded by `dos plugin new`. Registers ONE occupant
# under {seam.group} ({seam.purpose}). `pip install -e .` makes `{name}` discoverable —
# `dos plugins` lists it under {seam.group}, and the seam's resolver finds it BY NAME.
[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{pkg}"
version = "0.1.0"
description = "A DOS {seam.group} plugin: {name}"
requires-python = ">=3.11"
dependencies = ["dos-kernel"]

# The entry point the `dos` CLI / kernel discovers. `name = "module:object"` — DOS loads
# the object, and the {seam.group} resolver uses it (built-ins resolve FIRST and are
# unshadowable, so pick a name that is not a built-in: {floor}).
[project.entry-points."{seam.group}"]
{name} = "{pkg}:{_stub_symbol(seam, name)}"

[tool.setuptools]
packages = ["{pkg}"]
'''


def _stub_module(seam: SeamDescriptor, name: str) -> str:
    """The Python stub implementing the seam's contract — a correct, minimal occupant."""
    sym = _stub_symbol(seam, name)
    header = (
        f'"""A DOS {seam.group} plugin: {name}.\n\n'
        f"Contract: {seam.protocol}\n"
        f"Invariant: {seam.invariant}\n"
        f'Fill in the body; the shape below is what {seam.group}\'s resolver expects.\n"""\n'
        "from __future__ import annotations\n\n"
    )
    if seam.group == "dos.drivers":
        return header + (
            "from dos.config import (\n"
            "    LaneTaxonomy, PathLayout, SubstrateConfig,\n"
            "    gather_workspace_facts, resolve_workspace_root,\n"
            ")\n\n\n"
            f"def {sym}(workspace=None) -> SubstrateConfig:\n"
            f'    """The {name} host policy pack: its lanes/paths bound to `workspace`."""\n'
            "    root = resolve_workspace_root(workspace)\n"
            "    return SubstrateConfig(\n"
            "        lanes=LaneTaxonomy(\n"
            f'            concurrent=("{name}",), exclusive=("global",), autopick=("{name}",),\n'
            f'            trees={{"{name}": ("{name}/**/*",), "global": ("**/*",)}},\n'
            "        ),\n"
            "        paths=PathLayout.for_root(root),\n"
            "        # gather facts so the SELF_MODIFY guard is workspace-scoped (the kernel\n"
            "        # also backfills this, but a well-formed pack gathers its own).\n"
            "        workspace=gather_workspace_facts(root),\n"
            "    )\n"
        )
    if seam.group == "dos.mcp_tools":
        return header + (
            "def register(mcp) -> None:\n"
            '    """Register this package\'s MCP tool(s) on the DOS server\'s FastMCP `mcp`.\n\n'
            "    `mcp.tool` is the SAME decorator the built-in tools use (it carries the\n"
            "    call deadline + deep-answer link), so a tool registered here behaves like\n"
            '    a native one."""\n'
            "    @mcp.tool()\n"
            f"    def {name.replace('-', '_')}(text: str = \"\") -> dict:\n"
            f'        """One-line description of what {name} does (shown to the agent)."""\n'
            '        return {"ok": True, "echo": text}\n'
        )
    # The generic class-style occupant.
    seam_module = seam.group.split(".", 1)[1]
    return header + (
        f"class {sym}:\n"
        f'    name = "{name}"\n\n'
        f"    # implement {seam.protocol}\n"
        f"    # (see dos.{seam_module} for the Protocol it checks against)\n"
        "    pass\n"
    )


def _readme_for(seam: SeamDescriptor, name: str, pkg: str) -> str:
    return (
        f"# {pkg} — a DOS `{seam.group}` plugin\n\n"
        f"Registers `{name}` under `{seam.group}` ({seam.purpose}).\n\n"
        "## Install & verify\n\n"
        "```bash\n"
        "pip install -e .\n"
        f"dos plugins            # lists `{name}` under {seam.group}\n"
        "```\n\n"
        f"**Contract:** `{seam.protocol}`\n\n"
        f"**Invariant:** {seam.invariant}\n\n"
        f"Fill in `{pkg}/__init__.py` (the `{_stub_symbol(seam, name)}` stub).\n"
    )


@dataclass(frozen=True)
class ScaffoldPlan:
    """The files `dos plugin new` would write, as {relative_path: content}. Pure data."""
    seam: SeamDescriptor
    name: str
    pkg: str
    files: dict = field(default_factory=dict)


def scaffold(group: str, name: str) -> ScaffoldPlan:
    """Build the file set for a new plugin package — PURE (returns content, writes nothing).

    The CLI writes the returned files; keeping the planning pure makes it testable without
    touching the disk. Raises ValueError on an unknown seam (fail-loud, the selector
    posture) — with the known seam list, mirroring the resolvers."""
    seam = seam_for(group)
    if seam is None:
        known = ", ".join(s.group.split(".", 1)[1] for s in SEAMS)
        raise ValueError(
            f"unknown seam {group!r}; known: {known}. "
            f"Run `dos plugins` to see every seam and its contract.")
    safe = name.replace("-", "_")
    pkg = f"dos_{safe}_plugin"
    files = {
        "pyproject.toml": _pyproject_for(seam, name, pkg),
        f"{pkg}/__init__.py": _stub_module(seam, name),
        "README.md": _readme_for(seam, name, pkg),
    }
    return ScaffoldPlan(seam=seam, name=name, pkg=pkg, files=files)


__all__ = [
    "SeamDescriptor", "SeamReport", "SEAMS", "seam_for",
    "fold", "render_text", "render_json",
    "ScaffoldPlan", "scaffold",
]
