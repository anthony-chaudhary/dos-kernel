# The drift-rate scoreboard — methodology

**Short version:** we check whether an AI agent's commit message matches what
its diff actually did. The message can say anything; the diff can't. Below is
the long version — exactly what the check reads, what it skips, and every time
the check itself was wrong (we fixed the check, never trusted the subject).

> **What it measures:** how often a commit written by an AI coding agent makes
> a concrete claim in its subject line ("fix X", "add tests for Y") that the
> commit's **own diff does not witness** — an empty commit claiming a fix, a
> "tests pass" subject that touches no test file. We call that a **drift**:
> the claim rests on the message text alone, which the claimant authored, and
> not on the diff, which the claimant could not fake. The **drift rate** is
> `unwitnessed / checkable` over a corpus of public repositories with
> machine-attributable agent commits.
>
> This page is published **before** any number, and is the contract every
> report links back to. Plan: `docs/307_drift-rate-scoreboard-plan.md`;
> tracking issue: [#66](https://github.com/anthony-chaudhary/dos-kernel/issues/66).

## 1. The witness — what is actually checked

The per-commit verdict is `dos commit-audit`
([`dos.commit_audit`](../../src/dos/commit_audit.py)), which grades the
relationship between a commit subject's **claim kind** and its diff's
**shape**. The split it rides: the subject is authored by whoever wrote the
message (forgeable); the list of files the commit touched is authored by the
commit machinery (not forgeable).

- A **code-effect claim** ("fix/add/implement/remove …") is witnessed by a
  touched source file, or — for a CI-scoped claim like `fix(ci):` — by the CI
  config it names.
- A **test claim** ("add tests", "tests pass") is witnessed by a touched test
  file, and net-deleting test lines while claiming green is flagged.
- A **doc claim** is witnessed by any touched file; doc-scoping is honest.
- A subject with **no checkable claim** ("wip", "merge", "bump deps")
  **abstains** — it is excluded from the denominator entirely.

`CLAIM_UNWITNESSED` fires **only** where a concrete claim and a contradicting
diff coexist. The verdict is deliberately conservative: the verb taxonomy is
tight, unknown file types count as source (fewer fires), and every verdict
carries the files that decided it, so it is inspectable, never just a number.

**What it can never grade (the advisory line):** correctness, quality, or
intent. A real fix to the wrong bug passes; an honest doc-only cleanup with a
sloppy subject can fire. Drift is a **claim-vs-diff mismatch — never a
correctness or malice grade.** Anyone treating one repo's drift number as an
accusation is misreading this methodology, and this page is the
prior-published evidence that they are.

## 2. False positives — characterized first, from our own history

We ran the witness over this repository's own full visible history before
pointing it at anyone else's, then hand-adjudicated **every** fire:

- The raw sweep (2026-06-12) flagged 7 of 112 checkable claims (6.25%).
  **4 of the 7 were auditor over-fires** in two specific classes: a
  "testing" noun inside a documentation title out-ranking the `docs(...)`
  scope ([#79](https://github.com/anthony-chaudhary/dos-kernel/issues/79)),
  and a config/manifest edit (`pyproject.toml`, a `*.json` manifest, a
  lockfile) not counting as witness for a `fix:`/`feat:` claim even though
  the edit *was* the claimed change
  ([#81](https://github.com/anthony-chaudhary/dos-kernel/issues/81) — in an
  external 1,188-commit pilot this one class produced **98 of 99 raw
  flags**, reading 10–30× the adjudicated rate).
- Both classes were fixed the only allowed way — by **narrowing the fire**,
  never by believing the subject: a config/data diff now witnesses a
  code-effect claim on its own honestly-typed rung (`data-witnessed`, one
  step below `diff-witnessed`), and an explicit doc head wins before the
  test-noun window. The first documented over-fire of this kind —
  [#4](https://github.com/anthony-chaudhary/dos-kernel/issues/4), a
  `fix(ci):` commit whose entire (real) fix was a `.gitattributes` line —
  was fixed the same way, after one false positive in a small sweep
  produced a 100% drift headline. Lesson: **small denominators lie**; we
  report denominators everywhere and set a floor on corpus membership.
- After the fixes, this repository's own sweep reads **3 unwitnessed of 129
  checkable (2.3%)** — and all three are deliberate empty "re-stamp"
  commits, a workspace convention here where an empty commit's subject
  re-anchors a plan phase after a renumber. The witness is *right* that
  those claims rest on subject text alone; the intent is benign. Lesson: a
  raw drift rate includes convention-driven subject-only commits, not just
  over-claims.

So: expect a few points of any reported drift rate to be auditor
imprecision and convention noise rather than over-claiming. That is why the
number is advisory, why per-commit verdicts stay inspectable, and why the
report never grades an individual repository.

## 3. The attribution gate — which commits count as agent-authored

A commit enters the audit only if it is **machine-attributable** to an agent
toolchain by identity bytes a human does not normally write:

- a `<slug>[bot]@users.noreply.github.com` author/committer (the GitHub App
  form) for a closed slug set: `devin-ai-integration`, `copilot-swe-agent`,
  `copilot`, `claude`, `sweep-ai`, `google-labs-jules`, `openhands`,
  `openhands-agent`, `cursoragent`, `openclaw`, `clawsweeper`, `roomote`,
  `opencode-agent`;
- a toolchain's own attribution email, byte-verified against real history
  before entering the set: `noreply@anthropic.com` (Claude Code),
  `openhands@all-hands.dev`, `qwen-coder@alibabacloud.com`,
  `crush@charm.land`, `roomote@roocode.com`, `cursoragent@cursor.com`, the
  `aider.chat`/`aider.dev` domains, and `NNN+Copilot@users.noreply.github.com`;
- the explicit aider name form (`… (aider)`), the conjunctive Codex identity
  (name `Codex` AND `noreply@openai.com` — neither half alone), a
  `Co-authored-by:` trailer carrying any of the above, or the
  `Generated with [Claude Code]` footer.

**Under-matching is the rule.** A bare human name ("Claude", "Claudette")
never matches. Bots that are not coding agents (dependabot, renovate,
github-actions) are out. AI *review-suggestion* bots (gemini-code-assist,
ellipsis-dev, cubic-dev-ai, gru-agent, pre-commit-ci) are **deliberately
excluded** in v1: a human-authored commit that applied a reviewer's
suggestion is not an agent-authored commit. A missed agent commit shrinks
the sample; a human commit wrongly counted would poison the claim — so every
uncertain marker stays out. The full set is code, in
[`scripts/drift_scoreboard.py`](../../scripts/drift_scoreboard.py), with its
negative space pinned by tests.

## 4. Corpus selection — mechanical, published, claimant-neutral

A repository enters the corpus if and only if:

1. it has **≥ 20 machine-attributed commits** within the newest-10,000-commit
   scan window of its default branch (the small-denominator floor);
2. it has **≥ 500 stars** (the aggregate is about the practice, not about
   small personal projects);
3. it was **pushed within the last 90 days** (active);
4. it is **not a fork**; and
5. the DOS project has **no relationship in flight** with it (no open
   outreach PR, no pending listing request — we do not grade whom we are
   simultaneously courting).

Candidate pools are enumerated at run time — GitHub commit search over the
attribution markers plus public lists of agent-built projects — and the
filter above is applied mechanically. The corpus is intentionally a
**convenience sample of visible, attribution-honest projects**, not a random
sample of all agent-assisted development; repos that strip agent attribution
are invisible to this method, and the report says so.

## 5. The ethics floor (v1)

- **Aggregate-only.** No repository is named, in any artifact, without
  opt-in. This is structural, not editorial: the aggregation fold never
  receives repo names, URLs, or commit SHAs (a SHA is globally searchable,
  so it would name the repo) — pinned by
  `tests/test_drift_scoreboard.py::test_aggregate_is_identity_stripped`.
- **Advisory framing throughout.** Drift ≠ dishonesty (§1, §2).
- **Self-graded first.** Our own number, with the FP adjudication, is in §2,
  and any repo can compute its own in one command (§6).
- Future per-repo reporting, if it ever happens, is contact-first and
  opt-in. Not part of v1.

## 6. Reproduce it

For your own repository (no corpus machinery needed):

```bash
pip install dos-kernel
dos commit-audit --sweep --workspace . BASE..HEAD   # the same verdict, your history
```

For a corpus run (the exact form used for the published reports —
parameters are stated in each report):

```bash
python scripts/drift_scoreboard.py --corpus corpus.txt --out out/ \
    --scan-limit 10000 --audit-limit 300
```

`corpus.txt` is one repo per line. Per-repo verdict JSON lands in
`out/per-repo/` (kept private under the v1 floor); the identity-stripped
`aggregate.json` + `aggregate.md` are the publishable artifacts.

## 7. Known limitations

- The claim grammar is English-only; non-English subjects abstain (they
  shrink the denominator, they do not distort the rate).
- The scan window is the newest 10,000 default-branch commits; the audit cap
  per repo (stated in each report) bounds one giant repo's weight.
- Squash-merged PRs attribute to the human merger unless a trailer survives
  — another reason the sample under-counts agent work.
- The rate describes the corpus it was run on (a description, not a
  generalization), and each report states its corpus size and date.

## 8. Register your repo — the opt-in per-repo tier

The aggregate report names nobody (§5). The **opt-in tier** is the one place
a repository is named, and only because its own maintainer asked. Registering
publishes two static artifacts under `/scoreboard/<org>/<repo>/`, both
generated by [`scripts/scoreboard_surfaces.py`](../../scripts/scoreboard_surfaces.py)
(docs/312, issue [#85](https://github.com/anthony-chaudhary/dos-kernel/issues/85)),
never hand-written:

- **`verdict.json`** (schema `dos-scoreboard-verdict/v1`) — the machine
  endpoint: the audited range pinned to a head SHA, the
  witnessed/unwitnessed/abstained counts, the receipt SHAs behind any
  unwitnessed claim, the grader version that produced it, and a link back to
  this page. An agent can fetch it before believing a dependency's
  agent-written changelog.
- **`badge.json`** — a shields.io endpoint object derived from the verdict by
  a pure function (the badge can never say what the verdict does not):
  `commit-claims: audited clean (as of <date>)`, or the honest counts. Embed:
  `https://img.shields.io/endpoint?url=<served URL of badge.json>`.

The flow, end to end:

1. **Ask** — open a [scoreboard request](https://github.com/anthony-chaudhary/dos-kernel/issues/new?template=scoreboard-request.yml)
   naming your `<org>/<repo>`. Registration must come from someone with
   write access to that repository — the issue author's GitHub identity is
   the check.
2. **Right of reply, before publication** — the sweep runs, and you see both
   artifacts (and the per-commit verdicts behind them) on the issue *before*
   anything publishes. This is constitutive, not a courtesy: a verdict that
   has not survived its subject's reply does not publish.
3. **Publish** — the artifacts land under `/scoreboard/<org>/<repo>/` and
   you embed the badge. Until a scheduled cadence exists
   ([#84](https://github.com/anthony-chaudhary/dos-kernel/issues/84)),
   re-sweeps run on request and on dispute, and every artifact states its
   as-of date and audited range — a stale badge is honest about *when* it
   was true.
4. **Leave or dispute, same door** — delisting is the same form, honored
   without argument. A dispute names the SHA; it is re-adjudicated against
   this page, and the grader version in the verdict says exactly what fired.
   Where the auditor was wrong, the fix is the only allowed kind — narrow
   the fire (§2), then re-sweep everyone affected.

This repository is entry #1, registered by its own maintainer the same way —
its badge sits in the README and honestly reads non-zero
([§2](#2-false-positives--characterized-first-from-our-own-history): three
deliberate empty re-stamps), which is exactly the credibility the tier runs
on. Nobody is graded to make the scoreboard look used; the tier grows only by
the door above.
