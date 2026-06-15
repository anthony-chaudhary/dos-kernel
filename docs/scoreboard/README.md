# AI commit messages can lie. This catches it.

When Copilot, Cursor, or Claude writes a commit for you, the message is just text — `fix the login bug`, `tests pass`, `add caching`. Nothing checks that the change actually did that. The **diff** can't lie about which files it touched. The **message** can say anything.

This scoreboard asks two plain questions about public repos built with AI agents: **how much of the repo did the AI write — and did the AI tell the truth about what each commit did?** For every AI-authored commit we check whether its message's claim is backed by the commit's own diff. Each page below is the receipt.

### Score your own repo in one command

```bash
pip install dos-kernel
dos commit-audit --sweep --workspace . BASE..HEAD
```

That's the same check, on your history. No account, no upload.

> Across **15 repos** audited here — AI agents wrote about **3%** of recent commits — and **every one** of the concrete claims those commits made was backed by the commit's own diff.

## What "backed by the diff" means

The commit *message* is written by a person or an agent — it can say anything. The *diff* is written by git — it can't. We check whether a concrete claim in the message is **backed by** the commit's own diff. When it isn't, the claim rests on the words alone:

**The empty fix.**

> **says:** `fix: handle null user in the auth callback`  
> **did:** touched **0 files**

The message claims a fix. The commit changed nothing. The claim rests on the words alone.

**"Tests pass" that deletes the test.**

> **says:** `test: green after the refactor`  
> **did:** **deleted** lines from the test file, added none

The message claims the tests pass. The diff removed test code. Maybe that was the right call — but the subject says the opposite of what the diff shows.

Those are the two clearest mismatch shapes. Most real commits aren't mismatches at all — which is the whole point of a clean page.

## Start here — the auditor grades itself

We ran the check on our own repo first and published whatever it said. It says **non-zero** — a few commits that claim a fix but touched nothing. They're a deliberate house convention, and the page shows exactly why. We left them in. A scoreboard that airbrushed its own page to zero wouldn't be worth reading.

- **[anthony-chaudhary/dos-kernel](anthony-chaudhary/dos-kernel.md)** — our own grade, every flag explained.

## Repos that came back clean

Every checkable claim an AI commit made matched the commit's own diff over the audited range. "Clean" here is earned, not empty: each page shows the range, the count, and receipts you can re-run yourself.

What differs between them is how much of the repo the AI built and which agents did it — sorted by AI-built share. Click a repo for the full receipt.

| Repo | AI-built | Agents | Claims checked | Backed |
|---|---|---|---|---|
| [kenn-io/roborev](kenn-io/roborev.md) | 65% | claude 430 · copilot 1 · cursor 1 | 273 | 100% |
| [JuliusBrussee/caveman](JuliusBrussee/caveman.md) | 32% | claude 65 | 49 | 100% |
| [getzep/graphiti](getzep/graphiti.md) | 15% | claude 127 | 66 | 100% |
| [pydantic/pydantic-ai](pydantic/pydantic-ai.md) | 9% | claude 188 · devin 7 · copilot 4 · … | 139 | 100% |
| [exo-explore/exo](exo-explore/exo.md) | 4% | claude 99 · cursor 1 · jules 1 | 67 | 100% |
| [OpenInterpreter/open-interpreter](OpenInterpreter/open-interpreter.md) | 4% | codex 240 · claude 10 · copilot 3 | 118 | 100% |
| [crewAIInc/crewAI](crewAIInc/crewAI.md) | 3% | devin 51 · claude 29 · aider 3 · … | 69 | 100% |
| [agno-agi/agno](agno-agi/agno.md) | 3% | claude 159 · copilot 7 · aider 1 · … | 103 | 100% |
| [charmbracelet/crush](charmbracelet/crush.md) | 3% | crush 86 · copilot 9 · claude 1 | 50 | 100% |
| [farion1231/cc-switch](farion1231/cc-switch.md) | 2% | claude 40 · copilot 1 · cursor 1 | 30 | 100% |
| [livekit/agents](livekit/agents.md) | 2% | claude 45 · devin 17 · cursor 6 · … | 58 | 100% |
| [danny-avila/LibreChat](danny-avila/LibreChat.md) | 1% | claude 24 · copilot 13 · cursor 1 | 24 | 100% |
| [microsoft/autogen](microsoft/autogen.md) | 1% | copilot 28 · claude 2 | 27 | 100% |
| [unslothai/unsloth](unslothai/unsloth.md) | <1% | claude 26 · cursor 2 | 22 | 100% |
| [langchain-ai/langchain](langchain-ai/langchain.md) | <1% | copilot 24 · claude 15 | 29 | 100% |

## The fine print (it matters)

**A mismatch is not an accusation.** It does not mean the code is wrong, or that anyone lied. It means one thing only: a commit's subject claimed something its own diff doesn't show. A real fix to the wrong bug passes the check; an honest doc cleanup with a sloppy subject can flag. A message-vs-diff mismatch is **never** a correctness, honesty, or intent grade — only a note that a commit's words and its own diff disagree.

- **[How it works](methodology.md)** — exactly what the check reads, what it skips, and every time the check itself was wrong (we narrow the check, never trust the subject).
- **[The big picture](report-2026-06.md)** — the population mismatch rate across public repos, with every flag hand-checked and denominators everywhere.
- **Want your repo listed?** Clean or not, it's opt-in and you see the result before it publishes. See the methodology's registration section.

The pages above are the 15 repos we've audited and named. Another 20 repos were checked but not named — a non-clean or unadjudicated verdict is reported only as a count, never as a named page ([docs/311](../311_scoreboard-per-repo-index-plan.md) §2).

> The kernel is the part that doesn't believe the agents.
