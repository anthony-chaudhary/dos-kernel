# 339 — Agent-trace corpora (teich, the Fable-5 pools) and the witness tier: what to borrow, and the 2.0 DOS can power

> **One sentence.** A community is now pooling raw Claude Code agent traces into
> training datasets (the `teich` toolchain + the Fable-5 trace pools the r/ClaudeCode
> "26 sessions / 9k messages" analysis reads) — and every one of those corpora is a
> **tier-1 self-narrated** dataset by docs/332's taxonomy, which is exactly the gap a
> DOS-witnessed pool fills: the *2.0* of an agent-trace corpus is not more traces, it
> is traces **carrying the author-disjoint label teich structurally cannot mint.**

**Status:** framework / strategy note — no kernel change, no new syscall. **Date:** 2026-06-14.
**Builds on:** docs/332 (the four-tier provenance taxonomy + four axes — *read first*),
docs/84 (the kernel-as-labeler), docs/181/192 (effect witness + coverage floor),
docs/333 (verification as steering). **Triggered by:** an operator goal to read the
r/ClaudeCode post *"I analyzed 26 sessions / 9k messages of Fable 5 and …"* and its
open-source repo, and decide what DOS borrows vs. powers.

> **Provenance caveat, stated up front (this doc's own discipline).** The Reddit
> post body is **not recovered** — reddit.com and every proxy/archive route
> (jina, archive.org, archive.ph, search caches) returned 403/empty from this
> harness. So this note does **not** quote the post's findings or numbers; doing
> so would be a tier-1 fabrication of the kind docs/332 exists to refuse. What
> *is* recovered first-hand and load-bearing here is the **substrate** the post
> reads: the `teich` repo (read at source-file granularity) and the public
> `armand0e/claude-fable-5-claude-code` HF dataset (read from its card). The
> argument below rests only on those. If the post's text is later supplied,
> append its specific findings as §8 — they will land *on top of* this frame, not
> change it, because the frame is about the data tier, not any one finding.

---

## 1. What the corpus world actually is (recovered first-hand)

Two artifacts define the surface the Reddit analysis sits on:

- **`teich`** (TeichAI/teich, Apache-2.0, **v0.2.1, self-declared alpha**) — "turn
  coding agent traces into auditable supervised fine-tuning data." A `src/`-layout
  Typer CLI: `init` / `generate` (run agents in Docker against prompts) / `extract`
  (pull *local* sessions) / `anonymize` (a **separate, opt-in** pass) / `studio`
  (browser UI) / `pool upload` (a stub — "backend not deployed").
- **The Fable-5 trace pools** — e.g. `armand0e/claude-fable-5-claude-code` (the card
  cites 63 sessions / ~75 MB; the maintainer's call to action is literally *"please
  upload their fable-5 traces from whatever harness… we need to pool it together"*).
  The Reddit "26 sessions / 9k messages" piece is one reading of this kind of pool.

`teich`'s real engineering is in the **normalize → mask → audit** path, and it is
genuinely good (details in §3). The thing to see clearly is *where its evidence
stops*: `extract` for Claude Code **copies the raw `~/.claude/projects/**.jsonl`
verbatim** (`shutil.copy2`), reading events only to validate structure and filter by
model string. The label every downstream trainer learns is therefore **whatever the
session said happened** — the ReAct trace itself. That is docs/332 **tier 1**.

## 2. The one-line placement (docs/332 applied)

| Source | docs/332 tier | Deciding byte authored by | What a model trained on it learns |
|---|---|---|---|
| teich-extracted Claude Code traces | **1 · self-narrated** | the agent (the trace *is* the claim) | to reproduce the narration — including the confident-wrong narration |
| teich `generate` + a grader | 2 · grader/outcome | a grader reading the agent's "done" | to produce *convincing* "done", per METR's ~24pp grader over-optimism |
| an LLM-judge filter bolted on top | 3 · LLM-judge | a model reading agent text | to fool the judge (the "One Token to Fool" channel) |
| **a DOS-witnessed trace pool** | **4 · witnessed** | git / env / a third party | to actually drive the world to the correct state |

teich's own roadmap concedes the gap in its own words: an **open** design question is
*"quality filter thresholds for failed sessions."* Translation in docs/332 terms:
**teich has no tier-4 label and no plan to mint one** — its quality gate is
*structural validity* (well-formed messages, aligned masks, no fully-masked rows),
never *did-the-claimed-effect-land*. That is not a flaw in teich; it is the seam
where DOS begins.

## 3. Borrow directly — five concrete things teich does that DOS should adopt or echo

These are real, and several **converge with kernel discipline already** — worth
naming so we copy the good and notice the agreement:

1. **Re-derive the effect after the fact; don't trust the producing step.** teich's
   `audit_sft_dataset()` *re-tokenizes with offsets after the trainer's own
   tokenization* and independently re-checks `labels[i] == input_ids[i]` and
   full-masking. It verifies the *effect of masking*, not masking's say-so — a direct
   cousin of `commit-audit` (subject vs. its own diff) and `witness_effect`. **Borrow:**
   nothing to build; cite it in docs/332 §2 as an external, independent instance of
   the witness-the-effect pattern arising in a training pipeline.
2. **Closed, typed reports — never prose.** `PrepareReport` (kept / dropped /
   oversized / trimmed + token stats) and `SFTAuditReport` (ok / errors / warnings /
   samples) are structured verdicts with `raise_for_errors()`. Same shape as DOS's
   typed verdicts. **Borrow:** confirms the house style; no change.
3. **Fail-closed type detection.** `detect_trace_type()` returns `None` rather than
   guessing a fallback; incomplete traces (ending on a tool result) and
   no-trainable-signal rows are **dropped by default**. Conservative-by-default, like
   the arbiter's prefix-disjointness floor. **Borrow:** the posture is right.
4. **Provenance carried end-to-end as data.** Every row keeps `source_file`,
   `session_id`, `git_branch`, `cwd`, `usage`, `total_cost_usd`, timestamps, and the
   *full tool list including never-called tools*. This is the exact metadata a DOS
   witness join needs (the `root_id`/lineage join key of docs/84 §2, Axis-2 channel
   4). **Borrow:** when DOS emits a verified-trace row (§5), **match teich's field
   names** so a DOS pool is drop-in loadable by `teich load_traces()`. Cheap
   interop, large reach.
5. **The OpenAI message/tool normalization + supervision-span model.** teich already
   solves the boring-but-load-bearing problem of turning a Claude Code JSONL into
   `{messages, tools, metadata}` with typed `teich_supervised_spans`. **Borrow by
   reuse, not reimplementation:** DOS should *consume* teich's normalizer, not grow
   its own. (See §5 — the integration is a thin adapter, deliberately.)

Two anti-patterns to **not** copy, and to flag if we ever depend on teich:

- **Anonymization is opt-in and not run at `extract` time.** A fleet pipeline that
  pooled traces without the explicit `anonymize` pass would leak emails / home-dir
  usernames / API keys. DOS's leak-gate discipline must wrap any teich extract we run.
- **JSONL parse errors are silently swallowed** (`None` on `OSError/JSONDecodeError/
  UnicodeDecodeError`). DOS would want a typed `refuse(reason_class)` there, not a
  silent drop — a dropped row is invisible, and invisible is how a coverage claim
  lies.

## 4. The reframe DOS gives the "analyze the traces" genre

The Reddit-post genre — *read N sessions, report what the model does* — is **tier-1
observation**: it describes the narration. It is useful (patterns, ergonomics,
failure shapes) but it inherits the narration's blind spot. docs/332 §1 names the
stakes precisely: *at runtime a believed lie is one bad action; in a dataset a
believed lie is the lesson.* An analysis that counts "successful sessions" from the
trace is counting the agent's *framing* of success — the PAE result (27–78% of
tau-bench "successes" procedurally corrupt) says that framing is wrong a quarter to
three-quarters of the time on a comparable surface.

**So the DOS contribution to the analysis itself is a single column:** run
`commit-audit` / `witness_effect` over each session's claimed effects and tag every
session **CONFIRMED / REFUTED / UNWITNESSED**. That turns "I analyzed 26 sessions"
into "I analyzed 26 sessions and *N of them claimed an effect that git/the env does
not corroborate*" — the first trace analysis whose headline number isn't itself
self-narrated. That is a publishable, falsifiable artifact and a natural follow-on
to docs/333's "verification as steering."

## 5. The 2.0: a DOS-witnessed trace pool (`teich` as the normalizer, DOS as the label)

The clean design — and the reason it's small — is that **teich and DOS are
complementary layers, not competitors.** teich owns *format*; DOS owns *truth*.

```
~/.claude/projects/**.jsonl
        │  teich extract (verbatim copy) + teich anonymize (leak-gated by DOS)
        ▼
   normalized {messages, tools, metadata}      ← teich converter/loader (REUSED)
        │
        │  ── DOS witness adapter (NEW, thin) ──
        │     for each session: read its claimed effects from metadata
        │     (git_branch, cwd, the diffs/commits/files it says it made)
        │     → witness_effect() / commit-audit against the repo state
        │     → attach metadata.dos_witness = {verdict, provenance, refuted_claims[]}
        ▼
   tier-4-LABELED rows  (teich-loadable: same schema + one extra metadata key)
        │
        ▼  filter on dos_witness.verdict == CONFIRMED  →  a witnessed positive set
   the first agent-trace pool whose "good session" label is author-disjoint
```

Concretely the 2.0 is **one new driver-layer adapter**, not a kernel change:

- It lives where vendor/provider names are allowed (a `drivers/` module or a small
  standalone tool that `import dos` + `import teich`) — *not* under `src/dos/` (the
  kernel names no vendor, litmus-clean).
- It **reuses** teich's `load_traces()` output and **adds** one metadata key,
  `dos_witness`, computed by the existing kernel verbs. No new syscall: `verify`,
  `commit-audit`, and `witness_effect` already return exactly this.
- The coverage honesty of docs/332 §4 carries over for free: where a session's claim
  has no checkable witness (~38% by the docs/192 estimate), the row is tagged
  `UNWITNESSED` and **excluded from the witnessed-positive set**, never invented into
  one. A DOS pool's headline is "K of N sessions witnessed-confirmed", and the
  withheld remainder is a *count*, never dressed as coverage.

This is the docs/332 thesis made shippable against a real, growing external corpus:
the community is pooling tier-1 data right now and asking for more of it; the
differentiated thing DOS can put next to that pool is the **author-disjoint label**,
using their own normalizer so adoption cost is ~zero.

## 6. Integration vs. ingest — two postures, pick per-goal

- **Integrate (interop):** publish DOS-witnessed pools in teich's schema +
  contribute a `dos_witness` field upstream (a PR to teich, or a documented
  convention). Reach: every teich user can *filter on* the witness. This is the
  docs/138-style "be a rung in someone else's pipeline" play, applied to training
  data instead of CI. Low cost, compounding.
- **Ingest (consume):** use teich purely as DOS's front-end normalizer for building
  *our own* reward sets (docs/230→250, docs/322 poisoned-pool) from real Claude Code
  sessions instead of synthetic tau2 rows — which directly attacks the docs/250 §4.1
  data-starvation wedge ("the seam emits ~1 pair/bid; DPO wants hundreds"). Real
  pooled sessions are a *volume* source; the DOS witness is what keeps the poison out
  (docs/322's whole point). This is the higher-leverage internal use.

The two compose: ingest to build witnessed reward sets internally; integrate to make
the witness field a thing the external corpus world can adopt.

## 7. Honest boundary

- **The post itself is unread** (§ caveat). This note is about the *tier*, which is
  fixed by how teich extracts (verbatim copy of self-narration), not by any finding
  in the post. If the post turns out to *already* do effect-witnessing, that
  strengthens the §4 case (someone wants this) rather than weakening it.
- **"Witnessed-confirmed" means an effect landed, not that the code is good**
  (docs/332 §7, unchanged). A confirmed session can still be mediocre code. The label
  removes the *fabricated-success* rows; it does not rank quality.
- **The interop bet depends on teich's schema staying stable** (it's v0.2.1 alpha;
  the roadmap lists breaking items — Parquet output, train/val splits, Anthropic/
  Gemini export adapters). Pin a version if we build the adapter; treat the field
  contribution as the durable part and the code adapter as replaceable.
- **Coverage ceiling is the same ~38% no-witness slice.** A witnessed pool is
  smaller than the raw pool by construction. That is the price of the label being
  worth anything; docs/332 §4 already argues why the smaller-but-trustworthy set
  beats the larger self-narrated one *for training*.

## 8. Through-line

docs/84 said a non-believing kernel, by adjudicating a fleet, *labels* it. docs/332
turned that into a four-tier lens and a bet: verified data is the next generation
because its label is **author-disjoint**. This doc points that lens at the **live
external corpus world** — teich and the Fable-5 trace pools — and reads it cleanly:
they are building the tier-1 substrate, fast, and explicitly asking for more.
The DOS move is not to build a competing extractor; it is to **reuse their normalizer
and contribute the one thing their pipeline structurally cannot mint — the witness
label** — turning "a pool of what agents said they did" into "a pool of what agents
*actually did*." That is the 2.0, and it is a thin adapter plus a field name away.

### Next actions (witness-able)

- [ ] Spike the `dos_witness` adapter over the public `armand0e/claude-fable-5-claude-code`
      dataset: load via teich, run `commit-audit`/`witness_effect` where the session's
      repo state is reconstructable, report K-confirmed / N-total + the withheld count.
      (Most public rows won't have a reachable repo → expect a high `UNWITNESSED` rate;
      that *is* the honest finding, and it quantifies the tier-1 problem.)
- [ ] If the spike is informative, file an upstream issue/PR on teich proposing a
      `metadata.dos_witness` convention (interop posture, §6).
- [ ] Fold the §4 "one new column" idea into docs/333's verification-as-steering as the
      *dataset-side* instance of the same loop.
