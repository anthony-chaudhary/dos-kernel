#!/usr/bin/env python3
"""Render a per-repo drift-scoreboard page from a pinned sweep + adjudications.

docs/311 P2 — the renderer behind the standing per-repo scoreboard index
(issue #84). It consumes two JSON files and writes one markdown page:

  * the **sweep** — `dos commit-audit --sweep --json` output (the self page),
    or the docs/307 tool's per-repo artifact (`scripts/drift_scoreboard.py`),
    which nests the same summary under a ``"summary"`` key;
  * the **adjudications** — the page meta (repo, tier, the as-of fields) plus
    one typed record per raw flag: ``{sha, subject, ruling, rung, rationale}``
    with the closed ruling set ``AUDITOR_ARTIFACT`` (names the narrowing
    issue) / ``CONFIRMED`` (class ``convention`` or ``unexplained``) /
    ``UNADJUDICATED`` — the docs/311 §2 record, as data.

The structural rule this file exists to enforce (docs/311 §2, pinned by
``tests/test_scoreboard_page.py``): **the headline is DERIVED, never data.**
A ``DRIFT REPORTED`` headline can only arise from flags every one of which
carries a human-rung ``CONFIRMED`` record. A flag with no record (or an
explicit ``UNADJUDICATED``) forces ``RAW-ONLY — NO GRADE``, which a self or
opt-in page renders honestly and a curated/seeded page REFUSES to render at
all (tier 2a publishes CLEAN verdicts only; tier 2 never publishes raw-only —
the repo is not named anywhere, docs/311 §1–2). A record naming a SHA the
sweep did not flag is refused as stale: the records file cannot pad, excuse,
or pre-adjudicate.

Dev tooling, not a kernel module — stdlib-only, pure on its inputs (two JSON
files in, one page out, no git or network I/O). Nothing under ``src/dos/``
knows it exists.

Usage:
    python scripts/scoreboard_page.py --sweep SWEEP.json \\
        --adjudications ADJ.json [--out PAGE.md] [--check]

Exit codes: 0 rendered (or --check matched), 1 --check mismatch, 2 a refusal
or bad input. ``--check`` writes nothing — it re-renders and compares bytes
against ``--out`` (the P3 freshness loop's verification mode).
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

# Canonical prose width. Every wrapped paragraph — body or blockquote — wraps
# its CONTENT at this width; the page is byte-reproducible because the wrap is
# the tool's, never a hand's.
WRAP = 76

TIERS = ("self", "opt-in", "curated", "seeded")
RULINGS = ("AUDITOR_ARTIFACT", "CONFIRMED", "UNADJUDICATED")
CONFIRMED_CLASSES = ("convention", "unexplained")
RUNGS = ("deterministic", "judge", "human")

# Tiers that may honestly render a RAW-ONLY page. A curated/seeded page with
# an unresolved flag does not publish AT ALL (docs/311 §2 — not even
# "pending": that would read as a soft accusation).
RAW_ONLY_TIERS = ("self", "opt-in")

# The three §4 headline states.
CLEAN, DRIFT, RAW_ONLY = "CLEAN", "DRIFT", "RAW_ONLY"

_KIND_ORDER = ("code_effect", "test", "doc", "none")
_SCHEMA = "dos-scoreboard-page/v1"

# The auditor's own repo — only the narrowing-issue links in the receipts
# column point here; every commit link points at the AUDITED repo.
_AUDITOR_REPO = "anthony-chaudhary/dos-kernel"
_AUDITOR_URL = f"https://github.com/{_AUDITOR_REPO}"

# Standard prose every page carries (the §4 drift-is-not-deception line lives
# beside the number, not in a footnote; the relative links hold for every
# page at docs/scoreboard/<org>/<repo>.md depth).
_HEADLINE_TAIL = (
    "Schema and grade vocabulary: "
    "[docs/311](../../311_scoreboard-per-repo-index-plan.md). Drift is a "
    "claim-vs-diff mismatch — **never** a correctness, honesty, or intent "
    "grade."
)
_REPRODUCE_TAIL = (
    "A newer auditor over the same pinned range may count differently as "
    "fire-narrowing continues (each narrowing is a public issue, e.g. "
    "#79/#81); the as-of block above is what this page graded, with what."
)
_CORRECTIONS = (
    "A contested flag gets re-adjudicated and the page re-rendered (the "
    "[docs/311](../../311_scoreboard-per-repo-index-plan.md) §3 path). Until "
    "the `scoreboard-correction` template ships (docs/311 P4), open a plain "
    "issue naming this page and the SHA. Methodology — what the witness "
    "reads, what it abstains on, where it has been wrong: "
    "[docs/scoreboard/methodology.md](../methodology.md)."
)


class Refusal(Exception):
    """A structural rule refused the render (exit 2, no page written)."""


# ---------------------------------------------------------------------------
# Pure helpers — wrap, escape, load, validate, match, derive. No I/O.
# ---------------------------------------------------------------------------


def _fill(text: str) -> str:
    return textwrap.fill(text, width=WRAP,
                         break_long_words=False, break_on_hyphens=False)


def _fill_quote(text: str) -> str:
    return textwrap.fill(text, width=WRAP + 2,
                         initial_indent="> ", subsequent_indent="> ",
                         break_long_words=False, break_on_hyphens=False)


def _cell(text: str) -> str:
    """One markdown table cell: pipes escaped, newlines flattened."""
    return " ".join(str(text).split()).replace("|", "\\|")


def load_sweep(data: dict, *, repo: str) -> dict:
    """Normalize either accepted sweep shape to the bare summary dict."""
    if "summary" in data:
        # the docs/307 per-repo artifact — identity-carrying, operator-only
        if data.get("repo") not in (None, repo):
            raise Refusal(
                f"sweep artifact is for '{data.get('repo')}' but the "
                f"adjudications file is for '{repo}' — wrong pairing")
        data = data["summary"]
    missing = [k for k in ("commits", "checkable", "witnessed", "unwitnessed",
                           "abstained", "by_kind", "unwitnessed_shas")
               if k not in data]
    if missing:
        raise Refusal("sweep JSON missing field(s): " + ", ".join(missing))
    return data


def validate_meta(meta: dict) -> None:
    if meta.get("schema") not in (None, _SCHEMA):
        raise Refusal(f"unknown adjudications schema '{meta.get('schema')}' "
                      f"(this renderer speaks {_SCHEMA})")
    for key in ("repo", "tier", "rendered", "attribution", "auditor", "range"):
        if not meta.get(key):
            raise Refusal(f"adjudications file missing '{key}'")
    if "/" not in meta["repo"]:
        raise Refusal(f"repo '{meta['repo']}' is not in <org>/<name> form")
    if meta["tier"] not in TIERS:
        raise Refusal(f"unknown tier '{meta['tier']}' "
                      f"(one of: {', '.join(TIERS)})")
    for key in ("base_sha", "head_sha"):
        if not meta["range"].get(key):
            raise Refusal(f"adjudications range missing '{key}'")


def validate_record(rec: dict) -> None:
    sha = str(rec.get("sha", ""))
    if len(sha) != 40:
        raise Refusal(f"record sha '{sha}' is not a full 40-hex SHA "
                      "(the receipts table links the exact commit)")
    ruling = rec.get("ruling")
    if ruling not in RULINGS:
        raise Refusal(f"record {sha[:7]}: unknown ruling '{ruling}' "
                      f"(one of: {', '.join(RULINGS)})")
    if not rec.get("subject"):
        raise Refusal(f"record {sha[:7]}: missing the commit subject")
    if ruling == "CONFIRMED":
        if rec.get("class") not in CONFIRMED_CLASSES:
            raise Refusal(
                f"record {sha[:7]}: a CONFIRMED ruling needs a class "
                f"(one of: {', '.join(CONFIRMED_CLASSES)})")
        if rec.get("rung") != "human":
            # docs/311 §2: the judge's only question is "artifact?" — a flag
            # is CONFIRMED only by the HUMAN rung, on every tier.
            raise Refusal(f"record {sha[:7]}: a CONFIRMED ruling requires "
                          "the human rung")
        if not rec.get("rationale"):
            raise Refusal(f"record {sha[:7]}: a CONFIRMED ruling needs its "
                          "rationale (the receipts column is required)")
    elif ruling == "AUDITOR_ARTIFACT":
        if rec.get("rung") not in RUNGS:
            raise Refusal(f"record {sha[:7]}: an AUDITOR_ARTIFACT ruling "
                          f"needs a rung (one of: {', '.join(RUNGS)})")
        if not str(rec.get("issue", "")).lstrip("#").isdigit():
            raise Refusal(f"record {sha[:7]}: an AUDITOR_ARTIFACT ruling "
                          "names the narrowing issue (e.g. \"#81\")")


def match_records(flags: list[str],
                  records: list[dict]) -> list[tuple[str, dict | None]]:
    """Pair every raw flag with at most one record; refuse stale/ambiguous.

    Flags may be short SHAs (older auditors) while records carry full SHAs —
    matching is by prefix in either direction.
    """
    used: set[int] = set()
    matched: list[tuple[str, dict | None]] = []
    for flag in flags:
        hits = [i for i, r in enumerate(records)
                if r["sha"].startswith(flag) or flag.startswith(r["sha"])]
        if len(hits) > 1:
            raise Refusal(f"{len(hits)} adjudication records match flag "
                          f"{flag} — records must be unambiguous")
        if hits:
            if hits[0] in used:
                raise Refusal(f"record {records[hits[0]]['sha'][:7]} matches "
                              "more than one flag — records must be "
                              "unambiguous")
            used.add(hits[0])
            matched.append((flag, records[hits[0]]))
        else:
            matched.append((flag, None))
    stale = [r["sha"][:7] for i, r in enumerate(records) if i not in used]
    if stale:
        raise Refusal(
            "adjudication record(s) name SHA(s) the sweep did not flag: "
            + ", ".join(stale)
            + " — remove the stale record(s); the records file cannot "
              "pre-adjudicate")
    return matched


def derive_state(matched: list[tuple[str, dict | None]],
                 *, tier: str) -> tuple[str, int]:
    """The §4 headline state — derived from flags × rulings, never from data.

    Returns ``(state, confirmed_count)``; raises `Refusal` where the tier's
    publication gate forbids the state entirely.
    """
    confirmed = sum(1 for _, r in matched
                    if r is not None and r["ruling"] == "CONFIRMED")
    unresolved = [flag for flag, r in matched
                  if r is None or r["ruling"] == "UNADJUDICATED"]
    if unresolved:
        state = RAW_ONLY
    elif confirmed:
        state = DRIFT
    else:
        state = CLEAN
    if state == RAW_ONLY and tier not in RAW_ONLY_TIERS:
        raise Refusal(
            f"tier '{tier}': {len(unresolved)} flag(s) unadjudicated — a "
            "curated/seeded page with an unresolved flag does not publish "
            "at all (docs/311 §2)")
    if tier == "seeded" and state != CLEAN:
        raise Refusal(
            "tier 'seeded' publishes CLEAN verdicts only — a non-clean "
            "verdict is aggregate-only, never a named page (docs/311 §1, "
            "tier 2a)")
    return state, confirmed


# ---------------------------------------------------------------------------
# The renderer — the docs/311 §5 page, section by section. Pure.
# ---------------------------------------------------------------------------


def render_page(sweep: dict, meta: dict) -> tuple[str, str]:
    """Render the page; returns ``(markdown_text, state)``."""
    validate_meta(meta)
    records = list(meta.get("records", []))
    for rec in records:
        validate_record(rec)
    flags = [str(s) for s in sweep["unwitnessed_shas"]]
    matched = match_records(flags, records)
    state, confirmed = derive_state(matched, tier=meta["tier"])

    repo = meta["repo"]
    repo_url = f"https://github.com/{repo}"
    rng = meta["range"]
    notes = meta.get("notes", {}) or {}
    checkable = int(sweep["checkable"])

    # -- §5.1 the headline: derived, with n of m inline ---------------------
    if state == DRIFT:
        pct = f"{confirmed / checkable:.1%}"
        headline = (f"**DRIFT REPORTED — {confirmed} unwitnessed of "
                    f"{checkable} checkable claims ({pct}, adjudicated).**")
        adjudicated_cell = f"**{confirmed} of {checkable} ({pct})**"
    elif state == CLEAN:
        headline = (f"**CLEAN — 0 confirmed unwitnessed of {checkable} "
                    "checkable claims.**")
        adjudicated_cell = f"**0 of {checkable} (0.0%)**"
    else:
        pending = sum(1 for _, r in matched
                      if r is None or r["ruling"] == "UNADJUDICATED")
        headline = (f"**RAW-ONLY — NO GRADE — {pending} flag(s) not yet "
                    f"adjudicated of {checkable} checkable claims.**")
        adjudicated_cell = "**no grade — adjudication incomplete**"

    lines: list[str] = []
    lines.append(f"# {repo} — drift scoreboard")
    lines.append("")
    lines.append("> " + headline)
    quote_tail = " ".join(
        part for part in (notes.get("headline"), _HEADLINE_TAIL) if part)
    lines.extend(_fill_quote(quote_tail).splitlines())

    # -- §5.2 the as-of block ------------------------------------------------
    commits_cell = str(sweep["commits"])
    if rng.get("commits_note"):
        commits_cell += f" ({rng['commits_note']})"
    lines += [
        "",
        "## As of",
        "",
        "| | |",
        "|---|---|",
        f"| Audited range | `{rng['base_sha']}` → `{rng['head_sha']}` |",
        f"| Commits in range | {commits_cell} |",
        f"| Rendered | {meta['rendered']} |",
        f"| Auditor | {meta['auditor']} |",
        f"| Tier | {meta['tier']} |",
        f"| Attribution | {meta['attribution']} |",
    ]

    # -- §5.3 the verdict table: raw and adjudicated BOTH, always -----------
    raw_rate = f"{(int(sweep['unwitnessed']) / checkable if checkable else 0.0):.1%}"
    lines += [
        "",
        "## The verdict",
        "",
        "| Commits | Checkable | Witnessed | Unwitnessed (raw) | Abstained "
        "| Raw rate | Adjudicated |",
        "|---|---|---|---|---|---|---|",
        f"| {sweep['commits']} | {checkable} | {sweep['witnessed']} "
        f"| {sweep['unwitnessed']} | {sweep['abstained']} | {raw_rate} "
        f"| {adjudicated_cell} |",
    ]
    if notes.get("verdict"):
        lines += ["", _fill(notes["verdict"])]

    # -- §5.4 by claim kind ---------------------------------------------------
    by_kind = sweep["by_kind"]
    kinds = [k for k in _KIND_ORDER if k in by_kind]
    kinds += sorted(k for k in by_kind if k not in _KIND_ORDER)
    lines += [
        "",
        "## By claim kind",
        "",
        "| Kind | Witnessed | Unwitnessed | Abstained |",
        "|---|---|---|---|",
    ]
    for kind in kinds:
        row = by_kind[kind]
        if kind == "none":
            lines.append(f"| `none` (no checkable claim) | — | — "
                         f"| {row['abstain']} |")
        else:
            lines.append(f"| `{kind}` | {row['witnessed']} "
                         f"| {row['unwitnessed']} | {row['abstain']} |")

    # -- §5.5 the receipts: one row per raw flag ------------------------------
    lines += ["", "## The receipts — every flag, adjudicated", ""]
    if not matched:
        lines.append("No flags in range.")
    else:
        lines += [
            "| Commit | Subject | Ruling | Rung | Rationale |",
            "|---|---|---|---|---|",
        ]
        for flag, rec in matched:
            link_sha = rec["sha"] if rec else flag
            commit_cell = f"[`{link_sha[:7]}`]({repo_url}/commit/{link_sha})"
            if rec is None:
                subject, ruling, rung, rationale = (
                    "—", "`UNADJUDICATED`", "—", "—")
            else:
                subject = "`" + _cell(rec["subject"]) + "`"
                if rec["ruling"] == "CONFIRMED":
                    ruling = f"`CONFIRMED({rec['class']})`"
                elif rec["ruling"] == "AUDITOR_ARTIFACT":
                    num = str(rec["issue"]).lstrip("#")
                    ruling = (f"`AUDITOR_ARTIFACT` → "
                              f"[#{num}]({_AUDITOR_URL}/issues/{num})")
                else:
                    ruling = "`UNADJUDICATED`"
                rung = rec.get("rung") or "—"
                rationale = _cell(rec.get("rationale") or "—")
            lines.append(f"| {commit_cell} | {subject} | {ruling} "
                         f"| {rung} | {rationale} |")
    if notes.get("receipts"):
        lines += ["", _fill(notes["receipts"])]

    # -- §5.6 reproduce it -----------------------------------------------------
    lines += ["", "## Reproduce it", ""]
    if notes.get("reproduce"):
        lines += [_fill(notes["reproduce"]), ""]
    name = repo.split("/", 1)[1]
    install = ("pip install -e ." if repo == _AUDITOR_REPO
               else "pip install dos-kernel")
    lines += [
        "```bash",
        f"git clone https://github.com/{repo}.git && cd {name}",
        f"git checkout {rng['head_sha']}",
        install,
        "dos commit-audit --sweep --json --workspace . \\",
        f"    {rng['base_sha']}..{rng['head_sha']}",
        "```",
        "",
        _fill(_REPRODUCE_TAIL),
    ]

    # -- §5.7 the correction path ----------------------------------------------
    lines += ["", "## Corrections", "", _fill(_CORRECTIONS)]

    return "\n".join(lines) + "\n", state


# ---------------------------------------------------------------------------
# Boundary I/O — read the two inputs, write (or check) the page.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", required=True,
                    help="sweep JSON (dos commit-audit --sweep --json, or "
                         "the docs/307 per-repo artifact)")
    ap.add_argument("--adjudications", required=True,
                    help="adjudication-records JSON (page meta + one typed "
                         "record per raw flag)")
    ap.add_argument("--out", help="page path to write (default: stdout)")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 unless --out already holds "
                         "exactly the rendered bytes")
    args = ap.parse_args(argv)

    if args.check and not args.out:
        print("scoreboard-page: --check needs --out", file=sys.stderr)
        return 2

    try:
        meta = json.loads(
            Path(args.adjudications).read_text(encoding="utf-8"))
        validate_meta(meta)
        sweep = load_sweep(
            json.loads(Path(args.sweep).read_text(encoding="utf-8")),
            repo=meta["repo"])
        text, state = render_page(sweep, meta)
    except Refusal as exc:
        print(f"scoreboard-page: REFUSED — {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, KeyError, TypeError,
            ValueError) as exc:
        print(f"scoreboard-page: bad input — {exc}", file=sys.stderr)
        return 2

    rendered = text.encode("utf-8")
    if args.check:
        out = Path(args.out)
        current = out.read_bytes() if out.exists() else b""
        if current != rendered:
            print(f"scoreboard-page: {args.out} is NOT what the inputs "
                  "render — regenerate it", file=sys.stderr)
            return 1
        print(f"scoreboard-page: {args.out} matches its inputs ({state})")
        return 0
    if args.out:
        # write_bytes: the tracked page is LF-only on every platform
        Path(args.out).write_bytes(rendered)
        print(f"scoreboard-page: wrote {args.out} ({state})")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
