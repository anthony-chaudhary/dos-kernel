# How do I detect which model died across a fleet and reroute?

> Don't ask the fleet which model is down — read it from the transcripts the workers left behind: `pip install dos-kernel`, then `dos model-health --session <transcript>` names the dead model across every descendant and `dos model-reroute` proposes a sibling. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

When a model goes down mid-fleet, the failure is not in one place — it is smeared across descendant sessions (child, grandchild, deeper), each of which dies quietly on the same unavailable model. A worker's own "I couldn't reach the model" line is a self-report, and the run that died is the worst witness to why it died. So `dos model-health --session <transcript>` does not believe any worker: it folds the transcripts of all the descendants — bytes the dying workers did not author — and rolls the per-MODEL death signal up into one surface that names which model is down and how many units died on it.

That verdict deliberately stops at the diagnosis: it names no replacement and launches nothing, because the roster of live models is host policy, not kernel knowledge. `dos model-reroute` is the other half — a DRIVER on the advisory rung. It consumes the health verdict, picks a sibling from a roster you pass in, and PROPOSES the re-dispatch (one paste away), never spawning a worker. If every roster model is down, or the dead model was SUSPENDED by policy, it ESCALATEs to you instead of silently rerouting into another outage.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| `model-health` folds which MODEL died across all descendants | reads the transcripts of child → grandchild → … sessions, not a worker's status line | the descendant session transcripts (the dying workers did not author them) | [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) |
| Reroute is a DRIVER (advisory), proposes — never launches | emits a `RerouteProposal` (REROUTE / ESCALATE); calls no spawn, no `subprocess` | a host-supplied roster + the kernel's health verdict, joined in a pure function | [`src/dos/drivers/model_reroute.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/src/dos/drivers/model_reroute.py) |
| The consumer move when a model goes down mid-fleet | `dos model-health --session <transcript>` then `dos model-reroute --roster <alternates>` | the transcripts, read at the boundary, not the fleet's self-report | [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) |

There is no headline benchmark **J** for this mechanism — the win here is structural, not a measured count: the death signal is read from descendant transcripts, and the reroute proposal carries a command but never runs it. A SUSPENDED model ESCALATEs *before* any sibling is picked, so a policy pull surfaces to you instead of draining budget into a second down model.

## The one command

```bash
pip install dos-kernel        # the PyPI name is dos-kernel, never bare `dos`
dos model-health --session <transcript>
```

```text
model-health: model 'claude-fable-5' DOWN on 7 unit(s) across descendants
  child:run-a31f      DEAD  (model unreachable)
  grandchild:run-9c2  DEAD  (model unreachable)
  …
route AWAY from: claude-fable-5   (use `dos model-reroute --roster <alternates>` to propose a sibling)
```

`dos model-reroute --roster <alternates>` then folds that verdict against the roster you supply and prints a heal plan — `REROUTE claude-fable-5 → <sibling>` with the re-dispatch command, or `ESCALATE` when every roster model is down or the dead model was suspended by policy. It launches nothing; you (or a host driver) enact the paste.

## What this does — and does not — certify

`model-health` certifies **which model is down across the descendants**, read from their transcripts — not why it went down, and not that any particular replacement will succeed. `model-reroute` is **advisory and propose-only**: it carries the re-dispatch command, it never spawns the work, and it never silently routes past an unnamed or policy-suspended model. It is a DRIVER (the vendor/roster names live here, outside the kernel), so it produces a recommendation, not a kernel verdict. Whether the rerouted units actually ship is still the world-reading ship-verdict's job — `dos verify` is the one definition of "shipped" that survives the model swap.

## Sources / reproduce

- [`src/dos/drivers/model_reroute.py`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/src/dos/drivers/model_reroute.py) — the propose-only reroute driver (REROUTE / ESCALATE; launches nothing).
- [`docs/CLI.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/CLI.md) — the `dos model-health` verb's design notes.
- [`AGENTS.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/AGENTS.md) — "a model goes down mid-fleet" → the consumer move.
- [Multi-agent coordination without a central orchestrator](multi-agent-coordination-without-a-central-orchestrator.md) — how a fleet stays coherent without a single director.
- [How to detect an agent loop spinning without progress](how-to-detect-an-agent-loop-spinning-without-progress.md) — the temporal sibling: motion read from artifacts, not narration.
- [FAQ](../FAQ.md) — the short questions, answered.

## Also asked as

- How do I tell which model is down when a whole fleet of agents stalls?
- One model in my multi-agent run died — how do I find it and switch?
- Detect a model outage across child and grandchild sessions and reroute the work.
- My agents all failed at once — was it the model? how do I reroute them?
- Auto-heal a fleet when a frontier model goes offline mid-run.
- How do I reroute stranded agent units to a sibling model after an outage?
- Which model failed across my descendant sessions, and what do I route to?

> The kernel is the part that doesn't believe the agents.
