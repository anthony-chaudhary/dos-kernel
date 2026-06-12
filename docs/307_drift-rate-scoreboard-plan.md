# 307 — the public drift-rate scoreboard: `commit-audit --sweep` at corpus scale

> A commit message is a claim. The diff under it is evidence the claimant did
> not author. `dos commit-audit` already grades the two against each other for
> one repo; this plan aims the same witness at a *corpus* of public
> repositories with machine-attributable agent commits and publishes the
> aggregate **drift rate** — how often a concrete claim in an agent-authored
> commit subject is unwitnessed by its own diff. OpenSSF Scorecard became
> known by publicly scoring other people's repos; this is that move, run on
> the one number nobody else computes (the verdict reads bytes the claimant
> didn't write — the diff and the ancestry, never the narration). Issue #66
> is the tracking handle. Methodology ships FIRST; the first report is
> aggregate-only.

*Status: P1–P3 ship with this plan. The later rollout rungs (per-repo
contact-first reports, opt-in badges) are out of scope here and gated on
operator outreach; corpus curation guidance lives in the private repo
(`notes/2026-06-12_next-steps-private-companions.md`).*

## 0. The facts the design rests on

- **The verdict already exists; this plan adds NO kernel surface.** The
  per-commit witness is `dos.commit_audit.audit_commit` and the per-repo fold
  is `dos.commit_audit.sweep_summary`, both shipped. The scoreboard is dev
  tooling that operates ON repos — a `scripts/` module that `import dos`,
  the same one-way arrow as `trajectory_audit.py`. Nothing under `src/dos/`
  knows it exists.
- **The verdict is author-neutral, but the QUESTION is about agent-authored
  commits.** So the tool needs an attribution gate in front of the witness: a
  closed, published, mechanical marker set (committer/author identities and
  commit-trailer forms that only an agent toolchain writes). Under-matching
  is the conservative direction and is preserved by construction: a marker we
  are unsure of stays OUT. A missed agent commit merely shrinks the sample; a
  human commit wrongly counted would poison the headline claim.
- **Aggregate-first is a structural guarantee, not a promise.** The v1 report
  names no repository without opt-in. The aggregate fold *strips* repo
  identity (names, URLs, and the per-commit SHAs, which are globally
  searchable) before the render step ever sees the data — pinned by test, the
  same shape as `believe_under_floor`: the publishable artifact cannot carry
  what it never received. Per-repo artifacts are written separately for the
  operator and are not for publication.
- **The false-positive story publishes first.** The witness has documented
  over-fire history on this repo's own commits — issue #4 (`fix(ci):` over a
  `.gitattributes`-only diff read as an unwitnessed code claim; one FP made a
  small sweep read 100% drift) — and each was fixed by *narrowing the fire*,
  never by believing the subject. The methodology page leads with this:
  what the witness reads, where it has been wrong, what was done about it.
- **Drift is not wrongness (the Wall-3 line).** The verdict grades whether
  the diff did the KIND of thing the subject claims — never correctness, and
  never malice. `CLAIM_UNWITNESSED` fires only where a concrete code/test
  claim and a contradicting diff coexist; everything else ABSTAINS and is
  excluded from the denominator (and the report says how much abstained).
- **Publication surface vs. ship-stamp.** The canonical methodology and
  report are tracked docs in this repo (`docs/scoreboard/`) — so `master`
  ancestry carries the oracle-readable ship-stamps — and the Pages site
  (gh-pages) mirrors them as the published HTML page.

## P1 — the sweep tool (`scripts/drift_scoreboard.py`) + tests

1. **Corpus in:** a text file, one repo per line (a clone URL or a local
   path), `#` comments allowed. The format is public; any concrete corpus
   list used for a published run is operator-curated against the published
   criteria and is not committed here.
2. **Clone boundary:** each remote repo is cloned `--no-checkout` into a
   cache dir (re-runs `git fetch` instead); all git reads have timeouts and
   degrade to a skipped repo, never a crash.
3. **Attribution gate (pure):** `classify_attribution(name, email, body)`
   matches the closed marker set against author/committer identity and
   `Co-authored-by:` trailers / generator footers. Structural matches
   (exact emails, bot-account slugs, an explicit `(aider)` suffix), never a
   bare substring — a human named Claudette must not match.
4. **Per-repo sweep:** walk the default branch newest-first (scan cap),
   keep attributed commits (audit cap), run `audit_commit` on each, fold
   with `sweep_summary`. Write the full per-repo verdict JSON (markers,
   rates, offending SHAs) to `<out>/per-repo/` — the operator-only artifact.
5. **Aggregate fold (pure):** pooled drift rate over all checkable claims +
   the anonymous per-repo rate distribution (median/min/max) + the by-kind
   and by-marker grids. Identity-stripped by construction (no name, no URL,
   no SHA).
6. **Render (pure):** `aggregate.json` + `aggregate.md` — the publishable
   report.
7. **`--enumerate` helper:** print candidate repos from a `gh` commit search
   over the marker set (candidates only — selection against the published
   criteria stays a human step).
8. **Tests** (`tests/test_drift_scoreboard.py`, script imported by path like
   `test_trajectory_audit.py`): the matcher's positive and negative space
   (no substring traps), the fold arithmetic, the corpus parser, and the
   anonymization invariant — feed named per-repo summaries through the fold
   and assert no name/URL/SHA survives into either rendered artifact.

Done-condition: suite green; on a fixture corpus of two synthetic local
repos the tool writes per-repo JSON + `aggregate.json` + `aggregate.md`,
and the aggregate artifacts carry no repo identity.

## P2 — the methodology page, published before any number

`docs/scoreboard/methodology.md`: what the witness reads (claim kind vs.
diff shape) and what it abstains on; the false-positive characterization
seeded from this repo's own sweep history (the issue #4 worked example and
its fix direction); the attribution marker set; the mechanical corpus
selection criteria; the ethics floor (aggregate-first, advisory framing, no
per-repo naming without opt-in); and the reproduction recipe (the exact
tool invocation). Mirrored as a page on the Pages site, linked from the
landing page, listed in the sitemap.

Done-condition: the methodology page is live on the Pages site before any
aggregate number is.

## P3 — the first aggregate report

Run the tool over an operator-curated corpus meeting the published
criteria (machine-attributable agent commits; active; established — the
exact thresholds are stated on the methodology page; repos where an
outreach relationship is in flight are excluded to keep the grade and the
courtship apart). Publish the aggregate as `docs/scoreboard/` report +
the Pages page; per-repo artifacts go to the private repo, not here.

Done-condition: the first aggregate report (N repos, pooled + median drift
rate, by-kind grid) is on the Pages site next to the methodology.

## Out of scope (the later rollout rungs)

Contact-first per-repo reports, opt-in badges (`dos.verified`, issue #75 is
the adjacent handle), CI-recurring re-runs of the report, and any per-repo
public naming. Each is gated on operator decisions recorded outside this
repo.
