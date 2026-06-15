"""The SCALED labeled citation corpus for the legalcite LIVE arm (docs/279 §2 live arm).

`dataset.py` carries the frozen n=18 seed — the documented *Mata v. Avianca*
fabrications, a handful of synth perturbations, and ten landmark real cites. That
sample proved the *mechanism* offline. This module grows the same labeled shape to
the **hundreds**, so a token-holding operator can run the shipped `resolve()` over
the **live** CourtListener API at a scale that yields a real-world DETECT recall +
FALSE-FIRE number — the docs/277 §6 #1 experiment, MEASURED-at-scale rather than
DESIGNED.

The honest discipline (docs/277 §7, docs/279) is carried into the corpus itself:

  * REAL cites are only ones whose existence is **publicly auditable** — landmark
    and well-known cases whose reporter citation any reader can confirm. We do NOT
    invent "real" cases to pad the false-fire denominator; an unverifiable "real"
    label would be the exact forgery the benchmark exists to refuse. To grow the
    REAL set FROM live ground truth, use the operator-run `--harvest` path
    (`live_corpus.py`), which reads real clusters via the token endpoint and writes
    a dated fossil — never a hand-asserted cite.

  * FABRICATED cites scale safely via a deterministic **perturbation generator**:
    nudge a real cite's volume/page/reporter to a plausible non-existent neighbour.
    Each is labeled `synth` with the base cite it perturbs, so its "does not exist
    as claimed" ground truth is auditable by inspection (it is a real case's number,
    moved). This is how the detect denominator scales without inventing un-auditable
    "real" cases — the same move `dataset.py` makes, generalized.

Shape: every entry is a `LabeledCite(cite, claimed_name, quote, label, note)` where
`label` is `"real"` or `"fabricated"`. `real_cites()` / `fabricated_cites()` return
the two lists; `full_corpus(n_synth=...)` returns the combined labeled set the live
runner iterates.

This is CONSUMER-side benchmark data (it imports `dataset.py`, never `src/dos`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The frozen seed — reuse its documented provenance, never duplicate it.
from dataset import FABRICATED_CITES as _SEED_FAB  # noqa: E402
from dataset import REAL_CITES as _SEED_REAL  # noqa: E402


@dataclass(frozen=True)
class LabeledCite:
    """One labeled citation the live runner scores. `label` is the ground truth."""

    cite: str
    claimed_name: str
    quote: str
    label: str  # "real" | "fabricated"
    note: str = ""


# ---------------------------------------------------------------------------
# REAL cites — publicly auditable existence, correct name. A sound witness must
# NOT flag these (the false-fire floor). Each is a landmark / well-known case
# whose reporter citation any reader can verify; this is the curated extension of
# the dataset.py seed. (Grow further from LIVE ground truth via `--harvest`.)
# ---------------------------------------------------------------------------

# (cite, claimed_name, quote) — quote "" where we check existence + name only.
_EXTRA_REAL: list[tuple[str, str, str]] = [
    # --- SCOTUS, widely cited, easy to confirm ---
    ("347 U.S. 483", "Brown v. Board of Education", ""),
    ("410 U.S. 113", "Roe v. Wade", ""),
    ("505 U.S. 833", "Planned Parenthood v. Casey", ""),
    ("319 U.S. 624", "West Virginia State Board of Education v. Barnette", ""),
    ("372 U.S. 335", "Gideon v. Wainwright", ""),
    ("369 U.S. 186", "Baker v. Carr", ""),
    ("376 U.S. 254", "New York Times Co. v. Sullivan", ""),
    ("403 U.S. 713", "New York Times Co. v. United States", ""),
    ("418 U.S. 683", "United States v. Nixon", ""),
    ("304 U.S. 144", "United States v. Carolene Products Co.", ""),
    ("300 U.S. 379", "West Coast Hotel Co. v. Parrish", ""),
    ("198 U.S. 45", "Lochner v. New York", ""),
    ("323 U.S. 214", "Korematsu v. United States", ""),
    ("388 U.S. 1", "Loving v. Virginia", ""),
    ("478 U.S. 186", "Bowers v. Hardwick", ""),
    ("431 U.S. 494", "Moore v. City of East Cleveland", ""),
    ("381 U.S. 479", "Griswold v. Connecticut", ""),
    ("405 U.S. 438", "Eisenstadt v. Baird", ""),
    ("491 U.S. 397", "Texas v. Johnson", ""),
    ("521 U.S. 844", "Reno v. ACLU", ""),
    ("530 U.S. 290", "Santa Fe Independent School District v. Doe", ""),
    ("462 U.S. 919", "INS v. Chadha", ""),
    ("343 U.S. 579", "Youngstown Sheet & Tube Co. v. Sawyer", ""),
    ("488 U.S. 469", "City of Richmond v. J.A. Croson Co.", ""),
    ("539 U.S. 306", "Grutter v. Bollinger", ""),
    ("554 U.S. 570", "District of Columbia v. Heller", ""),
    ("567 U.S. 519", "NFIB v. Sebelius", ""),
    ("576 U.S. 644", "Obergefell v. Hodges", ""),
    ("585 U.S. 162", "Carpenter v. United States", ""),
    ("597 U.S. 215", "New York State Rifle & Pistol Association v. Bruen", ""),
    # --- Foundational doctrine cases ---
    ("304 U.S. 64", "Erie Railroad Co. v. Tompkins", ""),
    ("339 U.S. 33", "United States v. Rabinowitz", ""),
    ("367 U.S. 643", "Mapp v. Ohio", ""),
    ("392 U.S. 1", "Terry v. Ohio", ""),
    ("384 U.S. 436", "Miranda v. Arizona", ""),
    ("389 U.S. 347", "Katz v. United States", ""),
    ("332 U.S. 46", "Adamson v. California", ""),
    ("297 U.S. 1", "United States v. Butler", ""),
    ("301 U.S. 1", "NLRB v. Jones & Laughlin Steel Corp.", ""),
    ("312 U.S. 100", "United States v. Darby", ""),
    ("317 U.S. 111", "Wickard v. Filburn", ""),
    ("514 U.S. 549", "United States v. Lopez", ""),
    ("529 U.S. 598", "United States v. Morrison", ""),
    ("545 U.S. 1", "Gonzales v. Raich", ""),
    ("491 U.S. 110", "Michael H. v. Gerald D.", ""),
    ("521 U.S. 702", "Washington v. Glucksberg", ""),
    ("497 U.S. 261", "Cruzan v. Director, Missouri Department of Health", ""),
]


# ---------------------------------------------------------------------------
# The synth perturbation generator — scales the FABRICATED denominator from a
# REAL cite by moving its number to a plausible non-existent neighbour.
# Deterministic (seedless): same real cite → same perturbations, so the corpus is
# replayable and the labels are auditable by inspection.
# ---------------------------------------------------------------------------

_CITE_RE = re.compile(r"^(?P<vol>\d+)\s+(?P<rep>[A-Za-z.0-9 ]+?)\s+(?P<page>\d+)$")


def _perturb(cite: str, claimed_name: str) -> list[LabeledCite]:
    """Return up to two plausible non-existent neighbours of a real cite.

    A real `<vol> <reporter> <page>` becomes a fabrication two safe ways:
      1. page nudged far (×10 + 7) — almost certainly off the end of the volume;
      2. volume nudged far (+500) — almost certainly an unprinted volume.
    Both keep the claimed name, so the verdict is "this case does not appear AT the
    claimed cite" — a synth fabrication whose ground truth is auditable (a real
    case's number, moved). We never perturb the reporter into a real parallel slot.
    """
    m = _CITE_RE.match(cite.strip())
    if not m:
        return []
    vol, rep, page = int(m["vol"]), m["rep"].strip(), int(m["page"])
    out: list[LabeledCite] = []
    far_page = page * 10 + 7
    out.append(LabeledCite(
        f"{vol} {rep} {far_page}", claimed_name, "", "fabricated",
        f"synth: {claimed_name} page {page}->{far_page} (off the end of the volume)"))
    out.append(LabeledCite(
        f"{vol + 500} {rep} {page}", claimed_name, "", "fabricated",
        f"synth: {claimed_name} volume {vol}->{vol + 500} (unprinted volume)"))
    return out


# ---------------------------------------------------------------------------
# The public faces.
# ---------------------------------------------------------------------------

def real_cites() -> list[LabeledCite]:
    """All REAL labeled cites: the dataset.py seed + the curated extension, deduped
    by cite string (seed wins on a clash so its quote/provenance is preserved)."""
    seen: set[str] = set()
    out: list[LabeledCite] = []
    for cite, name, quote in _SEED_REAL:
        if cite in seen:
            continue
        seen.add(cite)
        out.append(LabeledCite(cite, name, quote, "real", "dataset seed"))
    for cite, name, quote in _EXTRA_REAL:
        if cite in seen:
            continue
        seen.add(cite)
        out.append(LabeledCite(cite, name, quote, "real", "curated landmark"))
    return out


def fabricated_cites(n_synth: int | None = None) -> list[LabeledCite]:
    """All FABRICATED labeled cites: the documented Mata + dataset synth seed, then
    generated perturbations of the REAL set until `n_synth` synth cites exist
    (None = perturb every real cite, the full generated set). Deduped by cite."""
    seen: set[str] = set()
    out: list[LabeledCite] = []
    # 1. The documented Mata fabrications + dataset synth seed (their provenance is
    #    the load-bearing, un-forgeable part — keep verbatim).
    for cite, name, quote, note in _SEED_FAB:
        if cite in seen:
            continue
        seen.add(cite)
        out.append(LabeledCite(cite, name, quote, "fabricated", note))
    # 2. Generated perturbations of the real set, in a stable order, until the synth
    #    budget is hit. (A perturbation that happens to collide with a seed cite or a
    #    real cite is dropped — we never emit a "fabricated" label on a known-real
    #    cite.)
    real_strings = {lc.cite for lc in real_cites()}
    synth_added = 0
    for lc in real_cites():
        if n_synth is not None and synth_added >= n_synth:
            break
        for p in _perturb(lc.cite, lc.claimed_name):
            if n_synth is not None and synth_added >= n_synth:
                break
            if p.cite in seen or p.cite in real_strings:
                continue
            seen.add(p.cite)
            out.append(p)
            synth_added += 1
    return out


def full_corpus(n_synth: int | None = None) -> list[LabeledCite]:
    """The combined labeled set the live runner iterates: every real cite + every
    fabricated cite (documented + up to `n_synth` generated). Real first, then
    fabricated, each in a stable order so a checkpoint resume is deterministic."""
    return real_cites() + fabricated_cites(n_synth=n_synth)


def summary() -> dict:
    """Counts for the runbook / preflight (no network)."""
    r = real_cites()
    f = fabricated_cites()
    return {
        "n_real": len(r),
        "n_fabricated_total": len(f),
        "n_documented": sum(1 for lc in f if not lc.note.startswith("synth")),
        "n_synth": sum(1 for lc in f if lc.note.startswith("synth")),
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import json

    print(json.dumps(summary(), indent=2))
