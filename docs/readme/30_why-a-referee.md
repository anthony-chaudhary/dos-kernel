## Why not just run N agents?

Fair question — why add a referee at all? Because N agents with no referee is
that open loop again: you launch them, they self-report, and you've got nothing
solid to steer on. DOS hands you that missing signal. Specifically, it gives
you **sensors** —

- `verify` — did it really ship? (from git, not the agent's word)
- `liveness` — is it ADVANCING, or just SPINNING / STALLED?
- `scope-gate` — did it stay in its lane? A binding pre-effect gate
  (`dos scope-gate`, ALLOW/REFUSE, exit 0/5/6) over the same `dos.scope`
  classifier that also reports post-hoc.

— and **actuators**: `arbitrate` (let this lane in, or refuse the collision) and
`refuse` (say no with a reason a machine can act on). Together they turn a pile
of workers into something you can actually drive. The kernel's job is the
signal, but it also ships a reference supervisor to show what you do with it:
`dos watch` checks `liveness` on each tracked run and *proposes* a halt when one
spins or blows its budget — it recommends, it never pulls the trigger — and
`dos loop` keeps N dispatch-loops alive. Use those, or build your own on the
same signal. Either way, it's the difference between *"I launched 20 sessions
and I'm hoping"* and *"I can see which two are lying and which one is wedged."*

You see that signal through three read-only screens — `dos top` (what's
running), `dos decisions` (what's waiting on you), `dos plan` (claim vs. ground
truth) — covered in [Three live projections](#three-live-projections-read-only-tuis)
below and walked end-to-end in
**[Debug a stuck fleet](https://github.com/anthony-chaudhary/dos-kernel/blob/master/examples/playbooks/06_debug-a-stuck-fleet.md)**.

### "I could write this in 20 lines of bash"

You could — and that instinct is correct. The core of `dos verify` really is
"grep git history for a stamp." DOS is that script taken seriously across the
six places the 20-line version quietly breaks:

1. **The stamp grammar is forgeable** unless it's a *closed, declared*
   vocabulary the agent can't widen on the fly — a bare grep believes any
   string the agent learns to print.
2. **Concurrent agents need a crash-safe lease journal**, not a grep: deciding
   whether two workers may touch the same tree is arbitration, and it has to
   survive a kill mid-write.
3. **"Spinning vs. stalled" is a failure detector with FLP edges**, not a
   timeout — a wedged run and a slow-but-live one look identical to `sleep`.
4. **One verdict shape has to render byte-identically across hosts** to be a
   standard a fleet can depend on, instead of a different ad-hoc check per tool.
5. **A skeptic-checkable signed receipt** (`dos attest`) is something a bash
   script the agent's own host runs simply cannot mint.
6. **`commit-audit` (claim-vs-diff) isn't greppable** — it reads what the diff
   *did* against what the subject *said*.

The load-bearing point under all six: a script your agent's own host runs can't
credibly be *the part that doesn't believe the agent*. The whole value is being
the layer that is structurally not talked past — and that's exactly the thing a
home-grown check, run inside the loop it's auditing, can't be.

This also answers the most sophisticated objection — **"doesn't durable
execution (Temporal) already do this?"** Durable execution guarantees the step
*ran* and durably records what it *returned*; if an agent step returns
`"deployed successfully"`, the history correctly records that the step *said
so*. DOS adjudicates exactly that residue: the claim against the world. They're
orthogonal layers. (The full comparison — evals, guardrails, in-toto, CI — is
in **[docs/ALTERNATIVES.md](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/ALTERNATIVES.md)**.)

The referee grows along two axes: deterministic *verdicts* that read artifacts
(`verify`, `liveness`, `scope`), and provider-backed *judges* — a model, a
debate — that rule on what no deterministic check can, kept outside the kernel
under a discipline that stops a wrong judge from clearing a falsehood. See
**[the adjudicator-population note](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/88_the-adjudicator-population.md)** for
that scalable-oversight story in code.

> **We caught ourselves doing the exact thing DOS exists to catch.** A design doc
> in this repo included a small worked example — "here's what this snippet prints" —
> written by the agent building DOS. It read perfectly plausible. It was reviewed. It
> was committed. And it was wrong, for the dullest possible reason: *nobody had
> actually run it.* The agent had reasoned out what the code "would" print and typed
> that down as fact. An adversarial review later did the one thing the author hadn't
> — executed the snippet — and the real output flatly contradicted the prose.
> That's the whole thesis in one anecdote: a confident narration is not evidence,
> even when the narrator is us, even after a human reviewed it. The reasoning felt
> like checking; it wasn't. The only thing that settled it was running the code and
> reading what came back — an independent witness, exactly the move `verify` makes
> against an agent's "done." The correction is pinned in git (`docs/124`, commit
> `651ba03`), because here too the record is the commit, not the claim.

> **And the first issue ever filed on this repo was closed the same way.**
> [Issue #1](https://github.com/anthony-chaudhary/dos-kernel/issues/1) is the
> publish pipeline's TestPyPI rehearsal failing its OIDC token exchange
> (`invalid-publisher`). The bug is ordinary; the closure is the demo. It wasn't
> closed on "fixed it" narration — it was closed on two read-backs the claimant
> didn't author: the next pipeline run's own conclusion
> ([the dry-run leg, green](https://github.com/anthony-chaudhary/dos-kernel/actions/runs/27309748423))
> and [the registry's own JSON](https://test.pypi.org/pypi/dos-kernel/json)
> reporting the artifact that leg exists to land. The closing comment runs the
> kernel's verdict on itself — `dos reward --claim --witness confirm` →
> **ACCEPT** — and the same evening, the same pipeline's witness gate
> [refused to publish release 0.23.0](https://github.com/anthony-chaudhary/dos-kernel/actions/runs/27310760144)
> because CI was red on the candidate commit: a release pipeline declining to
> believe an unwitnessed "ready." Every link is public — click the runs, read
> the registry JSON, audit the closure yourself.
