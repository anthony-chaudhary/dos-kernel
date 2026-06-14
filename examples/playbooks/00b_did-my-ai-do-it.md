# Playbook 00b — "Did my AI actually do it?" in 30 seconds

> **In plain words:** your AI said it added a feature. Did it really? This page
> is one command you point at your own project, and a plain answer comes back —
> "Probably yes" or "Not yet" — read from your code's history, not from what the
> AI told you. It's for anyone who lets an AI write code and wants a quick,
> honest check. No setup, no special words to learn.

You let an AI write most of the code. It said *"Done! Added dark mode and
password reset."* You didn't read every diff — nobody does. But one of those
two might not actually be in your project. The AI grades its own homework, and
sometimes the grade is wrong.

This page is the 30-second check. You point one command at **your own repo**
and a real answer comes back — "Probably yes" or "Not yet" — read from your
git history, not from what the AI said. No plans, no fleet, no jargon. It works
on any git repo.

<p align="center">
  <img src="https://raw.githubusercontent.com/anthony-chaudhary/dos-kernel/master/docs/assets/caught-lie-cast.svg" alt="A terminal recording of the caught lie. The agent reports two features shipped; git history backs only one. dos verify answers SHIPPED for the real one (exit 0) and NOT_SHIPPED for the claimed-but-missing one (exit 1) — the false 'done' is caught." width="100%">
  <br>
  <em>This is the whole idea: the agent claims two features shipped, git backs
  one, and <code>dos verify</code> catches the one that never landed. Every
  line is the real tool's verbatim output.</em>
</p>

---

## Step 1 — install (30 seconds)

```bash
pip install dos-kernel
```

> Install **`dos-kernel`**, never the bare `dos` — that PyPI name is an
> unrelated package. The command you run is still `dos`.

## Step 2 — wire it into the agent you already use

`cd` into the project the AI worked in and run one command. It writes a small
config file and detects the agent tool you already run, then hooks itself in —
you don't have to name anything:

```bash
cd ~/code/my-app
dos init --hooks auto .
```

```text
wrote /home/you/code/my-app/dos.toml
derived 1 concurrent lane(s) (src) + an exclusive 'global'
--hooks auto: detected claude-code
wired 3 DOS hook(s) into /home/you/code/my-app/.claude/settings.json: PreToolUse, PostToolUse, Stop
  bound to claude-code: a refused call is DENIED (pretool), a stalled stream is re-surfaced (posttool), a stop on an unverified claim is refused (stop).
DOS workspace initialised. Try:  dos doctor --workspace .
```
```text
exit code: 0
```

`--hooks auto` is the zero-decision path: it looks at which agent tool your
project already uses (it found Claude Code above; it also knows Cursor, Codex,
Gemini, and others) and wires itself into that tool's own config. If you don't
run any of them, that's fine too — you can skip this step and still run the
check in Step 3 by hand.

## Step 3 — ask: did my AI actually do it?

Pick a feature the AI told you it shipped and give it two short tags — one for
the feature, one for the specific bit you're checking (here `DARKMODE` and
`DARKMODE1`; any words work). Ask:

```bash
dos verify --workspace . --output plain DARKMODE DARKMODE1
```

```text
Probably yes: 'DARKMODE1' looks like it was added, but the only sign is a note in the project history, not the built result itself. Worth opening it to confirm it's really there. (This checks that it's present, not that it works.)
```
```text
exit code: 0
```

That answer came from your git history, **not** from the AI. The wording hedges
on purpose: the only sign here was a note in a commit, which is the weakest kind
of evidence — so it says *"Probably yes ... worth opening it to confirm."* It
never turns a weak sign into false confidence.

## Step 4 — the caught lie

Now ask about the other thing it claimed — the password reset:

```bash
dos verify --workspace . --output plain RESET RESET1
```

```text
Not yet: 'RESET1' isn't in what was built. The agent may have said it was done, but it isn't in the project yet. Ask it to actually add 'RESET1', then check again.
```
```text
exit code: 1
```

This is the moment that earns the 30 seconds. The AI said the password reset
was done. Your git history says it isn't there. You get an honest **"Not yet"**
and a clear next step — *"ask it to actually add it, then check again"* —
without reading a single line of code.

That `exit code: 1` is the part scripts use: `0` means it looks shipped, `1`
means not yet. So you can wire the same one command into a "before you trust the
AI's done" check and let it block a false claim automatically.

## The one thing to be honest about

This tells you the feature is **there**, not that it **works**. The check reads
your project's history and says "this landed" — it doesn't run the feature or
test it. A "Probably yes" means the work showed up; whether it does the right
thing is still a job for opening it, a test, or a quick look. The tool is honest
about that, and so should you be.

## What you have now

- One command answers *"did my AI actually do it?"* about your own repo —
  "Probably yes" or "Not yet."
- The answer comes from your git history, not the AI's say-so.
- A `1` exit code on "Not yet" lets you block a false "done" automatically.

## Where to go next

- **Hand the same plain-English verdict to a non-coder** (a PM, a founder) →
  [`00_non-coder-verdict-in-15-minutes.md`](00_non-coder-verdict-in-15-minutes.md).
- **Set up your repo properly** (so "Probably yes" becomes a firm "yes") →
  [`01_onboard-a-repo.md`](01_onboard-a-repo.md).

---

### Recap — the commands

```bash
pip install dos-kernel                                       # NOT `pip install dos`
cd ~/code/my-app
dos init --hooks auto .                                      # wire into the agent tool you already run
dos verify --workspace . --output plain DARKMODE DARKMODE1   # "Probably yes: ..."   exit 0
dos verify --workspace . --output plain RESET RESET1         # "Not yet: ..."        exit 1
```
