# docs/373 — pre-flight mechanistic verification: the cheapest rung that refutes

> **Status:** 📋 planned — buildable design, no code yet. The kernel leaf of
> [docs/372](372_the-tool-call-as-syscall-the-adjudicated-call-layer.md). Proposes a
> pure `preflight(call) -> Verdict` core plus a `dos preflight` verb, modeled on the
> existing refusal-vocabulary surface (`dos_refuse_reasons` / `dos_check_reason`).
>
> Origin: operator thread 2026-06-16 — *"more understanding of when deterministic or
> mechanistic verification can be used before a tool call is fired … instead of directly
> calling pytest it calls like a linter first to see if the pytest command itself is
> going to run properly so that it's confident it will run."*

## The gap

DOS catches a false claim *after* the work (`dos verify` reads git). It does not yet
help an agent avoid *spending a doomed tool call in the first place*. There is a whole
class of failures that are knowable mechanically and cheaply — a command that won't
parse, a binary not on PATH, a test file that won't import, an `apply` that the server
would reject — and today the agent learns them the expensive way: it fires the call,
pays the round-trip, and the failure output lands in context.

That last part is the real cost. The wall-clock of a failed call is recoverable; the
**context pollution is not.** A 40-line stack trace or a wrong file's contents, once in
the window, is reasoned around for the rest of the task and paid for on every subsequent
forward pass.

## The thesis

> Before a tool call is allowed to execute, run the cheapest mechanistic check that could
> *refute* it, in increasing cost order, and short-circuit the moment one refutes. A
> refuted call is one that never polluted context.

This is the refusal vocabulary pulled *forward* of the call: instead of explaining a
failure after, return a typed refusal before, when a deterministic check proves the call
would fail.

## The ladder

```
proposed tool call
  ├─ rung 0  static:  command parses? binary on PATH? args type-check against the tool schema?   (µs)
  ├─ rung 1  dry-run: --collect-only / --dry-run / linter / type-check / --help                   (ms)
  ├─ rung 2  probe:   run on a tiny read-only fixture, no network                                 (10s ms)
  └─ rung 3  fire for real                                                                         (full cost + pollution)
```

Each rung is strictly cheaper than the next and may **deny without spending the call**.
Worked mappings:

| Tool call | Cheaper rung that can refute it first |
|---|---|
| `pytest …` | `python -m pytest --collect-only` — do the imports even resolve? |
| `git push` | `git push --dry-run` |
| `kubectl apply -f x` | `kubectl apply --dry-run=server -f x` |
| any code-edit tool | run the linter / type-checker on the proposed edit before committing it |
| `curl https://…` | DNS + HEAD before the full body |
| a shell one-liner | parse with the shell's `-n` (noexec) before running |

The point is general, not the specific checks: **most failed tool calls are knowable
cheaply, and the cheapest check that refutes is worth far more than its cost because the
thing it saves is context, not seconds.**

## What ships (proposed)

| Piece | Home |
|---|---|
| Pure core `preflight(call, rung_budget) -> Verdict{PASS, REFUTE(reason), ABSTAIN}` | `src/dos/preflight.py` |
| A rung registry — per-tool cheapest-refuter, declared as data | a `[preflight]` table in `dos.toml` (policy is data; mechanism is the kernel) |
| CLI verb `dos preflight <tool> <args…>` returning a typed exit code | `dos preflight` in `src/dos/cli.py` |
| Reuse of the closed reason vocabulary for the refute case | `dos_refuse_reasons` |
| Tests | `tests/test_preflight.py` |

The verdict is **fail-to-abstain**: if no rung can cheaply refute, `preflight` returns
ABSTAIN and the call proceeds. It can only *refuse more*, never *force* a call — the same
refuse-more-only floor as the overlap policy and the freshness tie-breaker
([docs/254](254_the-freshness-sort-key-prefer-new-work-over-churn.md)).

## The RSI signal this generates

Every time a call passes rung *k* and then fails at rung *k+1* (or at rung 3), that is a
labeled example: *a cheaper rung should have caught this.* Folded back, it tightens the
ladder over time — which is exactly the kind of metric the keep-or-revert loop
([docs/280](280_the-self-improving-work-loop-the-kernel-adjudicates-its-own-improvement.md)
family / `dos-self-improve`) consumes. The
ladder earns precision from its own misses.

## Litmus

- The kernel core names no host — no module outside `drivers/` references `pytest`,
  `kubectl`, or any specific tool; those live in the `dos.toml [preflight]` table as data.
- `preflight` can only return PASS / REFUTE / ABSTAIN; it never *executes* the real call
  (rung 3 is the host's to fire after a PASS/ABSTAIN).
- Fail-to-abstain: an unknown tool with no declared refuter is ABSTAIN, never REFUTE.

## Done condition (proposed)

- `dos preflight pytest --collect-only-fixture` REFUTEs a known-unimportable target with a
  typed reason and exit code, and ABSTAINs on a healthy one — pinned by `tests/test_preflight.py`.
- The reason returned is drawn from the closed vocabulary (`dos_check_reason` accepts it).
- A measured pollution-avoided count: on a fixture corpus of doomed calls, the fraction
  refuted at rung 0–2 vs reaching rung 3 (the "pre-flight catch rate" KPI).

## Related

- [docs/372](372_the-tool-call-as-syscall-the-adjudicated-call-layer.md) — the syscall framing this is the pre-fault check of.
- [docs/254](254_the-freshness-sort-key-prefer-new-work-over-churn.md) — the same refuse-more-only / fail-open discipline on the ordering side.
- The `job` repo already ships a preflight scout (`scripts/dispatch_loop_preflight.py`) at the *dispatch* layer; this is the same instinct one level down, at the *tool-call* layer.
- Private: `dispatch-os-tool-call-is-a-syscall.md` §3.
