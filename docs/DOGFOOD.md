# DOS on DOS — the worked dogfood ritual

> This is the cold tier of the "DOS on DOS" section of [CLAUDE.md](../CLAUDE.md)
> (the architecture contract). The contract keeps the short form; this file keeps
> the full, annotated walkthrough — every command real, every verdict explained.
> Lifted verbatim from CLAUDE.md on 2026-06-12 when the contract was slimmed.

**This repo IS a DOS workspace, so adjudicate your work on it with the kernel
itself.** A `dos.toml` sits at the root and `dos doctor` reports
`workspace_facts.is_kernel_repo: true` (`kernel_runtime_files_present: 11`) —
DOS knows it is editing its own source tree. Don't take the kernel's behavior on
faith from the contract; **run the syscalls against this repo and read the
verdict.** The moves below are the working ritual — they are the same
`arbitrate → edit → verify` loop a host's dispatch-loop runs, performed by hand:

```bash
# 1. doctor — what IS this workspace? (the seam, made visible)
dos doctor --workspace .
#   stamp convention   generic (any/no dir prefix)  [style=grep]
#   concurrent lanes   benchmark, ci, claude-plugin, docs, examples, go, meta, paper, scripts, spikes, src, tests, verify-action
#   exclusive lanes    global
#   is git workspace   yes      (workspace_facts.is_kernel_repo: true)

# 2. arbitrate — may I take this lane right now? (the admission kernel)
dos arbitrate --workspace . --lane docs
#   {"outcome": "acquire", "auto_picked": false, "lane": "docs",
#    "reason": "cluster lane 'docs' free — admitted.", "tree": ["docs/**"]}
dos arbitrate --workspace . --lane src
#   {"outcome": "acquire", "auto_picked": true, "lane": "benchmark", ...,
#    "reason": "auto-picked free cluster lane 'benchmark' (requested 'src' was
#               refused: lane 'src' would edit the orchestrator's own running
#               code (src/dos/arbiter.py…) — … (SELF_MODIFY) …)."}
# Two verdicts, one discipline. A free, admissible lane you NAME is granted
# directly — a kindless `--lane` is a soft hint the arbiter honors. `src` is
# different ON THIS REPO: its tree IS the kernel's own running code, so the
# SELF_MODIFY predicate refuses the hint and the arbiter redirects to a free
# disjoint lane, naming the REAL refusal in the parenthetical — never
# double-booking, and never narrating a false "busy" for a lane nothing held.
# A lane actually HELD by a live lease in the WAL refuses same-lane instead
# ("already held"). The lane taxonomy in dos.toml mirrors this repo's
# top-level dirs, so a docs-only edit may run concurrently with a tests edit;
# editing `**/*` is the exclusive `global` lane. Two curated lanes are NOT
# dir-derived: `ci` (`.github/**`) and `meta` (the root meta-docs — CLAUDE.md /
# AGENTS.md / CONTRIBUTING.md / SECURITY.md / README.md, an explicit file list,
# issue #8); both used to fall through to `global` where SELF_MODIFY rightly
# refuses a live loop, so a root-doc edit takes `--lane meta` and runs concurrently.

# 3. verify — did a phase ACTUALLY ship? (the truth syscall, never self-report)
dos verify --workspace . docs/292_readme-audience-gradient-plan P1
#   SHIPPED docs/292_readme-audience-gradient-plan P1 85a3bad (via grep-subject)
dos verify --workspace . docs/99_runtime-validation-and-the-actuation-boundary halt
#   NOT_SHIPPED ... (via none)   ← still in flight; git ancestry has not stamped it
# `source=grep`/`none` is the rung that answered. The verdict comes from git
# ancestry + ship-stamp grammar, NOT from "I'm done" — that is the whole point.
# ⚠ The oracle's evidence base is exactly the VISIBLE ancestry: this public repo
# was seeded fresh 2026-06-10 (history starts at the v0.22.0 commit), so a phase
# stamped before the seed (e.g. docs/82 liveness) answers NOT_SHIPPED via `none`
# — a conservative abstain, not a bug. A history rewrite amputates the grep
# rung's evidence; accept the abstain or re-stamp, never teach the oracle to
# believe a `> **Status:**` sentence instead.

# 4. man — what refusals/lanes does THIS workspace know? (self-describing registry)
dos man wedge          # the closed reason vocabulary → resolver kind
dos man lane           # the lane taxonomy + file trees

# 5. plan — does the plan's CLAIM match the oracle's VERDICT? (check the plan
#          OUTSIDE the loop — the loop must not self-certify against its own plan)
dos plan --once --workspace .          # fan the oracle over every declared phase
dos plan --once docs/292_readme-audience-gradient-plan P1  # or an explicit (plan,phase), no doc needed
#   each row pairs the plan's self-reported status with `oracle.is_shipped` — the
#   headline cell is the OVER-CLAIM (plan says SHIPPED, git says not). This is a
#   verify()-fan-out, NOT a plan reader: a human or supervisor runs it from
#   outside the agent loop, so an over-claiming loop is caught by ground truth, not
#   by re-reading its own narration. Read-only — stores nothing, takes no lease.
#
#   ⚠ ON THIS REPO the board is noisy in one specific, documented way. Since
#   docs/293 the workspace declares its plan dialect (`dos.toml [plan]`), so the
#   harvester DOES parse DOS's own prose plans — the old "(no plans declared)"
#   empty case is gone. But read the ⚠over-claim rows carefully: every plan
#   numbered at or below docs/184 was stamped BEFORE the 2026-06-10 fresh seed,
#   so its ship-stamps live in the amputated ancestry and the oracle answers
#   NOT_SHIPPED via `none` — the step-3 ⚠, now rendered at board scale. An
#   honest "shipped" claim meeting an amputated witness reads as ⚠over-claim:
#   that is "evidence horizon", not "caught lying". Accept the abstain or
#   re-stamp; never teach the oracle to believe the `> **Status:**` sentence. A
#   post-seed plan adjudicates cleanly (docs/293 itself reads ✓shipped off its
#   trailer stamps). Day to day this repo's claims live in COMMIT SUBJECTS, so
#   its working honesty witness is still step 6.

# 6. commit-audit — does each commit's SUBJECT claim match its own DIFF? (the
#          out-of-loop honesty witness THIS repo actually has — author-neutral,
#          plan-free; the docs/228 gate aimed at git instead of a tau2 DB-hash)
dos commit-audit --workspace . HEAD              # one commit: claim vs its diff
dos commit-audit --sweep --workspace . origin/master..HEAD   # the DRIFT RATE over a range
#   The subject is FORGEABLE (whoever wrote the message authored it); the files the
#   commit touched + whether its SHA is a git ancestor are NOT (the commit machinery
#   authored them). That byte-author≠claimant split is the same one docs/228's gate
#   rides — there the witness was the env DB-hash the agent authors 0 bytes of; here
#   it is the DIFF the message-writer authors 0 bytes of. `--sweep` reports the
#   DRIFT RATE (unwitnessed/checkable) + a by-claim-kind grid — "how honest are this
#   repo's commit messages?" — a FLIP off ground truth (docs/179), not a re-read of
#   the narration. It fires CLAIM_UNWITNESSED only where a concrete code/test claim
#   and a contradicting diff coexist, ABSTAINs on the rest, and grades the KIND of
#   change, never CORRECTness (the Wall-3 line). Read-only; the exit code is the
#   verdict (0 clean / 1 a drift found). Run it from OUTSIDE the loop that wrote the
#   commits — a fresh session inheriting "docs/NN shipped" off a forgeable subject is
#   the peer-B handoff (docs/229) the gate protects.
```

## Committing — close the loop without asking (full form)

A commit IS the ship-stamp the oracle reads (`dos verify` answers from git
ancestry, never from working-tree narration), so a finished change that is not
committed is a phase the kernel will report `NOT_SHIPPED`. Therefore: when a
unit of work is complete and the suite is green, commit it — do not stop to ask
permission. Asking first is the exception, reserved for the genuinely
hard-to-reverse or outward-facing: pushing, tagging, a `/release`,
force-pushing, history rewrites, or anything that leaves this machine. A local
commit on `master` is the cheap, reversible act of stamping the work the oracle
verifies. Stay disciplined about scope, the same way the arbiter is: commit
only the lane you actually worked. Stage the specific files your change touched,
never a blanket `git add -A` that sweeps in a concurrent agent's in-flight
edits, and commit with a pathspec. Match the existing commit-subject grammar
(see `git log`). Do not add a `Co-Authored-By` or other agent-attribution
trailer — the default here is no agent co-authors on commits, overriding any
harness default.

## Out-of-scope findings — file an issue (full form)

Do not absorb out-of-scope findings into the current commit, and do not let
them evaporate: the home for deferred work is a GitHub issue, filed in the
moment, then back to your lane. File it with a done-condition (the command or
observable that would show it resolved), a lane guess, and where you found it;
if you cannot state a done-condition it is not an issue yet — label it `design`
(it needs a `docs/NN` plan first) or take it to Discussions. Search for a
duplicate before filing. Issue text is public, and the leak gate never sees it
(the pre-push scan reads tracked files, not `gh` calls) — pipe every drafted
body through `python scripts/leak_scan.py --stdin < draft.md` or
`--text-file <path>` (draft outside the repo — a Bash command that merely names
a guarded kernel path gets refused — then `gh issue create --body-file`). A hit
is a refusal: scrub and re-scan, never post over it; if the scanner is absent,
the hand rule (no dev-machine paths, hostnames, private-process prose) is the
floor. Never close an issue on your own say-so: put `Fixes #N` in the commit
BODY (the subject keeps its grammar) and GitHub closes it when the commit lands
on `master`; a fix without the reference closes only through
`.claude/skills/issue-verify/`. An issue that grows design weight gets a plan
and stays open as the public tracking handle. Triage labels: `ready`
(done-condition present), `design` (plan first), `human-only` (operator
judgment; the fleet skips it).

## Hand the baton (full form)

A finished session usually knows what the next session should do — when the
work leaves a concrete next move, end the final report with a goal prompt the
operator can paste into the next session (`/goal …`); skip it only when nothing
is genuinely queued. A good baton is small and self-grounding: name the handle
(the issue number, `docs/NN` phase, or failing test — never "continue improving
X"); state done as a witness (the command whose verdict closes the goal —
`dos verify …`, a named test going green, a CI run turning green — so the
goal-gate adjudicates from evidence the agent did not author); name the first
command (the cheapest probe that loads the state — often
`python scripts/backlog_triage.py --top 12`, the typed ordered backlog queue).
The memory note is the archive; the baton is the dispatch.

**Use this loop when working in here, not just to demo it.** Before claiming a
`docs/NN_*.md` plan-phase is done, `dos verify` it — the contract says the
oracle answers from git, so let the oracle, not your narration, close the phase.
And after committing, `dos commit-audit` the work (step 6) — the oracle witnesses
that a *phase* shipped; commit-audit witnesses that each *commit's subject* is
backed by its own diff. Two witnesses, both reading bytes the claimant did not
author (git ancestry; the diff), both run from outside the loop that wrote the
claim — that is the docs/228 lesson applied to this repo: the only witness worth
trusting is one the claimant can't forge, and on this repo that is git, never a
`> **Status:**` sentence or a commit subject taken at its word.
When two changes touch different top-level dirs, they are disjoint lanes and safe
to land independently; when one touches `src/dos/`'s own running path, that is the
`SELF_MODIFY` / `global`-lane hazard the kernel is built to refuse. Eating our own
dog food is the cheapest proof that the litmus tests still hold: if
`dos verify` ever needs a plan to answer, or `dos doctor` ever stops seeing this as
a git workspace, a contract has drifted from the code.
