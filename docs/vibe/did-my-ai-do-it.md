# Did my AI actually do it?

> **In plain words:** your AI said it built a feature. Did it really? This page
> is the short answer. You point one command at your own project and a plain
> result comes back — "Probably yes" or "Not yet" — read from your code's
> history, not from what the AI told you. No setup, no special words to learn.

You let an AI write most of the code — in Cursor, Claude Code, Windsurf, or a
browser builder. It said *"Done! Added dark mode and a password reset."* You
didn't read every diff. Nobody does. But one of those two might not actually be
in your project. The AI grades its own work, and sometimes the grade is wrong.

There is a 30-second check for exactly this. You run one command on **your own
repo** and get a real answer — "Probably yes" or "Not yet" — worked out from
your git history, not from what the AI said. It works on any git repo.

## The whole idea in three lines

```bash
pip install dos-kernel                                  # NOT `pip install dos` (that's a different package)
cd ~/code/my-app
dos verify --workspace . --output plain RESET RESET1    # did the AI add the password reset?
```

If the work is really there, you get back:

```text
Probably yes: 'RESET1' looks like it was added, but the only sign is a note in the project history, not the built result itself. Worth opening it to confirm it's really there. (This checks that it's present, not that it works.)
```
```text
exit code: 0
```

If the AI said it was done but it never landed, you get back:

```text
Not yet: 'RESET1' isn't in what was built. The agent may have said it was done, but it isn't in the project yet. Ask it to actually add 'RESET1', then check again.
```
```text
exit code: 1
```

That "Not yet" is the moment that earns the 30 seconds. The AI said the work
was done. Your git history says it isn't there. You get an honest answer and a
clear next step — *"ask it to actually add it, then check again"* — without
reading a line of code. The `1` at the bottom is the part a script reads: `0`
means it looks shipped, `1` means not yet, so you can wire the same command
into an automatic "before I trust the AI's done" check.

## The honest part

This tells you the work is **there**, not that it **works**. The check reads
your project's history and says "this landed." It does not run the feature or
test it. A "Probably yes" means the work showed up; whether it does the right
thing is still a job for opening it, a test, or a quick look. The tool is
honest about that, and so should you be.

## The full walkthroughs (with real, verbatim output)

This page is the short framing — *why* the check is worth 30 seconds. The
step-by-step walkthroughs, with every command run against a real repo and the
exact output and exit codes pasted in, live next to the kernel's other
on-ramps:

- **You have a terminal and a git repo** → the 30-second front door:
  [`examples/playbooks/00b_did-my-ai-do-it.md`](../../examples/playbooks/00b_did-my-ai-do-it.md).
  It also shows the one optional wire-in step (`dos init --hooks auto`), which
  hooks the check into the agent tool you already run.
- **You built it in a browser builder** (Lovable, v0, bolt.new) and pushed to
  GitHub → export-then-verify:
  [`examples/playbooks/00c_vibe-coders-export-then-verify.md`](../../examples/playbooks/00c_vibe-coders-export-then-verify.md).
- **You want to hand the same plain answer to a non-coder** (a PM, a founder) →
  [`examples/playbooks/00_non-coder-verdict-in-15-minutes.md`](../../examples/playbooks/00_non-coder-verdict-in-15-minutes.md).

## Why it can be trusted

The answer is computed by a small, plain program that **never reads the AI's
story** — it reads what actually happened in your code's history. So no amount
of confident "all done!" can change the verdict. That is the whole point of the
kernel: it is the part that doesn't believe the agents. The longer "why it
works" version is the [plain-words section of the README](../../README.md#the-plain-words-version).
