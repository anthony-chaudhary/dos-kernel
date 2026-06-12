# 305 — the guardrail-seat pair: dos verdicts at the OpenAI Agents SDK and CrewAI output seams

> Two agent frameworks give a task's final output a typed checkpoint, and both
> checkpoints are seats for an env-evidence verdict. The OpenAI Agents SDK runs
> `output_guardrails` over an agent's final output and HALTS the run when one
> trips (`OutputGuardrailTripwireTriggered`). CrewAI runs a task `guardrail`
> callable over each task's output and RETRIES the task when it fails. This
> plan ships one vendor-free effect gate plus two thin adapters that put the
> same verdict in both seats: when a finished task's declared deliverable (a
> commit, a file, a shipped phase) is ABSENT from a read-back the agent did not
> author, the OpenAI seat trips the tripwire and the CrewAI seat sends the task
> back — instead of the framework believing the narration. The docs/302 adapter
> shape, repeated twice: the drivers speak THEIR contracts; nothing upstream
> imports dos-kernel; layer-4, never kernel.

*Status: SHIPPED 2026-06-12 — all four phases oracle-verified (P1 `51bfd9e`,
P2 `4e1b881`, P3 `d536112`, P4 `44a7732`; `dos verify` answers SHIPPED via
grep-subject for each). Closed issues
[#56](https://github.com/anthony-chaudhary/dos-kernel/issues/56) (OpenAI
Agents SDK) and [#57](https://github.com/anthony-chaudhary/dos-kernel/issues/57)
(CrewAI); the deferred upstream-listing move is
[#77](https://github.com/anthony-chaudhary/dos-kernel/issues/77), gated on the
next release tag. The P2 integration slice was verified against the real
published SDK in a scratch venv (32 tests + recipe 7, green). Sibling of
docs/302 (AGT backend) and the docs/230/234 reward family; the
`examples/fleet_frameworks/` recipes 2 and 4 are the copy-paste ancestors —
context-keyed and oracle-only, where these drivers are shipped code with the
full claim model.*

## 0. The two contracts, pinned

OpenAI Agents SDK (PyPI `openai-agents`, imports as `agents`; contract read
2026-06-12 from openai.github.io/openai-agents-python/guardrails/):

- An output guardrail is a function `(ctx: RunContextWrapper, agent: Agent,
  output) -> GuardrailFunctionOutput`, sync or async, attached via
  `Agent(output_guardrails=[...])`.
- `GuardrailFunctionOutput(output_info=..., tripwire_triggered=...)` — a
  tripped tripwire raises `OutputGuardrailTripwireTriggered` out of
  `Runner.run(...)`; the caller catches it and re-dispatches instead of
  consuming the claim.

CrewAI (PyPI `crewai`; contract read 2026-06-12 from
docs.crewai.com/concepts/tasks):

- A task guardrail is a callable `(result: TaskOutput) -> tuple[bool, Any]`,
  attached via `Task(guardrail=...)` (or `guardrails=[...]`, run in sequence).
- `(True, value)` passes `value` onward; `(False, reason)` sends `reason` back
  to the agent and re-runs the task, up to `guardrail_max_retries` (default 3).
- The return contract is a plain tuple and the input only needs `.raw` — so
  the CrewAI adapter imports NOTHING from crewai, ever. The OpenAI adapter
  needs the SDK's two symbols, imported lazily with a loud install hint
  (you cannot use an Agents-SDK guardrail without the SDK installed, so
  missing-SDK is an error there, not an abstain).

## 1. The shared core — `dos.drivers._effect_gate`

One vendor-free module both adapters wrap. It is the boundary-I/O half of the
kernel's `effect_witness` keystone: gather read-backs, then let the PURE
`witness_effect` join decide.

**The claim model — declared, never parsed.** The kernel never parses prose,
so the gate does not either. A host DECLARES what the task is supposed to
produce, at gate construction:

- `CommitClaim(baseline=None)` — "new commit(s) landed beyond `baseline`".
  `baseline=None` → the gate captures `HEAD` at construction (build the
  guardrail before the run starts; anything the run lands is then visible).
  Read-back: `git rev-list --count baseline..HEAD` under the workspace root.
- `FileClaim(path, non_empty=False)` — "this file exists (and has bytes)".
  Read-back: a stat under the workspace root.
- `ShippedClaim(plan, phase)` — "this (plan, phase) shipped". Read-back: the
  ship oracle (`dos.oracle.is_shipped`), which needs no plan registry — the
  `verify`-needs-no-plan litmus holds through the adapter.

A host that DOES want to read claims out of the output text injects
`extract: (text) -> Sequence[Claim]` — its parser, its policy, never ours.
Declared claims and extracted claims combine; both flow through the same join.

**Completion is the claim.** A finished agent run / crew task asserting its
final output IS the completion claim — the gate does not hunt for the word
"done". A host whose agents honestly report failure injects `extract` (or an
empty `expect`) to narrow this. Documented on both factories.

**The verdict.** Per claim, the gate builds the `EffectClaim` + one read-back
`EvidenceFacts` (`attest` / `refute` / `no_signal`; accountability
`OS_RECORDED` — git's object database and the filesystem, read by the gate
process, not narrated by the agent) and folds through `witness_effect`. The
gate verdict over the rows:

| Rows | `GateVerdict.outcome` | OpenAI seat | CrewAI seat |
|---|---|---|---|
| any REFUTED | `TRIPPED` | tripwire fires | `(False, reason)` → retry |
| all CONFIRMED | `CLEAR` | no trip | `(True, result)` |
| no claims at all | `NO_CLAIM` | no trip | `(True, result)` |
| otherwise (some UNWITNESSED) | `ABSTAINED` | no trip | `(True, result)` |

Fail-to-abstain, twice over (the judges discipline, docs/86): a gate that
CRASHES — extractor raise, git unreachable — abstains with the failure named
in the verdict, never fabricates a trip, and never raises out of the seat
(an exception escaping a guardrail would fail the run on gate error, not on
evidence). And an abstain is never SILENT: every verdict, including the
abstain, rides `output_info` (OpenAI) so the consumer can see the gate looked
and could not tell. The trip direction is the safe one: only a REFUTED —
an accountable read-back that says the claimed effect is ABSENT — trips.

## 2. The two adapters

- `dos.drivers.openai_agents_guardrail.dos_output_guardrail(workspace=None, *,
  config=None, expect=(), extract=None, name="dos-effect-gate")` → the SDK's
  `OutputGuardrail`. Lazy `from agents import ...` inside the factory; absent
  SDK → `ImportError` with the install hint. The verdict-to-
  `GuardrailFunctionOutput` mapping is a module-level pure function so the
  no-SDK tests pin it with a structural stub.
- `dos.drivers.crewai_guardrail.dos_task_guardrail(workspace=None, *,
  config=None, expect=(), extract=None)` → a plain callable obeying CrewAI's
  tuple contract. Zero crewai imports — fully testable and runnable without
  the host package. On `TRIPPED` the False-reason is the typed, actionable
  feedback the retry loop hands the agent (what was claimed, what the witness
  saw, what would make it pass).

## Phase 1 — the shared gate + tests

`src/dos/drivers/_effect_gate.py` as §1; `tests/test_effect_gate.py` — the
claim kinds against real tmp git repos (over-claim → TRIPPED; actually-
committed → CLEAR; not-a-repo → ABSTAINED), the fold table, extractor
injection + extractor-crash → abstain, baseline auto-capture.

Done when: `python -m pytest -q tests/test_effect_gate.py` green with no
framework installed.

## Phase 2 — the OpenAI Agents SDK adapter (issue #56)

`src/dos/drivers/openai_agents_guardrail.py` as §2;
`tests/test_openai_agents_guardrail.py` — the mapping rows via a structural
stub (no SDK), the loud-ImportError path, and an integration slice (real
`Agent` + `OutputGuardrail.run`, the over-claim tripping through THEIR
machinery) skip-marked `pip install openai-agents`.

Done when: the suite is green without the SDK and the integration slice
passes with it.

## Phase 3 — the CrewAI adapter (issue #57)

`src/dos/drivers/crewai_guardrail.py` as §2;
`tests/test_crewai_guardrail.py` — the tuple contract end-to-end with a
duck-typed TaskOutput (no crewai needed at all): over-claim → `(False,
reason)` naming the absent effect; commit-then-pass → `(True, result)`
identity; crash-to-abstain → `(True, result)`.

Done when: green with no crewai installed.

## Phase 4 — the examples + the surface row

- `examples/fleet_frameworks/openai_agents_effect_gate.py` — recipe-4 style
  (no LLM, no key: invoke the guardrail directly both ways) but through the
  shipped driver and a `CommitClaim` over-claim in a demo repo.
- `examples/fleet_frameworks/crewai_task_guardrail.py` — the same over-claim
  caught, with zero crewai installed (the tuple contract needs none); real
  `Task(guardrail=...)` wiring shown in the docstring.
- `examples/fleet_frameworks/README.md` rows + the main README's
  fleet-frameworks pointer if its wording names the recipe count.

Done when: both example scripts run green from a fresh clone with only
`dos-kernel` installed (the OpenAI one skips itself politely without the
SDK), and the suite stays green. Issues #56/#57 close on this phase's
commit landing (the drivers + examples are their done-conditions).

## Out of scope (deliberately)

- **Upstream listing PRs** (an entry in either framework's docs/examples
  index) — after the next release tag carries the drivers, so the listings
  pin a pip-installable version. That is the distribution payoff move; it
  gets its own tracking when the release exists.
- **LangChain / MS-AF middleware (#50), NeMo rails action (#51), Braintrust
  scorer (#48), ADK plugin (#44)** — siblings, own plans.
- **Kernel changes of any kind.** `witness_effect`, `evidence`, and the
  oracle are consumed as shipped; if a gap turns up it becomes its own
  `docs/NN`, never a side-edit here.
- **Prose claim-mining in the drivers.** The extractor stays host-injected;
  shipping a default English-language claim parser would put policy (and a
  forgeable surface) inside the gate.
