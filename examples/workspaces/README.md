# Runnable example workspaces

Each subdirectory is a **self-contained DOS workspace** — a `dos.toml` plus a
tiny file tree — modeling one of the archetypes from the
[playbooks](../playbooks/README.md). They exist so you can run real `dos`
commands against a realistic lane taxonomy and stamp grammar without inventing
one.

| Workspace | Archetype | Playbook |
|---|---|---|
| [`acme-store/`](acme-store/) | polyglot web service (`api/` + `web/` + `infra/`) | [02](../playbooks/02_polyglot-web-service.md) |
| [`libkv/`](libkv/) | OSS library + docs (`src/` + `docs/` + `tests/`) | [03](../playbooks/03_oss-library-release.md) |
| [`riverflow/`](riverflow/) | data / ML pipeline (`ingest/` + `train/` + `serve/`) | [04](../playbooks/04_data-ml-pipeline.md) |
| [`gravel/`](gravel/) | infra / platform monorepo (`terraform/` + `k8s/`) | [05](../playbooks/05_infra-monorepo.md) |
| [`edu-rig/`](edu-rig/) | driver / ring-0 bring-up (`driver/` + `tests/` + one QEMU `edu` rig) | [08](../playbooks/08_driver-bringup-qemu-edu.md) |

## How to use one

```bash
cd examples/workspaces/acme-store
dos doctor --workspace .                 # this workspace's lanes + stamp grammar
dos man lane                             # list its lanes
dos arbitrate --lane web --kind cluster --leases '[]'   # may a loop take the web lane?
```

Every name in these (`acme-store`, `libkv`, `riverflow`, `gravel`, the series
ids like `AUTH`/`CART`) is **invented** — they evoke the *shape* of a familiar
real-world repo without being one. Swap in your own and the commands are
identical.

> These are *fixtures for reading and experimenting*, not git repos. `verify`'s
> git-history rung needs real commits, so the playbooks that demonstrate `verify`
> (e.g. [03](../playbooks/03_oss-library-release.md)) show you how to make a
> commit in your own repo and check it — the `dos.toml` here is the part you copy.

> **What a *plan* file looks like** — these workspaces ship a `dos.toml` but no
> plan doc. For a copyable example of the markdown `dos plan` reads (the
> `### N. PLAN PHASE —` grammar, the claim-vs-oracle divergence), see
> [`../plans/example-plan.md`](../plans/example-plan.md) and its
> [`README`](../plans/README.md).
