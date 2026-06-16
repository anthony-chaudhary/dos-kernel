# Show HN draft — DOS

> Drafted 2026-06-16 against v0.27.0. Every number traces to a reproducible
> command (noted inline). Lead with the relatable failure, demo in the body,
> the 19-repo scoreboard as the credibility anchor; the "Dispatch Operating
> System / syscall ABI" vocabulary is deliberately held below the fold — it's
> the architecture, not the hook. Pick one title; the body is shared.

## Title (pick one)

1. **Show HN: A lie detector for AI coding agents — verify "done" against git, not the agent**
2. **Show HN: I audited 19 popular AI-built repos — do agents' commit messages match their diffs?**
3. **Show HN: DOS — catch your AI agents when they lie about what they shipped**

Title 1 leads with the tool and the relatable pain (recommended for a tool-first
launch). Title 2 leads with the measurement and names repos people know (better
if you want the front-page "interesting data" pull); it makes the post about the
scoreboard, with the tool as the "and here's how." Title 3 is the README
headline verbatim — punchy but slightly less specific than 1.

## Body

I run a lot of coding agents, and the failure that kept biting me wasn't a bad
diff — it was a *confident* one. The agent says "Done! Shipped the login
endpoint and the password reset," and one of those two things never landed. With
one agent you catch it because you re-read the work. Run twenty at once and
nobody's reading; the false "done" just flows downstream.

So I built the smallest thing that fixes that: a check that reads what *actually*
happened instead of what the agent *said*. The nearest piece of "what actually
happened" is your git history.

```
$ dos verify AUTH AUTH1   # → SHIPPED      AUTH AUTH1 cf997ba   (exit 0)
$ dos verify AUTH AUTH2   # → NOT_SHIPPED  AUTH AUTH2           (exit 1)
```

The agent can claim AUTH2 all day; `verify` reports what the commits say, and
they say it didn't ship. **The exit code is the verdict** (0 shipped / 1 not), so
a CI step or dispatch loop branches on it without parsing a word — you replace
the line that trusts the agent's "done" with one that checks it. No plan, no
config, works on a plain git repo. Run the whole thing in a throwaway repo, zero
install:

```
uvx --from dos-kernel dos quickstart
```

Then I pointed the same check at repos you've heard of. The
[public scoreboard](https://anthony-chaudhary.github.io/dos-kernel/scoreboard/)
sweeps 19 popular AI-built repos (OpenInterpreter, crewAI, AutoGen, langchain,
codex, mem0, …): 66,645 commits scanned, 2,334 machine-attributed to agents,
1,739 making a concrete claim. **99.7% of those claims are backed by the
commit's own diff.** The honest headline is *not* "agents lie all the time" —
in named, active repos they mostly don't. The 5 unwitnessed claims are all on my
*own* repo's page, every one explained (deliberate empty re-stamp commits). The
whole rollup reproduces offline: `python scripts/scoreboard_rollup.py --check`.

This repo is itself built by a fleet of agents that this kernel referees, and
its own page carries the same verdict — including those 5 flags, not airbrushed
to zero.

### The two top comments I'd expect, answered up front

**"I could do this in 20 lines of bash."** You could — that instinct is right;
the core of `verify` really is "grep git for a stamp." DOS is that script taken
seriously across the six places the 20-line version quietly breaks: the stamp
grammar is forgeable unless it's a closed declared vocabulary; concurrent agents
need a crash-safe lease journal, not a grep, to arbitrate who may touch which
files; "spinning vs. stalled" is a failure detector with FLP edges, not a
timeout; one verdict shape has to render byte-identically across hosts to be a
standard; a skeptic-checkable signed receipt (`dos attest`) is something a
bash script can't mint; and claim-vs-diff (`commit-audit`) isn't greppable. The
load-bearing point: *a check your agent's own host runs can't credibly be the
part that doesn't believe the agent.*

**"Isn't this LangGraph / AutoGen / Temporal?"** Orthogonal. Orchestrators
(LangGraph, AutoGen, Swarm) decide what the agents *do next*; DOS decides whether
to *believe what they did*. Durable execution (Temporal) guarantees the step
*ran* and records what it *returned* — if a step returns "deployed
successfully," the history faithfully records that it *said so*. DOS adjudicates
exactly that residue: the claim against the world. It runs beside any of them as
a "referee node."

It's MIT, one runtime dependency (PyYAML), Python 3.11+, ~5,600 tests. The
architecture — it's a small deterministic kernel with a driver layer for
anything host-specific — is in the README for anyone who wants it, but the thing
to try is the two-line demo above.

Repo: https://github.com/anthony-chaudhary/dos-kernel

### Notes for the poster

- Every figure above is live as of v0.27.0; re-run `scoreboard_rollup.py --check`
  the morning of the launch and update the 99.7% / counts if a sweep has landed
  since.
- Have the throwaway-repo demo (`uvx --from dos-kernel dos quickstart`) open in a
  terminal — the most-upvoted thing on a tool launch is a commenter running it
  and pasting the output.
- If you lead with Title 2 (the scoreboard), move the scoreboard paragraph to the
  top of the body and the demo second.
