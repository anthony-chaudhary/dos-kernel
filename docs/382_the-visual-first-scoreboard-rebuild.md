# 382 — the visual-first scoreboard rebuild: lead with the verdict, put the hashes in a drawer

> The scoreboard's idea is good. Its front door is wrong. Today the first thing
> a person meets on every per-repo page is two 40-character commit hashes and a
> pile of house words — *drift*, *fire*, *rung*, *unwitnessed*, *re-stamp*. No
> stranger cares about a hash; a hash looks exact and says nothing. This plan
> keeps the finding (which is already mostly plain and is the board's whole
> value) and rebuilds the *presentation* around one rule: **lead with the
> verdict and the relatable failure; demote every hash, SHA, version string, and
> internal word into a drawer the skeptic can open and everyone else can skip.**
> It does not change a single verdict, touch `src/dos/`, or weaken any of the
> ethics guardrails that let us name other people's repos. It is a repaint, a
> reorder, and a set of deletions — not a new measurement.

*Status: this plan is the decisions (§§1–6) and the phased rebuild (P1–P5). It
edits `scripts/` and `docs/` only — never `src/dos/`, never the audit verdict.
Companion: the private positioning note that ranks the points DOS makes
("lead with / bury") is the why behind §3; this plan is the what and the how.
Parent threads: [docs/307](307_drift-rate-scoreboard-plan.md) (methodology),
[docs/311](311_scoreboard-per-repo-index-plan.md) (the per-repo index + ethics
floor), [docs/312](312_scoreboard-consumption-surfaces-plan.md) (surfaces).*

## 0. The facts this rests on

There are **two** boards today, and a person meets both:

1. **The drift scoreboard** (`docs/scoreboard/`) — runs `dos commit-audit` over
   agent-written commits in 19 popular repos and answers "how much did AI write,
   which agent, and did each commit do what it said." The front page
   ("How AI built the software you already use") is genuinely good and mostly
   plain. The rot is one click deep, on every per-repo page.
2. **The kernel charts** (`docs/assets/kernel/`) — "DOS by its own numbers,"
   folded live from this repo's own `.dos/` logs: the disbelief funnel, the
   self-modify wall, the memory-recall chart.

The diagnosis is the same for both, and it is narrow: **the concept is fine and
the top-level pitch is fine; the failure is that engineer artifacts — hashes,
SHAs, version strings, internal vocabulary, raw Python module names, journal
file paths — ride along onto surfaces aimed at people who will never run the
command.** The message-vs-diff *idea* is translated well on the front pages
("a commit message is just text the agent typed; the diff is what git actually
recorded"). The problem is never the idea. It is the raw receipts presented as
if they were the headline.

Two more facts that bound the work:

- **The scoreboard adds no kernel surface.** All of it lives in `scripts/` +
  `docs/` and imports `dos` one-way ([CLAUDE.md] layering law). This rebuild
  stays there. It must not edit `src/dos/` and must not change what
  `dos commit-audit` returns (fire-narrowing has its own issues).
- **The self page and the per-repo pages are *generated*** (the docs/311 P2
  renderer; the self page byte-reproduces from the tool). So "fix the page"
  means "fix the renderer + regenerate + update the fixture," not hand-edit the
  Markdown. Same for the kernel charts (`scripts/kernel_charts.py`).

## 1. The one principle

**Visual-first, verdict-first, hash-last.** Every human surface leads with:

1. a **plain-word verdict** (a person gets it in one glance), then
2. the **picture** (a number band or a bar), then
3. the **plain finding** in one sentence, and only then, behind a fold,
4. the **receipts** — the SHAs, the method, the version, the exact reproduce
   command — for the one reader in fifty who will check.

A hash is a footnote that *proves* a finding, never the finding. If a viewer has
to know what `abe74e88…` means to read the page, the page failed.

## 2. The guardrails this rebuild must NOT break

A repaint is dangerous precisely because "simplify" can delete the wrong thing.
These are not style; they are the ethics/legal floor that lets us name other
people's repositories. **Every one of these stays. Several are test-pinned.**

- **Never name a non-clean or unadjudicated foreign repo — anywhere.** A foreign
  page renders only on a CLEAN/adjudicated verdict; otherwise no page, no name,
  counted in the aggregate only (docs/311 §2).
- **A non-clean verdict never publishes from a raw flag.** Raw and adjudicated
  stay as two distinct numbers; the headline derives from adjudicated only
  (docs/311 §4). Keep both columns — do not collapse them to look cleaner.
- **Self-graded first, and honestly non-zero.** Our own page exists before any
  foreign one and shows its real flags, not an airbrushed zero (docs/311 §0).
- **Right of reply is constitutive.** A foreign verdict does not publish until
  its subject has seen it; delisting is the same door, honored without argument
  (docs/311 §1). This rebuild does **not** mass-rewrite foreign pages —
  presentation changes to a foreign page still go through the same door.
- **A mismatch is not an accusation, stated where the number is.** Drift ≠ wrong
  ≠ dishonest ≠ intent. Keep that sentence adjacent to every headline number.
- **The banned-word set stays banned.** No *honest / dishonest / lying /
  deceptive* on any surface. (The *kept* words — *witnessed/unwitnessed* — are
  revisable for plainer ones; see §6.)
- **No single gameable score / letter / grade-color.** The "grade" is the rate
  with its denominator plus receipts. We may add clearer visuals; we may not
  reduce a foreign verdict to one number or one stoplight color.
- **Aggregate stays identity-stripped by construction** (the fold never receives
  names/URLs/SHAs; test-pinned). Demoting SHAs on *named, published* pages is
  fine; the *aggregate* must keep receiving none.
- **Inspectable to the bottom.** Receipts, denominators, an as-of range, and a
  one-command reproduce must all still exist. We are **demoting** them into a
  drawer, **never deleting** them — deleting them breaks the credibility the
  whole board runs on.

> The split to hold in your head: *the presentation is repaint-able; the
> underlying constraint behind it is usually an ethics guardrail.* When a change
> feels like "this looks cleaner," check it against this list first.

## 3. What's reasonable at our actual stage (be honest about size)

The board must not borrow scale it doesn't have. Today:

- The drift board names **19 repos** (18 third-party + our own self page). That
  is a pilot, not a census. Say "19 repos we've audited so far," never imply a
  population truth.
- The kernel charts are **one repo's local logs** — tiny-n. The big absolute
  counts (`79,986` calls, `1,149` entries) invite a reader to over-read scale.
  Put the honest "this is one repo's own log" line *next to each picture*, not
  only in a methodology footer — or stop showing the big raw counts.
- Some surfaces refresh by hand, not CI (the gh-pages publish; the kernel charts
  have no byte-check gate). Where a page makes an "as-of" promise, it must be
  one the cadence can actually keep, or it reads as stale and dishonest.

The early-stage posture is a feature, not an embarrassment — *if stated.* "Here
is a careful pilot over 19 repos, and our own number first" beats a number that
pretends to be the whole world.

## 4. The per-element verdict

Legend: **keep** · **explain** (keep, add one plain sentence) · **simplify** ·
**demote** (move below the fold / into a drawer) · **delete** (from the human
surface).

### 4a. The drift scoreboard

| Element | Call | What to do |
|---|---|---|
| Front README headline + message-vs-diff pitch | **keep** | The model for everything else. Hash-free, plain, hooks the reader. |
| Stat-band SVG (19 repos · 66,645 commits · 4% · 99.7%) | **keep** | Cleanest artifact on the board. Four numbers, four plain labels. |
| `ai-share.svg` / `claim-kinds.svg` | **keep** | Ranked bars, plainly labeled, legible in seconds. |
| `agent-mix.svg` | **explain** | Add "these are all AI coding tools" caption; the no-number normalized bars hide absolute scale — add a tooltip/figure note. |
| "Who builds whose repo" (the 12-of-18 vs 19 wording) | **explain** | Reconcile the count out loud: "18 third-party repos + our own self-graded page = 19." |
| "Repo by repo" table | **explain** | Explain the self row's `—` AI-built ("we audit *all* our commits, not just agent ones") and make the backed % consistent with the per-page number (see §6). |
| Per-repo page **"As of" block leading with two 40-char SHAs** | **demote** | The single worst front door. Lead with "audited the last 500 commits, ending 2026-06-16." Move the SHAs, auditor version, and "#79/#81 fire-narrowing" line into a collapsed *Technical provenance* detail. |
| Per-repo **receipts table keyed on SHAs + `CONFIRMED(convention)` + `Rung`** | **simplify** | Lead each row with a plain one-liner ("an empty commit that re-labels an old plan — no code changed"). Demote the SHA, ruling code, and rung to secondary columns. Keep the row (inspectability guardrail). |
| Per-page **"Reproduce it"** (clone + checkout SHA + command) | **demote** | Collapse into a *Reproduce exactly* drawer. Noise to everyone who won't run it; essential to the one who will. |
| `badge.json` — orange, "5 unwitnessed of 315" | **simplify** | Orange = "warning" contradicts paragraphs insisting these are benign. Use neutral wording ("5 of 315 message-only claims") and a neutral color. Keep the non-zero honesty. |
| methodology.md — leads with *drift*, plan #s, issue #s, slug/email lists | **simplify** | It's the contract, so it can be dense — but lead with the plain "Short version," push the bot-slug/email attribution lists and schema names into a collapsed appendix. |
| `report-2026-06.md` (12 repos, 1.2% drift) **vs** `rollup.md` (19 repos, 99.7% backed) | **explain** | The biggest trust bug: two "aggregate" stories, different corpora, no bridge. Add a one-line signpost at the top of each ("this is the larger anonymous snapshot — separate from the named 19-repo board") and a hierarchy on the README so a reader knows which is which (see §6). |
| Foreign page "what a mismatch would have looked like" example box | **keep** | Teaches the method better than the entire methodology page. Make it the template. |

### 4b. The kernel charts ("DOS by its own numbers")

| Element | Call | What to do |
|---|---|---|
| Disbelief funnel | **explain** | The keeper. But label the unexplained 24% slate slice in plain words (or fold it so green truly dominates), drop the raw verb "pretool," and put the tiny-n note beside the picture. |
| Self-modify wall — "0 got through" | **keep** | Crisp, falsifiable, human. |
| Self-modify wall — **ranked bar by Python module name** (`arbiter.py` 221 …) | **delete** | The hash-equivalent on this surface: precise-looking, meaningless to a person, and misleading (a reader reads "agents attacked arbiter.py 221 times"; it's really the guard quoting its own filename — the README concedes this). Drop it; keep the wall + the zero. |
| Memory-recall chart | **explain** | The concept ("it won't even trust its own memory") is the most compelling of the three, but the data fights the headline: 72% land in "unverifiable." Define "unverifiable" beside the picture and say why it's expected, or the chart undercuts itself. State n=75 next to it. |
| PNG rasters | **keep** | Fallback for surfaces that can't render SVG; re-rasterize whenever the SVGs change. |
| `.dos/*.jsonl` source-path footers, `override-admit`/`reason_class` tokens | **demote** | Engineer plumbing on a reader surface → one methodology footnote. |
| The fold/render/build/test pipeline | **keep** | Sound (pure fold, no authored numbers, fail-soft, test-pinned). The fixes above live in `kernel_charts.py` / `kernel_metrics.py`. |

## 5. The proposed front door (what a person actually meets)

**Per-repo page, top to bottom (the new order):**

1. **One plain headline** — "Across roborev's last 668 commits, AI wrote 65%,
   and every one of the 273 commits that made a checkable claim did what it
   said." No SHA, no *drift*, no *unwitnessed*.
2. **The mismatch example box** — "here's what a flagged commit would have
   looked like" (`fix: handle null user` → touched 0 files). This stops a clean
   page from reading as "we found nothing, why bother."
3. **The non-accusation line**, right there: "a mismatch only means a commit's
   words and its own diff disagree — never that the code is wrong or anyone
   lied."
4. **One small at-a-glance line** — "audited 668 commits up to 2026-06-16."
5. **▸ Technical provenance & reproduce exactly** (collapsed) — the BASE→HEAD
   SHAs, auditor version, the `dos commit-audit --sweep` command, the per-flag
   SHA receipts. Everything the skeptic needs, nothing the stranger must read.

**The board root** keeps its strong front page. Add one fix: a clear hierarchy
so a reader isn't routed to two contradictory "aggregate" reports — one
*headline* number (the named 19-repo rollup) with the larger anonymous snapshot
clearly labeled as a separate, deeper read.

**The kernel charts** lead with the disbelief funnel and "0 got through," drop
the module-name bar, and carry the "one repo's own log" honesty beside each
picture.

## 6. The specific number/wording bugs to fix (independent of the repaint)

These erode trust on click-through and are worth fixing even before the bigger
rebuild:

- **12-vs-19 and 18-vs-19.** Reconcile every count in prose; add the
  "18 third-party + 1 self = 19" note.
- **98% vs 98.4% vs 99.7%.** The self repo reads three ways across the table,
  its own page, and the pooled rollup. Pin one rounding rule and one phrasing;
  explain that pooled ≠ per-repo.
- **The two aggregate reports.** Signpost both; pick the headline one.
- **The orange "unwitnessed" badge.** Neutral color + plain words.
- **The collision number framing.** "6 of 8" vs "all 6 across 8 pairs" — one
  phrasing everywhere (this one is in the README/why-a-referee, not the
  scoreboard, but it's the same class of bug; fold it in).
- **"Raw rate" vs "Final grade" columns** that show the same number on the self
  page — collapse or label why two exist.

## P1 — the per-repo page repaint (renderer + self page + foreign template)

**Files:** `scripts/scoreboard_page.py` (or the renderer of record),
`docs/scoreboard/anthony-chaudhary/dos-kernel.md` (regenerated),
`tests/test_build_scoreboard_pages.py` / `tests/test_scoreboard_*` (fixtures).

Reorder the per-repo page to §5: plain headline + mismatch example +
non-accusation line + at-a-glance line on top; SHAs, version, ruling codes,
reproduce command into a collapsed *Technical provenance* drawer. Keep raw +
adjudicated both visible. Regenerate the self page from the tool so it
byte-reproduces. Foreign pages re-render from the same template but publish only
through the right-of-reply door (no silent mass rewrite).

*Done-condition:* the self page leads with a plain headline and no SHA above the
fold; the SHAs/receipts still exist in the drawer; suite green; the self page
byte-reproduces from the renderer.

## P2 — the aggregate hierarchy + the count/rounding reconciliation

**Files:** `docs/scoreboard/README.md`, `docs/scoreboard/report-2026-06.md`,
`docs/scoreboard/rollup.md`, `scripts/scoreboard_rollup.py` (if the rounding
lives there), the relevant tests.

Add the one-line signpost to each aggregate; pick the headline number; fix
12/18/19 and 98/98.4/99.7 everywhere; explain the self row's `—`.

*Done-condition:* a reader clicking from the README to either aggregate sees a
plain "what this is and how it differs" line; no count or backed-rate disagrees
across surfaces without an explanation.

## P3 — the badge + methodology trim

**Files:** the badge generator + `docs/scoreboard/anthony-chaudhary/dos-kernel/badge.json`,
`docs/scoreboard/methodology.md`.

Neutral badge color + plain wording (keep the non-zero number). Methodology
leads with the plain "Short version"; bot-slug/email lists and schema names move
to a collapsed appendix. Keep every guardrail statement.

*Done-condition:* the badge no longer reads as a failing grade at a glance;
methodology's first screen is plain English; nothing in §2 was removed.

## P4 — the kernel charts repaint

**Files:** `scripts/kernel_charts.py`, `scripts/kernel_metrics.py`,
`docs/assets/kernel/README.md`, regenerated SVGs + PNGs,
`tests/test_kernel_charts.py`.

Label or fold the funnel's 24% slice; drop the module-name bar from the
self-modify wall; define "unverifiable" and state n beside the memory chart;
move journal paths/verb tokens to a methodology footnote; put the
"one repo's own log" line next to each picture.

*Done-condition:* every chart's headline is supported by what the picture
actually shows; no raw module name or journal path appears as a headline; the
no-authored-numbers invariant still holds (tests green).

## P5 — the published-site sync

**Files:** the gh-pages publish path + `scripts/check_published_site.py`.

Re-publish so the live site matches the rebuilt source, and verify assets don't
404 (the known hand-copy drift; see the gh-pages publish memory).

*Done-condition:* `scripts/check_published_site.py` passes against the published
site after the rebuild lands.

## Out of scope

Any change to `src/dos/` or to what `dos commit-audit` returns (fire-narrowing
is its own issue track); adding new audited repos; the opt-in badge endpoint
mechanics (docs/312 / #85 own them); foreign-page *content* beyond the
presentation template (still governed by right-of-reply); and the broader
README/landing-page rework (this plan is the scoreboard surfaces — the
points-ranking that informs the whole site's front door is tracked separately).
