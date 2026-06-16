# Playbook 07 — don't believe a dead subagent

> **The fold site is the one place an orchestrator trusts a worker's own bytes.**
> `dos verify-result` is the witness that reads a *different* byte-author.

An agent fan-out — an ultracode `Workflow`, an Agent-SDK app, any
spawn-many-and-collect harness — folds each subagent's return string back into the
parent as a finding. But a large fraction of real subagents return a
**harness-authored error** (a 429 / quota / auth / server string) that the agent's
own model never wrote: the runtime synthesized it when the worker died. That string
survives `.filter(Boolean)`, reads as plausible prose, and gets banked as a finished
result. On a measured corpus of 2,305 real subagents, **~32% returned exactly that**
(see [docs/197](../../docs/197_how-dos-is-directly-useful-to-ultracode.md)).

`dos verify-result` reads the transcript's terminal record and refuses to believe a
harness-authored death. It gates on `message.model == "<synthetic>"` — the marker
the agent's own model *cannot forge*, because the runtime harness, not the worker,
authored those bytes.

```text
OPEN LOOP  (folds a harness-authored death as a finding)

   subagent ──▶ ${result} string ──▶ .filter(Boolean) ──▶ banked as a FINDING
                     ▲ may be a harness 429 / quota / auth error
                       (message.model == "<synthetic>")

CLOSED LOOP  (verify-result gates the fold)

   subagent ──▶ transcript.jsonl ──▶ [ dos verify-result ]
                                            │
                        exit 0 HEALTHY      │      exit 3 DEAD
                              ▼             │            ▼
                     fold as a FINDING      │   route to a DEAD bucket
                                            │   (count it; never fold)

   The agent's own model cannot forge model == "<synthetic>" — that marker is
   authored by the runtime harness, which is exactly why it is trustworthy.
```

## The two branches, run

A worker *said* it finished; `verify-result` reads its terminal transcript record.
A harness-synthesized death is caught — **exit 3**:

```bash
dos verify-result --transcript dead.jsonl
#   DEAD SYNTHETIC class=OTHER — harness-authored terminal
#   (model=<synthetic> + stop_reason=stop_sequence) — the result is a OTHER error
#   string, not a finding; route to DEAD and do not fold
echo $?   # → 3   (count it in the denominator; never bank it as a result)
```

A real result from the same fan-out passes straight through — **exit 0**:

```bash
dos verify-result --transcript real.jsonl
#   HEALTHY — terminal assistant record is real-model authored with content
echo $?   # → 0
```

> **`0` also covers UNREADABLE** — a read fault never *fabricates* a death. The
> fail-safe floor is "when in doubt, don't declare the worker dead," so a missing or
> malformed transcript is HEALTHY-by-default (exit 0), not DEAD. Only a record the
> harness positively marked synthetic returns 3.

## Wire it into the fold

So a death is never banked as a finding, gate the fold on the exit code:

```bash
for t in run-*.jsonl; do
  if dos verify-result --transcript "$t"; then
    fold "$t"                       # exit 0 → a real result; fold it
  else
    echo "DEAD: $t" >> deadletter   # exit 3 → route to the dead-letter bucket
  fi
done
```

For a machine-readable verdict to branch on in code, `--json` emits the full object
the host folds on — `{state, dead, class, api_status, reason, envelope}`:

```bash
dos verify-result --transcript dead.jsonl --json
#   {"state": "SYNTHETIC", "dead": true, "class": "OTHER", "api_status": null,
#    "reason": "harness-authored terminal (model=<synthetic> + stop_reason=...) ...",
#    "envelope": {"verdict": "WEDGE", "reason_class": "RESULT_DEAD_OTHER", ...}}
```

## Why this is sound, not just a heuristic

`verify-result` is a *witness*, not a judge: it never reads the worker's claim about
whether it succeeded. It reads who **authored the terminal bytes**. `model ==
"<synthetic>"` is set by the agent-runtime harness — the same harness the worker
runs *inside* — so the worker cannot author that marker about itself. That is the
byte-author invariant the whole kernel turns on
([docs/138](../../docs/138_what-is-truth-the-throughline.md)): the trustworthy
signal is always the byte the judged agent *could not have written*.

`verify-result` is **advisory** — it reports DEAD, it never re-runs a worker. The
re-dispatch decision is yours; the verdict just makes sure a corpse is never counted
as a result.

---

*Next:* the verdict is one of ~40 the kernel exposes — see the
[verb cheat sheet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/guide/cli-reference.md#the-verbs-by-the-question-they-answer) for the rest,
and [playbook 06](06_debug-a-stuck-fleet.md) for the stuck-fleet troubleshooting map.
