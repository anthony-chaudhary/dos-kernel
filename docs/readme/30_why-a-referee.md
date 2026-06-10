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
**[Debug a stuck fleet](examples/playbooks/06_debug-a-stuck-fleet.md)**.

The referee grows along two axes: deterministic *verdicts* that read artifacts
(`verify`, `liveness`, `scope`), and provider-backed *judges* — a model, a
debate — that rule on what no deterministic check can, kept outside the kernel
under a discipline that stops a wrong judge from clearing a falsehood. See
**[the adjudicator-population note](docs/88_the-adjudicator-population.md)** for
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
