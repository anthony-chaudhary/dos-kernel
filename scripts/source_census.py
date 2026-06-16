#!/usr/bin/env python3
"""source_census — count the data sources DOS can REASONABLY WITNESS, from ground truth.

The operator goal "cover 98% of industry-known data sources that DOS can witness"
is unbounded prose until it has a number. This script is that number: a
re-runnable census of the industry-known universe of data sources an autonomous
fleet leaves effects on, mapped onto the witness SHAPES DOS actually ships — and
the coverage fraction that falls out, read from the repo's own ground truth, never
from a claim. Run it before and after a witness lands and the delta is progress,
measured. Companion narrative: docs/358_data-source-coverage-census.md.

It is dev tooling that operates ON the repo (it reads the package's registered
`dos.evidence_sources` entry-points at the boundary — the same one-way arrow
`discoverability_inventory.py` keeps by shelling the public CLI; the package is
unaware of this script).

The criterion — "reasonably witnessable" (docs/85 §2 gate 2, docs/93)
=====================================================================
DOS grounds a verdict only on a byte the JUDGED AGENT CANNOT AUTHOR. The criterion
is the 3-rung `Accountability` enum (`src/dos/evidence.py` / `log_source.py`):

  AGENT_AUTHORED  the agent (or a process it controls) wrote it — the forgeable
                  floor; a JUDGE hint only, NEVER a verdict. NOT witnessable.
  OS_RECORDED     the OS authored it (a kernel-launched exit code, a git blob).
  THIRD_PARTY     infra the agent can't write (a CI verdict, a cloud audit trail).

A source is "reasonably witnessable" iff its authoritative byte reaches
OS_RECORDED or THIRD_PARTY. The AGENT_AUTHORED floor and the two irreducible
residue classes (below) are NOT — and the honest 98% claim depends on carving
them out of the denominator, not hiding them.

A witness is a SHAPE, not a per-vendor driver (the pivot, docs/261 / docs/212)
==============================================================================
DOS does not need a driver per vendor. The industry universe collapses onto a
small CLOSED set of witness shapes, each proven by an already-registered backend:

  provider_ledger_readback  any external-effect ledger keyed by a reference
                            (payments, SMS, cloud audit, object-store events)
                            -> `provider_ledger`            (THIRD_PARTY band)
  persisted_state_diff      any persisted store read back vs a gold value
                            (relational/KV/doc DBs, object storage, config stores)
                            -> `content_diff` (+ the `state_diff` kernel helper)
  third_party_api_status    any third-party API verdict reader
                            (CI/Checks, status pages, control-plane, legal reporters)
                            -> `ci_status`, `citation_resolve`  (THIRD_PARTY)
  os_acceptance_exit_code   any exit-code-bearing command (psql/aws/kubectl/dig/gh)
                            -> `os_acceptance`                  (OS_RECORDED)
  git_presence              the VCS fossil — the kernel's flagship built oracle
                            -> the in-kernel `oracle`/`phase_shipped` (OS_RECORDED)
  agent_authored_floor      the un-witnessable floor, listed so it is excluded by
                            NAME not by omission -> `paste_log`, `null`

The honesty rule (the whole point of the product)
=================================================
A source is COVERED only when its witness shape resolves to a backend that is
ACTUALLY REGISTERED in this repo's `dos.evidence_sources` entry-points (or the
built-in git oracle) — recomputed at runtime, never a hardcoded "yes". A shape
that is only NAMED in a doc but not built is SPEC: listed, never folded into the
covered headline. The two residue classes are RESIDUE: structurally absent from
the denominator. So the number cannot inflate on a promise — `--check` (and the
gate `tests/test_source_census.py`) fail loudly if a declared-COVERED source's
backend was renamed or removed.

A second honesty rule: the per-shape RUNG is READ OFF THE LIVE SOURCE OBJECT
(`resolve_evidence_source(name).accountability`), never written in the DATA below
— because `content_diff`/`provider_ledger` declare a THIRD_PARTY *ceiling* capped
per-call, so a rung hardcoded here would itself be a self-report.

Exit code: 0 always (it is a report) unless --check is given, which exits 1 if any
declared-COVERED source's witness shape is not built at runtime (a rot pin).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# A built kernel oracle, not a pluggable entry-point — the git fossil verify()
# reads. Resolved by "does `dos.oracle` import" rather than by entry-point name.
GIT_ORACLE_SENTINEL = "__git_oracle__"
# Pseudo-shapes for the irreducible residue — no sound witness exists; excluded
# from the denominator by construction (docs/192 §6).
RESIDUE_SHAPES = {"__external_effect__", "__judge_only__"}

# shape -> the backend name(s)/sentinel that PROVE the shape is built. A shape is
# "built" iff >=1 of these resolves at runtime (entry-points for drivers; the
# import sentinel for the git oracle). The DATA names backends; the SCRIPT checks
# them against the live registry — COVERED is computed, never trusted.
WITNESS_SHAPES = {
    "provider_ledger_readback": ["provider_ledger"],
    "persisted_state_diff":     ["content_diff"],          # state_diff is a kernel helper, not a backend
    "third_party_api_status":   ["ci_status", "citation_resolve"],
    "human_approval_envelope":  ["slack_approval"],        # "a human approved this" — docs/93 §3
    "os_acceptance_exit_code":  ["os_acceptance"],
    "git_presence":             [GIT_ORACLE_SENTINEL],
    "agent_authored_floor":     ["paste_log", "null"],     # the floor — witnessable=False
}

# The industry-known universe, grouped by the shape each source maps onto. Each
# row: (source_name, witness_shape, declared_status). declared_status is the
# AUTHOR'S claim; the script OVERRIDES a COVERED claim to UNCOVERED_ROT when the
# shape's backend is absent at runtime. SPEC/RESIDUE are author-set (a doc names
# them, or no sound witness exists). The doc table (docs/358 §3) mirrors this.
SOURCES = [
    # --- provider_ledger_readback: external-effect ledgers (THIRD_PARTY) ----------
    ("Stripe / payment processor ledger",        "provider_ledger_readback", "COVERED"),
    ("PayPal / Adyen charge ledger",             "provider_ledger_readback", "COVERED"),
    ("Twilio / SMS-gateway delivery receipt",    "provider_ledger_readback", "COVERED"),
    ("SendGrid / email-send event log",          "provider_ledger_readback", "COVERED"),
    ("AWS CloudTrail audit trail",               "provider_ledger_readback", "COVERED"),
    ("GCP / Azure cloud activity log",           "provider_ledger_readback", "COVERED"),
    ("S3 / object-store access+event log",       "provider_ledger_readback", "COVERED"),
    ("Stripe-style webhook event store",         "provider_ledger_readback", "COVERED"),
    ("Payment refund / chargeback ledger",       "provider_ledger_readback", "COVERED"),
    ("Cloud billing / cost-explorer ledger",     "provider_ledger_readback", "COVERED"),
    # --- persisted_state_diff: persisted stores read back vs a gold (OS/THIRD) -----
    ("PostgreSQL / MySQL row read-back",         "persisted_state_diff", "COVERED"),
    ("SQLite file read-back",                    "persisted_state_diff", "COVERED"),
    ("MongoDB / document-store read-back",       "persisted_state_diff", "COVERED"),
    ("Redis / KV-store read-back",               "persisted_state_diff", "COVERED"),
    ("DynamoDB / wide-column read-back",         "persisted_state_diff", "COVERED"),
    ("etcd / Consul config-store read-back",     "persisted_state_diff", "COVERED"),
    ("S3 / GCS object CONTENT read-back",        "persisted_state_diff", "COVERED"),
    ("Elasticsearch / search-index document",    "persisted_state_diff", "COVERED"),
    ("Parquet / data-lake table read-back",      "persisted_state_diff", "COVERED"),
    ("Feature-store / vector-DB record",         "persisted_state_diff", "COVERED"),
    ("Filesystem file CONTENT read-back",        "persisted_state_diff", "COVERED"),
    ("Container-image / registry digest",        "persisted_state_diff", "COVERED"),
    # The generic read-back IS the shape: `state_diff` reads a remote store via a
    # host-supplied `read_state` reader, and its docstring names "a SaaS API, a
    # cloud DB" as THIRD_PARTY-tagged siblings. So a live HTTP/API re-GET is the
    # SAME built shape pointed at an HTTP store — COVERED, not a missing witness.
    # A vendor-convenience wrapper is a host job (docs/85 move B), not a DOS gap.
    ("Live HTTP/API re-GET read-back",           "persisted_state_diff", "COVERED"),
    # --- third_party_api_status: third-party verdict readers (THIRD_PARTY) ---------
    ("GitHub Actions / CI Checks verdict",       "third_party_api_status", "COVERED"),
    ("GitLab CI / CircleCI / Jenkins verdict",   "third_party_api_status", "COVERED"),
    ("Cloud control-plane resource status",      "third_party_api_status", "COVERED"),
    ("Kubernetes API object status",             "third_party_api_status", "COVERED"),
    ("Terraform / IaC state-plane status",       "third_party_api_status", "COVERED"),
    ("Package-registry release index (PyPI/npm)","third_party_api_status", "COVERED"),
    ("Statuspage / uptime-monitor verdict",      "third_party_api_status", "COVERED"),
    ("Legal reporter (CourtListener)",           "third_party_api_status", "COVERED"),
    ("DNS / WHOIS authoritative record",         "third_party_api_status", "COVERED"),
    ("TLS-certificate transparency log",         "third_party_api_status", "COVERED"),
    # --- os_acceptance_exit_code: any exit-code-bearing command (OS_RECORDED) ------
    ("psql / mysql client exit code",            "os_acceptance_exit_code", "COVERED"),
    ("aws / gcloud / az CLI exit code",          "os_acceptance_exit_code", "COVERED"),
    ("kubectl apply exit code",                  "os_acceptance_exit_code", "COVERED"),
    ("dig / nslookup resolver exit code",        "os_acceptance_exit_code", "COVERED"),
    ("curl / http probe exit code",              "os_acceptance_exit_code", "COVERED"),
    ("docker / podman build exit code",          "os_acceptance_exit_code", "COVERED"),
    ("gh / glab API-call exit code",             "os_acceptance_exit_code", "COVERED"),
    ("test-runner / linter exit code",           "os_acceptance_exit_code", "COVERED"),
    # --- git_presence: the VCS fossil (OS_RECORDED) --------------------------------
    ("git commit existence + ancestry",          "git_presence", "COVERED"),
    ("git blob CONTENT (content-addressed)",     "git_presence", "COVERED"),
    ("GitHub / GitLab hosted-repo state",        "git_presence", "COVERED"),

    # The Slack approval-envelope adjudicates a DISTINCT claim ("an accountable
    # human approved this") via the Slack audit API — its OWN witness shape. Built
    # here as `slack_approval` (docs/93 §3, the move-B driver after ci_status), so it
    # is COVERED, not SPEC: the number is EARNED by a registered backend, not asserted.
    ("Slack / chat human-approval envelope",     "human_approval_envelope", "COVERED"),  # docs/93 §3

    # --- RESIDUE: no sound witness — EXCLUDED from the denominator (docs/192 §6) ---
    ("Email recipient actually-read receipt",    "__external_effect__", "RESIDUE"),   # absent 3rd principal
    ("Downstream-system human acted-on it",      "__external_effect__", "RESIDUE"),   # no in-trace receipt
    ("Summary / advice quality (taste)",         "__judge_only__",      "RESIDUE"),   # no canonical end-state
    ("Open-ended 'recommend 3 X' goal",          "__judge_only__",      "RESIDUE"),   # JUDGE/HUMAN only
]


# --------------------------------------------------------------------------- #
# ground-truth reads — the live registry + the git oracle, fail-safe.
# --------------------------------------------------------------------------- #

def _registered_backends() -> set[str]:
    """The names ACTUALLY registered in `dos.evidence_sources`, read live. Empty
    set on any failure (the report degrades; never crashes off a missing package).
    `null` is the unshadowable built-in floor, always present."""
    try:
        from dos.evidence import active_evidence_source_names
        return set(active_evidence_source_names()) | {"null"}
    except Exception:
        return {"null"}


def _git_oracle_built() -> bool:
    """The git rung is an in-kernel oracle, not an entry-point. It is 'built' iff
    the kernel module that performs it imports."""
    try:
        import dos.oracle  # noqa: F401
        import dos.phase_shipped  # noqa: F401
        return True
    except Exception:
        return False


def _backend_resolvable(name: str) -> bool:
    if name == GIT_ORACLE_SENTINEL:
        return _git_oracle_built()
    return name in _registered_backends()


def _shape_is_built(shape: str) -> bool:
    """A shape is built iff >=1 of its proving backends resolves at runtime."""
    backends = WITNESS_SHAPES.get(shape)
    if not backends:
        return False
    return any(_backend_resolvable(b) for b in backends)


def _shape_rung(shape: str) -> str:
    """Read the rung OFF THE LIVE SOURCE OBJECT (never the DATA): the strongest
    accountability any proving backend reaches. The git sentinel is OS_RECORDED
    (a content-addressed, tamper-evident store). 'NO_SIGNAL' if nothing resolves."""
    backends = WITNESS_SHAPES.get(shape, [])
    rank = {"AGENT_AUTHORED": 0, "OS_RECORDED": 1, "THIRD_PARTY": 2}
    best = None
    for b in backends:
        if b == GIT_ORACLE_SENTINEL:
            cand = "OS_RECORDED" if _git_oracle_built() else None
        else:
            cand = None
            try:
                from dos.evidence import resolve_evidence_source
                acc = getattr(resolve_evidence_source(b), "accountability", None)
                cand = getattr(acc, "value", None)
            except Exception:
                cand = None
        if cand is not None and (best is None or rank.get(cand, -1) > rank.get(best, -1)):
            best = cand
    return best or "NO_SIGNAL"


def _is_residue(shape: str) -> bool:
    return shape in RESIDUE_SHAPES


def _witnessable(shape: str, rung: str) -> bool:
    """Witnessable = a real witness shape whose rung clears the floor. Residue
    pseudo-shapes and the AGENT_AUTHORED floor are not witnessable."""
    if _is_residue(shape) or shape == "agent_authored_floor":
        return False
    return rung in {"OS_RECORDED", "THIRD_PARTY"}


# --------------------------------------------------------------------------- #
# gather / headline / render — the discoverability_inventory shape.
# --------------------------------------------------------------------------- #

def gather() -> dict:
    shapes = {}
    for shape, backends in WITNESS_SHAPES.items():
        built = _shape_is_built(shape)
        shapes[shape] = {
            "backends": backends,
            "built": built,
            "rung": _shape_rung(shape),
        }
    sources = []
    for name, shape, declared in SOURCES:
        rung = _shape_rung(shape) if shape in WITNESS_SHAPES else "NO_SIGNAL"
        if declared == "COVERED":
            effective = "COVERED" if _shape_is_built(shape) else "UNCOVERED_ROT"
        else:
            effective = declared  # SPEC / RESIDUE pass through
        sources.append({
            "name": name,
            "shape": shape,
            "declared_status": declared,
            "effective_status": effective,
            "rung": rung,
            "witnessable": _witnessable(shape, rung),
        })
    return {
        "sources": sources,
        "shapes": shapes,
        "registered_backends": sorted(_registered_backends()),
        "git_oracle_built": _git_oracle_built(),
    }


def headline(inv: dict) -> dict:
    srcs = inv["sources"]
    covered = sum(1 for s in srcs if s["effective_status"] == "COVERED")
    spec = sum(1 for s in srcs if s["effective_status"] == "SPEC")
    residue = sum(1 for s in srcs if s["effective_status"] == "RESIDUE")
    rot = sum(1 for s in srcs if s["effective_status"] == "UNCOVERED_ROT")
    witnessable_universe = covered + spec  # residue is structurally OUT of the denominator
    coverage_pct = round(covered / witnessable_universe, 4) if witnessable_universe else 0.0
    return {
        "sources_total": len(srcs),
        "covered": covered,
        "spec": spec,
        "residue": residue,
        "uncovered_rot": rot,
        "witnessable_universe": witnessable_universe,
        "coverage_pct": coverage_pct,
        "shapes_total": len(inv["shapes"]),
        "shapes_built": sum(1 for v in inv["shapes"].values() if v["built"]),
    }


def render(inv: dict, h: dict) -> str:
    L = []
    L.append("# DOS data-source coverage census — the sources DOS can reasonably witness")
    L.append("")
    L.append("> Counted from the repo's own ground truth. A source is COVERED only when")
    L.append("> its witness SHAPE resolves to a backend registered in this tree; a")
    L.append("> doc-named-but-unbuilt shape is SPEC, never folded into the covered count;")
    L.append("> the irreducible residue (no sound witness) is excluded from the")
    L.append("> denominator. The rung is read off the live source object, not the DATA.")
    L.append("")
    L.append("## Headline")
    L.append("")
    pct = h["coverage_pct"] * 100
    L.append(f"- witnessable sources COVERED: **{h['covered']}/{h['witnessable_universe']}** "
             f"= **{pct:.1f}%** of the witnessable universe")
    L.append(f"- SPEC (witnessable shape, backend not built here): **{h['spec']}**")
    L.append(f"- RESIDUE (no sound witness — excluded from denominator): **{h['residue']}**")
    if h["uncovered_rot"]:
        L.append(f"- UNCOVERED_ROT (declared COVERED but backend MISSING): **{h['uncovered_rot']}**  [!]")
    L.append(f"- witness shapes built: **{h['shapes_built']}/{h['shapes_total']}**")
    L.append(f"- sources catalogued: **{h['sources_total']}**")
    L.append("")
    L.append("## Witness shapes (the closed set the universe collapses onto)")
    L.append("")
    L.append("> A witness is a SHAPE, not a per-vendor driver. Rung is read live.")
    L.append("")
    for shape, v in inv["shapes"].items():
        built = "built" if v["built"] else "NOT-BUILT"
        backs = ", ".join(b for b in v["backends"] if b != GIT_ORACLE_SENTINEL) or "(git oracle)"
        L.append(f"- `{shape}` — {built} · rung **{v['rung']}** · via `{backs}`")
    L.append("")
    L.append("## Sources, grouped by witness shape")
    L.append("")
    order = list(WITNESS_SHAPES.keys()) + sorted(RESIDUE_SHAPES)
    for shape in order:
        rows = [s for s in inv["sources"] if s["shape"] == shape]
        if not rows:
            continue
        L.append(f"### `{shape}`")
        for s in rows:
            mark = {
                "COVERED": "[covered]",
                "SPEC": "[SPEC]",
                "RESIDUE": "[residue — excluded]",
                "UNCOVERED_ROT": "[ROT — backend MISSING]",
            }.get(s["effective_status"], s["effective_status"])
            L.append(f"- {mark}  {s['name']}  (rung {s['rung']})")
        L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit the census + headline as JSON")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any declared-COVERED source's witness shape is not built (rot pin)")
    args = ap.parse_args(argv)

    # The report carries em-dashes / bullets; force UTF-8 so a cp1252 Windows
    # console doesn't crash the render (the same defensive move other scripts make).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    inv = gather()
    h = headline(inv)

    if args.check:
        rotten = [s["name"] for s in inv["sources"] if s["effective_status"] == "UNCOVERED_ROT"]
        if rotten:
            print("UNCOVERED_ROT — declared COVERED but witness backend missing:", file=sys.stderr)
            for n in rotten:
                print(f"  - {n}", file=sys.stderr)
            return 1
        return 0

    if args.json:
        print(json.dumps({"headline": h, "census": inv}, indent=2))
    else:
        print(render(inv, h))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
