## Hacking it

DOS is built to be extended without forking the package — add your own block
reasons, gate verdicts, admission/safety predicates, output renderers (the
`dos.renderers` entry-point group), and your own judge for the JUDGE rung
(`dos.judges`, scored by `dos judge-eval`), all as *workspace policy*, not
package edits. The block-reason vocabulary is fully data-driven: declare a
reason in four lines of `dos.toml` and it becomes emittable, verifiable,
refusable, and `dos man wedge`-documented through the same kernel calls a
built-in uses. See **[docs/HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md)** for the seven
extension axes and the plugin model, and **[`examples/dos_ext/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/dos_ext)**
for a copy-me skeleton.

## Documentation

- **[docs/QUICKSTART.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/QUICKSTART.md)** — runnable 5-minute hello-world. Start here.
- **[docs/FAQ.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/FAQ.md)** — the arriving questions ("how do I
  verify an agent's claim?", "does it need an LLM?"), each answered in one
  self-contained block.
- **[docs/README.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/README.md)** — the docs index (guides vs. design notes
  vs. the dated build-journal; the numbers are chronology, not a reading order).
- **[docs/HACKING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/HACKING.md)** — extend DOS without forking it.
- **[CLAUDE.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/CLAUDE.md)** / **[CONTRIBUTING.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/CONTRIBUTING.md)** — the
  architecture contract and how to send a change.
- **[verify-action/](https://github.com/anthony-chaudhary/dos-kernel/blob/master/verify-action/README.md)** — the CI gate: a
  composite Action and a reusable workflow that run `dos commit-audit` on every
  PR and merge-queue group and post the verdict as the named **dos-verify**
  status check; make it required and GitHub enforces what the kernel decides
  (the *verified by DOS* badge above is this gate on the kernel's own repo).
- **[docs/releases/](https://github.com/anthony-chaudhary/dos-kernel/tree/master/docs/releases)** — per-version release notes (the changelog).
- **[The website](https://anthony-chaudhary.github.io/dos-kernel/)** — this page,
  condensed to one screen (good for sending to someone).

## Playbooks & examples

**[`examples/playbooks/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/playbooks)** walks the syscalls end-to-end on
anonymized real-world repo shapes — every command was run and its output pasted
back verbatim:

- **[Onboard a repo in 10 minutes](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/01_onboard-a-repo.md)** —
  `pip install` → first verified ship, on any repo.
- Four archetypes — a [polyglot web-service fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/02_polyglot-web-service.md)
  (concurrent lanes), an [OSS library release](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/03_oss-library-release.md)
  (the stamp grammar), a [data/ML pipeline](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/04_data-ml-pipeline.md)
  (liveness), an [infra monorepo](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/05_infra-monorepo.md) (refusals).
- [**Debug a stuck fleet** + FAQ](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md) —
  symptom → the one command that diagnoses it.
- Three cookbooks: [from Python](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-python-api.md),
  [CI / MCP integration](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-ci-integration.md), and
  [fleet frameworks](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/cookbook-fleet-frameworks.md) — LangGraph,
  CrewAI, AutoGen, the OpenAI/Claude Agents SDK — with every framework recipe also
  shipped as a runnable, suite-pinned file under
  [`examples/fleet_frameworks/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/fleet_frameworks).
- [**Wire DOS into a Hermes / OpenClaw swarm**](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/hermes_integration) —
  the offline, A/B-measured swarm-runtime example: the `exec-capability` gate
  refuses a prompt-injected command before it runs (real at a single agent), and
  the arbiter serves as the swarms' missing lock manager so the lost updates the
  runtime would silently incur drop to zero (value grows with fleet size; the
  honest K=1 falsifier is included). Both scoreboards read non-forgeable witnesses
  ([docs/278](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/278_integrating-dos-with-hermes-and-openclaw-the-missing-lock-manager-for-agent-swarms.md)).
- Runnable [`examples/workspaces/`](https://github.com/anthony-chaudhary/dos-kernel/tree/master/examples/workspaces) — `cd` in and run `dos`
  against a realistic lane taxonomy.

*Next level up — evaluating the claims themselves: [For researchers](#for-researchers).*
