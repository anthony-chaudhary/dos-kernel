# 360 — Tool-call error handling as a first-class primitive: `try / except / finally`, adjudicated

> **The idea (operator, 2026-06-16).** Python gives error handling a *shape*:
> `try` a block, `except` the failures you named, run `finally` no matter what.
> An agent calling tools has the same shape with none of the structure — it
> issues a call, the call may fail, and what happens next is improvised prose
> the agent narrates to itself. Make tool-call error handling a **first-class
> primitive in DOS**, the way `try/except/finally` is first-class in Python.

This doc works out what that primitive actually is once it meets DOS's one
non-negotiable rule — **the kernel believes nothing the agents say** — and finds
that the analogy survives, but inverts the authority. Python's `try/except` is a
construct the *interpreter executes*. DOS's is a contract the *kernel
adjudicates and refuses to let the agent self-certify*. No new execution
authority; only a typed verdict over which arm of a declared handler the
observed reality has entered.

The mechanism reuses the shipped POST-moment outcome classifier
([`tool_stream.classify_stream`](../src/dos/tool_stream.py), docs/145) and the
intent-ledger's claimed-vs-verified asymmetry
([docs/107](107_resumable-work-and-the-intent-ledger.md)); it adds one pure leaf
and one verb. Read
[docs/197](197_how-dos-is-directly-useful-to-ultracode.md) (witness routing) and
[docs/99](99_runtime-validation-and-the-actuation-boundary.md) (PDP-not-PEP)
before editing — they are the two walls this design has to stay inside.

## 1. The gap, stated precisely

DOS today has a rich surface for tool-call outcomes — and not one piece of it
*composes*:

| Piece | What it answers | Scope |
|---|---|---|
| `hook_exit.classify_exit` | a script exited N → PASS/WARN/BLOCK | one call's exit code |
| `breaker.record_failure` | this failure class keeps tripping → escalate | one named class, counters |
| `tool_stream.classify_stream` | did the result stream advance or stall? | one session's repeats |
| `pretool_sensor.decide` | may this proposed call proceed? | one call, pre |
| `posttool_sensor` | did the result repeat N times? | one call, post |
| `lane_journal` (`OP_ENFORCE`) | what intervention did a handler apply? | one decision, forensic |

Every row classifies **one call in isolation**, after the fact, as an
*advisory*. Stack them and you still cannot express the one thing a programmer
reaches for a hundred times a day:

> *"**Try** this tool. If it fails **with this kind of error**, do that instead.
> Whatever happens, **finally** release the lease and record the outcome."*

There is no entry kind, verdict, or config surface for a **handler structure** —
a declared relationship between a call, its anticipated failures, and the
cleanup that must run regardless. The agent improvises it in prose, and prose is
exactly the forgeable self-report DOS exists to distrust. An agent that says
*"the API call failed so I fell back to the cache and cleaned up"* is making
three claims — *it failed*, *I recovered*, *I cleaned up* — and DOS currently
checks none of them as a unit.

## 2. The crux: why a naive port is forbidden — and what that forces

The obvious move is to give DOS a `dos try` that runs the fallback. **That is
exactly the move the kernel cannot make**, and seeing why is the whole design.

Python's `except` branch works because the interpreter owns the call stack: it
*executes* the recovery. For DOS to execute a fallback, the kernel would have to
**author the bytes of a new tool call** — "now call `cache_read` instead." That
violates the byte-author invariant (docs/138): the kernel that mints a corrective
call is doing precisely what it distrusts in agents — narrating an effect into
being. A `try/except` the kernel runs would be a kernel that believes its own
recovery worked because it issued it. Self-certification, one layer up.

So the constraint is sharp and productive: **the recovery branch is authored by
the agent or the host; the kernel's sole job is to adjudicate which arm the
contract is in, and to refuse the agent the right to declare it.** This splits
the Python construct cleanly along DOS's existing mechanism/policy line:

| Python | Who runs it | DOS analogue | Who runs it | Kernel's role |
|---|---|---|---|---|
| evaluate `try:` block | interpreter | agent issues the tool call | agent | classify the *outcome* (not the attempt) |
| match `except E:` | interpreter | a failure of a *declared class* is observed | env produces the error bytes | **adjudicate** which class — from env-authored bytes |
| run `except` body | interpreter | agent/host issues the fallback call | agent/host | classify *that* call's outcome too |
| run `finally:` | interpreter | host runs cleanup (lease release, record) | host | **refuse the turn's close until cleanup is witnessed** |
| swallow vs re-raise | interpreter | escalate vs absorb | breaker/judge | the *existing* escalation ladder |

The kernel never enters the second column's "interpreter" rows. It owns only the
right-most column: **outcome classification at each boundary, and a refusal that
the `finally` ran.** That is a primitive DOS *can* hold without breaking its own
rule, because every byte it reads to decide the arm was authored by the
environment (the error result) or by git (the cleanup effect) — never by the
agent claiming success.

> **The one-line version.** `try/except/finally` in DOS is not control flow the
> kernel runs. It is a **typed contract the kernel adjudicates** — the agent
> declares the handler shape up front, and at each tool boundary the kernel says
> which arm reality is in and whether the mandatory arm (`finally`) has actually
> been witnessed. The agent proposes the recovery; the kernel disbelieves the
> claim that it worked.

## 3. The primitive: a `ToolHandler` contract and a `classify_arm` verdict

### 3.1 The declared shape (policy — data, not code)

A handler is declared *before* the `try` call, as data — the same way a lane
tree or a reason vocabulary is data. It names the call, the failure classes it
anticipates, and the cleanup that must run regardless:

```python
@dataclass(frozen=True)
class ToolHandler:
    """A declared try/except/finally contract over one tool call. PURE DATA."""
    try_tool: str                       # the tool the `try` arm issues
    except_classes: tuple[str, ...]     # anticipated failure classes (from the reason vocab)
    finally_effect: Optional[str] = ""  # the cleanup effect that must be witnessed (e.g. a lease RELEASE)
    on_unhandled: Escalation = Escalation.JUDGE   # a failure of NO declared class → escalate, never swallow
```

`except_classes` are drawn from the **closed reason vocabulary** (docs/78,
`reasons.py`) — so "the kinds of failure you may catch" is itself a typed,
verifiable set, not free text. You cannot `except` a failure you cannot name,
and you cannot name one outside the vocabulary. This is the load-bearing
difference from a bare try/except: Python lets you `except Exception` and swallow
anything; DOS forces every caught class to be one the kernel can independently
recognize from env-authored bytes.

### 3.2 The arm verdict (mechanism — pure classify)

At each tool boundary the kernel folds the observed outcome against the declared
handler and returns *which arm reality is in* — never executing the next arm,
only naming it:

```python
class Arm(str, enum.Enum):
    TRY_OK        = "TRY_OK"        # try call succeeded; except arm never enters; finally still owed
    EXCEPT_MATCHED = "EXCEPT_MATCHED"  # try failed with a DECLARED class; recovery is licensed
    UNHANDLED     = "UNHANDLED"     # try failed with an UNDECLARED class; escalate, do not recover
    FINALLY_OWED  = "FINALLY_OWED"  # turn cannot close: the declared finally_effect is not yet witnessed
    SETTLED       = "SETTLED"       # finally_effect witnessed in git/journal; the contract is discharged

@dataclass(frozen=True)
class ArmEvidence:
    """Everything classify_arm needs — gathered at the boundary, all env/git-authored."""
    try_result_class: Optional[str]      # the failure class of the try result, or None on success
                                         #   — from the ENV's error bytes, via the reason classifier
    finally_witnessed: Optional[bool]    # did the declared cleanup effect land in git/journal? None = unknown
    head_advanced: bool = False          # ground truth moved (informs SETTLED vs FINALLY_OWED)

@dataclass(frozen=True)
class ArmVerdict:
    arm: Arm
    reason: str                          # one-line operator summary
    licensed_recovery: bool              # may the host issue the except body? (true only on EXCEPT_MATCHED)
    evidence: ArmEvidence

def classify_arm(handler: ToolHandler, ev: ArmEvidence,
                 policy: ArmPolicy = DEFAULT_POLICY) -> ArmVerdict:
    """PURE — no I/O. Folds a declared handler against an observed outcome.

    Ladder, top to bottom:
      1. try_result_class is None                      -> TRY_OK   (then finally is owed)
      2. try_result_class in handler.except_classes    -> EXCEPT_MATCHED (licensed_recovery=True)
      3. try_result_class set, NOT declared            -> UNHANDLED (escalate per on_unhandled; NO license)
      4. contract reached its end, finally not witnessed-> FINALLY_OWED  (refuse turn close)
      5. finally_effect witnessed                      -> SETTLED
    """
```

Three properties make this DOS-shaped rather than a workflow engine bolted on:

1. **`try_result_class` is env-authored.** The arm is decided by *the error bytes
   the tool returned*, classified through the same reason vocabulary the rest of
   the kernel uses — never by the agent's prose claim that it failed or
   succeeded. This is the `tool_stream` honesty (docs/145 §5a) re-aimed: there it
   was "did the env return identical bytes?"; here it is "what *class* of failure
   did the env's bytes carry?" Both are pure questions about env output.

2. **`licensed_recovery` is the only thing the kernel grants — and it grants
   nothing more than permission.** It says *"a failure of a class you declared
   occurred, so issuing your declared fallback is in-contract."* It does **not**
   issue the fallback, name the fallback, or check the fallback succeeded as a
   separate matter (that is a fresh `classify_arm` over the recovery call). The
   kernel licenses; the host acts; the kernel then re-adjudicates. Authority
   never crosses the line.

3. **`FINALLY_OWED` is a refusal, not a log line — and it rides the one effect
   the actuation boundary already sanctions.** This is where the primitive earns
   "first-class." A bare advisory would note "you didn't clean up." The `finally`
   arm instead **gates the turn's close** through the existing `dos hook stop`
   discipline (the `dos-goal-gate` skill): the Stop is *refused* while the
   declared `finally_effect` (e.g. an `OP_RELEASE` on the held lease, or a
   recorded outcome) is unwitnessed in the journal.

   This stays inside the docs/99 §3.1 line, and that is not incidental — it is
   the whole reason the arm is legal. Refusing the agent's *own* Stop is
   **self-control-flow** (the agent declines to close its own turn), the exact
   in-bounds move `loop_decide` makes on `UNMEASURED_SHIPPED` and `dos-goal-gate`
   makes on a premature "done" — *not* the kernel killing a foreign process,
   which would demand the host-specific domain knowledge the kernel is defined
   not to have. The kernel only **records** the owed-cleanup decision on the WAL
   and **surfaces** it; it never *delivers* the cleanup (that `OP_RELEASE` is the
   host's act, the docs/99 row-3 driver behavior). `finally` in Python is a
   guarantee the runtime *runs*; `FINALLY_OWED` in DOS is the kernel *refusing to
   let the agent stop pretending the guarantee was met* — same guarantee,
   enforced from the distrust side, without the kernel crossing into execution.

### 3.3 What `UNHANDLED` buys — the bare-`except` defense

Python's most common error-handling bug is `except Exception: pass` — swallowing
a failure class you never anticipated. **Within a declared handler**, `UNHANDLED`
makes that move unexpressible: a failure whose class is not in `except_classes`
cannot be silently absorbed *under the contract* — it routes to `on_unhandled`
(default `JUDGE`, the advisory human-in-the-loop rung). The agent can write prose
claiming it handled the error, but the kernel, reading the env-authored class
against the declared set, returns `UNHANDLED` and escalates regardless. *Within
the contract* you cannot catch what you did not name, and you cannot pretend you
named it.

Note the scope carefully, because it is the line the doc must not cross: this
**structures recovery for the calls a host routes through a handler**; it does
**not** *prevent* an agent from skipping the handler entirely and improvising
unstructured recovery as before (see §5, hole 3). The value is that *opted-in*
recovery becomes typed and witnessed — not that all recovery is forced through
the type. That weaker, true claim is the honest one.

## 4. The seam — where it plugs in (no new boundary)

This adds **zero** new hook surface. It folds the existing boundaries:

- **`try` call** → the agent's tool call, observed at `posttool_sensor`. The
  result digest already computed there carries (or is classified into) the
  `try_result_class`. One new step: run the reason classifier over the error
  bytes to get the class, exactly as `tool_stream` runs its digest comparison.
- **`except` license** → surfaced as `additionalContext` at POST (advisory,
  docs/99): *"this failure matched your declared `disk_full` class; the fallback
  you declared is in-contract."* The host, not the kernel, issues it.
- **`finally` gate** → `dos hook stop` consults `classify_arm`; a `FINALLY_OWED`
  refuses the Stop with the typed reason and the unblock action (*run the
  declared cleanup*). This is the same machinery `dos-goal-gate` already uses to
  refuse a premature "done."
- **the durable record** → one new lane-journal op, `OP_HANDLER`, recording
  `(handler_id, arm, try_result_class, finally_witnessed)` — the forensic twin of
  `OP_ENFORCE`. The contract's life is replayable; a crashed run mid-`try`
  resumes against its declared `finally` the same way `resume.py` resumes against
  verified steps.

```
   declare ToolHandler (policy, data)
        │
        ▼
   agent issues try_tool ──► posttool_sensor ──► classify_arm
        │                         (env bytes →        │
        │                          try_result_class)  ├─ TRY_OK ........... finally owed
        │                                             ├─ EXCEPT_MATCHED ... license recovery (host acts)
        │                                             └─ UNHANDLED ........ escalate (JUDGE), no swallow
        ▼
   dos hook stop ──► classify_arm ──► FINALLY_OWED? ── refuse turn close until cleanup witnessed
                                          │
                                          └─ SETTLED ── contract discharged, OP_HANDLER recorded
```

## 5. The honest holes (named, not buried)

The `tool_stream` doc names its eventual-consistency hole out loud; this design
has three, and they bound the claim precisely:

1. **DOS cannot guarantee the `finally` *ran*, only that its effect is
   *witnessed*.** If the declared cleanup is a git/journal effect (lease release,
   recorded outcome), the kernel can confirm it landed. If it is an *outside-the-
   envelope* effect (an external API rollback, a remote unlock), DOS has no Undo
   and the witness is at-best the host's own claim — the same docs/342 boundary
   `resume` lives behind. **Honest scope: `finally_effect` must name a
   git/journal-witnessable effect to be *enforced*; an external one can be
   *declared and surfaced* but not *guaranteed*.** We do not pretend otherwise.

2. **The kernel cannot classify a failure the env didn't make legible.** If a
   tool returns `exit 0` with a corrupt payload and no error bytes, there is no
   env-authored class to read — `try_result_class` is `None` and the arm is
   `TRY_OK`. This is correct distrust direction (we never *invent* a failure),
   but it means the primitive is only as good as the env's error signalling. It
   catches *named, signalled* failures, not silent corruption. (Silent
   corruption is `commit-audit`/`coverage` territory, downstream.)

3. **The primitive is opt-in, and opting out is always available.** An agent
   that declares *no handler* is back to unstructured recovery — the kernel has
   no handler to adjudicate, so it falls through to the per-call advisories of §1
   and nothing new bites. Likewise a host that gets `licensed_recovery` and
   improvises a *different* fallback than it declared is outside the contract.
   This is the same shape as docs/99's advisory-only floor: the POST advisory
   cannot cut the turn, and the kernel cannot force a call through a handler it
   was never given. So state the value at its true strength and no higher: the
   primitive **structures and witnesses recovery for the calls a host routes
   through it** — it does not, and given the actuation boundary cannot, *force*
   every recovery to be typed.

   The one arm that bites without the host's cooperation is `FINALLY_OWED`, and
   only *because a handler was declared* — it rides the Stop gate (§3.1
   self-control-flow), which a DOS host honors, so a declared `finally` cannot be
   silently skipped *for that handler*. An agent can still dodge it two ways, both
   honest limits: declare a **no-op `finally_effect`** (then there is nothing to
   witness — but the no-op is now *on the record* as the declared cleanup, which
   is itself legible), or **declare no handler** (then there was never a `finally`
   to owe). So the guarantee ladder, stated precisely: for a **declared**
   handler, `finally` is *enforced* (turn-close refused until its
   git/journal-witnessable effect lands), `except` is *licensed-and-advised*, and
   `try` is *classified*. Outside a declared handler, none of this applies — by
   design, not by oversight.

## 6. Why this is a primitive and not a workflow engine

A workflow/saga engine *owns execution*: it runs the retry, runs the
compensation, drives the state machine. That is the PEP role DOS refuses
(docs/99). The repo's own SOTA survey (docs/180 §1e) names the field precisely:
**SagaLLM** (arXiv 2503.11951) is "Saga + compensation, the dominant academic
pattern — author-and-roll-back, *not* DOS's replay/constrain-unforgeable-bytes,"
and **Temporal / DBOS Transact** are the durable-execution products crossing into
the early majority — runtimes you hand your control flow to. Every one of them is
a PEP. DOS's `try/except/finally` is the PDP inverse, the same line this repo
already draws between `arbitrate` (a pure admission verdict) and a lock *manager*
that takes the lock:

| Saga/workflow engine | DOS `try/except/finally` |
|---|---|
| executes the recovery branch | classifies which branch reality is in |
| owns the retry/compensation loop | licenses recovery; the host loops |
| guarantees `finally` by running it | refuses turn-close until `finally` is *witnessed* |
| trusts its own orchestration succeeded | disbelieves the claim; re-adjudicates each arm from env/git bytes |
| a runtime you hand your control flow to | a substrate that keeps your control flow honest |

The same domain-free, believe-nothing core, re-aimed at the one shape of agent
behavior it had observed-in-pieces but never held as a unit: **the structured
relationship between a call, its failures, and its cleanup.** It composes the six
isolated classifiers of §1 into one typed contract — without the kernel ever
issuing a byte the agent didn't author, and without believing a word the agent
says about whether it recovered.

## 7. Shippable slice (smallest honest first step)

1. `src/dos/tool_handler.py` — `ToolHandler`, `Arm`, `ArmEvidence`, `ArmVerdict`,
   `classify_arm`. Pure leaf, frozen-fixture testable with zero env access (the
   `tool_stream` keystone). Imports stdlib + `reasons` + `breaker.Escalation`
   only — kernel layer, no driver, no host name.
2. `dos handler classify --handler <id> --result-class <c> [--finally-witnessed]`
   — the verdict at the CLI boundary; the I/O (reading the error class, checking
   the journal for the finally effect) happens in the shell, `classify_arm` stays
   pure.
3. `OP_HANDLER` in `lane_journal.py` (forensic op, non-state-mutating, mirrors
   `OP_ENFORCE`) + a `dos hook stop` consult of `FINALLY_OWED`.
4. The litmus tests, unchanged in spirit: it imports no host (the handler names a
   *tool*, never a host lane); `classify_arm` needs no plan; the verdict is a
   frozen dataclass with a `reason`; the recovery is never authored by the
   kernel. If any of those fails, the design has drifted back across the PEP line.

> **Status:** design only. This is the argument and the typed surface, not the
> code. The next step is the §7.1 leaf + its frozen-fixture tests; nothing here
> is `dos verify`-shipped yet.
