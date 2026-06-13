"""docs/321 P2 — pin the forge-challenge page renderer (`scripts/forge_page.py`).

The structural rule: the headline is DERIVED from the floor's own numbers, never
asserted. A FLOOR HELD headline requires zero floor forgeries AND the soundness
check; a single floor forgery forces FORGERY LANDED. The renderer is loaded by
file path (it lives under scripts/, not on the suite's import path).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_PAGE_PY = Path(__file__).resolve().parents[1] / "scripts" / "forge_page.py"
_BENCH = str(Path(__file__).resolve().parents[1] / "benchmark")
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)


def _load_renderer():
    spec = importlib.util.spec_from_file_location("forge_page", _PAGE_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["forge_page"] = mod
    spec.loader.exec_module(mod)
    return mod


def _live_run() -> dict:
    from forge_arena.harness import run_ladder
    return run_ladder().to_dict()


def test_floor_held_headline_on_a_clean_run():
    fp = _load_renderer()
    page = fp.render(_live_run())
    assert "## Verdict: **FLOOR HELD**" in page
    assert "text alone never flipped the admit bit" in page
    # the two-gate table is present with the floor's zero.
    assert "**0 / 18**" in page or "**0 /" in page


def test_forgery_landed_headline_is_derived_not_asserted():
    """A run whose floor admitted a forgery MUST render FORGERY LANDED, even if a
    (hostile) checks block claims floor_no_forgery=True. The headline reads the
    floor's own forge_admitted count, not the self-reported check alone."""
    fp = _load_renderer()
    bad = _live_run()
    bad["gates"]["floor"]["forge_admitted"] = 1   # a forgery landed
    # even if the checks block lies, the headline derives from the count:
    bad["checks"]["floor_no_forgery"] = True
    page = fp.render(bad)
    assert "## Verdict: **FORGERY LANDED**" in page
    assert "witness-tamper hole" in page


def test_honest_denominator_caption_present():
    fp = _load_renderer()
    page = fp.render(_live_run())
    assert "honest denominator" in page.lower()
    assert "no-witness" in page
    assert "docs/234" in page


def test_bad_input_is_refused_not_rendered():
    fp = _load_renderer()
    for bad in ({}, {"benchmark": "something_else"}, {"benchmark": "forge_arena"}):
        try:
            fp.render(bad)
        except fp.BadInput:
            continue
        raise AssertionError(f"expected BadInput for {bad!r}")


def test_check_mode_is_byte_reproducible(tmp_path: Path):
    """--out then --check against the same run matches byte-for-byte."""
    fp = _load_renderer()
    run = _live_run()
    page = fp.render(run)
    out = tmp_path / "forge-challenge.md"
    out.write_text(page, encoding="utf-8")
    run_json = tmp_path / "run.json"
    run_json.write_text(json.dumps(run), encoding="utf-8")
    rc = fp.main(["--run", str(run_json), "--out", str(out), "--check"])
    assert rc == 0
