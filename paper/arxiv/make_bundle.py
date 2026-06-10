#!/usr/bin/env python3
"""Build a ready-to-upload arXiv source tarball from the paper sources.

arXiv submission itself is NOT automatable (no public submission API; a
first-time cs.* author needs an endorsement; every upload is human-moderated).
What IS automatable is everything UP TO the upload: regenerate the LaTeX,
collect exactly the files arXiv needs, strip the cruft it rejects, and produce
one `.tar.gz` you drag into the web uploader. That is what this script does.

The pipeline (each step explained inline):

    1. regenerate  arxiv/sections/*.tex   from the single source of truth
                   (sections/*.html + meta.py) via assemble_arxiv.py — so the
                   bundle can never ship a stale .tex (the repo's anti-drift rule).
    2. stage       main.tex + sections/ + refs.bib + ONLY the referenced figs/
                   into a clean build dir (arxiv references ../figs/, which a flat
                   upload can't keep -- so figures are copied in next to main.tex and
                   the staged main.tex's graphicspath already lists {figs/}).
    3. bibliography ensure a compiled main.bbl rides along if present. arXiv now
                   runs BibTeX/Biber itself ("including .bbl is optional but
                   recommended"), but shipping the .bbl removes all ambiguity — if
                   it's there, arXiv uses it verbatim. We DON'T fabricate one (no
                   local TeX here); we copy it if you've compiled once (Overleaf
                   "Logs and output files" → main.bbl, or a local pdflatex+bibtex).
    4. clean       run arxiv-latex-cleaner if installed: strips comments + aux
                   files, drops unused images, can compress figures toward the
                   50 MB limit. If it's NOT installed we still produce a correct
                   bundle (the staging already excludes aux/unused files) and say so.
    5. tar         gzip the staged dir into releases/arxiv-<date>.tar.gz.

Usage:
    python paper/arxiv/make_bundle.py
    python paper/arxiv/make_bundle.py --no-regen     # trust the existing .tex
    python paper/arxiv/make_bundle.py --no-clean     # skip arxiv-latex-cleaner
    python paper/arxiv/make_bundle.py --out NAME.tar.gz
    python paper/arxiv/make_bundle.py --json

It is dev tooling that operates ON the paper, not part of the kernel. Install the
cleaner with the project's `[paper]` extra:  pip install -e ".[paper]"
(or `pip install arxiv-latex-cleaner`). The tar still builds without it.

The printed checklist is the manual-only residue (endorsement, license,
moderation) — the part no script can take for you.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):  # cp1252 Windows console safety (see build_dist.py)
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

ARXIV_DIR = Path(__file__).resolve().parent       # paper/arxiv/
PAPER_DIR = ARXIV_DIR.parent                        # paper/
FIGS_DIR = PAPER_DIR / "figs"                        # paper/figs/
SECTIONS_DIR = ARXIV_DIR / "sections"               # paper/arxiv/sections/
RELEASES_DIR = ARXIV_DIR / "releases"               # where the tarball lands


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=True
    )


def _referenced_figures() -> set[str]:
    r"""Every figure named by an \includegraphics in the generated sections.

    arXiv wants exactly the figures the paper uses — no more (unused images bloat
    the tarball; the cleaner would drop them anyway). We scan the .tex for
    \includegraphics{...} / [..]{...} and resolve the basename against figs/.
    """
    names: set[str] = set()
    pat = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
    for tex in SECTIONS_DIR.glob("*.tex"):
        for m in pat.finditer(tex.read_text(encoding="utf-8")):
            target = m.group(1).strip()
            # \graphicspath makes the reference a bare stem or name; normalise to a
            # filename and let the figs/ extension-resolve below find the real file.
            names.add(Path(target).name)
    return names


def _resolve_fig(name: str) -> Path | None:
    r"""Map a referenced figure name (possibly extensionless) to a real file in
    figs/. LaTeX lets you write \includegraphics{fig1} and resolves the extension;
    mirror that so we copy the actual PNG/PDF/JPG."""
    cand = FIGS_DIR / name
    if cand.exists():
        return cand
    if cand.suffix == "":  # extensionless reference — try the graphics extensions
        for ext in (".pdf", ".png", ".jpg", ".jpeg"):
            alt = FIGS_DIR / (name + ext)
            if alt.exists():
                return alt
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-regen", action="store_true",
                    help="do not regenerate the .tex (use existing arxiv/sections/*.tex)")
    ap.add_argument("--no-clean", action="store_true",
                    help="skip arxiv-latex-cleaner even if installed")
    ap.add_argument("--out", default=None,
                    help="output tarball name (default: releases/arxiv-<today>.tar.gz)")
    ap.add_argument("--date", default=None,
                    help="date stamp for the default tarball name (YYYY-MM-DD); "
                         "avoids a nondeterministic clock in the filename")
    ap.add_argument("--json", action="store_true", help="emit a JSON report")
    args = ap.parse_args(argv)

    report: dict[str, object] = {"steps": []}

    def note(step: str, **extra: object) -> None:
        report["steps"].append({"step": step, **extra})  # type: ignore[attr-defined]
        if not args.json:
            detail = "  " + "  ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
            print(f"[{step}]{detail}")

    # 1. regenerate the .tex from the single source of truth ----------------------
    if not args.no_regen:
        try:
            # assemble_arxiv.py does `import meta`, so it must run with cwd=paper/.
            _run([sys.executable, "assemble_arxiv.py"], cwd=PAPER_DIR)
            note("regen", tex=len(list(SECTIONS_DIR.glob("*.tex"))))
        except subprocess.CalledProcessError as e:
            note("regen", error=(e.stderr or e.stdout)[-1500:])
            return _finish(report, args, 1)
    else:
        note("regen", skipped=True)

    if not (ARXIV_DIR / "main.tex").exists():
        note("error", message="arxiv/main.tex not found")
        return _finish(report, args, 1)
    if not list(SECTIONS_DIR.glob("*.tex")):
        note("error", message="no generated sections/*.tex (run without --no-regen)")
        return _finish(report, args, 1)

    # 2. stage into a clean build dir ---------------------------------------------
    build = ARXIV_DIR / "_bundle"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir()
    (build / "sections").mkdir()
    (build / "figs").mkdir()

    # main.tex: copy verbatim. Its \graphicspath already lists {figs/} (the flat
    # layout) ahead-of/alongside {../figs/}, so a figs/ subdir next to main.tex
    # resolves on arXiv without editing the file.
    shutil.copy2(ARXIV_DIR / "main.tex", build / "main.tex")
    for tex in SECTIONS_DIR.glob("*.tex"):
        shutil.copy2(tex, build / "sections" / tex.name)
    if (ARXIV_DIR / "refs.bib").exists():
        shutil.copy2(ARXIV_DIR / "refs.bib", build / "refs.bib")
        note("stage_bib", refs_bib=True)
    else:
        note("stage_bib", refs_bib=False, warning="no refs.bib — \\cite keys will be undefined")

    referenced = _referenced_figures()
    copied, missing = [], []
    for name in sorted(referenced):
        src = _resolve_fig(name)
        if src is None:
            missing.append(name)
            continue
        shutil.copy2(src, build / "figs" / src.name)
        copied.append(src.name)
    note("stage_figs", referenced=len(referenced), copied=len(copied),
         missing=missing or None)

    # 3. bibliography: ride the compiled .bbl along if it exists ------------------
    # arXiv runs BibTeX/Biber, but a shipped .bbl is used verbatim and removes any
    # version-skew risk. We never fabricate it (no local TeX); copy if present.
    bbl = ARXIV_DIR / "main.bbl"
    if bbl.exists():
        shutil.copy2(bbl, build / "main.bbl")
        note("stage_bbl", main_bbl=True)
    else:
        note("stage_bbl", main_bbl=False,
             hint="compile once (Overleaf or `pdflatex main && bibtex main`) and "
                  "drop main.bbl next to main.tex, then re-run, to ship the .bbl")

    # 4. clean with arxiv-latex-cleaner if available ------------------------------
    cleaned_dir = build
    if not args.no_clean:
        cleaner = _have_cleaner()
        if cleaner:
            try:
                # The cleaner writes <dir>_arXiv next to its input. Point it at the
                # staged tree; use its own defaults (strip comments, drop aux/unused).
                _run(cleaner + [str(build)], cwd=ARXIV_DIR)
                produced = build.parent / (build.name + "_arXiv")
                if produced.exists():
                    cleaned_dir = produced
                    # arxiv-latex-cleaner PRUNES files it deems unused — and it drops
                    # refs.bib (it keeps a compiled .bbl but not the .bib source). But
                    # main.tex does \bibliography{refs}, so without refs.bib AND
                    # without a .bbl, arXiv's BibTeX run fails and every \cite renders
                    # [?]. Restore the bibliography source/compiled files into the
                    # cleaned tree so the citations survive the clean. (Silent loss
                    # here would read as "bundle ready" while shipping broken refs.)
                    restored = []
                    for bibfile in ("refs.bib", "main.bbl"):
                        staged = build / bibfile
                        if staged.exists() and not (produced / bibfile).exists():
                            shutil.copy2(staged, produced / bibfile)
                            restored.append(bibfile)
                    note("clean", tool="arxiv-latex-cleaner", output=produced.name,
                         restored_bib=restored or None)
                else:
                    note("clean", tool="arxiv-latex-cleaner",
                         warning="cleaner ran but no _arXiv dir found; using raw staging")
            except subprocess.CalledProcessError as e:
                note("clean", tool="arxiv-latex-cleaner", failed=True,
                     stderr=(e.stderr or e.stdout)[-1200:],
                     note="bundling the un-cleaned staging instead (still valid)")
        else:
            note("clean", skipped="arxiv-latex-cleaner not installed",
                 hint='pip install -e ".[paper]"  (or: pip install arxiv-latex-cleaner)')
    else:
        note("clean", skipped="--no-clean")

    # 5. tar it up ----------------------------------------------------------------
    RELEASES_DIR.mkdir(exist_ok=True)
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = RELEASES_DIR / out
    else:
        stamp = args.date or "undated"
        out = RELEASES_DIR / f"arxiv-{stamp}.tar.gz"
        if not args.date and not args.json:
            print("  (no --date given; tarball named 'arxiv-undated.tar.gz' — pass "
                  "--date YYYY-MM-DD for a dated name)")

    with tarfile.open(out, "w:gz") as tar:
        # arcname="" flattens the staged dir to the tar root, which is what arXiv
        # expects (main.tex at the top level, sections/ and figs/ beneath it).
        for item in sorted(cleaned_dir.iterdir()):
            tar.add(item, arcname=item.name)

    size_kb = out.stat().st_size // 1024
    note("tar", path=str(out), size_kb=size_kb)

    report["ok"] = True
    report["tarball"] = str(out)
    report["missing_figures"] = missing
    if not args.json:
        _print_checklist(out, missing, bbl.exists(), (ARXIV_DIR / "refs.bib").exists())
    return _finish(report, args, 0)


def _have_cleaner() -> list[str] | None:
    """Return the command to invoke arxiv-latex-cleaner, or None if absent.

    It exposes both a console script (`arxiv_latex_cleaner`) and a module
    (`python -m arxiv_latex_cleaner`); prefer the module form so it runs under the
    SAME interpreter that has it installed (the [paper] extra)."""
    probe = subprocess.run(
        [sys.executable, "-c", "import arxiv_latex_cleaner"],
        capture_output=True,
    )
    if probe.returncode == 0:
        return [sys.executable, "-m", "arxiv_latex_cleaner"]
    if shutil.which("arxiv_latex_cleaner"):
        return ["arxiv_latex_cleaner"]
    return None


def _print_checklist(out: Path, missing: list[str], have_bbl: bool, have_refs: bool) -> None:
    print(f"\n  Bundle ready: {out}")
    print("  ----------------------------------------------------------------------")
    print("  This is the AUTOMATABLE half. The upload itself is manual — arXiv has")
    print("  no submission API. Before you submit:")
    print()
    print("   1. Compile once (Overleaf: upload main.tex + sections/ + figs/, or")
    print("      `pdflatex main && bibtex main && pdflatex main && pdflatex main`).")
    print("      Fix any straggler a real TeX engine flags — edit the .html source")
    print("      or assemble_arxiv.py, NOT the generated .tex (it's overwritten).")
    if not have_bbl:
        print("   2. Grab the resulting main.bbl, drop it next to main.tex, re-run")
        print("      this script so the bundle ships the .bbl (removes BibTeX skew).")
    else:
        print("   2. main.bbl is bundled. (Re-compile + re-bundle if refs.bib changed.)")
    if not have_refs:
        print("   !  No refs.bib was staged — \\cite keys will render as [?]. Fix first.")
    if missing:
        print(f"   !  Missing figures (referenced but not in figs/): {', '.join(missing)}")
        print("      Run `python paper/build.py --no-pdf` to refresh figs/, then re-bundle.")
    print("   3. Upload the tarball at https://arxiv.org/submit (manual web step).")
    print("      First-time cs.* author → you need an ENDORSEMENT first")
    print("      (https://info.arxiv.org/help/endorsement.html). A co-author with")
    print("      cs.* posting history removes the gate.")
    print("   4. Pick categories (cs.SE primary, cs.AI; optionally cs.DC) + a license,")
    print("      confirm the title/author metadata, submit. Expect human moderation.")
    print("  ----------------------------------------------------------------------")


def _finish(report: dict, args: argparse.Namespace, code: int) -> int:
    report.setdefault("ok", code == 0)
    if args.json:
        print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
