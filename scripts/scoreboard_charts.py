#!/usr/bin/env python3
"""scoreboard_charts — self-contained inline SVG for the scoreboard front door.

The drift scoreboard collects rich per-repo facts — how much of each repo AI
built, which agents did it, and what KIND of work those commits claimed — and
then flattens all of it into one word: "clean". This module is the visual half
of the observatory reframe: it turns the facts we already have into a few
honest pictures a cold reader takes in at a glance, instead of a wall of
identical green links.

What it draws, all from the committed per-repo `sweep.json` data:

  * **AI-built share** — a ranked horizontal bar per repo (`attributed /
    commits_scanned`), so "how much of THIS popular repo did agents write" is
    the first thing the eye lands on.
  * **The agent mix** — one stacked bar per repo over the marker counts
    (claude / codex / copilot / devin / cursor / …), so which agent builds
    which repo is a shape, not a buried `·`-joined string.
  * **Claim-kind composition** — one stacked bar of the whole set's checkable
    claims by kind (code / test / doc), so the reader sees what agents are
    actually doing, not just that it checked out.

Design constraints this file keeps (they are load-bearing, not cosmetic):

  * **Pure + deterministic.** Data in, SVG string out. No clock, no random, no
    I/O. The same rows always render byte-identical, so the committed assets
    are reproducible and a `--check` can prove they match the data — the same
    honesty gate `scoreboard_rollup.py` runs on its numbers.
  * **No JS, no external fetch, no CSS variables.** A committed `.svg` embedded
    via Markdown image syntax renders on BOTH GitHub's Markdown (which strips
    inline `<svg>` and `<style>`, and ignores CSS custom properties) AND the
    Pages HTML site. So every colour is an explicit literal, every label is a
    real `<text>` element, and each chart paints its own background rectangle
    so it is legible on a light OR a dark page.
  * **Stdlib only, dev tooling.** Lives under `scripts/`; nothing under
    `src/dos/` imports it — the one-way arrow `drift_scoreboard.py` and the
    other scoreboard generators follow.

The numbers are NEVER authored here. Every count comes from the caller (the
folded `sweep.json` rows); this module owns geometry and colour, never a
hard-coded total that could drift from the data.
"""
from __future__ import annotations

from html import escape

# ---------------------------------------------------------------------------
# The palette — explicit literals (no CSS vars; GitHub Markdown ignores them).
# Chosen to read on a white Pages background AND GitHub dark mode: every chart
# paints a near-white panel rect first, so dark axis/label text always sits on
# light. The agent colours are a fixed, named map so the same agent is the same
# colour across every chart on the site — a legend the eye learns once.
# ---------------------------------------------------------------------------

PANEL = "#f6f8fa"          # the panel fill behind every chart (matches the site)
PANEL_BORDER = "#d0d7de"   # 1px frame
FG = "#1f2328"             # dark label / axis text — always on the light panel
MUTED = "#59636e"          # secondary labels, gridlines
BAR_BG = "#e7ebef"         # the empty track behind a value bar
ACCENT = "#0969da"         # the AI-built-share bar
GREEN = "#1a7f37"          # backed / code-effect

# The agent colour map. Keys are the marker labels drift_scoreboard emits. An
# agent not in the map falls back to OTHER (deterministic — same input, same
# colour), so a new toolchain never breaks a render, it just shares the grey.
# Hues chosen to stay distinguishable side-by-side in a stacked bar: the three
# biggest agents (claude / codex / copilot) get the most separated hues, and no
# two agents that commonly co-occur share a near-identical tone (crush and devin
# were both blue and read as one segment — crush is now teal).
AGENT_COLORS = {
    "claude": "#d97757",     # the Anthropic terracotta
    "codex": "#10a37f",      # OpenAI green
    "copilot": "#8250df",    # GitHub purple
    "devin": "#2f81f7",      # blue
    "cursor": "#e3b341",     # amber
    "aider": "#bf3989",      # magenta
    "crush": "#0fb5c0",      # charm teal (distinct from devin's blue)
    "jules": "#fb8500",      # orange-amber (distinct from copilot's purple)
    "openhands": "#3fb950",  # green
    "qwen": "#db61a2",       # pink
    "sweep": "#f0883e",      # orange
    "roomote": "#56d364",    # light green
    "opencode": "#a371f7",   # light violet
}
OTHER = "#8b949e"           # any unmapped agent — the shared grey

# Claim-kind colours — the three checkable kinds plus the skipped bucket.
KIND_COLORS = {
    "code_effect": "#0969da",
    "test": "#1a7f37",
    "doc": "#8957e5",
    "none": "#8b949e",
}
KIND_LABELS = {
    "code_effect": "code (fix/add/remove)",
    "test": "tests",
    "doc": "docs",
    "none": "no checkable claim",
}


# ---------------------------------------------------------------------------
# small SVG helpers — pure string builders. Every coordinate is rounded to a
# stable string so the output never carries a float that re-renders differently.
# ---------------------------------------------------------------------------


def _n(x: float) -> str:
    """Format a coordinate: integers as ints, else 2dp — deterministic bytes."""
    r = round(float(x), 2)
    if r == int(r):
        return str(int(r))
    return f"{r:.2f}".rstrip("0").rstrip(".")


def _rect(x: float, y: float, w: float, h: float, fill: str,
          *, rx: float = 0.0, stroke: str = "", sw: float = 0.0) -> str:
    parts = [f'x="{_n(x)}"', f'y="{_n(y)}"', f'width="{_n(max(w, 0))}"',
             f'height="{_n(max(h, 0))}"', f'fill="{fill}"']
    if rx:
        parts.append(f'rx="{_n(rx)}"')
    if stroke:
        parts.append(f'stroke="{stroke}"')
        parts.append(f'stroke-width="{_n(sw or 1)}"')
    return f"<rect {' '.join(parts)}/>"


def _text(x: float, y: float, s: str, *, fill: str = FG, size: float = 13,
          anchor: str = "start", weight: str = "normal") -> str:
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    w = f' font-weight="{weight}"' if weight != "normal" else ""
    return (f'<text x="{_n(x)}" y="{_n(y)}" font-size="{_n(size)}" '
            f'fill="{fill}"{a}{w}>{escape(s)}</text>')


# A shared font stack on the root <svg> so labels match the site without a
# <style> block (which GitHub would strip).
_FONT = ('font-family="-apple-system, BlinkMacSystemFont, ' "'Segoe UI'" ', '
         "Roboto, Helvetica, Arial, sans-serif\"")


def _svg_open(width: float, height: float, *, title: str, desc: str) -> str:
    """Open an <svg> with an accessible title/desc and the panel background."""
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(width)}" '
        f'height="{_n(height)}" viewBox="0 0 {_n(width)} {_n(height)}" '
        f'role="img" aria-label="{escape(title)}" {_FONT}>',
        f"  <title>{escape(title)}</title>",
        f"  <desc>{escape(desc)}</desc>",
        "  " + _rect(0, 0, width, height, PANEL, rx=8,
                     stroke=PANEL_BORDER, sw=1),
    ])


# ---------------------------------------------------------------------------
# chart 1 — AI-built share, a ranked horizontal bar per repo.
# ---------------------------------------------------------------------------


def ai_share_chart(rows: list[dict], *, width: int = 720) -> str:
    """Ranked horizontal bars of AI-built share (`attributed / scanned`).

    `rows` is the leaderboard row list (each {full, attributed, scanned, …}).
    Rows with no scan denominator are dropped (no honest share to draw). The
    caller controls order; this draws them top-to-bottom as given. Returns a
    self-contained `<svg>` string. Empty rows → "".
    """
    data = [(r["full"], int(r.get("attributed", 0) or 0),
             int(r.get("scanned", 0) or 0))
            for r in rows if int(r.get("scanned", 0) or 0) > 0]
    if not data:
        return ""

    pad_top, pad_bot = 56, 20
    row_h, gap = 26, 8
    label_w = 240          # left gutter for the repo name
    pct_w = 64             # right gutter for the "NN%" value
    bar_x = label_w
    bar_w = width - label_w - pct_w
    height = pad_top + len(data) * (row_h + gap) - gap + pad_bot

    max_share = max((a / s) for _, a, s in data) or 1.0

    out = [_svg_open(width, height,
                     title="How much of each repo AI built",
                     desc="Horizontal bars of agent-authored commit share per "
                          "repository, ranked highest first.")]
    out.append("  " + _text(16, 26, "How much of each repo AI built",
                            size=16, weight="bold"))
    out.append("  " + _text(16, 44, "agent-authored share of recent commits",
                            size=12, fill=MUTED))

    y = pad_top
    for full, attributed, scanned in data:
        share = attributed / scanned
        # the value bar scales to the LARGEST share so the field is comparable,
        # never to 100% (most shares are small — a 4% bar against a 100% track
        # would be invisible). The "% of max" framing is honest because the
        # axis caption says "ranked"; the number printed is the true percent.
        fill_w = bar_w * (share / max_share)
        name = full if len(full) <= 32 else full[:31] + "…"
        out.append("  " + _text(16, y + row_h * 0.68, name, size=13))
        out.append("  " + _rect(bar_x, y, bar_w, row_h, BAR_BG, rx=4))
        out.append("  " + _rect(bar_x, y, fill_w, row_h, ACCENT, rx=4))
        pct = f"{share * 100:.0f}%" if share >= 0.005 else "<1%"
        out.append("  " + _text(width - 12, y + row_h * 0.68, pct,
                                size=13, anchor="end", weight="bold"))
        y += row_h + gap

    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# chart 2 — the agent mix, one 100%-stacked bar per repo.
# ---------------------------------------------------------------------------


def agent_color(label: str) -> str:
    """The fixed colour for an agent marker — OTHER for anything unmapped."""
    return AGENT_COLORS.get(label.lower(), OTHER)


def agent_mix_chart(rows: list[dict], *, width: int = 720) -> str:
    """One 100%-stacked horizontal bar per repo over its agent marker counts.

    Each bar is normalized to the repo's own attributed total, so the SHAPE
    (which agent dominates) is comparable across repos of very different sizes.
    A small legend of the agents that actually appear is drawn at the top.
    `rows` carry `mix` = sorted list of (label, count). Empty → "".
    """
    data = [(r["full"], list(r.get("mix") or [])) for r in rows
            if r.get("mix")]
    data = [(full, mix) for full, mix in data if sum(c for _, c in mix) > 0]
    if not data:
        return ""

    # the legend: every agent appearing anywhere, biggest total first.
    totals: dict[str, int] = {}
    for _full, mix in data:
        for label, count in mix:
            totals[label] = totals.get(label, 0) + int(count)
    legend = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))

    pad_top = 40 + 22 * ((len(legend) + 3) // 4)  # legend rows, 4 per line
    pad_bot = 20
    row_h, gap = 24, 9
    label_w = 240
    bar_x = label_w
    bar_w = width - label_w - 16
    height = pad_top + len(data) * (row_h + gap) - gap + pad_bot

    out = [_svg_open(width, height,
                     title="Which agent built which repo",
                     desc="One stacked bar per repository over its agent "
                          "marker counts, normalized to the repo total.")]
    out.append("  " + _text(16, 24, "Which agent built which repo",
                            size=16, weight="bold"))

    # legend chips, 4 per row.
    lx, ly = 16, 42
    per_row = 4
    col_w = (width - 32) / per_row
    for i, (label, total) in enumerate(legend):
        col = i % per_row
        rown = i // per_row
        cx = lx + col * col_w
        cy = ly + rown * 22
        out.append("  " + _rect(cx, cy - 10, 12, 12, agent_color(label), rx=2))
        out.append("  " + _text(cx + 18, cy, f"{label} {total:,}",
                                size=12, fill=FG))

    y = pad_top
    for full, mix in data:
        total = sum(int(c) for _, c in mix) or 1
        name = full if len(full) <= 32 else full[:31] + "…"
        out.append("  " + _text(16, y + row_h * 0.68, name, size=13))
        out.append("  " + _rect(bar_x, y, bar_w, row_h, BAR_BG, rx=4))
        seg_x = bar_x
        for label, count in mix:
            seg_w = bar_w * (int(count) / total)
            out.append("  " + _rect(seg_x, y, seg_w, row_h,
                                    agent_color(label)))
            seg_x += seg_w
        y += row_h + gap

    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# chart 3 — claim-kind composition across the whole published set.
# ---------------------------------------------------------------------------


def claim_kind_chart(by_kind_totals: dict[str, int], *, width: int = 720,
                     backed: int | None = None,
                     checkable: int | None = None) -> str:
    """One 100%-stacked bar of the set's checkable claims by kind.

    `by_kind_totals` maps a claim kind (`code_effect`/`test`/`doc`) to its
    witnessed count across the set. The `none`/skipped bucket is excluded — the
    bar is over CHECKABLE claims, which is what the kinds describe. An optional
    `backed`/`checkable` pair prints the headline trust fact beneath the bar.
    Empty totals → "".
    """
    kinds = [(k, int(v)) for k, v in by_kind_totals.items()
             if k != "none" and int(v) > 0]
    if not kinds:
        return ""
    kinds.sort(key=lambda kv: (-kv[1], kv[0]))
    total = sum(v for _, v in kinds) or 1

    width = int(width)
    bar_x, bar_w = 16, width - 32
    bar_y, bar_h = 64, 34
    height = bar_y + bar_h + 30 + 28 * ((len(kinds) + 2) // 3)
    if backed is not None and checkable:
        height += 26

    out = [_svg_open(width, height,
                     title="What kind of work AI commits claimed",
                     desc="A single stacked bar of checkable claims by kind "
                          "across the published set.")]
    out.append("  " + _text(16, 28, "What kind of work AI commits claimed",
                            size=16, weight="bold"))
    out.append("  " + _text(16, 48, "checkable claims across the published "
                            "set, by kind", size=12, fill=MUTED))

    seg_x = bar_x
    out.append("  " + _rect(bar_x, bar_y, bar_w, bar_h, BAR_BG, rx=4))
    for kind, count in kinds:
        seg_w = bar_w * (count / total)
        out.append("  " + _rect(seg_x, bar_y, seg_w, bar_h,
                                KIND_COLORS.get(kind, OTHER)))
        seg_x += seg_w

    # legend, 3 per row, with the count + percent.
    ly = bar_y + bar_h + 26
    per_row = 3
    col_w = (width - 32) / per_row
    for i, (kind, count) in enumerate(kinds):
        col = i % per_row
        rown = i // per_row
        cx = 16 + col * col_w
        cy = ly + rown * 28
        out.append("  " + _rect(cx, cy - 11, 13, 13,
                                KIND_COLORS.get(kind, OTHER), rx=2))
        pct = count / total * 100
        label = KIND_LABELS.get(kind, kind)
        out.append("  " + _text(cx + 20, cy, f"{label} — {count:,} ({pct:.0f}%)",
                                size=12, fill=FG))

    if backed is not None and checkable:
        rate = backed / checkable if checkable else 0.0
        # No apostrophe in the SVG text: an escaped &#x27; renders fine in a
        # browser but is noise in the markup and in any non-HTML rasterizer.
        msg = (f"{backed:,} of {checkable:,} claims backed by the diff "
               f"({rate * 100:.1f}%)")
        out.append("  " + _text(16, height - 12, msg, size=12,
                                fill=GREEN, weight="bold"))

    out.append("</svg>")
    return "\n".join(out)
