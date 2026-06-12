# Examples

Every subdirectory here is runnable or copy-pasteable against the shipped
`dos-kernel` package. Start with `playbooks/` if you want prose, `demo/` if
you want to run something in the next minute.

| Directory | What it shows |
|---|---|
| [`demo/`](demo/) | the 60-second caught-lie demo as scripts + the plugin smoke checks — the fastest "see it work" path |
| [`playbooks/`](playbooks/) | numbered walkthroughs (solo dev → fleet → CI) plus the Python-API / CI-MCP / fleet-framework cookbooks |
| [`workspaces/`](workspaces/) | ready-made `dos.toml` workspaces — `cd` in and run `dos` against a realistic lane taxonomy |
| [`fleet_frameworks/`](fleet_frameworks/) | suite-pinned recipes wiring DOS into LangGraph, CrewAI, AutoGen, and the OpenAI/Claude Agents SDK |
| [`hermes_integration/`](hermes_integration/) | the swarm-runtime worked example — the exec-capability gate and the arbiter as a swarm's missing lock manager, A/B-measured |
| [`serverless_rl/`](serverless_rl/) | `dos reward` as a deterministic client-side `weave.Scorer` / ART reward function — the witness-gated, non-distillable label in the W&B Serverless-RL signal path |
| [`dos_ext/`](dos_ext/) | a complete extension package occupying the plugin seams (judge, overlap policy, predicate, renderer) — the hackability surface as working code |
| [`drivers/`](drivers/) | a minimal host policy pack — what a layer-4 driver looks like |
| [`plans/`](plans/) | an example phased-plan doc in the dialect the plan harvester parses |
