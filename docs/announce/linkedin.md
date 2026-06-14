# LinkedIn launch post

> Paste into LinkedIn's composer (or its native scheduler). Attach the high-res
> hero poster `docs/assets/loop-hero.png` (3600×1920 — LinkedIn doesn't render the
> SVG), or a screen-recording of `dos quickstart`. Fill the links once the repo +
> PyPI are live. The opening is the user-approved "concurrency changes the verdict"
> wording — keep it verbatim.
>
> *Refreshed 2026-06-14 against v0.26.0 (repo public, `pip install dos-kernel`
> live, scoreboard pages serving). Numbers trace to `paper/meta.py`; the
> load-bearing figures (6/8, 10 @ 8.3%) were re-verified at this refresh.*

---

## The post

**Your AI coding agents lie about what they shipped. With one agent that's
annoying. With a fleet, it's a correctness bug you can't fix from inside any one
agent.**

Checking one agent is easy. That's not the problem. Anyone can wrap an agent in
"if the check fails, run it again" in a few lines. But that assumes the world holds
still between the check and the act, and with concurrent agents it doesn't. Your
check reads true the moment agent A looks and false by the time A writes, because
agent B changed the shared state in between.

A database could close that race by itself: a transaction, or a write that only
lands if the value is still what you read. Agents don't get that. The tool APIs
they act through only do unconditional writes, so nothing you bolt onto A can fix
it. You need a referee between the agents: one that orders their writes and trusts
none of their reports.

That's what I've been building. It's called **DOS**, a small deterministic kernel
that decides ground truth across a crowd of unreliable, self-reporting workers. The
core move is one command:

```
dos verify AUTH AUTH1   # → SHIPPED      (exit 0), git backs the claim
dos verify AUTH AUTH2   # → NOT_SHIPPED  (exit 1), nothing landed
```

What the agent says about itself never enters in. The answer comes from git
history, the file tree, the clock. Bytes the agent could not have written.

Here's the part I didn't expect. I ran it live and measured where that verdict is
actually worth spending.

Hand it back to the agent that earned it (warn it, rewind it, feed it the fix)?
Flat to harmful. A capable agent already re-checks and fixes its own mistake, so
stepping in just disturbs a run that was going to pass.

Hand it to the rest of the fleet? It pays, on both ways a fleet fails. Two agents
racing one record: the referee prevents **6 of 8** lost-update clobbers. One agent
reporting a write that never landed before a peer inherits it: a write-admission
gate blocks **10 real over-claims** across two model tiers. All checked against the
environment's own database hash, a byte no agent wrote.

So the boundary is the whole result. A verdict the agent can't forge is harmful
handed back to that agent and valuable handed to anyone else, and "anyone else" is
the rest of the fleet and the training loop behind it.

It's open source, MIT, one `pip install`, runs on a plain git repo with no config.
There's an MCP server so you can wire it into the agent host you already run.

To show it isn't just grading its own homework, I ran the same git-evidence check
over real third-party agent repos and published the audits — 197 commit-claims
checked across four outside projects, 0 I could confirm as unwitnessed. On
honestly-built code it comes back clean, and that's the point: the check is cheap
enough to leave running.

🔗 Repo: github.com/anthony-chaudhary/dos-kernel
📦 `pip install dos-kernel`
📄 Paper: [arXiv link once it's up]

If you're running more than one agent against the same codebase, I'd love your
eyes on it.

#AIAgents #MultiAgent #LLM #DeveloperTools #OpenSource #AgentOrchestration

---

## Shorter variant (if the above runs long)

Your AI agents lie about what they shipped. With one agent you can wrap it in
"re-run if the check fails," but only if the check is one the agent can't fake
(re-reading its own work just grades the story against itself) and only if it's
cheap enough to run every turn. Neither is free. That's already real work.

A fleet breaks the trick outright. The check reads true when agent A looks and
false by the time A writes, because agent B changed the shared state in between —
and the tool APIs agents act through offer no transaction to close that gap. No
wrapper around A can fix it.

So I built **DOS**, a referee between agents. It answers "did this actually ship?"
from git, not from the agent's say-so:

```
dos verify AUTH AUTH1   # → SHIPPED      (exit 0)
dos verify AUTH AUTH2   # → NOT_SHIPPED  (exit 1)
```

Live, it prevents **6 of 8** write-collisions between racing agents and blocks **10
real over-claims** before a peer inherits them, all checked against the
environment's own ground truth instead of the agent's word.

The surprise: hand the same verdict back to the agent and it's flat to harmful (the
agent self-heals anyway). The value is all outside the loop, in the fleet around
it.

MIT, `pip install dos-kernel`, no config on a plain git repo, MCP server included.

🔗 github.com/anthony-chaudhary/dos-kernel

#AIAgents #MultiAgent #OpenSource

---

## One-liner (comment / repost hook)

A "done" check can read true when agent A looks and false when A writes, because
agent B moved the shared state in between — and agent tool APIs offer no
transaction to close the gap. You can't wrap your way out. DOS is the referee
between agents: `pip install dos-kernel`.

---

## Notes for posting

- **Attach a visual.** The high-res hero poster `docs/assets/loop-hero.png` (the
  open-vs-closed-loop hero) or a 15-second screen recording of `dos quickstart` will outperform a text-only
  post on LinkedIn's feed ranking.
- **The numbers are load-bearing — keep them exact.** J = 6/8 (coordination,
  published; a later live run reproduced 9/10), 10 over-claims blocked across two
  models at an identical 8.3%. Don't round "6 of 8" to "most" — the specificity is
  the credibility.
- **Scope the single-agent claim precisely — don't oversell *or* undersell it.**
  The honest, distinctive claim is about *where you spend the verdict*: handed back
  *in-loop* an active fix is ≈0-to-harmful; the payoff is out-of-loop. That
  asymmetry is more interesting than a generic "we catch bugs" pitch. But don't let
  it collapse into "single-agent is trivial" — the unforgeable check and a loop
  cheap enough to run every turn are real single-agent value (just not an *active
  in-loop fix*). Concede the wrapper, not the value.
- **Concede the transactional store too.** Where shared state offers a transaction
  or a compare-and-swap, a wrapper around even one agent can fuse check-and-act and
  close the race with no referee — the claim is scoped to agents acting through
  tool APIs that offer no such primitive. The drafts state that scope; don't let a
  trimming pass cut it.
- **First comment:** drop the paper link + a line inviting the fleet-runners
  ("running >1 agent on one repo? try it and tell me where it breaks").
