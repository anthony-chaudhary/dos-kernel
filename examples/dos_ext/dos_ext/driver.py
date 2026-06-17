"""dos_ext.driver — an example host-policy DRIVER (the `dos.drivers` seam, Axis 1).

This is the copy-me demonstration that a THIRD PARTY can ship a whole host policy pack
in their OWN pip package, with no kernel fork. `acme_config(workspace)` is a factory of
the same shape `dos.drivers.workshop:workshop_config` has — a `LaneTaxonomy` bound to a
workspace root — but it lives OUTSIDE the `dos` package. It is registered under the
`dos.drivers` entry-point group in this package's `pyproject.toml`:

    [project.entry-points."dos.drivers"]
    acme = "dos_ext.driver:acme_config"

so `dos --driver acme --workspace .` resolves it BY NAME. In-tree packs (workshop/job)
resolve FIRST and are unshadowable; a third-party name like `acme` is found only after
the in-tree miss — exactly the built-in-first contract every DOS seam shares.

The `acme` shop: two concurrent build lanes (`mobile` ∩ `cloud`, provably tree-disjoint
so two agents run at once) + an exclusive `ship` lane + the catch-all exclusive `global`.
"""

from __future__ import annotations

from pathlib import Path

from dos.config import (
    LaneTaxonomy,
    PathLayout,
    SubstrateConfig,
    gather_workspace_facts,
    resolve_workspace_root,
)

# The acme shop's concurrency policy, as data. `mobile` ∩ `cloud` is tree-disjoint
# (`app/` vs `services/`), so the two build agents run concurrently; `ship`/`global`
# are exclusive so a release runs alone.
ACME_LANE_TAXONOMY = LaneTaxonomy(
    concurrent=("mobile", "cloud"),
    exclusive=("ship", "global"),
    autopick=("mobile", "cloud"),
    trees={
        "mobile": ("app/**/*", "ios/**/*", "android/**/*"),
        "cloud": ("services/**/*", "infra/**/*"),
        # whole-repo glob → honestly exclusive (a release touches everything).
        "ship": ("deploy/**/*", "**/VERSION"),
        "global": ("**/*",),
    },
    aliases={
        "app": "mobile", "ios": "mobile", "android": "mobile", "mobile": "mobile",
        "svc": "cloud", "services": "cloud", "infra": "cloud", "cloud": "cloud",
        "release": "ship", "deploy": "ship", "ship": "ship",
    },
)


def acme_config(workspace: Path | str | None = None) -> SubstrateConfig:
    """The acme host policy pack, pointed at ``workspace``.

    Mirrors `dos.drivers.workshop:workshop_config`: binds the taxonomy to the workspace
    root and gathers workspace facts so the SELF_MODIFY guard is workspace-scoped. (The
    kernel's `drivers_seam` resolver ALSO backfills facts if a factory forgets, but a
    well-formed pack gathers its own — this is the reference shape to copy.)
    """
    root = resolve_workspace_root(workspace)
    return SubstrateConfig(
        lanes=ACME_LANE_TAXONOMY,
        paths=PathLayout.for_root(root),
        workspace=gather_workspace_facts(root),
    )


__all__ = ["ACME_LANE_TAXONOMY", "acme_config"]
