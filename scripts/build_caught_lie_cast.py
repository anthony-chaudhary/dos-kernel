#!/usr/bin/env python
"""build_caught_lie_cast.py — record the README's first-screen caught-lie cast.

> **Tooling that operates ON the package, never inside it** (CLAUDE.md "Four
> things live OUTSIDE the four layers"). Like `build_readme.py`, this consumes
> the repo and the kernel is unaware it exists. It keeps ONE fact true: that
> the animated terminal cast the README opens with
> (`docs/assets/caught-lie-cast.svg`) is a RECORDING of the real CLI — every
> verdict line in it is verbatim `dos verify` output from a session this
> script drives — never a hand-typed dramatization.

Why a script at all
===================

The first screen of the README must SHOW the kernel catching a lie before the
reader scrolls (issue #64): the agent claims two features shipped, git backs
one, `dos verify` answers SHIPPED / NOT_SHIPPED, and the false "done" is
caught. A hand-drawn figure would rot the moment the CLI's output changed —
so the cast is *recorded*, not drawn:

  1. build a throwaway git repo telling the canonical caught-lie story
     (`dos._demo_story` — the same single source `dos quickstart` renders);
  2. drive the REAL CLI against it (`dos verify`, twice) and capture the
     verdict lines + exit codes byte-for-byte;
  3. render the transcript as a self-contained animated SVG (the repo's
     signature identity: near-black canvas, acid-green SHIPPED, hot-red
     NOT_SHIPPED, typed commands, a freeze-safe settle-and-hold ending with
     a `prefers-reduced-motion` fallback).

The recording is DETERMINISTIC: the commit's author/committer identity and
dates are pinned, so the demo repo's SHA — and therefore every byte of the
SVG — is the same on every machine. That is what lets
`tests/test_caught_lie_cast.py` run `--check` and pin the committed asset to
a fresh re-recording: if the CLI's output ever changes shape, the test goes
red and the fix is to re-run this script, never to hand-edit the SVG.

Robustness notes (the same concerns as `loop-hero.svg`):

  * a Markdown surface that strips `<style>` shows the FINAL frame (every
    element's natural state is fully visible; the intro hides lines via
    `animation-fill-mode: backwards`, which dies with the stylesheet);
  * `prefers-reduced-motion: reduce` disables the intro entirely;
  * nothing loops back to a blank state — the cast settles and holds, with
    only the prompt cursor blinking.

Usage
=====

    python scripts/build_caught_lie_cast.py            # re-record + write the SVG
    python scripts/build_caught_lie_cast.py --check    # verify committed == re-recorded
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from dos import _demo_story as story

OUT = Path("docs") / "assets" / "caught-lie-cast.svg"

# Pinned commit identity + dates — what makes the demo repo's SHA (and so the
# whole recording) byte-deterministic across machines and re-runs. The identity
# matches the one `dos quickstart` configures for its own throwaway repo.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "DOS Quickstart",
    "GIT_AUTHOR_EMAIL": "quickstart@example.com",
    "GIT_COMMITTER_NAME": "DOS Quickstart",
    "GIT_COMMITTER_EMAIL": "quickstart@example.com",
    "GIT_AUTHOR_DATE": "2026-01-02T03:04:05Z",
    "GIT_COMMITTER_DATE": "2026-01-02T03:04:05Z",
}

# ---------------------------------------------------------------------------
# recording — drive the real CLI, capture verbatim bytes
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """The repo top-level — git's answer, NOT __file__ relative math."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return Path(out.stdout.strip())


def _dos(work: Path, *args: str) -> tuple[str, int]:
    """Run the real `dos` CLI (the same `dos.cli:main` the console script is)
    in a subprocess, against the throwaway workspace; return (stdout, exit)."""
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


def record() -> dict[str, str]:
    """Build the canonical demo repo and capture the real transcript.

    Returns the verbatim strings the SVG embeds. Raises if the contrast the
    demo exists to show (SHIPPED / NOT_SHIPPED) did not come out — an
    environment fault to fail on, never to paper over.
    """
    import os
    import shutil

    if shutil.which("git") is None:
        raise RuntimeError("recording needs git on PATH")

    with tempfile.TemporaryDirectory(prefix="dos-cast-") as tmp:
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
                stdin=subprocess.DEVNULL,  # docs/295
            ).stdout.strip("\n")

        _git("init", "-q")
        _git("config", "commit.gpgsign", "false")
        _git("config", "core.autocrlf", "false")
        # The one real commit — the quickstart's shipped scene, byte for byte:
        # the work lands under src/, the subject is the canonical ship-stamp.
        (work / "src").mkdir()
        (work / "src" / story.WORK_FILE).write_bytes(
            story.WORK_CONTENT.encode("utf-8")
        )
        _git("add", "-A")
        _git("commit", "-q", "--no-verify", "-m", story.COMMIT_SUBJECT)
        log_line = _git("log", "--oneline")

        v1, c1 = _dos(work, "verify", story.PLAN, story.SHIPPED_PHASE)
        v2, c2 = _dos(work, "verify", story.PLAN, story.UNSHIPPED_PHASE)

    if not (
        c1 == 0
        and c2 == 1
        and v1.startswith("SHIPPED ")
        and v2.startswith("NOT_SHIPPED ")
        and "\n" not in v1
        and "\n" not in v2
        and "\n" not in log_line
    ):
        raise RuntimeError(
            "the recording did not produce the expected SHIPPED/NOT_SHIPPED "
            f"contrast: verify#1 -> {v1!r} (exit {c1}), "
            f"verify#2 -> {v2!r} (exit {c2})"
        )
    return {"log_line": log_line, "v1": v1, "v2": v2}


# ---------------------------------------------------------------------------
# rendering — the transcript as a self-contained animated SVG
# ---------------------------------------------------------------------------

# Layout constants. CHAR_W is a conservative monospace advance at FONT px —
# only used to budget line lengths against the canvas, never to place glyphs.
VIEW_W = 1040
MARGIN_X = 34
FONT = 16
ROW_H = 30
GAP = 14  # extra lead before each new beat of the story
TYPE_S = 0.045  # seconds per typed character
BAR_H = 44

_PALETTE = """
    .canvas  { fill: #0a0a0f; }
    .window  { fill: #10101a; stroke: #272736; }
    .bar     { fill: #15151f; }
    .ink     { fill: #eef1f6; }
    .dim     { fill: #8b93a7; }
    .faint   { fill: #5b6478; }
    .lie     { fill: #ff4d57; }                 /* hot-red — NOT_SHIPPED */
    .ship    { fill: #2bff88; }                 /* acid-green — SHIPPED */
    .accent  { fill: #36e0ff; }                 /* electric cyan — the prompt */
    .claim   { fill: #ffc24d; }                 /* amber — the forgeable narration */
"""

_ANIMATION = """
    /* Every element's NATURAL state is the final, fully-visible frame; the
       intro hides it via `backwards` fill only. A renderer that strips this
       stylesheet therefore shows the finished transcript, and
       reduced-motion gets it immediately. Nothing returns to blank. */
    .row { animation: rise 0.35s ease-out backwards; }
    @keyframes rise { from { opacity: 0; transform: translateY(5px); } }
    .tc { animation: pop 1ms steps(1, end) backwards; }
    @keyframes pop { from { opacity: 0; } }
    .thump {
      animation: thump 0.5s cubic-bezier(0.2, 1.6, 0.4, 1) backwards;
      transform-box: fill-box; transform-origin: center;
    }
    @keyframes thump { from { opacity: 0; transform: scale(1.7); } }
    .cursor { animation: blink 1.2s steps(1, end) infinite; }
    @keyframes blink { 50% { opacity: 0; } }
    @media (prefers-reduced-motion: reduce) {
      .row, .tc, .thump, .cursor { animation: none; }
    }
"""


def _typed(text: str, x: int, y: int, start: float) -> tuple[str, float]:
    """A `$ `-prompted command whose characters pop in on a typing clock.

    Returns (svg, finish_time). The prompt glyph is part of the row reveal;
    each command character carries its own absolute delay.
    """
    chars = []
    t = start
    for ch in text:
        chars.append(
            f'<tspan class="tc" style="animation-delay:{t:.2f}s">'
            f"{escape(ch)}</tspan>"
        )
        t += TYPE_S
    svg = (
        f'<text class="row ink" x="{x}" y="{y}" xml:space="preserve" '
        f'style="animation-delay:{start:.2f}s">'
        f'<tspan class="accent">$ </tspan>{"".join(chars)}</text>'
    )
    return svg, t


def _row(text: str, cls: str, x: int, y: int, at: float) -> str:
    return (
        f'<text class="row {cls}" x="{x}" y="{y}" xml:space="preserve" '
        f'style="animation-delay:{at:.2f}s">{escape(text)}</text>'
    )


def _verdict(
    verdict: str, note: str, cls: str, x: int, y: int, at: float
) -> str:
    return (
        f'<text class="row" x="{x}" y="{y}" xml:space="preserve" '
        f'style="animation-delay:{at:.2f}s">'
        f'<tspan class="{cls}">{escape(verdict)}</tspan>'
        f'<tspan class="faint">   # {escape(note)}</tspan></text>'
    )


def render(capture: dict[str, str]) -> str:
    """Compose the cast. Pure on its inputs — same capture, same bytes."""
    x = MARGIN_X
    body: list[str] = []
    t = 0.0  # the cast clock
    y = BAR_H + 52

    # The agent's cheerful over-claim — the line the story opens with.
    body.append(_row(f"agent> {story.AGENT_CLAIM}", "claim", x, y, t))

    y += ROW_H + GAP
    t += 1.2
    body.append(
        _row(
            "# One claim is true. One is a lie. Don't ask the agent — "
            "ask the repo:",
            "dim", x, y, t,
        )
    )

    y += ROW_H
    t += 0.9
    svg, t = _typed("git log --oneline", x, y, t)
    body.append(svg)

    y += ROW_H
    t += 0.45
    body.append(_row(capture["log_line"], "ink", x, y, t))

    y += ROW_H + GAP
    t += 1.1
    body.append(
        _row(
            f"# Claim 1 — did {story.SHIPPED_FEATURE} "
            f"({story.SHIPPED_PHASE}) really ship? Ask git, not the agent:",
            "dim", x, y, t,
        )
    )

    y += ROW_H
    t += 0.8
    svg, t = _typed(f"dos verify {story.PLAN} {story.SHIPPED_PHASE}", x, y, t)
    body.append(svg)

    y += ROW_H
    t += 0.45
    body.append(_verdict(capture["v1"], "exit 0", "ship", x, y, t))

    y += ROW_H + GAP
    t += 1.1
    body.append(
        _row(
            f'# Claim 2 — "{story.UNSHIPPED_FEATURE} '
            f'({story.UNSHIPPED_PHASE}) shipped too." Did it?',
            "dim", x, y, t,
        )
    )

    y += ROW_H
    t += 0.8
    svg, t = _typed(
        f"dos verify {story.PLAN} {story.UNSHIPPED_PHASE}", x, y, t
    )
    body.append(svg)

    y += ROW_H
    t += 0.45
    lie_y = y
    body.append(_verdict(capture["v2"], "exit 1", "lie", x, y, t))

    # The verdict stamp — the moment the cast exists for.
    t += 0.55
    body.append(
        f'<g transform="translate(640 {lie_y - 6}) rotate(-8)">'
        f'<g class="thump" style="animation-delay:{t:.2f}s">'
        f'<rect x="-86" y="-26" width="172" height="46" rx="7" fill="none" '
        f'stroke="#ff4d57" stroke-width="3.5"/>'
        f'<text class="lie" x="0" y="9" text-anchor="middle" '
        f'font-family="\'Arial Black\',\'Helvetica Neue\',Arial,sans-serif" '
        f'font-weight="900" font-size="27" letter-spacing="3">CAUGHT</text>'
        f"</g></g>"
    )

    y += ROW_H + GAP
    t += 0.95
    body.append(
        _row(
            '# The exit code IS the verdict: gate your agent\'s "done" on '
            "it, and a false claim cannot land.",
            "dim", x, y, t,
        )
    )

    y += ROW_H + GAP
    t += 0.7
    body.append(
        f'<g class="row" style="animation-delay:{t:.2f}s">'
        f'<text class="accent" x="{x}" y="{y}" xml:space="preserve">$ </text>'
        f'<rect class="cursor ink" x="{x + 20}" y="{y - 13}" width="9" '
        f'height="17"/></g>'
    )

    window_bottom = y + 26
    footer_y = window_bottom + 30
    view_h = footer_y + 18

    aria = (
        "A terminal recording of the caught lie. The agent reports: "
        f"{story.AGENT_CLAIM} git log shows one commit — "
        f"{capture['log_line']}. dos verify {story.PLAN} "
        f"{story.SHIPPED_PHASE} answers {capture['v1']}, exit 0. "
        f"dos verify {story.PLAN} {story.UNSHIPPED_PHASE} answers "
        f"{capture['v2']}, exit 1 — caught. The exit code is the verdict: "
        "gate the agent's done on it and a false claim cannot land."
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW_W} {view_h}" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace" font-size="{FONT}" role="img" aria-label={quoteattr(aria)}>
  <title>DOS — the caught lie, recorded from the real CLI</title>
  <desc>{escape(aria)}</desc>

  <!-- GENERATED FILE — do not edit by hand. Recorded by
       scripts/build_caught_lie_cast.py driving the real `dos` CLI;
       tests/test_caught_lie_cast.py pins these bytes to a fresh
       re-recording. To change the cast, change the script and re-run it. -->

  <style>
{_PALETTE}
{_ANIMATION}
  </style>

  <rect class="canvas" width="{VIEW_W}" height="{view_h}"/>
  <rect class="window" x="8" y="8" width="{VIEW_W - 16}" height="{window_bottom - 8}" rx="14" stroke-width="1.5"/>
  <path class="bar" d="M9.5 22 a12.5 12.5 0 0 1 12.5 -12.5 h{VIEW_W - 44} a12.5 12.5 0 0 1 12.5 12.5 v{BAR_H - 14} h-{VIEW_W - 19} z"/>
  <circle cx="34" cy="{8 + BAR_H // 2}" r="6.5" fill="#ff5f57"/>
  <circle cx="56" cy="{8 + BAR_H // 2}" r="6.5" fill="#febc2e"/>
  <circle cx="78" cy="{8 + BAR_H // 2}" r="6.5" fill="#28c840"/>
  <text class="dim" x="{VIEW_W // 2}" y="{8 + BAR_H // 2 + 5}" text-anchor="middle" font-size="13.5">the caught lie — dos verify, recorded from the real CLI</text>

  {chr(10).join("  " + line for line in body)}

  <text class="faint" x="{VIEW_W // 2}" y="{footer_y}" text-anchor="middle" font-size="12.5">every line above is the CLI's verbatim output — scripts/build_caught_lie_cast.py re-records this cast</text>
</svg>
"""


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed SVG matches a fresh re-recording "
        "(exit 1 if not); write nothing",
    )
    args = parser.parse_args(argv)

    expected = render(record())
    out = _repo_root() / OUT
    # Universal-newlines read so a CRLF-smudging checkout still compares equal
    # (the .gitattributes eol=lf pin keeps the committed bytes LF regardless).
    actual = out.read_text(encoding="utf-8") if out.exists() else None

    if args.check:
        if actual != expected:
            print(
                f"{OUT.as_posix()} is out of sync with a fresh recording of "
                "the real CLI — run: python scripts/build_caught_lie_cast.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUT.as_posix()} matches a fresh re-recording.")
        return 0

    if actual == expected:
        print(f"{OUT.as_posix()} already up to date.")
        return 0
    out.write_bytes(expected.encode("utf-8"))
    print(f"wrote {out} from a fresh recording.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
