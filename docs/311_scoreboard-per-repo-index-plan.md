# 311 — the standing per-repo scoreboard index: a page + adjudicated verdict per repository

> docs/307 ships the drift-rate methodology and ONE aggregate report. This
> plan decides the deferred rollout rung behind it (issue
> [#84](https://github.com/anthony-chaudhary/dos-kernel/issues/84)): the
> scoreboard as a **standing per-repo index** — a page per repository, each
> carrying the *adjudicated* drift verdict plus its receipts, refreshed on a
> cadence — instead of a one-shot report. The exemplars that turned scoring
> into distribution all made this exact move: Snyk Advisor (a page + health
> verdict per package), OpenSSF Scorecard (public grades over other people's
> repos, cron-refreshed, methodology published), deps.dev / libraries.io (the
> page-per-entity move over public data). A report is news for a day; an
> index is infrastructure — each indexed repo's own name becomes a landing
> query. This plan decides the four questions the issue names: tier
> sequencing, the adjudication pipeline at index scale, freshness +
> revocation, and the grade vocabulary — and ships page #1, graded against
> this repository itself, before anyone else is named.

*Status: P1 (the decisions, the page schema, and the self-graded page #1)
ships with this plan. P2–P5 are open, each behind the gate its section
names. Companion issue
[#85](https://github.com/anthony-chaudhary/dos-kernel/issues/85) owns the
machine half (the opt-in badge + per-repo `verdict.json`); this plan owns
the human-readable page. Parent thread:
[#66](https://github.com/anthony-chaudhary/dos-kernel/issues/66).*

## 0. The facts the design rests on

- **The verdict and the sweep already exist; this plan adds NO kernel
  surface.** The per-commit witness is `dos commit-audit` and the corpus
  sweep is the docs/307 tool (`scripts/drift_scoreboard.py`, with the
  methodology contract at
  [`docs/scoreboard/methodology.md`](scoreboard/methodology.md) — both
  shipped under docs/307 P1–P2, alongside its P3 aggregate report). A
  scoreboard page is a rendered document; the renderer (P2) is `scripts/`
  dev tooling that `import dos`, the usual one-way arrow.
- **Raw flag rates are unpublishable — measured, twice.** The #66 pilot
  (1,188 commits, 3 repos) read 10–30× the adjudicated rate before the
  [#81](https://github.com/anthony-chaudhary/dos-kernel/issues/81) /
  [#79](https://github.com/anthony-chaudhary/dos-kernel/issues/79)
  fire-narrowing — 98 of 99 raw flags were auditor artifacts. The standing
  anti-rule this plan inherits and makes structural: **a non-clean verdict
  never publishes from a raw flag** (§2).
- **The index is mostly green, and that is the point.** Curated,
  human-reviewed OSS adjudicates ~clean (the pilot's finding). Like Snyk
  Advisor, where most packages grade healthy, a page is a wall of
  receipts-backed green a repo can *want* — not a wall of shame. The rare
  non-clean entry carries its receipts or does not render at all.
- **The binary badge cannot do this job.** docs/112's kill-list rule stands:
  a pass/fail badge must never be aimed at a foreign repo (it would
  false-accuse the abstain majority). A *page* has the room a badge lacks —
  it can say "raw-only, no grade", show denominators, and itemize receipts.
  The page is the three-state-safe surface; the badge (docs/BADGE.md, #85)
  stays an opt-in adoption mark on the repo's own README.
- **Self-graded first is a hard ordering rule.** The index never names a
  foreign repository before this repository's own page exists and shows its
  own receipts. Page #1 (P1, shipped with this plan) is this repo — and it
  honestly reads non-zero (3 unwitnessed of 120 checkable), which is exactly
  the credibility the index runs on.

## 1. Decision — tier sequencing (opt-in vs. public-data grading)

Three tiers, strictly in order. A tier opens only when the one before it has
shipped and its gates hold.

| Tier | Who is indexed | Gate |
|---|---|---|
| **0 — self** | This repository only. The worked example, the proof the schema renders, and the standing demonstration that we publish our own number first. | Ships with this plan (P1). |
| **1 — opt-in** | A repo whose owner asks (a `scoreboard-request` issue), or that already wears the DOS gate (docs/BADGE.md) and requests the page. The owner sees the rendered page **before** it publishes (right of reply is constitutive, not a courtesy). | P4 — needs the P2 renderer + the #85 `verdict.json` shape agreed, so the page and the machine endpoint state the same verdict. |
| **2 — curated public set** | The Scorecard move: repos meeting the docs/307 methodology's published corpus criteria, no opt-in. This **reverses docs/307's aggregate-only v1 rule — deliberately, and only here.** | P5 — operator-gated. Prerequisites, all of them: the methodology page live on the Pages site; #81 and [#82](https://github.com/anthony-chaudhary/dos-kernel/issues/82) landed; the §2 pipeline operational end-to-end on tiers 0–1; the right-of-reply + correction path documented and exercised at least once; an operator sign-off recorded outside this repo. |

Repos with a DOS outreach relationship in flight stay excluded at every tier
(the methodology's conflict rule, carried over verbatim): the grade and the
courtship stay apart.

## 2. Decision — adjudication at index scale

Hand-adjudication (the pilot method) does not scale to a refreshed index.
The pipeline is the trust ladder, applied per raw flag:

1. **Deterministic narrowing (the auditor itself).** The #79/#81 classes are
   fixed in code — the dominant artifact classes never reach the pipeline
   because the auditor no longer fires on them. Every future artifact class
   found gets the same treatment: narrow the fire in `dos.commit_audit`,
   never post-process the verdict.
2. **The JUDGE rung (advisory, fail-to-abstain).** Each surviving flag goes
   to the `dos.judges` seam with one question only: *is this flag an auditor
   artifact?* A judge ABSTAIN leaves the flag **unadjudicated** — which
   blocks publication of a non-clean verdict (it never converts to
   "confirmed", and never to "clean"). Fail-to-abstain here means
   fail-to-unpublishable.
3. **The HUMAN rung.** Any non-clean verdict on a *foreign* page (tiers 1–2)
   requires a human review of the judge-confirmed residue plus the
   right-of-reply window before the page renders it. The self page (tier 0)
   substitutes the operator's own adjudication note, in public, with the
   receipts (page #1 does exactly this).

Every flag carries a typed **adjudication record**:
`{sha, raw_fire, ruling, rung, rationale}` with the closed ruling set
`AUDITOR_ARTIFACT` (names the issue that narrows the fire) /
`CONFIRMED` (with a class: `convention` — a documented workspace convention
writes subject-only commits deliberately, e.g. this repo's re-stamp commits —
or `unexplained`) / `UNADJUDICATED`. The structural rule, pinned by test in
P2: **the renderer refuses to render any non-clean verdict from a flag
without a `CONFIRMED` adjudication record.** A tier-2 page with any
`UNADJUDICATED` flag does not publish at all (it is listed as "pending
adjudication" in the index root, with no numbers).

## 3. Decision — freshness + revocation

- **Cadence:** a scheduled CI re-sweep, weekly, re-renders every indexed
  page (tier 0 from P3 on; tiers 1–2 as they open). Every page states its
  **as-of block** — the exact range (`BASE..HEAD` SHAs), the render date,
  the auditor version — so a page is a pinned claim about a pinned range,
  never a floating one.
- **Staleness:** a page older than two cadence windows renders a stale
  banner. The as-of block is load-bearing, not decoration.
- **Correction path:** a `scoreboard-correction` issue template. A contested
  flag gets a `contested` marker on the page (visible while open), a
  re-adjudication through §2, and a re-render. A correction that reveals an
  auditor artifact class feeds rung 1 (the fire narrows in code) — the same
  flag class can then never mis-fire on anyone else.
- **Revocation:** a tier-1 (opt-in) repo may leave the index on request —
  opt-in means revocable. A tier-2 page changes only through the correction
  path, never through displeasure (the Scorecard precedent); tier 2 is
  operator-gated until that posture is defensible end-to-end.

## 4. Decision — the grade vocabulary (and the naming ethics it encodes)

**No scores, no letters, no colors.** Scorecard's 0–10 invited
score-chasing; any threshold over a drift rate is arbitrary and gameable,
and small denominators lie. The "grade" is the **adjudicated verdict
itself** — a rate with its denominator, plus itemized receipts — under a
closed three-value headline:

- **`CLEAN`** — zero confirmed unwitnessed claims in the audited range
  (artifacts excluded by adjudication, each exclusion receipts-linked).
- **`DRIFT REPORTED — n of m checkable`** — at least one `CONFIRMED` flag;
  every one itemized with its SHA, subject, class, and rationale. On a
  foreign page this headline requires the full §2 pipeline (judge + human +
  right of reply) — never a raw flag.
- **`RAW-ONLY — NO GRADE`** — flags exist whose adjudication is incomplete.
  A self or opt-in page may render this state honestly; a tier-2 page in
  this state does not publish.

The word set on every page stays **witnessed / unwitnessed / abstained /
drift / auditor artifact / convention** — never honesty, dishonesty, or
lying (the Wall-3 line: the witness grades whether the diff did the KIND of
thing the subject claims, never correctness, never intent, never character).
Drift ≠ deception, and the page says so where the number is, not in a
footnote.

## 5. The per-repo page schema

Source of truth: `docs/scoreboard/<org>/<repo>.md` in this repo; the Pages
site mirrors it at `/scoreboard/<org>/<repo>/`. Sections, in order — every
field below is REQUIRED unless marked optional:

1. **Title** — `<org>/<repo> — drift scoreboard` and the headline verdict
   (one of the §4 three), with `n of m` inline.
2. **As-of block** — audited range (full `BASE` SHA → `HEAD` SHA), commit
   count, render date, auditor version, tier (`self` / `opt-in` /
   `curated`), attribution mode (`all commits, author-neutral` for the self
   page; `agent-attributed only` + marker-set link for foreign pages).
3. **The verdict table** — commits, checkable, witnessed, unwitnessed
   (raw), abstained, raw rate, adjudicated rate. Raw and adjudicated are
   BOTH always shown; the headline derives only from the adjudicated column.
4. **By claim kind** — the witnessed/unwitnessed/abstain grid per claim
   kind (`code_effect` / `test` / `doc` / `none`).
5. **The receipts** — one row per raw flag: linked SHA, subject, ruling
   (`AUDITOR_ARTIFACT` → the narrowing issue / `CONFIRMED(class)` /
   `UNADJUDICATED`), rung, rationale. Zero flags renders "no flags in
   range".
6. **Reproduce it** — the exact one-command form
   (`dos commit-audit --sweep --json --workspace . BASE..HEAD`) any reader
   can run against the same range.
7. **Correction path** — the contested-flag route (§3) and the methodology
   link (`docs/scoreboard/methodology.md`).

The machine twin of this page is #85's `verdict.json` — same verdict, same
receipts, same as-of, one fewer rendering. P4 keeps the two generated from
one record so they cannot disagree.

## P1 — the decisions, the schema, page #1 (ships with this plan)

This document (decisions §§1–4, schema §5) plus the worked example:
`docs/scoreboard/anthony-chaudhary/dos-kernel.md`, rendered from a real
`dos commit-audit --sweep --json` over this repository's full visible
history. We are page #1; the page shows our own non-zero verdict with the
receipts. Closes #84 (its done-condition verbatim).

Done-condition: both files tracked on `master`; the page's numbers match a
fresh sweep over the same pinned range.

## P2 — the renderer + the structural test

`scripts/scoreboard_page.py`: consume a per-repo verdict JSON (the docs/307
tool's per-repo artifact, or `dos commit-audit --sweep --json` for the self
page) plus an adjudication-records file, render the §5 page. Tests pin: the
§2 structural rule (a non-clean headline without `CONFIRMED` records is a
refusal, not a render), the §4 headline derivation, and byte-reproduction of
page #1 from its pinned inputs.

Done-condition: suite green; page #1 regenerates byte-identical from the
tool.

## P3 — the refresh loop (tier 0)

A scheduled CI workflow re-sweeps this repo weekly and re-renders page #1
(commit by the workflow, subject-stamped); the stale-banner rule from §3
lands in the renderer.

Done-condition: two consecutive scheduled runs have refreshed the as-of
block without hand intervention.

## P4 — tier 1, opt-in (gated on #85)

The `scoreboard-request` + `scoreboard-correction` issue templates, the
right-of-reply flow, and the shared verdict record powering both this page
and #85's `verdict.json`. First foreign page renders for an opt-in repo.

## P5 — tier 2, the curated public set (operator-gated)

Open only when every §1 tier-2 prerequisite holds, recorded operator
sign-off included. Until then the index is tiers 0–1 by construction.

## Out of scope

The badge + `verdict.json` endpoint themselves (#85 owns them); any change
to `dos.commit_audit` (fire-narrowing continues under its own issues, e.g.
#81/#82); the aggregate report and corpus criteria (docs/307 owns them);
any foreign-repo page before its tier's gate holds.
