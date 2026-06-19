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
| [`nemo_guardrails/`](nemo_guardrails/) | the effect-check rail — a NeMo Guardrails custom action that refuses a bot's "done" when the claimed effect is absent from repo evidence |
| [`hermes_integration/`](hermes_integration/) | the swarm-runtime worked example — the exec-capability gate and the arbiter as a swarm's missing lock manager, A/B-measured |
| [`opencode/`](opencode/) | the MCP wiring kit for opencode — `init_opencode.py` idempotently injects the `dos` MCP server into an opencode config and self-verifies; opencode is MCP-only (no hook surface), so this is the honest install path where `dos init --hooks` does not apply |
| [`serverless_rl/`](serverless_rl/) | `dos reward` as a deterministic client-side `weave.Scorer` / ART reward function — the witness-gated, non-distillable label in the W&B Serverless-RL signal path |
| [`braintrust_scorer/`](braintrust_scorer/) | `dos reward` as a Braintrust custom code scorer — ACCEPT/REJECT_POISON from a recorded or live read-back; abstains score `None`, never a silent 0 |
| [`kernel_keep_gate/`](kernel_keep_gate/) | `dos improve` as a non-forgeable keep bit for an LLM-generated kernel — honest-faster KEEP vs reference-copy / tolerance-exploit / harness-edit REVERT, with the #35 harness-tamper floor as a tree check |
| [`dos_ext/`](dos_ext/) | a complete extension package occupying the plugin seams (judge, overlap policy, predicate, renderer) — the hackability surface as working code |
| [`drivers/`](drivers/) | a minimal host policy pack — what a layer-4 driver looks like |
| [`plans/`](plans/) | an example phased-plan doc in the dialect the plan harvester parses |
| [`residual_review/`](residual_review/) | the next-generation diff — project `commit-audit` per-commit so review attention sorts to the **residual** (the claims the kernel could not witness), spends ~0 on the witnessed set, and re-adds an advisory semantic lens |
