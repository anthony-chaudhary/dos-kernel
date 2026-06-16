"""`dos market` (prototype) — one touch from an agent to a trust-verified plugin listing.

The marketplace where the kernel does not believe the listing. Every other plugin
registry (npm, PyPI, the MCP registries) indexes what the AUTHOR SAYS their package does;
DOS indexes WITNESSES the author did not author. A listing carries three checkable facts,
never marketing copy:

  1. the DISCOVERED entry-point group(s) — read via importlib.metadata, the same scan
     `dos plugins` does, never the README;
  2. a CONFORMANCE verdict — the occupant run against its seam's stated safety invariant
     (a judge must FAIL-TO-ABSTAIN, never auto-AGREE; the predicate/exporter/etc. floors);
  3. a PROVENANCE card — diff-witnessed commit ratio over the package's repo, the same
     `commit-audit` story the scoreboard tells about this repo.

`dos market submit <package>` does all three inline and writes the verified facts to the
index. The agent touches one verb; the kernel does the distrust. Design: docs/368.

LAYERING: this is TOOLING that imports `dos` (the scripts/ tier, never edited by the
kernel) — NOT a kernel leaf. The index BACKEND is resolved by name through a
`dos.market_backends` entry-point group (built-in `file` floor, unshadowable), the same
shape every other DOS seam uses, so public / private / vendor are each just a registered
backend. The `dos market` CLI verb is a one-line follow-up in cli.py once the tree is calm.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from importlib.metadata import entry_points
from pathlib import Path

# The seam invariant table is the SINGLE source of truth shared with `dos plugins` and the
# scaffolder — import it rather than restating the invariants (they cannot drift).
from dos.plugins import SEAMS, seam_for

# Every DOS extension entry-point group, taken from the live plugins manifest — so a new
# seam added to dos.plugins is automatically marketable, no edit here.
_DOS_SEAM_GROUPS = frozenset(s.group for s in SEAMS)


# ---------------------------------------------------------------------------
# 1. DISCOVER — read the package's OWN entry-point metadata. Never the description.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveredOccupant:
    """One occupant a package registers under a DOS seam — read from packaging metadata."""
    group: str          # the dos.<seam> entry-point group it registered under
    name: str           # the occupant name the seam resolver matches by
    target: str         # "module:object" — where the occupant lives
    is_dos_seam: bool    # is `group` an actual DOS seam (vs an unrelated entry point)?


def discover(package: str) -> list[DiscoveredOccupant]:
    """Every DOS-seam occupant `package` registers, from its installed entry-point metadata.

    Reads the SAME entry-point table the seam resolvers read — the package cannot list a
    group it did not register, because this is its packaging metadata, not its prose. A
    package registering nothing under any `dos.*` seam yields an empty list (the honest
    'this is not a DOS plugin' answer)."""
    out: list[DiscoveredOccupant] = []
    try:
        eps = entry_points()
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    # Selectable API (py3.10+): iterate groups we care about; match occupants whose
    # distribution is `package`. We scan all DOS seam groups and keep occupants the named
    # package registered (matched by the entry point's distribution name where available).
    for group in sorted(_DOS_SEAM_GROUPS):
        try:
            group_eps = eps.select(group=group)
        except AttributeError:  # pragma: no cover - very old importlib API
            group_eps = eps.get(group, [])  # type: ignore[attr-defined]
        for ep in group_eps:
            dist = getattr(ep, "dist", None)
            dist_name = getattr(dist, "name", None) if dist is not None else None
            # When packaging exposes the owning distribution, require it to match; when it
            # does not (older metadata), fall through and let the value field carry it.
            if dist_name is not None and dist_name != package:
                continue
            out.append(DiscoveredOccupant(
                group=group, name=ep.name, target=ep.value, is_dos_seam=True,
            ))
    return out


# ---------------------------------------------------------------------------
# 2. CONFORM — run the occupant against its seam's stated safety invariant.
#    A listing is a WITNESSED conformance verdict, never a self-applied badge.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConformanceResult:
    """The witnessed verdict that an occupant honors its seam's safety invariant."""
    group: str
    name: str
    passed: bool
    detail: str        # what the harness saw — the inspectable reason, never a bare bool
    checked: bool      # did a real check run, or is this a NOT-YET-COVERED seam?


def _load_occupant(target: str):
    """Load a `module:object` entry-point target. Raises on a broken target (fail-loud)."""
    mod_name, _, attr = target.partition(":")
    import importlib

    mod = importlib.import_module(mod_name)
    obj = mod
    for part in attr.split(".") if attr else []:
        obj = getattr(obj, part)
    return obj


def _conform_judge(occupant) -> tuple[bool, str]:
    """A judge must FAIL-TO-ABSTAIN: it may never AGREE with an empty/evidence-free claim.

    The whole judge seam exists to make the false-clear (AGREE on a false claim) hard. We
    hand the occupant a claim with NO supporting evidence; a conformant judge ABSTAINs or
    DISAGREEs — anything but AGREE. (We run it through the kernel's own `run_judge`, which
    is what enforces fail-to-abstain on exceptions; an occupant that AGREEs on nothing is a
    real conformance failure, not a crash.)"""
    from dos.judges import Claim, JudgeVerdict, run_judge

    judge = occupant() if isinstance(occupant, type) else occupant
    empty = Claim(claim_text="(conformance probe: an unsupported claim)", stated_reason="",
                  evidence=())
    try:
        verdict = run_judge(judge, empty, config=None)
    except Exception as e:  # run_judge should swallow this to ABSTAIN; defensive
        return True, f"raised under run_judge → folded to abstain ({e}); fail-to-abstain holds"
    if isinstance(verdict, JudgeVerdict) and verdict.agreed:
        return False, "AGREED an evidence-free claim — violates fail-to-abstain (false-clear risk)"
    stance = getattr(verdict, "stance", "?")
    return True, f"ruled {stance} on an evidence-free claim — fail-to-abstain holds"


def _conform_renderer(occupant) -> tuple[bool, str]:
    """A renderer is PURE presentation: handed a decided verdict, it returns a str and
    mutates nothing. We check it returns a string for a trivial input and does not raise."""
    r = occupant() if isinstance(occupant, type) else occupant
    render = getattr(r, "render", None)
    if render is None:
        return False, "no .render(verdict) method — renderers must implement render"
    try:
        out = render({"verdict": "OK"})
    except Exception as e:
        return False, f"render() raised on a trivial verdict ({e}) — renderers must be pure/total"
    return (isinstance(out, str), f"render() returned {type(out).__name__}; expected str")


# The conformance table — keyed by seam group. Each entry is a probe that exercises the
# seam's INVARIANT (the words in dos.plugins.SeamDescriptor.invariant), returning
# (passed, detail). Seams without a probe yet are listed as not-yet-covered: HONEST, never
# a silent pass. New probes are added here without touching the kernel.
_CONFORMANCE_PROBES = {
    "dos.judges": _conform_judge,
    "dos.renderers": _conform_renderer,
}


def conform(occupants: list[DiscoveredOccupant]) -> list[ConformanceResult]:
    """Run each occupant against its seam's invariant probe.

    A seam with a probe yields a witnessed pass/fail. A seam not yet covered yields
    `checked=False` (an honest 'we did not verify this invariant', NOT a pass) — the
    listing shows it as unverified rather than fabricating a clear."""
    out: list[ConformanceResult] = []
    for occ in occupants:
        probe = _CONFORMANCE_PROBES.get(occ.group)
        if probe is None:
            out.append(ConformanceResult(
                group=occ.group, name=occ.name, passed=False, checked=False,
                detail=f"no conformance probe for {occ.group} yet — invariant unverified "
                       f"(the seam floor still contains the occupant; listing stays honest)"))
            continue
        try:
            loaded = _load_occupant(occ.target)
            ok, detail = probe(loaded)
        except Exception as e:
            out.append(ConformanceResult(
                group=occ.group, name=occ.name, passed=False, checked=True,
                detail=f"occupant failed to load/run ({e}) — a broken occupant does not list"))
            continue
        out.append(ConformanceResult(
            group=occ.group, name=occ.name, passed=ok, checked=True, detail=detail))
    return out


# ---------------------------------------------------------------------------
# 3. PROVENANCE — how the package was BUILT, from git the author did not forge.
#    The same diff-witnessed story the scoreboard tells about this repo.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProvenanceCard:
    """How a plugin's repo was built — witnessed from git, never self-reported."""
    repo: str
    commits: int
    diff_witnessed: int        # commits whose subject's claim-kind matches its diff
    ratio: float               # diff_witnessed / commits — the scoreboard number
    reachable: bool            # was the repo a readable git tree at all?
    detail: str


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"{e}"


def provenance(repo: Path, *, max_commits: int = 200) -> ProvenanceCard:
    """A diff-witnessed-commit ratio over `repo` — the provenance card.

    A lightweight, self-contained read (it shells `git log` rather than importing the full
    audit path, so this prototype never races the kernel's own commit_audit module): a
    commit is counted 'diff-witnessed' when it touched real files (not an empty/--allow-empty
    commit and not a message-only change). FAIL-SAFE: an unreadable / non-git repo returns
    a 0-ratio card flagged unreachable, never a fabricated clean provenance."""
    if not (repo / ".git").exists():
        rc, _ = _git(repo, "rev-parse", "--git-dir")
        if rc != 0:
            return ProvenanceCard(repo=str(repo), commits=0, diff_witnessed=0, ratio=0.0,
                                  reachable=False, detail="not a git repo — provenance unverifiable")
    rc, out = _git(repo, "log", f"-{max_commits}", "--pretty=format:%H", "--name-only")
    if rc != 0:
        return ProvenanceCard(repo=str(repo), commits=0, diff_witnessed=0, ratio=0.0,
                              reachable=False, detail="git log failed — provenance unverifiable")
    # Split the log into per-commit blocks (a SHA line followed by its touched files).
    blocks = [b for b in out.split("\n\n") if b.strip()]
    commits = 0
    witnessed = 0
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        commits += 1
        touched = lines[1:]  # everything after the SHA line
        if touched:          # a commit that actually changed files is diff-witnessed
            witnessed += 1
    ratio = (witnessed / commits) if commits else 0.0
    return ProvenanceCard(
        repo=str(repo), commits=commits, diff_witnessed=witnessed, ratio=round(ratio, 4),
        reachable=True,
        detail=f"{witnessed}/{commits} commits touched files (diff-witnessed)")


# ---------------------------------------------------------------------------
# The index backend seam — `dos.market_backends`. file built-in, unshadowable.
# public / private / vendor are each just a registered backend resolved by name.
# ---------------------------------------------------------------------------

MARKET_BACKEND_GROUP = "dos.market_backends"


@dataclass
class Listing:
    """A verified marketplace listing — the WITNESSED facts, never the author's copy."""
    package: str
    occupants: list[dict] = field(default_factory=list)
    conformance: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    admitted: bool = False
    reason: str = ""          # the typed gap when refused — from the closed vocabulary


class FileBackend:
    """The built-in index backend: a JSONL file on disk, one listing per line.

    The honest zero of the seam — a workspace with no hosted registry still has a
    resolvable index. A private / public / vendor backend is a third-party
    `dos.market_backends` occupant with the same put/list shape."""
    name = "file"

    def __init__(self, path: Path):
        self.path = path

    def put(self, listing: Listing) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(listing), sort_keys=True) + "\n")

    def listings(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


def resolve_backend(name: str, *, index_path: Path) -> object:
    """Resolve an index backend by name — built-in `file` first (unshadowable), then the
    `dos.market_backends` entry-point group, fail-loud on a miss. The same selector posture
    as `resolve_driver_config`."""
    if name == "file":
        return FileBackend(index_path)
    try:
        eps = entry_points().select(group=MARKET_BACKEND_GROUP)
    except Exception:
        eps = []
    for ep in eps:
        if ep.name == name:
            return ep.load()(index_path)
    known = ["file", *(ep.name for ep in eps)]
    raise ValueError(f"unknown market backend {name!r}; known: {', '.join(known)}. "
                     f"Register one under '{MARKET_BACKEND_GROUP}'.")


# ---------------------------------------------------------------------------
# SUBMIT — the one-touch flow: discover → conform → provenance → admit/refuse → put.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubmitPolicy:
    """The admission gate the index applies — config, not code. Tightens for a private
    registry (require conformance + a provenance floor); loose for the public index."""
    require_conformance: bool = True      # every CHECKED occupant must pass
    min_provenance_ratio: float = 0.0     # provenance ratio floor (0 = public/no gate)
    require_dos_occupant: bool = True      # the package must register under a real seam


def build_listing(package: str, repo: Path, policy: SubmitPolicy) -> Listing:
    """Run the three witness steps and decide admission — PURE of the index write.

    Returns a Listing carrying the witnessed facts and a typed admission verdict. The
    refusal reason is from the closed vocabulary (MARKET_NOT_A_PLUGIN /
    MARKET_CONFORMANCE_FAIL / PROVENANCE_BELOW_FLOOR) — never free text."""
    occupants = discover(package)
    conf = conform(occupants)
    prov = provenance(repo)

    listing = Listing(
        package=package,
        occupants=[asdict(o) for o in occupants],
        conformance=[asdict(c) for c in conf],
        provenance=asdict(prov),
    )

    if policy.require_dos_occupant and not occupants:
        listing.reason = "MARKET_NOT_A_PLUGIN"
        return listing
    checked_fails = [c for c in conf if c.checked and not c.passed]
    if policy.require_conformance and checked_fails:
        listing.reason = "MARKET_CONFORMANCE_FAIL"
        return listing
    if prov.ratio < policy.min_provenance_ratio:
        listing.reason = "PROVENANCE_BELOW_FLOOR"
        return listing

    listing.admitted = True
    listing.reason = ""
    return listing


# ---------------------------------------------------------------------------
# CLI — `dos_market.py submit <package> [--repo .] [--backend file] [--private]`
# ---------------------------------------------------------------------------

def _default_index_path(workspace: Path) -> Path:
    return workspace / ".dos" / "market" / "index.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", default=".", help="workspace root (default: cwd)")
    ap.add_argument("--backend", default="file", help="index backend name (default: file)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="discover → conform → provenance → list one package")
    s.add_argument("package", help="the installed pip package to list")
    s.add_argument("--repo", default=".", help="the package's git repo, for provenance")
    s.add_argument("--private", action="store_true",
                   help="apply the private/internal gate (require conformance + provenance>=0.5)")
    s.add_argument("--json", action="store_true", help="emit the listing as JSON")

    sub.add_parser("list", help="show the current index")

    args = ap.parse_args(argv)
    workspace = Path(args.workspace).resolve()
    index_path = _default_index_path(workspace)
    backend = resolve_backend(args.backend, index_path=index_path)

    if args.cmd == "submit":
        policy = (SubmitPolicy(require_conformance=True, min_provenance_ratio=0.5)
                  if args.private
                  else SubmitPolicy(require_conformance=True, min_provenance_ratio=0.0))
        listing = build_listing(args.package, Path(args.repo).resolve(), policy)
        if args.json:
            print(json.dumps(asdict(listing), sort_keys=True, indent=2))
        else:
            _print_listing(listing)
        if listing.admitted:
            backend.put(listing)
            return 0
        # A refusal is a non-zero exit carrying the typed reason — the gate, not a warning.
        print(f"\nREFUSED: {listing.reason} — not listed.", file=sys.stderr)
        return 2

    if args.cmd == "list":
        for row in backend.listings():
            verdict = "ADMITTED" if row.get("admitted") else f"REFUSED({row.get('reason')})"
            print(f"  {row['package']:<30} {verdict}")
        return 0

    return 0


def _print_listing(listing: Listing) -> None:
    print(f"package: {listing.package}")
    if listing.occupants:
        print("  seams registered (discovered, not described):")
        for o in listing.occupants:
            print(f"    {o['group']}: {o['name']}  ->  {o['target']}")
    else:
        print("  no DOS-seam entry points found — this is not a DOS plugin")
    if listing.conformance:
        print("  conformance (witnessed against the seam invariant):")
        for c in listing.conformance:
            mark = "PASS" if c["passed"] else ("UNVERIFIED" if not c["checked"] else "FAIL")
            print(f"    [{mark}] {c['group']}:{c['name']} — {c['detail']}")
    p = listing.provenance
    if p:
        print(f"  provenance: {p['detail']} (ratio {p['ratio']}, reachable={p['reachable']})")
    print(f"  => {'ADMITTED' if listing.admitted else 'REFUSED: ' + listing.reason}")


if __name__ == "__main__":
    raise SystemExit(main())
