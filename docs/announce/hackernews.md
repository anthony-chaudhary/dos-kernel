# Hacker News post

> For news.ycombinator.com. HN is its own register: a plain-text title (no emoji,
> no hashtags, no marketing voice), a substantive author's first comment, and —
> the part that wins on HN specifically — the honest floor stated up front as an
> *asset*, not buried. That crowd rewards a self-stated weakness and punishes a
> pitch. Lead with the mechanism, not the adjective. Fill the links once the repo
> + PyPI are live; post the paper as a separate item once arXiv assigns an ID.

*Refreshed 2026-06-14 against v0.26.0 (repo public, `pip install dos-kernel`
serves 0.26.0, scoreboard pages live). Numbers trace to `paper/meta.py` (the
single source of truth) — re-check before posting; the load-bearing figures
(6/8, 10 @ 8.3%, 0/1634, 2–7%, 88–98%) were re-verified at this refresh.*

---

## Title

It's a Show HN because you can `pip install` it and run it, not just read it. Keep
the title under ~80 chars, no hype:

> **Show HN: DOS – a referee between AI agents that doesn't believe their "done"**

URL field: the GitHub repo (`github.com/anthony-chaudhary/dos-kernel`). Post the
paper as a separate submission once arXiv assigns an ID. Don't bundle them.

## The author's first comment (post immediately after submitting)

Hi HN. I run fleets of coding agents, and the failure that kept biting me isn't
that an agent gets something wrong. It's that it reports success it didn't get. It
runs twenty tool calls, writes "All done, the login endpoint is live," and nothing
landed. For one agent that's just annoying. You re-run your check and move on. For
a fleet it's a correctness bug you can't fix from inside any one agent, and that's
the part I want to show this crowd.

Here's why. The obvious fix for one agent is "if the check fails, run it again."
That assumes the world stays still between the check and the act. With concurrent
agents it doesn't. Your check reads true the moment agent A looks, and false by the
time A writes, because agent B changed the shared state in between. That's a TOCTOU
race, except on world state instead of a file. The textbook cure lives in the
store: a transaction or a compare-and-swap fuses the check and the act into one
atomic step, and where the store offers that, a wrapper around one agent is enough
— no referee needed. Agents don't get that primitive. They act through tool APIs
(cancel a reservation, file a ticket) that only do unconditional writes, so the
check and the act stay two separate calls with a gap between them, and no number
of re-runs makes two calls one. Nothing you bolt onto A can fix that. You need a
referee sitting between the agents that orders their writes and trusts none of
their reports.

That's DOS. The core move is one command:

    dos verify AUTH AUTH1   # → SHIPPED      (exit 0), git backs the claim
    dos verify AUTH AUTH2   # → NOT_SHIPPED  (exit 1), nothing landed

The verdict comes from git history, the file tree, the clock. Bytes the agent
couldn't have written. What it says about itself never enters in. It's a small
deterministic kernel (close to stdlib Python), MIT, `pip install dos-kernel`, runs
on a plain git repo with no config, and ships an MCP server so you can wire it into
whatever agent host you already run.

To show it on something other than my own code, I pointed it at a handful of real
third-party agent repos and published the audits: every commit whose subject claims
a behavior change, checked against its own diff. Four external repos so far, 197
checkable claims, 0 I could confirm as unwitnessed — the same `commit-audit` rung
you can run on your repo in one command. It's not a "gotcha" tool; on honestly-built
code it mostly comes back clean, and that's the point — the check is cheap enough to
leave on.

The result I didn't expect, and the reason it turned into a paper: I held the check
fixed (one the agent can't fake) and measured only where it's worth spending.

- Spend it back inside the loop (warn the agent, rewind it, inject the fix) and
  it's flat to harmful. A capable agent already re-checks and fixes its own phantom,
  so stepping in mostly disturbs a run that was going to pass anyway. The only
  in-loop move that helped was the negative one: give up on a doomed run. Zero
  false-halts across 1,634 winners, ~11% compute saved.
- Spend it on the rest of the fleet and it pays. Two agents racing one record: the
  arbiter prevents 6 of 8 lost-update clobbers, checked against the environment's
  own database hash. One agent reporting a write that never landed, before a peer
  inherits it: a write-admission gate blocks 10 real over-claims across 120 tasks,
  same 8.3% rate on a mid and a strong model, so the signal doesn't fade at the top
  tier.

Now the weak spot, because it's the interesting part. On an offline,
third-party-scored replay of 22 models the detectors are precise (88-98%) but
low-recall. They catch only ~2-7% of failures, and that share shrinks on the
strongest models. The downstream outcome delta from blocking one phantom, measured
at the single easiest hop, is about zero, because the consumer recovers on its own.
So I report the blocked count (real corruptions a check the agent can't forge
stopped), never a downstream delta I can't back. That low ceiling is the reason the
value sits outside the loop instead of inside it.

If you run more than one agent against the same codebase, I'd really like to know
where this breaks for you. Repo and every recomputable number are linked above. The
paper goes up separately once it has an arXiv ID.

## Notes for posting

- **Title discipline.** No emoji, no hashtags, no "revolutionary/game-changing."
  HN flags hype. "Show HN:" only if the repo is public and runnable at post time
  (it must be — that's the rule for Show HN).
- **The honest-floor paragraph is the asset, not a liability.** This is the one
  venue where stating your own low recall + ≈0 ΔB *up front* is rewarded — it
  pre-empts the skeptical top comment and reads as someone who measured rather than
  marketed. Do not soften it.
- **Lead with the fleet/TOCTOU mechanism, concede the single-agent wrapper.** The
  load-bearing claim is the concurrency one; an in-loop active fix is ≈0-to-harmful
  and you say so. Don't let it slide into "single-agent is trivial" — an unforgeable
  check and a loop cheap enough to run every turn are real work (just not an active
  in-loop fix). The user-approved "concurrency changes the verdict" wording lives in
  `linkedin.md`; reuse its shape, don't re-invent the framing.
- **Concede the transactional store before the thread does.** The sharpest early
  comment will be "CAS/OCC/transactions close TOCTOU with no referee" — and it's
  right wherever the store offers that primitive. The draft concedes it up front
  and scopes the claim to what agents actually act through (tool APIs with no
  transaction, no conditional write). Don't trim that concession when shortening —
  on HN it's the difference between a correction and a credibility hit.
- **Keep the numbers exact.** 6/8 (coordination; a later live run reproduced 9/10),
  10 over-claims at an identical 8.3% across two models, 0/1634 false-halts, ~2–7%
  recall, 88–98% precision. Don't round "6 of 8" to "most" — the specificity is the
  credibility, and on HN it's also the difference between a comment thread and a
  flag.
- **Don't bundle the paper.** Post the code as one Show HN now; post the paper as a
  separate item once arXiv assigns an ID (the abstract + framings are in
  `arxiv-abstract.md`).
- **Be present in the thread.** A Show HN lives or dies on the author answering the
  first hour of comments. Have the repo's quickstart (`dos quickstart`) and the
  `dos verify` two-liner ready to paste.
