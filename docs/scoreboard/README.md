# AI commit messages can lie. This catches it.

When Copilot, Cursor, or Claude writes a commit for you, the message is just text — `fix the login bug`, `tests pass`, `add caching`. Nothing checks that the change actually did that. The **diff** can't lie about which files it touched. The **message** can say anything.

This scoreboard runs one check over public repos built with AI agents: **does each commit's message match what its diff actually did?** When it doesn't — an empty commit that says "fixed it", a "tests pass" that deletes the test — we call that a **drift**. Each page below is the receipt.

### Score your own repo in one command

```bash
pip install dos-kernel
dos commit-audit --sweep --workspace . BASE..HEAD
```

That's the same check, on your history. No account, no upload.

## What a "drift" looks like

The commit *message* is written by a person or an agent — it can say anything. The *diff* is written by git — it can't. A **drift** is when the two don't match: the message makes a concrete claim the diff doesn't back up.

**The empty fix.**

> **says:** `fix: handle null user in the auth callback`  
> **did:** touched **0 files**

The message claims a fix. The commit changed nothing. The claim rests on the words alone.

**"Tests pass" that deletes the test.**

> **says:** `test: green after the refactor`  
> **did:** **deleted** lines from the test file, added none

The message claims the tests pass. The diff removed test code. Maybe that was the right call — but the subject says the opposite of what the diff shows.

Those are the two clearest shapes. Most real commits aren't drifts at all — which is the whole point of a clean page.

## Start here — the auditor grades itself

We ran the check on our own repo first and published whatever it said. It says **non-zero** — a few commits that claim a fix but touched nothing. They're a deliberate house convention, and the page shows exactly why. We left them in. A scoreboard that airbrushed its own page to zero wouldn't be worth reading.

- **[anthony-chaudhary/dos-kernel](anthony-chaudhary/dos-kernel.md)** — our own grade, every flag explained.

## Repos that came back clean

Every checkable commit message matched its diff over the audited range. "Clean" here is earned, not empty: each page shows the range, the count, and receipts you can re-run yourself.

- [JuliusBrussee/caveman](JuliusBrussee/caveman.md)
- [farion1231/cc-switch](farion1231/cc-switch.md)
- [kenn-io/roborev](kenn-io/roborev.md)
- [unslothai/unsloth](unslothai/unsloth.md)

## The fine print (it matters)

**A drift is not an accusation.** It does not mean the code is wrong, or that anyone lied. It means one thing only: a commit's subject claimed something its own diff doesn't show. A real fix to the wrong bug passes the check; an honest doc cleanup with a sloppy subject can flag. A drift is a message-vs-diff mismatch — **never** a correctness, honesty, or intent grade.

- **[How it works](methodology.md)** — exactly what the check reads, what it skips, and every time the check itself was wrong (we narrow the check, never trust the subject).
- **[The big picture](report-2026-06.md)** — the population drift rate across public repos, with every flag hand-checked and denominators everywhere.
- **Want your repo listed?** Clean or not, it's opt-in and you see the result before it publishes. See the methodology's registration section.

The pages above are the 5 repos we've audited and named. Another 22 repos were checked but not named — a non-clean or unadjudicated verdict is reported only as a count, never as a named page ([docs/311](../311_scoreboard-per-repo-index-plan.md) §2).

> The kernel is the part that doesn't believe the agents.
