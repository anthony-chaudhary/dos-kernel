#!/usr/bin/env python3
"""kernel_charts_build — render the kernel-data charts to committed assets.

Folds this repo's own `.dos/` journals (`kernel_metrics.py`), draws the three
kernel-behaviour charts (`kernel_charts.py`), writes them as self-contained
SVGs under `docs/assets/kernel/`, and rasterizes each to a PNG for the
image-only surfaces Reddit/social use (a headless-browser screenshot, the same
path `build_social_card.py` uses).

Unlike the scoreboard generator, the input here is a LIVE local journal that
grows every time the kernel runs, so there is no stable `--check` byte-equality
gate — re-running simply refreshes the assets to the newer, larger truth. What
IS guaranteed is that no number is authored in the chart code: the SVG is a pure
function of the fold, so the committed asset always matches the data that was
present when it was generated.

Usage:
    python scripts/kernel_charts_build.py            # SVGs + PNGs
    python scripts/kernel_charts_build.py --no-png    # SVGs only
    python scripts/kernel_charts_build.py --print     # fold summary, write nothing
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "docs" / "assets" / "kernel"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


metrics = _load("kernel_metrics")
charts = _load("kernel_charts")


def build_svgs() -> dict[str, str]:
    """Fold the live journals and render every kernel chart to an SVG string."""
    data = metrics.fold_all()
    obs = data["observations"]
    pretool_p50 = (obs.get("latency", {}).get("pretool", {}) or {}).get("p50", 0.0)
    return {
        "disbelief-funnel.svg": charts.disbelief_funnel_chart(
            obs["outcomes"], pretool_p50_ms=pretool_p50),
        "self-modify-wall.svg": charts.self_modify_wall_chart(
            data["lane"]["self_modify"],
            enforce_block=data["lane"]["enforce_block"],
            enforce_warn=data["lane"]["enforce_warn"],
            n_entries=data["lane"]["n_entries"]),
        "memory-recall.svg": charts.memory_recall_chart(
            data["verdict"]["recall"]),
    }


# Approximate device-independent viewport per chart (matches the SVG's own
# width/height). The rasterizer doubles these for a crisp 2x PNG.
_VIEWPORT = {
    "disbelief-funnel.svg": (720, 352),
    "self-modify-wall.svg": (720, 430),
    "memory-recall.svg": (720, 316),
}


def _rasterize(svg_path: Path, png_path: Path, w: int, h: int) -> str | None:
    """SVG -> PNG via a headless-browser screenshot at 2x for crispness.

    Renders a temporary 2x-dimensioned copy of the SVG (it carries a viewBox, so
    scaling the root width/height is lossless) and screenshots that. Returns the
    tool name, or None if no rasterizer is on PATH (the SVG is the source of
    truth either way).
    """
    npx = shutil.which("npx")
    if not npx:
        return None
    svg = svg_path.read_text(encoding="utf-8")
    # bump the first width/height on the root <svg> to 2x; the viewBox keeps the
    # coordinate system, so the bitmap is sharp at double resolution.
    big = svg.replace(f'width="{w}" height="{h}"',
                      f'width="{w * 2}" height="{h * 2}"', 1)
    tmp = svg_path.with_suffix(".2x.svg")
    tmp.write_text(big, encoding="utf-8")
    try:
        subprocess.run(
            [npx, "playwright", "screenshot",
             f"--viewport-size={w * 2},{h * 2}",
             str(tmp), str(png_path)],
            check=True,
        )
    finally:
        tmp.unlink(missing_ok=True)
    return "playwright"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-png", action="store_true",
                    help="write the SVGs only; skip rasterization")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="print the fold summary and write nothing")
    args = ap.parse_args(argv)

    if args.print_only:
        import pprint
        pprint.pp(metrics.fold_all())
        return 0

    svgs = build_svgs()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, svg in svgs.items():
        if not svg:
            print(f"skip {name}: empty fold (no data yet)")
            continue
        path = OUT_DIR / name
        path.write_text(svg, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO)} ({len(svg):,} bytes)")
        written += 1
        if not args.no_png:
            w, h = _VIEWPORT[name]
            png = path.with_suffix(".png")
            try:
                tool = _rasterize(path, png, w, h)
            except (subprocess.CalledProcessError, OSError) as exc:
                tool = None
                print(f"  rasterize failed ({exc}) — SVG written, export PNG "
                      "manually")
            if tool:
                size = png.stat().st_size
                print(f"  wrote {png.relative_to(REPO)} via {tool} "
                      f"({size:,} bytes)")
            else:
                print("  no rasterizer (npx playwright) — SVG only")

    print(f"\n{written} kernel chart(s) written to "
          f"{OUT_DIR.relative_to(REPO)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
