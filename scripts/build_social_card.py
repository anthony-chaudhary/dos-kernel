#!/usr/bin/env python
"""build_social_card.py — render the repo's 1280x640 GitHub social-preview card.

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four
> things live OUTSIDE the four layers"). Like `build_caught_lie_cast.py`, this
> consumes the repo and the kernel is unaware it exists. It keeps ONE fact
> true: that the share card GitHub unfurls for this repo
> (`docs/assets/social-card.svg` + its `.png`) is a RENDERING of the real CLI
> — the SHIPPED sha and both verdicts on it are verbatim `dos verify` output
> from a session this script drives — never a hand-typed dramatization.

Why a card at all
=================

A repo's **social preview** is the image platforms show *before* anyone
clicks — the Slack/Discord/Twitter-X/LinkedIn unfurl, the Open Graph card.
GitHub's default is an auto-generated card with the avatar + description; it
does no marketing work. The repo already ships a signature visual identity
(`loop-hero.svg`, the near-black canvas, acid-green SHIPPED / hot-red
NOT_SHIPPED), but the hero is 1200x640 narrative art tuned for an inline
README embed — too busy to read at thumbnail size, and the wrong file type
(GitHub's social slot takes a PNG/JPG/GIF under 1 MB at 1280x640, not an SVG).

So this builds a PURPOSE-BUILT card for that slot:

  * **1280x640** — GitHub's recommended social-preview size (2:1).
  * **reads at thumbnail size** — one hook line, the two-line money moment
    (one SHIPPED, one NOT_SHIPPED), a big verdict stamp; not the full
    two-panel loop diagram, which turns to mud when scaled down.
  * **honest by construction** — the sha and both verdict strings are
    captured live from the real `dos verify` against the canonical caught-lie
    demo (`dos._demo_story`), the same single source `dos quickstart` and the
    cast builder render. If the CLI's output changes shape, `--check` goes red
    and the fix is to re-run this script, never to hand-edit the asset.
  * **freeze-safe** — it's a STATIC card (no animation to depend on); every
    element rests at its natural, fully-visible state, so a renderer that
    strips CSS or never animates still produces the finished image. That also
    makes the SVG->PNG rasterization a single deterministic frame.

How it gets onto GitHub (the one human-only step)
=================================================

GitHub exposes no API for the social-preview slot — it is uploaded through
the web UI. After this script writes the PNG:

    Repo -> Settings -> (General) -> "Social preview" -> Edit -> upload
    docs/assets/social-card.png

That is the single manual action; everything up to the bytes is scripted here.
`docs/assets/README.md` carries the same instruction next to the asset.

Usage
=====

    python scripts/build_social_card.py            # re-record + write the SVG (+ PNG if a renderer is present)
    python scripts/build_social_card.py --check     # verify committed SVG == a fresh re-render
    python scripts/build_social_card.py --no-png     # SVG only (skip rasterization)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from dos import _demo_story as story

SVG_OUT = Path("docs") / "assets" / "social-card.svg"
PNG_OUT = Path("docs") / "assets" / "social-card.png"

# Pinned commit identity + dates — what makes the demo repo's sha (and so the
# captured verdict lines) byte-deterministic across machines and re-runs. Kept
# identical to build_caught_lie_cast.py so both assets tell the same scene with
# the same sha.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "DOS Quickstart",
    "GIT_AUTHOR_EMAIL": "quickstart@dos.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000",
    "GIT_COMMITTER_NAME": "DOS Quickstart",
    "GIT_COMMITTER_EMAIL": "quickstart@dos.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000",
}


# ---------------------------------------------------------------------------
# recording — drive the REAL CLI against the canonical demo, capture verdicts
# ---------------------------------------------------------------------------
def record() -> dict[str, str]:
    """Build the throwaway caught-lie repo, run `dos verify` twice, return the
    verbatim verdict lines + the short sha. Raises if the contrast is not the
    expected SHIPPED(exit 0) / NOT_SHIPPED(exit 1) — an environment fault to
    fail on, never to paper over."""
    if shutil.which("git") is None:
        raise RuntimeError("recording needs git on PATH")

    with tempfile.TemporaryDirectory(prefix="dos-card-") as tmp:
        work = Path(tmp)
        env = {**os.environ, **_GIT_ENV}

        def _git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(work), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                env=env,
                stdin=subprocess.DEVNULL,
            ).stdout.strip("\n")

        _git("init", "-q")
        _git("config", "commit.gpgsign", "false")
        _git("config", "core.autocrlf", "false")
        (work / "src").mkdir()
        (work / "src" / story.WORK_FILE).write_bytes(
            story.WORK_CONTENT.encode("utf-8")
        )
        _git("add", "-A")
        _git("commit", "-q", "--no-verify", "-m", story.COMMIT_SUBJECT)
        sha = _git("rev-parse", "--short", "HEAD")

        v1, c1 = _dos(work, "verify", story.PLAN, story.SHIPPED_PHASE)
        v2, c2 = _dos(work, "verify", story.PLAN, story.UNSHIPPED_PHASE)

    if not (
        c1 == 0
        and c2 == 1
        and v1.startswith("SHIPPED ")
        and v2.startswith("NOT_SHIPPED ")
        and sha and sha in v1
        and "\n" not in v1
        and "\n" not in v2
    ):
        raise RuntimeError(
            "the recording did not produce the expected SHIPPED/NOT_SHIPPED "
            f"contrast: verify#1 -> {v1!r} (exit {c1}), "
            f"verify#2 -> {v2!r} (exit {c2}), sha {sha!r}"
        )
    return {"sha": sha, "v1": v1, "v2": v2}


def _dos(work: Path, *args: str) -> tuple[str, int]:
    """Run the real `dos` CLI (the same `dos.cli:main` the console script is)
    in a subprocess against the throwaway workspace; return (stdout, exit).
    Mirrors build_caught_lie_cast.py byte for byte so both assets capture the
    identical verdict lines."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from dos.cli import main; sys.exit(main())",
            *args,
            "--workspace",
            str(work),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
    )
    return proc.stdout.strip("\n"), proc.returncode


# ---------------------------------------------------------------------------
# rendering — a static, freeze-safe 1280x640 card in the signature identity
# ---------------------------------------------------------------------------
VIEW_W = 1280
VIEW_H = 640


def render(capture: dict[str, str]) -> str:
    sha = capture["sha"]
    # The card shows the money moment, not the full loop. Split the captured
    # verdict lines into a head token + the parenthetical so the big stamp can
    # carry the verdict and a dim sub-line carry the (via ...) provenance.
    v1_head, v1_via = _split_verdict(capture["v1"])
    v2_head, v2_via = _split_verdict(capture["v2"])

    aria = (
        "DOS social card. Headline: catch your AI agents when they lie about "
        "what they shipped. An agent claims it shipped two features, AUTH1 and "
        f"AUTH2. dos verify AUTH AUTH1 answers SHIPPED {sha}, exit 0. "
        "dos verify AUTH AUTH2 answers NOT_SHIPPED, exit 1 — caught. The "
        "verdict comes from git, never the agent's word. pip install "
        "dos-kernel, MIT."
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW_W} {VIEW_H}" width="{VIEW_W}" height="{VIEW_H}" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" role="img" aria-label={quoteattr(aria)}>
  <title>DOS — catch your AI agents when they lie about what they shipped</title>
  <desc>The GitHub social-preview card. Every verdict string is verbatim `dos verify` output recorded from the real CLI against the canonical caught-lie demo — a rendering of ground truth, never a hand-typed dramatization. Static and freeze-safe: every element rests at its fully-visible state.</desc>

  <style>
    .canvas  {{ fill: #0a0a0f; }}
    .vignette{{ fill: url(#vg); }}
    .ink     {{ fill: #eef1f6; }}
    .dim     {{ fill: #8b93a7; }}
    .faint   {{ fill: #5b6478; }}
    .lie     {{ fill: #ff4d57; }}   /* hot-red — NOT_SHIPPED */
    .ship    {{ fill: #2bff88; }}   /* acid-green — SHIPPED */
    .accent  {{ fill: #36e0ff; }}   /* electric cyan — the 'ask git' rung */
    .claim   {{ fill: #ffc24d; }}   /* amber — the forgeable narration */
    .mono    {{ font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    .stamp   {{ font-family: "Arial Black","Helvetica Neue",Arial,sans-serif; font-weight: 900; letter-spacing: 1px; }}
  </style>

  <defs>
    <radialGradient id="vg" cx="50%" cy="30%" r="80%">
      <stop offset="0%"  stop-color="#13131c"/>
      <stop offset="60%" stop-color="#0a0a0f"/>
      <stop offset="100%" stop-color="#060609"/>
    </radialGradient>
  </defs>

  <!-- backdrop -->
  <rect class="canvas"   x="0" y="0" width="{VIEW_W}" height="{VIEW_H}"/>
  <rect class="vignette" x="0" y="0" width="{VIEW_W}" height="{VIEW_H}"/>

  <!-- ===================== WORDMARK ===================== -->
  <text x="64" y="92" class="ink stamp" font-size="46">DOS</text>
  <text x="180" y="92" class="dim" font-size="24">— the kernel that doesn&#8217;t believe the agents</text>

  <!-- ===================== HEADLINE ===================== -->
  <text x="64" y="178" class="ink stamp" font-size="50">Catch your AI agents</text>
  <text x="64" y="240" class="ink stamp" font-size="50">when they <tspan class="lie">lie</tspan> about what they shipped.</text>

  <!-- ===================== THE MONEY MOMENT ===================== -->
  <!-- a terminal-tinted card holding the two verbatim verify lines -->
  <rect x="64" y="288" width="{VIEW_W - 128}" height="210" rx="14" fill="#10101a" stroke="#272736" stroke-width="1.5"/>

  <!-- the agent's forgeable claim -->
  <text x="92" y="330" class="claim mono" font-size="19" font-weight="700">agent: {escape(_agent_line())}</text>

  <!-- verify #1 -> SHIPPED -->
  <text x="92" y="378" class="accent mono" font-size="20">$ dos verify {escape(story.PLAN)} {escape(story.SHIPPED_PHASE)}</text>
  <g>
    <rect x="560" y="356" width="280" height="34" rx="7" fill="#062417" stroke="#2bff88" stroke-width="1.4"/>
    <text x="578" y="380" class="ship stamp" font-size="17">{escape(v1_head)}</text>
    <text x="828" y="379" text-anchor="end" class="ship mono" font-size="18" font-weight="700">&#10003;</text>
  </g>
  <text x="858" y="380" class="faint mono" font-size="15">{escape(v1_via)} &#183; exit 0</text>

  <!-- verify #2 -> NOT_SHIPPED -->
  <text x="92" y="438" class="accent mono" font-size="20">$ dos verify {escape(story.PLAN)} {escape(story.UNSHIPPED_PHASE)}</text>
  <g>
    <rect x="560" y="416" width="280" height="34" rx="7" fill="#23090d" stroke="#ff4d57" stroke-width="1.4"/>
    <text x="578" y="440" class="lie stamp" font-size="17">{escape(v2_head)}</text>
    <text x="828" y="439" text-anchor="end" class="lie mono" font-size="18" font-weight="700">&#10007;</text>
  </g>
  <text x="858" y="440" class="claim mono" font-size="15">{escape(v2_via)} &#183; exit 1 &#8212; caught</text>

  <text x="92" y="478" class="dim mono" font-size="15.5">the verdict comes from <tspan class="accent">git</tspan>, never the agent&#8217;s word &#8212; gate &#8220;done&#8221; on the exit code and a false claim can&#8217;t land</text>

  <!-- ===================== FOOTER ===================== -->
  <text x="64" y="568" class="ink mono" font-size="22" font-weight="700">pip install dos-kernel</text>
  <text x="{VIEW_W - 64}" y="556" text-anchor="end" class="mono" font-size="16">
    <tspan class="ship">verify</tspan> <tspan class="faint">&#183;</tspan> <tspan class="accent">arbitrate</tspan> <tspan class="faint">&#183;</tspan> <tspan class="claim">refuse</tspan> <tspan class="faint">&#183;</tspan> <tspan class="dim">liveness</tspan></text>
  <text x="{VIEW_W - 64}" y="580" text-anchor="end" class="faint mono" font-size="14">works on a plain git repo &#183; MIT</text>
</svg>
"""


def _agent_line() -> str:
    """The agent's claim, trimmed of its quotes for the inline label."""
    return story.AGENT_CLAIM.strip('"')


def _split_verdict(line: str) -> tuple[str, str]:
    """Split 'SHIPPED AUTH AUTH1 ee8f8e6 (via grep-subject)' into the stamp head
    ('SHIPPED ee8f8e6') and the provenance ('via grep-subject'). Falls back
    gracefully if the shape ever changes — the head is the first token, the via
    is whatever is parenthesized."""
    via = ""
    if "(" in line and line.rstrip().endswith(")"):
        head_part, _, paren = line.partition("(")
        via = paren.rstrip(")").strip()
        line = head_part.strip()
    toks = line.split()
    verdict = toks[0] if toks else line
    # keep the short sha if present (last token that looks like a hex sha)
    sha = next(
        (t for t in reversed(toks[1:]) if _looks_like_sha(t)),
        "",
    )
    head = f"{verdict} {sha}".strip()
    return head, via


def _looks_like_sha(tok: str) -> bool:
    return 6 <= len(tok) <= 12 and all(c in "0123456789abcdef" for c in tok)


# ---------------------------------------------------------------------------
# rasterization — SVG -> PNG (a single static frame, under GitHub's 1 MB cap)
# ---------------------------------------------------------------------------
def rasterize(svg_path: Path, png_path: Path) -> str | None:
    """Render the static SVG to a 1280x640 PNG. Tries rsvg-convert, then a
    headless-browser screenshot (playwright/npx) — both produce the same single
    frame since the card has no animation. Returns the tool used, or None if no
    rasterizer is available (the SVG is still the source of truth; the README
    documents the manual convert recipe)."""
    # shutil.which resolves the real executable (incl. the .cmd shim on
    # Windows, where bare "npx"/"rsvg-convert" in argv[0] would not be found);
    # call the resolved path so subprocess works cross-platform without shell=.
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        subprocess.run(
            [rsvg, "-w", str(VIEW_W), "-h", str(VIEW_H),
             str(svg_path), "-o", str(png_path)],
            check=True,
        )
        return "rsvg-convert"
    npx = shutil.which("npx")
    if npx:
        subprocess.run(
            [npx, "playwright", "screenshot",
             f"--viewport-size={VIEW_W},{VIEW_H}",
             str(svg_path), str(png_path)],
            check=True,
        )
        return "playwright"
    return None


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed SVG matches a fresh re-render (CI gate); "
        "writes nothing",
    )
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="write the SVG only; skip rasterization",
    )
    args = parser.parse_args(argv)

    svg = render(record())

    if args.check:
        if not SVG_OUT.exists():
            print(f"MISSING: {SVG_OUT} does not exist — run without --check")
            return 1
        committed = SVG_OUT.read_text(encoding="utf-8")
        if committed != svg:
            print(
                f"STALE: {SVG_OUT} differs from a fresh re-render — "
                "re-run `python scripts/build_social_card.py`"
            )
            return 1
        print(f"OK: {SVG_OUT} matches a fresh re-render")
        return 0

    SVG_OUT.write_text(svg, encoding="utf-8")
    print(f"wrote {SVG_OUT}")

    if not args.no_png:
        try:
            tool = rasterize(SVG_OUT, PNG_OUT)
        except (subprocess.CalledProcessError, OSError) as exc:
            # The SVG is the source of truth; a missing/broken rasterizer is a
            # convenience gap, not a build failure. Document the manual recipe.
            tool = None
            print(f"rasterizer failed ({exc}) — SVG written; export the PNG manually")
        if tool:
            size = PNG_OUT.stat().st_size
            print(f"wrote {PNG_OUT} via {tool} ({size:,} bytes)")
            if size > 1_000_000:
                print(
                    f"WARNING: {PNG_OUT} is {size:,} bytes — over GitHub's "
                    "1 MB social-preview cap; re-export at lower DPR"
                )
        else:
            print(
                "no rasterizer found (rsvg-convert / npx playwright) — "
                f"SVG written; export {PNG_OUT} manually (see "
                "docs/assets/README.md 'Want an actual .png?')"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
