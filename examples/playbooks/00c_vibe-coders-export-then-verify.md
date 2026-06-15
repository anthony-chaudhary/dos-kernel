# Playbook 00c — built it in Lovable / v0 / bolt.new? Export, then verify.

> **In plain words:** you built an app in a browser tool like Lovable, v0, or
> bolt.new by chatting with an AI. You never opened a terminal. Then you hit
> **"Push to GitHub"** and now your code lives in a GitHub repo. This page is
> the one honest check you can run on that repo to ask: *did the AI actually
> build what it said it did?* The answer comes from your code's history, not
> from what the AI told you.

You can't install DOS *inside* Lovable, v0, or bolt.new — they're browser
app-builders with no terminal and no local git, and pretending otherwise would
be a lie. But every one of these tools has an **Export to GitHub** /
**Push to GitHub** button, and once your app is a GitHub repo, the check is the
same one any developer runs: `dos verify` needs no plan and works on **any
pushed git repo**. So the honest on-ramp starts *after* the export.

> **The split this page is honest about.** The **export step is yours** — you
> click the button in your builder (pointers below). The **verify step is the
> one shown working here**, against a real public GitHub repo, with the real
> command output and exit code pasted in. Nothing about a Lovable/v0/bolt
> account or export is faked or quoted second-hand; the demonstration runs
> against a repo that is already on GitHub.

---

## Step 0 — your part: push your app to GitHub (one button, no terminal)

Do this in your builder; it takes one click and a GitHub sign-in. Each tool
calls it slightly different, but it's the same idea — *create a GitHub repo from
this project*:

| Builder | Where the button is | What it does |
|---|---|---|
| **Lovable** | the **GitHub** icon in the editor's top bar → **Connect GitHub** → **Connect Project** | creates a repo and pushes your code as the first commit; every later AI change becomes a commit ([docs.lovable.dev/integrations/github](https://docs.lovable.dev/integrations/github)) |
| **v0** (Vercel) | the **•••** menu → **Export to GitHub** (or **Download ZIP** and push it yourself) | one-way export of the generated code to a repo ([v0 community thread](https://community.vercel.com/t/how-can-i-push-the-code-written-in-v0-to-my-github-repository/7558)) |
| **bolt.new** | the native **GitHub** integration → connect to a new private repo (auto-pushes edits) | connects the project to a repo and pushes ([support.bolt.new/integrations/git](https://support.bolt.new/integrations/git)) |

When you're done you have a normal GitHub repo — `github.com/<you>/<your-app>`.
That repo is all the rest of this page needs. (The builder names above are the
user-facing feature today; if a tool moves the button, look for "GitHub",
"Export", or "Push" in its share/settings menu — the on-ramp below is unchanged.)

---

## Step 1 — get the code on a machine that has a terminal (90 seconds)

The check runs on your laptop, not in the browser builder. You need three
things people without a dev setup usually have or can get in a minute: **git**,
**Python**, and the kernel.

```bash
pip install dos-kernel        # NOT `pip install dos` — that's an unrelated package
git clone https://github.com/<you>/<your-app>    # the repo your builder just pushed
cd <your-app>
```

> **Why a full clone (no `--depth 1`).** The check reads your repo's *history*
> to decide what really landed. A shallow clone hides older commits, so clone
> the whole thing — it's a one-time copy and small for an app-builder project.

That's the whole setup. No `dos init`, no config, no account. From here the
check is two commands.

---

## Step 2 — ask: did the AI actually build it? (`dos verify`)

This is the witness. Pick something the AI told you it built and give it two
short tags — any words. The command reads your git history and answers, and the
**exit code is the verdict**: `0` = it looks shipped, `1` = not yet.

Below is a **real run against a real public GitHub repo** (the DOS kernel's own
repo, freshly cloned the way your app would be) — the output and exit codes are
verbatim, not illustrations:

```bash
dos verify --workspace . docs/126 P1
```

```text
SHIPPED docs/126 P1 8a7a259 (via grep-subject)
```
```text
exit code: 0
```

And the **caught lie** — ask about something the history does *not* back, and
it comes back red:

```bash
dos verify --workspace . docs/999 NEVER
```

```text
NOT_SHIPPED docs/999 NEVER (via none)
```
```text
exit code: 1
```

That `exit code: 1` is the moment that earns the check: the claim is contradicted
by your own git history, computed by the `dos` process — *not* by the AI that
made the claim.

Prefer plain English over `SHIPPED`/`NOT_SHIPPED`? Add `--output plain` (the
same two real runs):

```bash
dos verify --workspace . --output plain docs/126 P1
```
```text
Probably yes: 'P1' looks like it was added, but the only sign is a note in the project history, not the built result itself. Worth opening it to confirm it's really there. (This checks that it's present, not that it works.)
```
```text
exit code: 0
```
```bash
dos verify --workspace . --output plain docs/999 NEVER
```
```text
Not yet: 'NEVER' isn't in what was built. The agent may have said it was done, but it isn't in the project yet. Ask it to actually add 'NEVER', then check again.
```
```text
exit code: 1
```

The tags `docs/126`/`P1` are this demo repo's own ship grammar. Your app won't
have that yet, which is fine — the no-vocabulary check in Step 3 needs nothing.

## Step 3 — no special tags yet? Audit the AI's last commit (`commit-audit`)

`dos verify` is sharpest when a repo stamps phases. A fresh app-builder export
doesn't — so use the floor check that needs **no vocabulary at all**:
`commit-audit` grades whether a commit's *message* matches what its *diff*
actually changed. It catches a `fix:` that touched only a README, an empty
"shipped" commit, a "added tests" that deleted them.

A **real run against the same freshly-cloned public repo** — its tip commit's
subject makes no checkable code claim, so the check honestly *abstains* (it
refuses to fire when it can't ground a verdict) and exits `0`:

```bash
dos commit-audit --workspace . HEAD
```
```text
· abstain     881f74b  [abstain]  subject makes no checkable code/test claim
```
```text
exit code: 0
```

When a commit *does* make a claim its diff backs, you get a green witness line
(a real run, same tooling):

```text
✓ witnessed   99d3a25  [diff-witnessed]  code-effect claim witnessed by a touched source file
```
```text
exit code: 0
```

And when a commit over-claims — says it did something its diff doesn't show —
the line is flagged and the exit code is `1`. That `1` is the signal a script
(or a CI gate, Step 5) reads to block a false "done."

> **The honest scope.** Both checks tell you the work is **there**, not that it
> **works** — they read history, they don't run your app. A green means "this
> landed"; whether it does the right thing is still a job for opening it or a
> test. The tool is honest about that, and the page is too.

---

## Step 4 — see it on a repo right now (copy-paste, ~1 minute)

Don't have your export handy? Run the exact demonstration this page is built
from against any public repo. This clones a real GitHub repo and runs both
checks — it's the same sequence you'd run on `github.com/<you>/<your-app>`:

```bash
pip install dos-kernel
git clone https://github.com/anthony-chaudhary/dos-kernel
cd dos-kernel
dos verify --workspace . docs/126 P1     # SHIPPED … exit 0  (a phase that really landed)
dos verify --workspace . docs/999 NEVER  # NOT_SHIPPED … exit 1  (a claim nothing backs)
dos commit-audit --workspace . HEAD      # claim-vs-diff on the tip commit
```

Swap the clone URL for your own exported repo and the `docs/126 P1` tags for a
feature your AI claimed, and the same two commands answer for *your* app.

---

## Step 5 (optional) — make the check automatic on every push

The walkthrough above is the **primary** path: it works for any reader today,
needs only git + pip, and you run it by hand whenever you want an answer. The
**fallback / "make it automatic"** step is to let GitHub run the same verdict on
every push, so you never have to remember to. DOS ships a GitHub Action — the
real one, quoted verbatim from [`verify-action/`](../../verify-action/README.md):

```yaml
# .github/workflows/dos-gate.yml  — drop this in your exported repo
name: dos-gate
on:
  pull_request:
jobs:
  dos-verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }          # the audit walks git ancestry
      - uses: anthony-chaudhary/dos-kernel/verify-action@master   # pin a release tag for reproducible CI
        with:
          mode: commit-audit              # the no-vocabulary floor — same as Step 3
          fail-on: unwitnessed            # or: none = observe-only while you trial it
```

Then make `dos-gate` a **required check** in your repo's branch-protection
settings, and GitHub blocks a merge whose commits over-claim. DOS computes the
verdict and sets the exit code; GitHub's required-check setting is what actually
blocks — the enforcement is yours, opt-in, and visible. The full recipe (the
reusable one-line workflow form, the `verify` mode, GitLab, pre-commit) is
[`cookbook-ci-integration.md`](cookbook-ci-integration.md) and
[`cookbook-exit-code-tier.md`](cookbook-exit-code-tier.md).

## Step 6 (optional) — wear the proof

Once the gate runs green on your repo, you can paste the **"verified by DOS"**
badge so anyone looking at your repo sees it checks its AI's ship-claims against
git, not the AI's word:

[![verified by DOS](https://img.shields.io/badge/verified%20by-DOS-2ea44f)](https://github.com/anthony-chaudhary/dos-kernel)

The badge markup and the one honesty rule that comes with it (it asserts your
*process*, not a live per-commit verdict — wear it only once the gate actually
runs) are on [`docs/BADGE.md`](../../docs/BADGE.md); the shareable
caught-the-lie screenshot is [`docs/SHARE.md`](../../docs/SHARE.md). The badge
is the *shareable proof*, not the verifier — the load-bearing check is Step 2/3.

---

## What you have now

- An app you built in a browser, pushed to GitHub with one button — your part.
- One command that answers *"did the AI actually build it?"* against that repo,
  read from git history, not the AI's say-so — the part shown working here.
- A `1` exit code on "not yet" you can wire into a gate so a false "done" gets
  caught on every push, without you watching.

## Where to go next

- **The 30-second front door** (any git repo, the same idea, even shorter) →
  [`00b_did-my-ai-do-it.md`](00b_did-my-ai-do-it.md).
- **The non-coder framing** ("Probably yes" / "Not yet" for a PM or founder) →
  [`00_non-coder-verdict-in-15-minutes.md`](00_non-coder-verdict-in-15-minutes.md).
- **Set your repo up properly** (so "Probably yes" becomes a firm "yes") →
  [`01_onboard-a-repo.md`](01_onboard-a-repo.md).
- **Automate it in CI / a pre-push hook** →
  [`cookbook-ci-integration.md`](cookbook-ci-integration.md) ·
  [`cookbook-exit-code-tier.md`](cookbook-exit-code-tier.md).

---

### Recap — the commands (real, run-against-a-real-repo)

```bash
pip install dos-kernel                                  # NOT `pip install dos`
git clone https://github.com/<you>/<your-app>           # the repo your builder pushed
cd <your-app>
dos verify --workspace . PLAN PHASE                     # "did the AI build it?"  exit 0=yes 1=no
dos commit-audit --workspace . HEAD                     # claim-vs-diff, no tags needed  exit 0=clean 1=over-claim
```
