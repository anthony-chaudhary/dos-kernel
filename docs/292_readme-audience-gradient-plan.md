# 292 — The README audience gradient: one front door, six on-ramps

> **Status:** P1–P4 shipped (`85a3bad`, `7a337da`, `f1436a5`, `348147e` — let
> `dos verify` be the judge, not this sentence). §6 adds P5–P8 from the
> fresh-lens findings in [issue #16](https://github.com/anthony-chaudhary/dos-kernel/issues/16);
> P8 waits on the program owner. Extends the modular README
> (`docs/readme/`, one file per section, assembled in filename order by
> `scripts/build_readme.py`, pinned by `tests/test_readme_assembly.py`) with
> explicitly audience-graduated sections — from a reader who never opens a
> terminal up to a researcher evaluating the claims. The part-split (docs/285-era
> restructure) made this cheap: a new audience section is a new file at a free
> number, not surgery on an 850-line README.

## 1. The problem: the README graduates by function, not by reader

The current part sequence is a *task* gradient — try it (`10`), understand the
failure it fixes (`20`/`30`), audit the evidence (`40`), wire it in (`50`/`70`),
learn the surface (`60`/`80`), extend it (`90`), cite it (`95`). That ordering
is right and stays. But the README already names two explicit axes of adoption
— *how deep your config goes* ("How far you take it") and *how you call the
referee* ("How you plug it in") — while the third axis, **who is reading**, is
only implicit in the ordering. Concretely:

- A **non-technical evaluator** (the engineering manager deciding whether the
  team adopts agent fleets at all) has exactly one artifact: the "30-second
  mental model" `<details>` block in `00_front-door.md` — collapsed by default,
  and the reader least likely to click an expander is precisely this one.
- A **researcher** has strong raw material — `40_evidence.md` is unusually
  honest (the proven / projected / bet split), `95` has the BibTeX — but no
  on-ramp tying *claims → reproduction → invariants → literature* together.
  The two load-bearing formal ideas (the non-forgeable witness, the
  non-distillable label) appear only inline, mid-table, in `60_syscalls.md`.
- A **fleet operator** gets a verb listing (`80_cli.md` is reference, not
  guide); the day-2 story — triage the morning, watch the fleet, drain the
  decision queue — lives across three subsections and a playbook link.
- Nobody can *see where their tier starts*. The reader either reads top-to-
  bottom or scans headings that name features, not readers.

## 2. The gradient — six levels

At least five graduations were asked for; the honest count for this repo is
six, plus one the repo already treats as first-class. Each level names who the
reader is, the question they arrive with, where they should land, and what is
missing today.

| | Reader | The question they bring | Lands at | Today | Gap |
|---|---|---|---|---|---|
| **L0** | **Non-technical / evaluator** — EM, PM, exec deciding whether agent fleets are adoptable; may have arrived from a headline | "What is this, in plain words, and why should my team care?" | a no-code narrative: the grade-their-own-homework problem, the referee, what adopting it costs (one engineer, one afternoon) | one collapsed `<details>` block | **a real section** (P1) |
| **L1** | **First-touch developer** — uses a coding agent casually | "Show me, fast" | `10_try-it.md` (`dos quickstart`, the caught lie) | strong | none — keep |
| **L2** | **Practitioner-integrator** — runs agents seriously; owns a CI pipeline or an agent-host config | "How do I wire the verdict into *my* stack?" | the plug-in surface table in `20`, `50_agent-hosts.md`, `70_install.md` | strong | router visibility only |
| **L3** | **Fleet operator** — many agents, every day; pages when it wedges | "How do I *run* this — observe, triage, debug, supervise?" | `80_cli.md` §projections + the stuck-fleet playbook | reference exists; the guide doesn't | **an operator guide** (P3) |
| **L4** | **Extender / systems engineer** — wants org-specific lanes, judges, dialects without forking | "How do I bend it to my org?" | `90_extending-and-docs.md` → HACKING.md, CLAUDE.md | strong | router visibility only |
| **L5** | **Researcher / claims evaluator** — reads the benchmark, reproduces, situates in literature, cites | "What is actually proven, can I re-run it, and what is the formal contribution?" | `40_evidence.md` + `95_citation-license.md` | evidence yes; no on-ramp joining claims → repro → invariants → related work | **a researcher section** (P2) |
| (L6) | **The AI agent itself** — an agent reading the repo to orient | "How do I work here?" | the AGENTS.md pointer in `00` | already first-class | none — name it in the router |

Two design facts about the gradient worth stating, because they shape every
phase below:

- **Audience and understanding are different dimensions that happen to
  correlate.** A researcher is at L0 *understanding of DOS specifically* on
  first contact. So the router (P1) routes by *the question you brought*, never
  by job title — "I don't write code", "show me it working", "I already run
  agents", "I operate a fleet", "I want to extend it", "I'm evaluating the
  claims". Levels are entry points, not castes; every section hands off upward.
- **The README section is the on-ramp; depth lives in `docs/`.** The same
  hot/cold split as CLAUDE.md vs `docs/ARCHITECTURE.md`. A new audience section
  earns its place by being one screen that *routes*, not a chapter that
  *contains* — the researcher section is a claims register with links, not a
  literature review; the operator section is the triage loop, not a manual.

## 3. The graduation contract (rules every part follows)

1. **Declare the reader once, in the router — not per-section.** A per-section
   "*For: …*" tag is a hand-kept roster across eleven files and it will rot
   (the same argument CLAUDE.md makes for not enumerating the kernel module
   roster). One router table is the single source of the audience map.
2. **Every level is skippable without breaking the next.** Already true of the
   existing parts; the new parts must not introduce forward dependencies.
3. **Every section ends with a hand-off** — one line, up a level ("ready to
   wire it in? → …") and where sensible down ("lost? the plain-words version →
   …"). Cheap to add to existing parts (P4), structural in new ones.
4. **No anchor renames.** Inbound links (`#try-it-in-60-seconds`,
   `#the-two-money-moments-rendered`, …) are referenced from PyPI, the plugin
   README, and external posts. New parts add anchors; nothing existing moves.
5. **`<details>` is for optional depth for a reader already engaged** (the
   5-line manual walkthrough in `10`), never for a whole audience's only
   content. Promoting L0 out of an expander is half of P1's point.
6. **Length is budgeted.** The three new parts + router land ≈150–190 lines on
   today's 851. The router pays part of that back: a reader who can jump stops
   needing the redundant signposting prose that accumulates when every section
   must defend against every reader.

## 4. The part-level changes

New files slot into free numbers — no renumbering, and concurrent edits to
other sections can land independently (the disjoint-lanes property the part
split exists to provide).

- **P1 — `05_who-this-is-for.md` (router + the L0 narrative).** Two pieces in
  one short part, placed right after the front door:
  - *The router*: the six-row table from §2, compressed to "You're asking… →
    start at → then". One screen.
  - *The plain-words story* (the L0 destination, visible, no expander): coding
    agents grade their own homework; a fleet of them compounds the lie; DOS is
    the referee that reads what actually happened — the commit, the file, the
    clock — and never the agent's account of it. What adopting it means
    operationally (one package, one config file, works on day one with
    neither). Four–six paragraphs, zero shell blocks. The existing 30-second
    `<details>` block in `00` then shrinks to a pointer (its prose moves here)
    rather than duplicating.
- **P2 — `93_for-researchers.md` (the L5 on-ramp).** One screen, four blocks:
  - *Claims register*: the `40_evidence.md` results restated as a compact
    claim / status / where-it-reproduces table (each row → `benchmark/` write-up).
  - *The two invariants, stated precisely*: (1) the **non-forgeable witness** —
    every kernel verdict is a pure function of bytes the claimant did not
    author (docs/138); (2) the **non-distillable label** — the reward-set
    admission bit cannot be moved by any answer text, only by the environment
    state (docs/230/234). These are the paper's contribution and currently
    live mid-table in `60`.
  - *Reproduction*: the one command / entry point per proven row, and the
    J-count discipline (a J is failures blocked off ground truth, never an
    outcome delta) restated as the reading rule for the numbers.
  - *Situating it*: the lineage the design already cites — reference monitor /
    minimal-TCB, ARIES recovery, serializability for the arbiter, and the
    reward-hacking / scalable-oversight line for `reward()` — one line each,
    then hand off to the paper and `95`'s BibTeX.
- **P3 — `85_operating-a-fleet.md` (the L3 guide).** The day-2 loop as
  narrative, keeping `80_cli.md` as pure reference (the guide/reference split):
  morning triage = `dos top` (what's running) → `dos decisions` (what needs
  me) → `dos plan` (claim vs truth); push it to where you are (`dos notify`),
  drain it to dashboards (`dos export`), and when something wedges, the
  symptom→command table is the stuck-fleet playbook (link, don't duplicate).
  Ends handing up to L4.
- **P4 — hand-off lines + router back-links.** One closing line each in `10`,
  `20`, `50`, `90` pointing a level up; `00` gains one line pointing at the
  router. Smallest phase, done last so it links to parts that exist.

Each phase is: write the part → `python scripts/build_readme.py` → assembly
test green → commit (the part and the regenerated `README.md` together, since
the test pins them to each other).

## 5. What does NOT go here

The audience gradient is **documentation ergonomics, not market segmentation**.
Persona depth — who buys, which segment converts, positioning language per
audience — is the strategy genre and lives in the private strategy repo, not in
a README section (the one-way-arrow rule in CLAUDE.md). The README's L0 section
explains the problem in plain words; it does not argue the business case.

Also out of scope: restructuring the existing task gradient (it works), and the
registry-first install-prose flip that PyPI going live makes due in `10`/`70` —
that is a separate, already-tracked edit; it touches different parts, so the
two workstreams land independently.

## 6. The fresh-lens findings — P5–P8 (issue #16)

After P1–P4 shipped, a 2026-06-10 audit read the assembled README as a stranger
([issue #16](https://github.com/anthony-chaudhary/dos-kernel/issues/16), the
public tracking handle). Every command, flag, and link checked out real — the
problem is volume and ordering, not accuracy: ~11k words where comparable infra
READMEs run 1.5–3k, and the on-ramps this plan added now *compete* with the
hero instead of routing away from it. Each finding gets the minimal fix
consistent with §2's own rule — one screen that routes, not a chapter that
contains. Four more phases:

- **P5 — define the five words before first use** (finding 1). "lane" is used
  from the fleet section onward but defined only in a note under the syscall
  table; "oracle" and "stamp" are never defined at all. Fix: one short glossary
  callout at the end of `00_front-door.md` — **plan / phase / lane / oracle /
  stamp**, one clause each — so the vocabulary lands just before the router
  that uses it. No new section, no expander.
- **P6 — one install default, one reproducible demo** (findings 4 + 5). The
  60-second demo says `pip install dos-kernel`; the install matrix then leads
  with `uv tool install` "(recommended)" — telling the reader the command they
  just ran was the wrong choice. Fix: the demo's `pip` line IS the default
  (it is also what the plugin path and a host's own pin use); `70_install.md`
  reorders to lead with it and presents `uv` as the isolated-CLI alternative,
  keeping every channel. And the by-hand expansion in `10_try-it.md` shows a
  hardcoded commit SHA (`e389e8b`) a copy-paster will never reproduce —
  annotate it `<your-sha>` (the figure caption already does this).
- **P7 — split the build journal out of the docs index** (finding 6).
  `docs/README.md` opens as a routed index (the arrows + the guides table) and
  then degrades into a research lab notebook — renumbering history, arc
  synopses, relocated-doc notes. Fix: the index keeps the orientation, the
  arrows, and the guides table; everything below moves to a clearly-labeled
  **`docs/BUILD_JOURNAL.md`** (design notes, plan records, research arcs,
  companions — content unchanged, including the renumbering note, which is
  journal material). The index points at it in one paragraph. No inbound
  anchor links exist to the moved sections (checked 2026-06-10), so nothing
  breaks.
- **P8 — the reference-weight moves** (findings 2 + 3) — **gated on the
  program owner; do not ship unilaterally.** Two deliberate placements are
  re-litigated here: the 17-row syscall table mid-README (finding 2 would keep
  ~6 headline verbs + one link and move the full table to a docs page) and the
  two first-person dogfood anecdotes in `30_why-a-referee.md` (finding 3 would
  compress each to a sentence + link, or move both to the evidence tier — the
  issue itself notes the second was added deliberately in v0.23.2).
  Recommendation: do both — the audit's calibration says the on-ramps cannot
  do their routing job while this much reference weight sits mid-page, and
  both contents survive intact one click away. But placement was the owner's
  call when it was made and stays the owner's call to unmake.

Same shipping discipline as §4: each phase is part-edit → `python
scripts/build_readme.py` → assembly test green → one commit carrying the part
and the regenerated `README.md` together (P7 touches no part, so no rebuild).
The issue stays open until P8 is decided; phases close by `dos verify`, never
by this file's Status line.
