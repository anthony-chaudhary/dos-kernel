#!/usr/bin/env python3
"""kernel_charts — self-contained inline SVG for the kernel's OWN behaviour.

The scoreboard charts draw what the kernel found in other repos. These three
draw what the kernel did to ITSELF, folded live from this repo's `.dos/`
journals (`kernel_metrics.py`) — the visual answer to "the kernel is the part
that doesn't believe the agents", in the kernel's own numbers:

  * **The disbelief funnel** (`disbelief_funnel_chart`) — every adjudicated
    tool call at true scale: almost all pass, a real few are stopped, and the
    tiny stopped slice is magnified so the catch is visible without inflating
    it. Source: `.dos/metrics/observations.jsonl`.
  * **The self-modify wall** (`self_modify_wall_chart`) — every time a live
    agent tried to edit the kernel adjudicating it, refused, with a ranked bar
    of which kernel module each refusal NAMED (the arbiter dominates — agents
    reached for the exact code doing the refusing). Source:
    `.dos/lane-journal.jsonl`.
  * **The memory-recall chart** (`memory_recall_chart`) — of the memories the
    kernel recalled, how many re-verified as still TRUE; it re-checks its own
    notes before believing them. Source: `.dos/verdict-journal.jsonl`.

Same load-bearing constraints as `scoreboard_charts.py` (and the geometry
helpers + palette are imported from it, so there is one source of truth):

  * **Pure + deterministic.** Data dict in, SVG string out. No clock, no
    random, no I/O. Same input → byte-identical output, so a committed asset is
    reproducible and a `--check` can prove it matches the data.
  * **Self-contained SVG.** No JS, no `<style>`, no CSS variables, no external
    fetch, no `<foreignObject>`. Every colour an explicit literal, every label
    a real `<text>` — so it renders on GitHub Markdown AND a Pages site AND
    rasterizes to a PNG cleanly. The two new primitives (`<line>`, `<polygon>`)
    use only plain stroke/points attributes.
  * **No number is authored here.** Every count comes from the caller's fold;
    this module owns geometry and colour only. The journals grow as the kernel
    runs, so a regenerated chart shows the newer, larger truth.

Stdlib only, dev tooling under `scripts/`; nothing under `src/dos/` imports it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# Reuse the scoreboard chart module's geometry kernel + palette — one source of
# truth for _n / _rect / _text / _svg_open and the shared colour constants.
_spec = importlib.util.spec_from_file_location(
    "scoreboard_charts", Path(__file__).resolve().parent / "scoreboard_charts.py")
_sc = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_sc)

_n = _sc._n
_rect = _sc._rect
_text = _sc._text
_svg_open = _sc._svg_open
PANEL = _sc.PANEL
PANEL_BORDER = _sc.PANEL_BORDER
FG = _sc.FG
MUTED = _sc.MUTED
BAR_BG = _sc.BAR_BG
GREEN = _sc.GREEN

# The kernel-chart palette additions — explicit literals (GitHub Markdown
# ignores CSS vars). A severity ramp for the funnel/wall/memory: pass = green,
# routed = slate, other = pale, warn = amber, deny = red, block = dark red.
SLATE = "#57606a"     # routed-to-full-check
PALE = "#afb8c1"      # the honest "other outcomes" bucket
AMBER = "#bf8700"     # warned (an intervention, but a let-through-with-note)
RED = "#cf222e"       # denied / refused
DARKRED = "#82071e"   # hard-blocked — the kernel said NO
WHITE = "#ffffff"
AMBER2 = "#d4a72c"    # the advisory amber on the wall / memory ramp
# Pre-lightened flow tints for the memory chart (no opacity attribute allowed).
TINT_FRESH = "#bfe3c8"
TINT_UNVER = "#f0e0a8"
TINT_STALE = "#f3c0c4"


# ---------------------------------------------------------------------------
# two new primitives the scoreboard charts never needed — <line> and <polygon>.
# Both emit only plain attributes, so the self-contained-SVG contract holds.
# ---------------------------------------------------------------------------


def _line(x1: float, y1: float, x2: float, y2: float, stroke: str,
          *, sw: float = 1.0, dash: str = "") -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
            f'stroke="{stroke}" stroke-width="{_n(sw)}"{d}/>')


def _poly(points: list[tuple[float, float]], fill: str,
          *, stroke: str = "", sw: float = 0.0) -> str:
    s = f' stroke="{stroke}" stroke-width="{_n(sw or 1)}"' if stroke else ""
    pts = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
    return f'<polygon points="{pts}" fill="{fill}"{s}/>'


# A coarse text-width estimate so a centered in-bar label never overruns its
# segment (no DOM to measure against in a pure builder). ~0.56em per glyph for
# the bold sans stack at the sizes used here — deliberately generous so the test
# stays conservative.
def _fits(text: str, avail_px: float, size: float = 12) -> bool:
    return len(text) * size * 0.56 <= avail_px


# ---------------------------------------------------------------------------
# chart 1 (HERO) — the disbelief funnel.
# ---------------------------------------------------------------------------


def disbelief_funnel_chart(outcomes: dict[str, int], *,
                           pretool_p50_ms: float = 0.0,
                           width: int = 720) -> str:
    """A true-scale bar of every adjudicated call + a magnified stopped slice.

    `outcomes` is the full outcome Counter from `fold_observations()['outcomes']`
    — the builder derives every segment from it and authors no number. The
    stream is drawn over the five witnessed outcomes (pass / delegate / warn /
    deny / block) plus one honest `other` bucket for everything else the sensor
    emitted; `stopped` = warn+deny+block (override-admit lets a call THROUGH, so
    it is not counted as stopped). Empty → "".
    """
    passed = int(outcomes.get("passthrough", 0) or 0)
    delegate = int(outcomes.get("delegate", 0) or 0)
    warn = int(outcomes.get("warn", 0) or 0)
    deny = int(outcomes.get("deny", 0) or 0)
    block = int(outcomes.get("block", 0) or 0)
    total_all = sum(int(v or 0) for v in outcomes.values())
    other = total_all - (passed + delegate + warn + deny + block)
    total = passed + delegate + other + warn + deny + block
    if total <= 0:
        return ""
    stopped = warn + deny + block
    rate = stopped / total

    bx, bw = 16, width - 32
    header, stream_h, bracket_gap = 58, 56, 34
    connector, callout, footer = 28, 150, 26
    height = header + stream_h + bracket_gap + connector + callout + footer

    out = [_svg_open(
        width, height,
        title="What the kernel did to every agent action",
        desc="A full-width true-scale bar of all adjudicated tool calls, "
             "nearly all passed, with the tiny stopped slice broken out and "
             "magnified into a second bar split into warn, deny and block.")]
    out.append("  " + _text(16, 26,
                            f"What the kernel did to {total:,} agent actions",
                            size=16, weight="bold"))
    out.append("  " + _text(16, 46,
                            "every live tool call, adjudicated by the hook — "
                            "almost all pass, but it is always watching",
                            size=12, fill=MUTED))

    # the stream bar — every call, true scale.
    by, bh = 72, 40
    out.append("  " + _rect(bx, by, bw, bh, BAR_BG, rx=4))
    stream = [
        (passed, GREEN, "passed through"),
        (delegate, SLATE, "routed to full check"),
        (other, PALE, "other"),
        (warn, AMBER, "warned"),
        (deny, RED, "denied"),
        (block, DARKRED, "blocked"),
    ]
    seg_x = bx
    for count, colour, label in stream:
        seg_w = max(bw * count / total, 1) if count else 0
        if not seg_w:
            continue
        out.append("  " + _rect(seg_x, by, seg_w, bh, colour))
        if seg_w > 70:
            pct = count / total * 100
            full = f"{label} {count:,} ({pct:.0f}%)"
            short = f"{count:,} ({pct:.0f}%)"
            # the label is centered on the segment; pick the longest form whose
            # full width fits the segment AND whose right half stays on-panel.
            room = min(seg_w, 2 * (bx + bw - (seg_x + seg_w / 2)))
            lbl = full if _fits(full, room) else (
                short if _fits(short, room) else "")
            if lbl:
                out.append("  " + _text(
                    seg_x + seg_w / 2, by + bh * 0.62, lbl, size=12,
                    fill=WHITE, anchor="middle", weight="bold"))
        seg_x += seg_w
    # left edge of the warn segment = where "stopped" begins.
    stop_x = bx + bw * (passed + delegate + other) / total

    # the tap bracket pointing at the stopped slice (the thin warn+deny+block
    # tail). No inline label here — the slice is too thin to caption without
    # overrunning; the connector caption + footer carry the "stopped" count.
    out.append("  " + _line(stop_x, 112, stop_x, 126, MUTED, sw=1.5))
    out.append("  " + _line(stop_x, 126, bx + bw, 126, MUTED, sw=1.5))
    out.append("  " + _line(bx + bw, 122, bx + bw, 130, MUTED, sw=1.5))

    # dashed connector — a short right-side drop from the bracket to the callout
    # bar, kept clear of the left-aligned caption below.
    out.append("  " + _line(stop_x, 126, stop_x, 168, MUTED, sw=1, dash="4 3"))
    out.append("  " + _line(stop_x, 168, bx + bw, 168, MUTED, sw=1, dash="4 3"))
    out.append("  " + _text(
        16, 162,
        f"the {stopped:,} stopped calls (warn + deny + block), magnified",
        size=12, fill=RED, weight="bold"))

    # the callout bar — denominator is `stopped`, so the catch is visible.
    cy, ch = 172, 34
    out.append("  " + _rect(bx, cy, bw, ch, BAR_BG, rx=4))
    callout_segs = [(warn, AMBER, "warned"), (deny, RED, "denied"),
                    (block, DARKRED, "blocked")]
    seg_x = bx
    denom = stopped or 1
    for count, colour, label in callout_segs:
        seg_w = max(bw * count / denom, 3) if count else 0
        if not seg_w:
            continue
        out.append("  " + _rect(seg_x, cy, seg_w, ch, colour))
        lbl = f"{label} {count:,}"
        if seg_w > 70 and _fits(lbl, seg_w):
            out.append("  " + _text(seg_x + seg_w / 2, cy + ch * 0.64,
                                    lbl, size=12, fill=WHITE,
                                    anchor="middle", weight="bold"))
        seg_x += seg_w

    # callout legend — every stopped kind named, with its share of stopped.
    cy0 = cy + ch + 30
    legend = [
        (warn, AMBER, "warned"),
        (deny, RED, "denied"),
        (block, DARKRED, f"hard-blocked {block} — the kernel said NO"),
    ]
    for i, (count, colour, name) in enumerate(legend):
        cyi = cy0 + i * 26
        out.append("  " + _rect(16, cyi - 11, 13, 13, colour, rx=2))
        if name.startswith("hard-blocked"):
            txt = name
        else:
            pct = count / denom * 100
            txt = f"{name} {count:,} ({pct:.0f}%)"
        out.append("  " + _text(16 + 20, cyi, txt, size=12, fill=FG))

    # footer — the honest headline + the median check cost.
    out.append("  " + _text(
        16, height - 12,
        f"{stopped:,} of {total:,} calls stopped ({rate * 100:.1f}%)  ·  "
        f"median pretool latency {pretool_p50_ms:.1f}ms",
        size=12, fill=FG, weight="bold"))

    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# chart 2 — the self-modify wall.
# ---------------------------------------------------------------------------


def self_modify_wall_chart(self_modify: dict, *, enforce_block: int = 0,
                           enforce_warn: int = 0, n_entries: int = 0,
                           width: int = 720) -> str:
    """A tally of every self-modify refusal against a wall, + which module each
    refusal named, + the full admission-intervention strip.

    `self_modify` is `fold_lane_journal()['self_modify']`
    ({total, block, warn, got_through, by_module}). All counts come from the
    caller. Empty (`total == 0`) → "".
    """
    sm_total = int(self_modify.get("total", 0) or 0)
    if sm_total <= 0:
        return ""
    sm_block = int(self_modify.get("block", 0) or 0)
    sm_warn = int(self_modify.get("warn", 0) or 0)
    got_through = int(self_modify.get("got_through", 0) or 0)
    by_module = list(self_modify.get("by_module") or [])

    height = 430
    out = [_svg_open(
        width, height,
        title="The self-modify wall",
        desc="A tally of every self-modify refusal stacked against a solid "
             "wall with zero passing through, a ranked bar of which kernel "
             "module each refusal named, and the full intervention strip.")]
    out.append("  " + _text(16, 28, "The self-modify wall", size=16,
                            weight="bold"))
    out.append("  " + _text(16, 47,
                            "every time a live agent tried to edit the kernel "
                            "adjudicating it", size=12, fill=MUTED))

    # ZONE 1 — the wall. A tick per refusal (red = blocked first, amber = warned
    # after), then a solid wall, then the "got through" tally on the far side.
    out.append("  " + _text(16, 118, "agent edit -> kernel", size=12,
                            fill=MUTED))
    cols = 9
    per_col = max((sm_total + cols - 1) // cols, 1)
    for i in range(sm_total):
        col = i // per_col
        row = i % per_col
        tx = 140 + col * 36
        ty = 66 + row * (98 / per_col)
        fill = RED if i < sm_block else AMBER2
        out.append("  " + _rect(tx, ty, 14, 2.5, fill))
    out.append("  " + _rect(486, 62, 6, 104, FG))  # the wall
    out.append("  " + _text(504, 110, f"{got_through} got through", size=13,
                            fill=FG, weight="bold"))
    out.append("  " + _text(504, 128, f"{sm_block} blocked, {sm_warn} warned",
                            size=11, fill=MUTED))

    # ZONE 2 — which kernel module each refusal named (the arbiter dominates).
    out.append("  " + _text(16, 198, "the kernel module named in each refusal",
                            size=13, weight="bold"))
    max_n = (by_module[0][1] if by_module else 1) or 1
    row_h, gap = 18, 10
    bar_max = 430          # leaves room for the count + the arbiter annotation
    y = 210
    for name, cnt in by_module:
        bw = cnt / max_n * bar_max
        out.append("  " + _text(162, y + 13, name, size=12, anchor="end",
                                fill=FG))
        out.append("  " + _rect(170, y, bw, 18, RED, rx=2))
        out.append("  " + _text(170 + bw + 6, y + 13, f"{cnt}", size=11,
                                fill=FG))
        if name.endswith("arbiter.py"):
            # the punchline annotation — placed inside the (longest) bar,
            # right-aligned in white, so it never overruns the panel edge.
            note = "the module doing the refusing ->"
            if _fits(note, bw - 16, 11):
                out.append("  " + _text(170 + bw - 8, y + 13, note, size=11,
                                        fill=WHITE, anchor="end",
                                        weight="bold"))
        y += row_h + gap

    # ZONE 3 — the full admission-intervention strip (block vs warn).
    enforce_total = (enforce_block + enforce_warn) or 1
    out.append("  " + _rect(16, 344, 688, 22, BAR_BG, rx=4))
    blockw = 688 * enforce_block / enforce_total
    warnw = 688 * enforce_warn / enforce_total
    if blockw:
        out.append("  " + _rect(16, 344, blockw, 22, RED))
        if blockw > 70:
            out.append("  " + _text(16 + blockw / 2, 359,
                                    f"{enforce_block:,} blocked", size=11,
                                    fill=WHITE, anchor="middle"))
    if warnw:
        out.append("  " + _rect(16 + blockw, 344, warnw, 22, AMBER2))
        if warnw > 70:
            out.append("  " + _text(16 + blockw + warnw / 2, 359,
                                    f"{enforce_warn:,} warned", size=11,
                                    fill=FG, anchor="middle"))
    out.append("  " + _text(
        16, 386,
        f"{enforce_total:,} admission interventions — {sm_total:,} were "
        "self-modify refusals", size=11, fill=MUTED))
    out.append("  " + _text(
        16, 404,
        f"source: this repo's own .dos/lane-journal.jsonl, {n_entries:,} "
        "entries · generated, not hand-counted", size=10, fill=MUTED))

    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# chart 3 — the kernel re-checks its own memory.
# ---------------------------------------------------------------------------


def memory_recall_chart(recall: dict[str, int], *, width: int = 720) -> str:
    """A single stacked bar of recalled memories by re-check verdict.

    `recall` is `fold_verdict_journal()['recall']` — {RECALL_FRESH,
    RECALL_UNVERIFIABLE, RECALL_STALE} counts. A flow from one source node into
    three verdict segments (fresh / unverifiable / stale). Empty → "".
    """
    fresh = int(recall.get("RECALL_FRESH", 0) or 0)
    unver = int(recall.get("RECALL_UNVERIFIABLE", 0) or 0)
    stale = int(recall.get("RECALL_STALE", 0) or 0)
    total = fresh + unver + stale
    if total <= 0:
        return ""

    height = 316
    out = [_svg_open(
        width, height,
        title="The kernel re-checks its own memory before trusting it",
        desc="Of the memories the kernel recalled, a single stacked bar split "
             "into re-verified-still-true, could-not-re-verify, and "
             "gone-stale, with a flow from one source node.")]
    out.append("  " + _text(16, 30,
                            "It re-checks its own memory before trusting it",
                            size=16, weight="bold"))
    out.append("  " + _text(16, 50,
                            f"{total} memories recalled — how many were still "
                            "TRUE on re-check", size=12, fill=MUTED))

    # the source node — all recalled memories.
    out.append("  " + _rect(16, 90, 18, 150, SLATE, rx=3))
    out.append("  " + _text(16, 260, f"{total} recalled", size=13,
                            weight="bold", fill=FG))

    # the outcome bars + the flow polygons, top→bottom by severity-of-doubt.
    ox, bar_w, fh, y0 = 470, 234, 150, 90
    segs = [
        ("RECALL_FRESH", fresh, GREEN, TINT_FRESH, "still true"),
        ("RECALL_UNVERIFIABLE", unver, AMBER2, TINT_UNVER, "unverifiable"),
        ("RECALL_STALE", stale, RED, TINT_STALE, "stale"),
    ]
    seg_y = y0
    src_y = y0
    for _key, count, colour, tint, short in segs:
        if not count:
            continue
        h = max(fh * count / total, 1.6)
        s_h = fh * count / total
        # flow from the contiguous source slice to this bar.
        out.append("  " + _poly(
            [(34, src_y), (ox, seg_y), (ox, seg_y + h), (34, src_y + s_h)],
            tint))
        out.append("  " + _rect(ox, seg_y, bar_w, h, colour, rx=3))
        if h > 16:
            pct = count / total * 100
            out.append("  " + _text(
                ox + 8, seg_y + h / 2 + 4,
                f"{count} ({pct:.0f}%) {short}", size=12, fill=FG))
        else:
            # the stale sliver — a leader line + label so n=1 stays visible.
            out.append("  " + _line(ox + bar_w, seg_y + h / 2, 660, 270, RED,
                                    sw=1))
            out.append("  " + _text(656, 274,
                                    f"{count} STALE — the note was wrong now",
                                    size=11, fill=RED, weight="bold",
                                    anchor="end"))
        seg_y += h
        src_y += s_h

    # the honest caption — two lines so it never clips at 720px wide.
    out.append("  " + _line(16, 278, 704, 278, PANEL_BORDER, sw=1))
    out.append("  " + _text(
        16, 294,
        f"Only {fresh} of {total} recalled memories re-verified as still true; "
        f"{unver} were unverifiable, {stale} had gone stale.",
        size=11, fill=MUTED))
    out.append("  " + _text(
        16, 309,
        "FRESH = the memory's claims re-check against git + the working tree "
        "now.", size=11, fill=MUTED))

    out.append("</svg>")
    return "\n".join(out)
