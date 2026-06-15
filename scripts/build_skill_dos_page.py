#!/usr/bin/env python3
"""build_skill_dos_page — render the skill-with/without-DOS dashboard (issue #176).

The `benchmark/skill_dos_ablation/` harness MEASURES whether a `dos-skillify`-converted
skill over-claims less than its original (docs/345 §6). This tool turns that measurement
into one self-contained public HTML page — the same shape as the drift-rate scoreboard
(`scripts/drift_scoreboard.py` → `drift-scoreboard.html`): methodology-first, honest about
scope, every number DERIVED from the harness, never hand-typed.

The page draws the one contrast that makes the result legible: the field audits a skill by
GRADING THE AGENT'S OUTPUT (an LLM-judge rubric score — Tessl's eval methodology); DOS
grounds the "done" bit on a WITNESS THE AGENT DID NOT AUTHOR (git ancestry, an env terminal,
a tool-stream, a recall re-probe). Judge-the-output vs. witness-the-effect.

This is dev tooling, not a kernel module: it `import`s the benchmark + `dos`, the one-way
arrow (nothing under `src/dos/` imports it). Pure on its inputs — the harness `compute()` is
deterministic over a committed fixture, so the page is byte-reproducible under `--check`.

Usage:
    python scripts/build_skill_dos_page.py                          # render to stdout
    python scripts/build_skill_dos_page.py --out docs/skill-dos-benchmark.html
    python scripts/build_skill_dos_page.py --check --out docs/skill-dos-benchmark.html

Exit codes: 0 rendered (or --check matched); 1 --check mismatch OR a harness invariant
violation (a broken benchmark must not publish a page); 2 bad input.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

# `benchmark/` is an in-repo package, not pip-installed — put the repo root on the path so
# this `scripts/` tool can import it when run directly (`python scripts/build_skill_dos_page.py`).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The one-way arrow: this tool imports the benchmark + the kernel; neither imports it.
from benchmark.skill_dos_ablation import corpus as _corpus  # noqa: E402
from benchmark.skill_dos_ablation import harness as _harness  # noqa: E402

CANONICAL = "https://anthony-chaudhary.github.io/dos-kernel/skill-dos-benchmark.html"

# Per-skill reader-facing labels: the trust seam the original closes on the agent's word,
# the rigged-failure shape, and the witness rung the `-dos` variant grounds on instead.
# Sourced from corpus.py's per-skill docstrings + witnesses.py's rungs — kept here as the
# render layer's static copy (the corpus stays the auditable INPUT).
SKILL_COPY: dict[str, dict[str, str]] = {
    "ship-verify": {
        "seam": "“the phase shipped”",
        "rigged": "a commit claimed but never landed (its subject is absent from the git log)",
        "rung": "git ancestry (<code>dos verify</code> / <code>dos commit-audit</code>)",
    },
    "fanout-collect": {
        "seam": "“all fan-out workers finished”",
        "rigged": "a worker that died on a synthetic terminal (non-zero exit / error channel)",
        "rung": "the env-authored worker terminal",
    },
    "loop-finish": {
        "seam": "“the tool loop finished”",
        "rigged": "a loop that made no progress (byte-identical tool results every iteration)",
        "rung": "tool-stream no-advance",
    },
    "memory-recall": {
        "seam": "“this recalled fact is current”",
        "rigged": "a recalled memory now stale (the token it claims present is gone from the tree)",
        "rung": "recall staleness (<code>dos recall</code>)",
    },
    "prose-polish": {
        "seam": "“this rewrite reads more clearly”",
        "rigged": "a worse rewrite claimed as “polished”",
        "rung": "UNWITNESSABLE — pure-prose, no env byte to ground on",
    },
}

# The Tessl contrast — sourced, falsifiable, with the docs/345 §6.5 concessions baked in so
# the page cannot over-claim. Each (label, url) is a primary source from the landscape scan.
TESSL_SOURCES = [
    ("Three context-eval methodologies — Tessl",
     "https://tessl.io/blog/three-context-eval-methodologies/"),
    ("Review a skill against best practices — Tessl Docs",
     "https://docs.tessl.io/evaluate/evaluating-skills"),
    ("Stop guessing whether your skill works — Tessl",
     "https://tessl.io/blog/stop-guessing-whether-your-skill-works-skill-optimizer-measures-and-improves-it/"),
    ("Securing the Agent Skills Registry — Snyk + Tessl",
     "https://snyk.io/blog/snyk-tessl-partnership/"),
]

# The shared house style, byte-for-byte from drift-scoreboard.html (one palette, one width).
_STYLE = """\
  <style>
    :root {
      --bg: #0d1117; --panel: #161b22; --border: #30363d;
      --fg: #e6edf3; --muted: #9198a1; --accent: #58a6ff; --green: #3fb950; --red: #f85149;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--fg);
      font: 17px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    main { max-width: 760px; margin: 0 auto; padding: 48px 20px 64px; }
    h1 { font-size: 1.7rem; line-height: 1.25; margin: 0 0 8px; }
    h2 { font-size: 1.3rem; margin: 40px 0 8px; }
    p { margin: 12px 0; }
    .tagline { color: var(--muted); font-size: 1.05rem; margin-top: 0; }
    .big { font-size: 2.6rem; font-weight: 700; margin: 18px 0 2px; }
    .big small { font-size: 1rem; font-weight: 400; color: var(--muted); display: block; }
    pre {
      background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
      padding: 14px 16px; overflow-x: auto; font-size: 14px; line-height: 1.5;
    }
    code { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; }
    p > code, li > code, td > code {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 4px; padding: 1px 5px; font-size: 0.9em;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .ok { color: var(--green); } .no { color: var(--red); } .dim { color: var(--muted); }
    ul { padding-left: 22px; } li { margin: 6px 0; }
    table { border-collapse: collapse; width: 100%; font-size: 0.95em; }
    td, th { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
    th { background: var(--panel); }
    .note { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; font-size: 0.95em; }
    footer { margin-top: 56px; color: var(--muted); font-size: 0.85rem; border-top: 1px solid var(--border); padding-top: 16px; }
  </style>"""


def _pct(rate: float) -> str:
    return f"{rate * 100:.0f}%"


def _rate_cell(s) -> str:
    """The over-claim cell: 'n/d = X%', red when it leaks, green when it refused to zero."""
    txt = f"{s.silent_overclaims}/{s.n_failed} = {_pct(s.overclaim_rate)}"
    cls = "no" if s.overclaim_rate > 0 else "ok"
    return f'<span class="{cls}">{txt}</span>'


def render_html(scores: dict, n_failed: int, violations: list[str]) -> str:
    """The self-contained dashboard page. Pure; every number comes from `scores`."""
    groundable = [s for s in _corpus.SKILLS if s != _corpus.NEGATIVE_SKILL]
    neg = _corpus.NEGATIVE_SKILL

    desc = (
        f"On {n_failed} rigged-failure tasks (ground truth = this did NOT work), a skill that "
        f"closes its trust seam on the agent's own word over-claims 100%; the same skill grounded "
        f"on an independent witness by dos-skillify over-claims 0%. A pure-prose skill shows DOS "
        f"not helping. Calibrated fixture, $0, deterministic."
    )

    out: list[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en">')
    out.append("<head>")
    out.append('  <meta charset="utf-8">')
    out.append('  <meta name="viewport" content="width=device-width, initial-scale=1">')
    out.append("  <title>Skill trustworthiness with vs. without DOS — the over-claim benchmark</title>")
    out.append(f'  <meta name="description" content="{html.escape(desc)}">')
    out.append(f'  <link rel="canonical" href="{CANONICAL}">')
    out.append('  <meta property="og:type" content="article">')
    out.append('  <meta property="og:title" content="Skill trustworthiness with vs. without DOS — the over-claim benchmark">')
    out.append(f'  <meta property="og:description" content="{html.escape(desc)}">')
    out.append(f'  <meta property="og:url" content="{CANONICAL}">')
    out.append(_STYLE)
    out.append("</head>")
    out.append("<body>")
    out.append("<main>")

    # -- headline ------------------------------------------------------------
    out.append("  <h1>Does grounding a skill on a witness make it over-claim less?</h1>")
    out.append('  <p class="tagline">A skill closes its “done / shipped / found” bit on what the agent '
               "<em>says</em>. <code>dos-skillify</code> re-grounds that bit on evidence the agent did "
               "not author. This is the measured difference.</p>")
    out.append(f'  <p class="big">100% → 0% <small>silent over-claim rate on {n_failed} rigged-failure '
               "tasks: the original skill declares success on every one; the DOS-grounded variant "
               "refuses every one it can witness. Calibrated fixture, $0, deterministic.</small></p>")
    out.append("  <p>A <strong>silent over-claim</strong> is declaring success when the ground truth is "
               "failure. We rig tasks whose correct answer is “this did NOT work” — a commit that never "
               "landed, a fan-out worker that died, a loop that made no progress, a recalled fact gone "
               "stale — and ask each variant: did it still say success? The original reads the agent's "
               "own <code>final_text</code> (forgeable), so it leaks on all of them. The "
               "<code>-dos</code> variant reads a witness instead.</p>")

    # -- the verdict table (derived) -----------------------------------------
    out.append("  <h2>The verdict — every skill, both variants</h2>")
    out.append("  <table>")
    out.append("    <tr><th>skill</th><th>variant</th><th>silent over-claim</th>"
               "<th>refused</th><th>+tokens</th><th>+latency</th></tr>")
    for skill in _corpus.SKILLS:
        for variant in ("original", "-dos"):
            s = scores[skill][variant]
            tag = " <span class=\"dim\">(negative)</span>" if (
                skill == neg and variant == "-dos") else ""
            out.append(
                f"    <tr><td><code>{skill}</code></td><td><code>{variant}</code>{tag}</td>"
                f"<td>{_rate_cell(s)}</td><td>{s.refused}/{s.n_failed}</td>"
                f"<td>{s.added_tokens}</td><td>{s.added_ms}ms</td></tr>")
    out.append("  </table>")
    out.append('  <p class="dim">Reported as refused / advisory counts, never a “caught N” headline '
               "(the docs/333 honesty floor). The <code>+tokens</code> / <code>+latency</code> columns "
               "are the real cost of the witness step — a trade-off, shown not hidden.</p>")

    # -- the four shapes + witness rung --------------------------------------
    out.append("  <h2>What the witness reads — one rung per trust seam</h2>")
    out.append("  <p>The original closes each seam on the agent's word. The <code>-dos</code> variant "
               "closes it on a byte the agent did not author:</p>")
    out.append("  <table>")
    out.append("    <tr><th>skill</th><th>the seam it closes</th>"
               "<th>rigged failure (truth = failed)</th><th>witness rung (<code>-dos</code>)</th></tr>")
    for skill in _corpus.SKILLS:
        c = SKILL_COPY[skill]
        out.append(f"    <tr><td><code>{skill}</code></td><td>{c['seam']}</td>"
                   f"<td>{c['rigged']}</td><td>{c['rung']}</td></tr>")
    out.append("  </table>")

    # -- the negative --------------------------------------------------------
    neg_o = scores[neg]["original"].overclaim_rate
    neg_d = scores[neg]["-dos"].overclaim_rate
    out.append("  <h2>The built-in negative — where DOS does <em>not</em> help</h2>")
    out.append(f'  <p class="note"><code>{neg}</code> is a pure-prose / taste skill (“rewrite this more '
               "clearly”). Its only trust seam is the agent's own judgment — there is no git log, no "
               "terminal, no tool stream, no recall token to ground on. So its rigged failure is "
               f"<strong>UNWITNESSABLE</strong>: the <code>-dos</code> over-claim rate ({_pct(neg_d)}) "
               f"<strong>equals</strong> the original's ({_pct(neg_o)}), and the witness cost buys "
               "nothing. A benchmark that can only confirm its own pitch is the bias the kernel refuses "
               "(docs/333) — so this skill is in the corpus on purpose, and the harness's in-band "
               "falsifier asserts the negative never “improves”.</p>")

    # -- Tessl contrast ------------------------------------------------------
    out.append("  <h2>Judge the output, or witness the effect?</h2>")
    out.append("  <p>The field already audits skills. The closest neighbour, "
               '<a href="https://tessl.io/">Tessl</a>, grades a skill three ways: a static '
               "<em>Skill Review</em> (a SKILL.md lint plus two LLM-judge scores, no execution), and "
               "<em>Task / Repo Evals</em> that run an agent with and without the skill and have a "
               "<strong>judge model score both outputs against a rubric</strong>. Even the strongest "
               "rung anchors its <em>scenarios</em> in real git history, but the <strong>verdict is "
               "still a model's opinion of model output</strong>, not a checked effect. (Snyk's scan "
               "of the registry catches unsafe skills — prompt injection, malware — but, in Snyk's own "
               "words, “does not verify that skills function properly.”)</p>")
    out.append("  <p>DOS asks a different question. It does not score the agent's output; it reads a "
               "byte the agent could not author — git ancestry, an env terminal's exit code, whether a "
               "tool stream advanced, whether a recalled token still exists in the tree — and refuses "
               "the “done” bit when that byte says failure. <strong>Judge-the-output</strong> measures "
               "whether a grader liked what the agent produced; <strong>witness-the-effect</strong> "
               "measures whether the effect occurred.</p>")
    out.append("  <p>Two honest concessions (docs/345 §6.5): <em>gating a step inline</em> is not novel "
               "— many tools do it — and <em>“distrust the narration, check an oracle”</em> is an active "
               "research direction, not ours to claim. The narrow, defensible difference is the "
               "<em>conjunction</em>: an independent-witness grounding that is mechanical, additive over "
               "<em>any</em> existing skill, and witness-type-agnostic — rather than a property of one "
               "bespoke pipeline, an offline eval, or a content-safety gate.</p>")
    out.append("  <ul>")
    for label, url in TESSL_SOURCES:
        out.append(f'    <li><a href="{url}">{html.escape(label)}</a></li>')
    out.append("  </ul>")

    # -- scope / honesty -----------------------------------------------------
    out.append("  <h2>What this is — and is not</h2>")
    out.append('  <p class="note">A <strong>calibrated fixture</strong>: synthetic committed '
               "trajectories, the <strong>real byte-clean witness logic</strong> of the kernel seams, "
               "$0, fully deterministic. The over-claim detection is a function over fixture bytes, "
               "<strong>not a live LLM run</strong>. The measurement that would replace the fixture is a "
               "live <code>dos-skillify</code> A/B over real skill runs (needs a model and a key) — out "
               "of scope for this offline page. The corpus is the auditable input: every rigged task and "
               "its env-authored evidence is visible in the source, and the witness logic reads only "
               "env/git-authored bytes, never the agent's claim.</p>")
    if violations:
        out.append('  <p class="note no"><strong>Invariant violated.</strong> The harness\'s own '
                   "contract did not hold when this page was built — regenerate it:</p>")
        out.append("  <ul>")
        for x in violations:
            out.append(f"    <li class=\"no\">{html.escape(x)}</li>")
        out.append("  </ul>")

    # -- reproduce -----------------------------------------------------------
    out.append("  <h2>Reproduce it</h2>")
    out.append("  <pre><code>pip install -e \".[dev]\"\n"
               "python -m benchmark.skill_dos_ablation.harness            # the scored report\n"
               "python -m benchmark.skill_dos_ablation.harness --check    # assert the invariants\n"
               "python -m pytest -q benchmark/skill_dos_ablation/         # the tests</code></pre>")
    out.append("  <p>The harness is "
               '<a href="https://github.com/anthony-chaudhary/dos-kernel/tree/master/benchmark/skill_dos_ablation">'
               "<code>benchmark/skill_dos_ablation/</code></a> (corpus in, scored rows out); this page is "
               "built from it by "
               '<a href="https://github.com/anthony-chaudhary/dos-kernel/blob/master/scripts/build_skill_dos_page.py">'
               "<code>scripts/build_skill_dos_page.py</code></a>. It tracks issue "
               '<a href="https://github.com/anthony-chaudhary/dos-kernel/issues/176">#176</a> '
               "(design: docs/345 §6).</p>")

    # -- footer --------------------------------------------------------------
    out.append("  <footer>")
    out.append('    <p><a href="index.html">DOS — the Dispatch Operating System</a> · the kernel that '
               "doesn't believe your AI agents. Numbers derived from the committed fixture harness; "
               "this page is byte-reproducible under <code>--check</code>. See also the "
               '<a href="drift-scoreboard.html">drift-rate scoreboard</a>.</p>')
    out.append("  </footer>")
    out.append("</main>")
    out.append("</body>")
    out.append("</html>")
    return "\n".join(out) + "\n"


def build() -> tuple[str, list[str]]:
    """Run the harness in-process and render. Returns (page_text, violations)."""
    tasks = _corpus.corpus()
    scores = _harness.compute(tasks)
    n_failed = _harness.total_failed(tasks)
    violations = _harness.check_invariants(scores)
    return render_html(scores, n_failed, violations), violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="page path to write (default: stdout)")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 unless --out already holds exactly the rendered bytes")
    args = ap.parse_args(argv)

    if args.check and not args.out:
        print("build_skill_dos_page: --check needs --out", file=sys.stderr)
        return 2

    text, violations = build()
    if violations and not args.check:
        # A broken benchmark must not silently publish a page (the page still renders the
        # violation banner, but the exit code is the loud kill for any CI / build step).
        print("build_skill_dos_page: harness invariants VIOLATED — page reflects it, exit 1:",
              *violations, sep="\n  ", file=sys.stderr)

    rendered = text.encode("utf-8")
    if args.check:
        out = Path(args.out)
        current = out.read_bytes() if out.exists() else b""
        if current != rendered:
            print(f"build_skill_dos_page: {args.out} is NOT what the harness renders — "
                  "regenerate it", file=sys.stderr)
            return 1
        if violations:
            print("build_skill_dos_page: page matches inputs, but harness invariants are "
                  "violated", file=sys.stderr)
            return 1
        print(f"build_skill_dos_page: {args.out} matches its inputs")
        return 0

    if args.out:
        # write_bytes: the tracked page is LF-only on every platform (the CRLF trap).
        Path(args.out).write_bytes(rendered)
        print(f"build_skill_dos_page: wrote {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdout.write(text)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
