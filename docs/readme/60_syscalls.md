<a id="the-verbs-by-the-question-they-answer"></a>

## The syscall ABI

Every syscall answers a question you'd otherwise have to *take the agent's word
for*. "Reach for this when…" is the plain-English trigger; the rest is the
contract — and the module names are auditable.

| Syscall | Reach for this when… | What it is | Module |
|---|---|---|---|
| `verify()` | an agent says a unit of work is **done** and you don't want to take its word | the **truth syscall** — "did (plan, phase) actually ship?" registry-first, ancestry-checked, from git history if there's no plan at all | `dos.oracle`, `dos.phase_shipped` |
| `liveness()` | a long run says it's **"making progress"** and you want to know if it actually is | the **temporal verdict** — "is the run ADVANCING, or just SPINNING / STALLED?" from the git/journal delta and the clock | `dos.liveness` |
| `verify-result()` | a **subagent hands a result back** to an orchestrator that folds it as a finding — but the result string may be a harness-synthesized error the worker never authored | the **fold-site result-state witness** ([docs/197](docs/197_how-dos-is-directly-useful-to-ultracode.md)) — classifies a subagent transcript's terminal record, gating on `message.model == "<synthetic>"` (the unforgeable harness-authorship marker), never the agent's self-report; **exit 3 = DEAD** (a harness 429 / quota / auth / server error), `0` = HEALTHY / UNREADABLE | `dos.result_state` |
| `resume()` | a run **died or paused mid-flight** and you need to continue without re-doing work or double-applying it | the **third ARIES phase** — "how far did the *fossils* say it got, and what's the residual?" over a run-id-keyed intent ledger; re-enters from a git-VERIFIED SHA, never the dead run's self-report (RESUMABLE / COMPLETE / DIVERGED / UNRESUMABLE) | `dos.resume`, `dos.intent_ledger` |
| `complete()` | you need to know if the **whole declared job** is verifiably done, not just one phase | the **completion verdict** — `residual = declared − verified`, asked forward; read-only, never self-certifies | `dos.completion` |
| `rewind()` | a run thrashed and you want to **excise the dead-end turns** without the kernel authoring a correction | the **conversation-rewind verdict** — replays the ledger for a minted checkpoint and PROPOSES the excision (never truncates; the host owns the transcript) | `dos.rewind` |
| `productivity()` | a long run is burning turns and you want to know if it's **still doing work, or fading** | the **loop-economics verdict** ([docs/218](docs/218_the-productivity-verdict-diminishing-returns-as-a-syscall.md)) — `classify(work-deltas) -> PRODUCTIVE / DIMINISHING / STALLED` over a trend of per-step work; pure, no I/O | `dos.productivity` |
| `efficiency()` | you want to know if the **tokens a run spent actually bought work** (a run can be productive yet burn 10× its work's worth) | the **token-effectiveness verdict** ([docs/263](docs/263_token-effectiveness-verdict-plan.md)) — `work / tokens -> EFFICIENT / COSTLY / WASTEFUL`; both counts are env-authored, so a run can't narrate its way to EFFICIENT | `dos.efficiency` |
| `improve()` | a **self-improving loop** proposes a change to its own code and you must decide **keep or revert** — without trusting the loop's own claim that it helped | the **keep-gate** ([docs/280](docs/280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md)) — `KEEP / REVERT / ESCALATE` from witnesses the candidate's author didn't write: the suite green on the candidate-only tree, the truth syscall clean, and a strictly-measured metric gain; a regression always REVERTs, a run of non-keeps ESCALATEs to a human | `dos.improve` |
| `reward()` | a fine-tune is about to **train on an agent's trajectory** and the "it worked" label came from the agent itself | the **reward-set admission verdict** ([docs/230](docs/230_the-lab-facing-twin-rlvr-admit-the-non-distillable-reward-label.md)) — `ACCEPT / REJECT_POISON / ABSTAIN` off a witness the agent authored zero bytes of, so no answer text can flip a reject to an accept (the non-distillable label) | `dos.reward` |
| `breaker()` | a **failure class keeps tripping** and you want to stop retrying and escalate | the **circuit-breaker primitive** ([docs/223](docs/223_the-circuit-breaker-primitive-failure-counting-as-mechanism.md)) — a pure two-counter state machine, `CLOSED / OPEN`, tripping on consecutive *or* total failures; an OPEN verdict names the escalation rung (none / judge / human) | `dos.breaker` |
| `hook_exit()` / `exec_capability()` | you wire a **plain shell hook** into a runtime, or need to know if a command **grants arbitrary code execution** | two classifier leaves the cheapest integrations consult — `hook_exit` maps an exit code to an intervention ([docs/226](docs/226_the-hook-exit-classifier-a-shell-scripts-exit-code-as-a-verdict.md): 0 pass / 2 BLOCK / other WARN), `exec_capability` classifies the *invoked program token* — never a substring — as `GRANTS_ARBITRARY_EXEC / BOUNDED` ([docs/224](docs/224_the-exec-capability-classifier-a-shape-not-a-word.md)) | `dos.hook_exit`, `dos.exec_capability` |
| `refuse(reason)` | you need to say **why** a pick was blocked in a way a machine can act on | **structured refusal** — a closed, declared reason vocabulary (`dos.reasons`, extensible per-workspace), every reason emittable, verifiable, and refusable | `dos.wedge_reason`, `dos.picker_oracle` |
| `lease()` / `arbitrate()` | two agents might **touch the same files** and you need to admit one without a collision | the **pure admission kernel** — `arbitrate(request, live_leases, config) -> decision`, state-in / decision-out, no I/O | `dos.arbiter` |
| `spawn()` / `reap()` | you need every run to carry a **traceable identity** and its effects to be replayable | the **correlation spine** (sortable, lineage-carrying run-ids) + the lease **write-ahead log** | `dos.run_id`, `dos.lane_journal` |
| `enumerate()` / `pickable()` / `cooldown()` / `reconcile()` | an unattended loop must know **is there anything pickable, why-not, have I tried it, and did the claim hold?** — without re-storming a known drain or believing a "done" the git can't confirm | the **picker substrate** ([docs/207](docs/207_dispatch-workflow-extraction-and-the-pickable-substrate-completion.md)) — `enumerate` is the phase-list producer (the `declared` set, never a silent empty); `pickable` the pre-dispatch gate (OFFERABLE / HELD(reason)); `cooldown` the anti-churn fold over pick-attempts (CLEAR / RECENTLY_ATTEMPTED); `reconcile` the quiet-completion join (VERIFIED / QUIET_INCOMPLETE / HONEST_OPEN, fail-closed on the claim) | `dos.enumerate`, `dos.pickable`, `dos.cooldown`, `dos.reconcile` |

> Three terms the table assumes: a **plan** (e.g. `AUTH`) groups **phases** —
> a phase is a named unit of work (e.g. `AUTH1`); a **lane** is a leased region of
> the file tree an agent works in. All are defined in the
> [quickstart](docs/QUICKSTART.md).

> **The newest catch — a result that *died*.** When a subagent hands a result
> back to an orchestrator that folds it as a finding (an ultracode `Workflow`, an
> Agent-SDK fan-out), the result string itself may be a **harness-synthesized
> error** the worker never authored — and ~32% of real subagents return exactly
> that (a 429 / quota / auth string) where the fold expects a finding ([docs/197](docs/197_how-dos-is-directly-useful-to-ultracode.md)).
> `verify-result` reads the transcript's terminal record and refuses to believe a
> harness-authored death:
>
> ```bash
> dos verify-result --transcript dead.jsonl
> #   DEAD SYNTHETIC class=OTHER — harness-authored terminal
> #   (model=<synthetic> + stop_reason=stop_sequence) — not a finding; route to DEAD, do not fold
> echo $?   # → 3   (count it in the denominator; never bank it as a result)
>
> dos verify-result --transcript real.jsonl
> #   HEALTHY — terminal assistant record is real-model authored with content
> echo $?   # → 0
> ```
>
> It gates on `message.model == "<synthetic>"` — the marker the agent's own model
> *cannot forge* (the runtime harness authored those bytes, not the worker) — which
> is broader than rate-limits alone: quota, auth, and server deaths are caught too.

Around these sit ~30 supporting kernel modules — the file-tree disjointness
algebra, the timeline reader, the gate/loop classifiers, the typed-verdict
contract, the JUDGE-rung seam. The full map is in **[CLAUDE.md](CLAUDE.md)**.
