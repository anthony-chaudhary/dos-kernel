# Visual assets

Self-contained images for the README and docs. They are **SVG, hand-built from
real binary output** — not screen recordings — so they render inline on GitHub
with no external host, version cleanly in git (they're text), and stay honest
(every string was produced by the actual `dos` CLI; see the capture commands
below).

> **Two kinds live here.** The **SVGs** below embed inline on GitHub (the README
> `<img>` tags). The **interactive `*_visual.html` walkthroughs** (next section) are
> stepped, click-through explainers of a single result — GitHub renders HTML as
> *source*, so they're opened locally or via an HTML previewer, and the README links
> to them rather than embedding them.

## Interactive walkthroughs (stepped HTML)

Each is a self-contained, zero-dependency HTML page (one inline `<style>` + one
inline `<script>`, no network, no build) that walks **one real result** forward a
frame at a time — Back / Step ▶ / Reset, plus ← / → / space. They share the
**signature DOS identity** — a near-black `#0a0a0f` canvas, acid-green `SHIPPED` /
hot-red `NOT_SHIPPED` verdicts, an electric-cyan "ask git" accent, a violet steering
signal, and display-type verdict stamps — the same palette as the README hero
(`loop-hero.svg`), so the whole set reads as one family. The rule that keeps them
honest is the kernel's own: **every number, ID, and quote is a verbatim read-off
from a committed run** — the visual is a rendering of ground truth, never a
hand-typed dramatization.

| File | Walks through | Source of truth |
|---|---|---|
| [`loop_visual.html`](loop_visual.html) | Open loop vs. closed loop, side by side: narration believed → silent corruption, vs. a `verify` verdict steering the next step. The **stepped, click-through form of the README hero** (`loop-hero.svg`). | The README narrative (conceptual — no single run). |
| [`../../examples/demo/verify_visual.html`](../../examples/demo/verify_visual.html) | The money-moment: an agent claims `AUTH1` **and** `AUTH2` shipped; `dos verify` reads git and returns `SHIPPED` (exit 0) / `NOT_SHIPPED via none` (exit 1). | `examples/demo/verify_demo.sh` — every line is its verbatim output. |
| [`../../benchmark/agentprocessbench/writeadmit/gate_visual.html`](../../benchmark/agentprocessbench/writeadmit/gate_visual.html) | The out-of-loop write-admission gate: a live Gemini agent over-claims a tau2 booking, the DB-hash refutes it, the gate BLOCKs before a peer inherits the phantom — then a contrast where an honest write is admitted. | `paper/_VERIFIED_FACTS_232_2026-06-08.md` + docs/228/232 (J = 10/120, 8.3% on both models). |
| [`../../benchmark/agentprocessbench/writeadmit/f2_visual.html`](../../benchmark/agentprocessbench/writeadmit/f2_visual.html) | The natural-collision coordination payoff: two live agents on one reservation, a stale write clobbering a cancellation under naive, the arbiter serializing them. | `benchmark/agentprocessbench/writeadmit/_f2results.txt` (J = 4/6, the real pair IDs). |

The two under `writeadmit/` and the one under `examples/demo/` deliberately live
**next to the run they render**, welded to their source data; only `loop_visual.html`
(which renders the conceptual narrative, not one run) lives here with the SVGs. To
view any of them: open the file in a browser, or paste its raw GitHub URL into an
HTML previewer (e.g. `htmlpreview.github.io`).

> **Each walkthrough has a rendered SVG companion — that is what the README
> embeds.** GitHub shows HTML as *source*, so an HTML link can't be the README's
> primary visual; every walkthrough therefore ships a static **`*-moment.svg`** of
> its key frame (`+ .png` raster backup), which embeds inline on GitHub, and the
> README links the HTML only as the "step through it locally" upgrade. The pairs:
> `loop_visual.html` → [`loop-hero.svg`](loop-hero.svg) (the hero already serves as
> its still); `verify_visual.html` →
> [`../../examples/demo/verify-moment.svg`](../../examples/demo/verify-moment.svg);
> `gate_visual.html` →
> [`../../benchmark/agentprocessbench/writeadmit/gate-moment.svg`](../../benchmark/agentprocessbench/writeadmit/gate-moment.svg);
> `f2_visual.html` →
> [`../../benchmark/agentprocessbench/writeadmit/f2-moment.svg`](../../benchmark/agentprocessbench/writeadmit/f2-moment.svg).
> Each `*-moment.svg` is the same freeze-safe, signature-identity build as the hero,
> and every string in it is a verbatim read-off from the same source-of-truth file
> listed above — a rendering of ground truth, never a hand-typed dramatization.
> Regenerate a `.png` from its `.svg` with the `playwright screenshot` recipe below
> (a 1300 ms `--wait-for-timeout` lets the intro settle to the poster frame).

## Static images (SVG)

| File | What it shows | Source of truth |
|---|---|---|
| `social-card.svg` / `social-card.png` | **The GitHub social-preview card** — the image platforms unfurl *before* anyone clicks (the Slack / Discord / Twitter-X / LinkedIn card, the Open Graph image). A purpose-built **1280×640** (GitHub's recommended 2:1) still in the signature identity: the wordmark, the one-line hook ("catch your AI agents when they **lie** about what they shipped"), the two-line money moment (`dos verify AUTH AUTH1` → `SHIPPED` ✓ exit 0, `dos verify AUTH AUTH2` → `NOT_SHIPPED` ✗ exit 1 — caught), and the `pip install dos-kernel` line. **Static** (no animation — GitHub rasterizes one frame) and freeze-safe. The `.png` is what you upload to GitHub's Social-preview slot (≤ 1 MB). | **Rendered, not drawn**: `scripts/build_social_card.py` drives the real `dos verify` against the canonical caught-lie demo (`dos._demo_story`, pinned commit identity/dates → deterministic) and renders the captured verdicts; `tests/test_social_card.py` re-renders and pins the committed SVG byte for byte. |
| `caught-lie-cast.svg` | **The README's first-screen terminal cast** (issue #64): the quickstart's caught-lie moment as an animated recording — the agent claims both features shipped, `git log` shows the one real commit, `dos verify` answers `SHIPPED` (exit 0) then `NOT_SHIPPED` (exit 1) — CAUGHT. Typed commands, line-by-line reveal, settle-and-hold ending, `prefers-reduced-motion` + stripped-stylesheet fallbacks (the final frame is every element's natural state). | **Recorded, not drawn**: `scripts/build_caught_lie_cast.py` drives the real `dos` CLI in a throwaway repo (pinned commit identity/dates → byte-deterministic) and renders the captured transcript; `tests/test_caught_lie_cast.py` re-records and pins the committed bytes. |
| `loop-hero.svg` | **The README hero.** An animated, self-contained SVG of the open-loop-vs-closed-loop contrast: left, a fleet whose `done!` reports are believed until lies / collisions / spin pile up into a codebase that "sorta works"; right, `dos verify` reads git and the run branches `SHIPPED` (exit 0, land it) / `NOT_SHIPPED` (exit 1, re-dispatch — caught), the verdict steering the next step. CSS-keyframe reveal with a `prefers-reduced-motion` fallback. | The README narrative (conceptual — the AUTH/`e62f74d` strings mirror the `dos verify` money-moment). |
| `loop-hero.png` | **High-res raster backup** of the hero's resolved (reduced-motion) frame — 3600×1920. For surfaces that don't render SVG (LinkedIn / Twitter-X cards, slide decks, the announce docs). GitHub embeds the SVG; this is the fallback poster. | Rendered from `loop-hero.svg` at 3× DPR (the convert recipe below). |
| `fleet-loop.svg` | **A static SVG backup for the "what goes wrong in a fleet" Mermaid flowchart** — the same open-loop-vs-closed-loop contrast as the hero (NO REFEREE: `done!` reports believed until a lie / collision / spin pile up; DOS ADJUDICATES: `dos verify` reads git and branches `SHIPPED` / `NOT_SHIPPED`, the verdict steering the next step). Same signature identity + freeze-safe reveal as `loop-hero.svg`. **Not currently embedded in the README** — the section now leads on the inline Mermaid flowchart (which renders on GitHub) and the verbatim failure table, with the hero already carrying the same contrast at the top; this SVG is kept as the always-embeds fallback for Markdown surfaces that render Mermaid blank. | The README narrative (conceptual; the `AUTH`/`e62f74d`/`exit 0`/`exit 1` strings mirror the `dos verify` money-moment, same as the hero). |
| `fleet-loop.png` | **Raster backup** of `fleet-loop.svg` — 2400×1120 (the resolved frame). For non-SVG surfaces. Like its SVG, kept as a fallback but not embedded in the current README. | Rendered from `fleet-loop.svg` (the convert recipe below). |
| `decisions-tui.svg` | A designed view of the `dos decisions` operator queue: four pending refusals on the left, each **routed to its resolver** (ORACLE may auto-clear / JUDGE could rule / HUMAN your call), with the selected `SELF_MODIFY` decision expanded on the right — meaning, typical fix, and runnable action keycaps. Full-width two-column composition in the signature identity. | The decision content is real `dos decisions` output over a seeded lane journal (see below); the layout is purpose-built, not a raw terminal capture. |
| `decisions-tui.png` | **High-res raster backup** of `decisions-tui.svg` — 2760×1680. For non-SVG surfaces (slides, social). | Rendered from `decisions-tui.svg` at 3× DPR. |
| `verified-by-dos.svg` | The **adoption badge** an adopter repo pastes into its README: a shields.io-style two-segment pill — dark `verified by` label, green `DOS` message — asserting the repo gates its agents' ship-claims with `dos verify`. | Hand-authored static badge (no live query); the paste-me page is [`docs/BADGE.md`](../BADGE.md). |

## Reproducing the content

**`caught-lie-cast.svg`** — the one asset here that is fully scripted: run
`python scripts/build_caught_lie_cast.py` and it re-records the whole cast — it
builds a throwaway git repo telling the canonical caught-lie story
(`dos._demo_story`), drives the real `dos verify` against it, and renders the
captured transcript as the animated SVG. The recording is deterministic (pinned
commit identity + dates), so `--check` can compare the committed file to a fresh
re-recording byte for byte — that is what `tests/test_caught_lie_cast.py` runs.
**Never edit the SVG by hand**: when the CLI's output changes shape, the test
goes red and the fix is to re-run the script.

**`social-card.svg` / `social-card.png`** — fully scripted, like the cast: run
`python scripts/build_social_card.py` and it re-renders both — it drives the
real `dos verify` against the canonical caught-lie demo (`dos._demo_story`,
pinned identity/dates → the SHIPPED sha is deterministic), then writes the
1280×640 SVG and rasterizes it to a sub-1 MB PNG (via `rsvg-convert`, else a
headless `npx playwright` screenshot; falls back to "SVG only, export the PNG
manually" if neither is present). `--check` byte-compares the committed SVG to
a fresh render — that is `tests/test_social_card.py`'s pin 1. **Never edit the
SVG by hand**: when the CLI's output changes shape, the test goes red and the
fix is to re-run the script.

> ### Installing the card on GitHub (the one human-only step)
>
> GitHub exposes **no API** for the social-preview slot — it is uploaded
> through the web UI. After `build_social_card.py` writes the PNG:
>
> **Repo → Settings → (General) → "Social preview" → Edit → upload
> `docs/assets/social-card.png`.**
>
> That single manual action is everything; the bytes themselves are scripted.
> Confirm it took with `gh repo view --json usesCustomOpenGraphImage`
> (`true` once uploaded) — or just paste the repo URL into Slack and watch it
> unfurl. The image is cached by each platform, so a re-upload can take a
> while to propagate; GitHub's own card refreshes on the next repo touch.

**`loop-hero.svg`** — there's no script to capture: it renders the README's
open-loop-vs-closed-loop *narrative* (the same conceptual story as
[`loop_visual.html`](loop_visual.html)), not one binary run. Edit the SVG by hand;
the `AUTH` / `e62f74d` / `exit 0` / `exit 1` strings deliberately mirror the
`dos verify` money-moment so the hero and the
[`verify_visual.html`](../../examples/demo/verify_visual.html) walkthrough agree.
The reduced-motion fallback is built in (`@media (prefers-reduced-motion: reduce)`
freezes it on the fully-resolved frame). To preview a rasterized frame, screenshot
it in a headless browser (the convert recipe below).

**`fleet-loop.svg`** — like `loop-hero.svg`, there's no capture script: it renders
the README's *narrative* (the open-loop-vs-closed-loop story), not one binary run,
and exists because **Mermaid renders blank on some Markdown surfaces** so the
flowchart needed a static backup that always embeds. Edit the SVG by hand; keep its
`AUTH` / `e62f74d` / `exit 0` / `exit 1` strings in step with the hero and the
[`verify_visual.html`](../../examples/demo/verify_visual.html) walkthrough. The
reduced-motion fallback is built in (the intro defaults to its final visible frame).
The README keeps the original Mermaid source in a collapsed `<details>` right below
the image, so a surface that *does* render Mermaid still gets the live chart.

**`decisions-tui.svg`** — the *content* is real: the four rows are `Decision`s
collected from a lane journal seeded with arbiter refusals (the same path the
kernel writes on a real refusal), and the `SELF_MODIFY` detail (meaning, fix,
resolver, actions) is what `decisions.render_detail_plain` projects from the
built-in `ReasonRegistry`. To regenerate the underlying text, seed a journal with
`lane_journal.append({"op": "REFUSE", …})` and render with `dos decisions` (full
snippet in the project notes). The *layout* is a purpose-built two-column graphic
(queue + resolver-routing on the left, the selected decision expanded on the
right), hand-authored in the signature identity — edit the SVG directly to restyle.

**`verified-by-dos.svg`** — there's nothing to capture: it's a static,
hand-authored badge (label `verified by`, message `DOS`, the DOS ship green
`#2ea44f`), not a snapshot of binary output. Edit the two segment widths and the
`<text>` strings by hand if the wording changes. The paste-me adoption page —
the markdown an adopter copies, plus the shields.io static-endpoint form and the
honesty note (it asserts *adoption*, not a live per-commit verdict) — is
[`docs/BADGE.md`](../BADGE.md).

## Want an actual `.gif` or `.png`?

SVG is the better default (sharp, tiny, inline on GitHub). But some surfaces —
Twitter/X cards, LinkedIn, a slide deck — don't render SVG in previews. Convert
on demand; the SVG stays the source:

```bash
# The high-res hero poster docs/assets/loop-hero.png (3600×1920, the resolved
# reduced-motion frame) is committed; regenerate it by screenshotting the SVG in a
# headless browser at 3× DPR (a browser honors the CSS animation + reduced-motion;
# librsvg does not animate). Playwright is the one-liner:
npx playwright screenshot --viewport-size=1200,640 --device-scale-factor=3 \
    docs/assets/loop-hero.svg docs/assets/loop-hero.png
# static PNG (poster frame) via librsvg, if you only need the first frame:
rsvg-convert -w 1640 docs/assets/decisions-tui.svg > decisions-tui.png
# the fleet-loop section image's raster backup (2400×1120):
npx playwright screenshot --viewport-size=2400,1120 \
    docs/assets/fleet-loop.svg docs/assets/fleet-loop.png
```

For a true recorded GIF of a *live* terminal (e.g. a social card), a
`charmbracelet/vhs` `.tape` driving the real `dos` binary is the cleanest route —
`examples/demo/verify_demo.tape` is the money-moment cast. The SVGs here are the
zero-dependency, always-current default the README embeds.
