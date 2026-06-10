# Contributing to DOS

Thanks for looking at DOS — the domain-free trust substrate for fleets of autonomous
agents. This file is the contributor-facing companion to [`CLAUDE.md`](CLAUDE.md),
which is the full architecture contract. **Read `CLAUDE.md` before sending a change to
`src/dos/`** — it defines the layering the whole design rests on, and most review
feedback will be "which layer does this belong in?"

> **An AI agent sending a change?** Read [`AGENTS.md`](AGENTS.md) first — it is the
> short orientation (build/test commands, the files worth reading, the rules a change
> must satisfy) that gets you to the point where the rest of this file makes sense.

## The one rule: respect the layering

DOS is built on a single rule — **mechanism is the kernel; policy is a driver; the
workflow is host concern.** Four layers, each may import the layer above it, never
below:

1. **Kernel** (`src/dos/{oracle,phase_shipped,arbiter,…}.py`) — the syscalls, pure.
   No host names, no plan schema, no I/O policy.
2. **Seam** (`src/dos/config.py`) — `SubstrateConfig`, the single injected boundary.
3. **Seam data** (`src/dos/{reasons,stamp}.py`) — the hackability registries carried
   as values.
4. **Helpers** (`src/dos/{cli,timeline,decisions,…}.py`) and **Drivers**
   (`src/dos/drivers/<host>.py`) — thin shells and per-host policy packs.

**The most common contribution mistake is putting policy in the kernel.** If your
change teaches a kernel module about a specific host, lane, plan format, or domain —
it belongs in a `drivers/` module or in `dos.toml` workspace config, not in the
kernel. Adding support for a new host = adding a `drivers/` module, **never** editing
`config.py` or a kernel file.

The litmus tests below are not style preferences — they are enforced by the suite and
are the closest thing this project has to a constitution.

### The litmus tests (CI-enforced — your PR must keep these green)

- **Kernel imports no host.** No module under `src/dos/` (except `drivers/`) may name
  a host (`job`, `apply`, `tailor`, …). The generic default is `main`/`global`.
- **`verify` needs no plan.** `tests/test_verify_no_plan.py` — the truth syscall must
  run against a plain git repo with no plan registry.
- **The package never assumes it lives in the repo it serves.** Every path resolves
  against `SubstrateConfig.workspace`, never `__file__`.
- **The kernel never imports its own tooling** (`scripts/`) **or the MCP server**
  (`dos_mcp`). Those consume the package; the package is unaware they exist.
- **A shipped generic skill names no host.** No `SKILL.md` under `src/dos/skills/`
  may name a host directory, lane, or commit prefix — pinned by
  `tests/test_skill_pack_*.py`.

If you want to extend DOS, the intended path is almost always **without forking the
package**: add block reasons, gate verdicts, admission/safety predicates, and output
renderers as *workspace policy* (`dos.toml`, the `dos.renderers` entry-point group, a
`drivers/` module). See [`docs/HACKING.md`](docs/HACKING.md) and the copy-me skeleton
in [`examples/dos_ext/`](examples/dos_ext/).

## Dev setup & the green bar

```bash
pip install -e ".[dev]"     # editable install + pytest/build
python -m pytest -q         # the full kernel suite — must stay green
dos doctor --workspace .    # sanity: report the active workspace + lane taxonomy
```

For the MCP server surface: `pip install -e ".[mcp]"` then `dos-mcp`.

**Every PR must keep `pytest -q` green.** The kernel is small and near-stdlib on
purpose; the suite is fast. A change that can't keep the suite green is a change that
isn't ready.

## Branching & merging

`master` is the only long-lived branch, and it is protected:

- **Changes land via pull request.** Direct pushes are reserved to the
  maintainer (a transitional state); the contributor path is fork → branch → PR.
- **One required check: `ci-ok`.** It folds the whole CI matrix (leak scan, lint,
  the test legs, the wheel build) into a single verdict. The PR can merge when it
  is green; no approving-review count is configured today (single-maintainer
  reality — see "Maintenance reality" below).
- **Rebase or squash only.** Merge commits are disabled and linear history is
  enforced. A single-commit PR keeps its authored subject on squash, so write
  the subject to the repo's grammar (`git log` shows it).
- **Branch naming:** `lane/<lane>/<slug>` (e.g. `lane/docs/fix-readme-link`) is
  the house style — `<lane>` mirrors the top-level directory the change touches,
  the same taxonomy `dos arbitrate` adjudicates. Helpful but not enforced for
  external PRs.
- **Release tags are immutable** (`v*` and `stable/*`, by ruleset), and head
  branches auto-delete on merge.

## What makes a good change

- **Small, in one layer.** A PR that edits the kernel *and* a driver *and* a skill is
  usually three PRs.
- **Tested.** New mechanism gets a unit test; new policy gets a driver/config test.
  Pure functions (the arbiter, the classifiers) are tested with state-in/decision-out
  cases — no live processes.
- **Honest about evidence.** DOS's whole thesis is "the artifact outranks the
  narration." Don't describe behavior you didn't run; if a test fails, say so. This
  applies to the *docs* as much as the code — see **Claims discipline** below.
- **Near-stdlib.** The kernel's only runtime dependency is PyYAML. Adding a dependency
  to the core (not an extra) is a significant change and needs a strong reason.

## Claims discipline (the same distrust, turned on our own prose)

The kernel distrusts an agent's *narration* about code and checks the artifact. The
docs should hold themselves to the same bar: distrust your own narration about a
*result*, and write the claim the evidence actually supports — no stronger, no
weaker. A `docs/NN_*.md` that overclaims is the documentation equivalent of an agent
reporting "all work completed" over an empty tree.

This is a calibration rule, **not** a "soften everything" rule. The two failure
directions are equally wrong: a marketing superlative that outruns the data, and a
nervous hedge that buries a result you genuinely measured. Aim for the middle — state
what you found, scoped to how you found it.

**Write the scope into the claim.** A result measured on one corpus / one run / a
simulated denominator is reported as exactly that: *"measured on one real fleet,"*
*"on this corpus,"* *"observed in N runs,"* not *"proven"* or *"definitively."* Save
the strong verbs for things that are structurally true.

**Keep — these are load-bearing, not overclaim:**

- **Mechanism invariants** stated as such. A correctness property honestly says the
  arbiter *"never double-books a lane"* or a verdict is *"byte-clean / non-forgeable"*
  when that is structurally guaranteed (a deterministic floor, a git-ancestry check).
  That is a claim about mechanism, and it earns the absolute.
- **Honest negative results about DOS's own bets.** KILLED, REFUTED, "the bet
  collapses," "net loss," "unproven" applied to *our own* hypotheses are intellectual
  honesty — the opposite of overclaim. Softening a frank self-refutation makes the
  docs *less* honest. Leave it.
- **The closed typed verdicts.** `SHIPPED` / `NOT_SHIPPED` / `SPINNING` / `REFUSE` /
  `ABSTAIN` … are the kernel's vocabulary, not shouting.

**Fix — these are the real targets:**

- **Marketing superlatives.** "revolutionary," "game-changing," "paradigm shift,"
  "nobody else can." Heat, not signal — cut them.
- **Unhedged proof words on empirical claims.** "definitively," "conclusively," "a
  smoking gun," "the strongest signal in the field" — when the evidence is one study
  the same sentence grades as not-peer-reviewed. Scope it to what was measured.
- **Contempt toward other work.** Keep the substantive critique; drop the sneer. A
  fair critique names the limitation ("measures the gap but ships no adjudicator")
  without "the disease" / "cargo-cult" / "snake oil."
- **`ALL-CAPS` for emphasis** where it adds heat not signal (the typed verdicts are
  the only legitimate caps).

**The advisory lint.** `python scripts/claims_lint.py` sweeps `docs/` + the root
`*.md` and reports candidate spans for the three flagged classes above. It is
**advisory** — it exits 0 and edits nothing (a PDP, not a PEP, like the kernel
itself). Most hits are judgement calls; some are legitimate (a doc quoting an
objection to answer it). Run it before sending docs, weigh each hit, and clear or fix
it. The deeper "why" is [`docs/138`](docs/138_what-is-truth-the-throughline.md) (truth
is the byte an author could not forge) applied reflexively to the docs themselves.

## Stability surfaces (don't break these casually)

DOS is consumed by others. Treat these as compatibility contracts:

- the `dos` **CLI verbs** and their `--output` renderers,
- the **`dos_mcp` tool ABI** (`verify` / `arbitrate` / `doctor` / the refusal
  vocabulary over MCP),
- the five shipped **`SKILL.md`** screenplays under `src/dos/skills/`,
- the **`SubstrateConfig`** seam and the `dos.toml` schema (`[lanes]`, `[paths]`,
  `[reasons]`, `[stamp]`).

Breaking changes to these need a version bump and a note in the release.

## Issues — the backlog, and how one closes

The issue tracker is the project's backlog: bugs, chores, small features, and
"later" items live there. Design-shaped work lives in `docs/NN_*.md` plans; an
issue that needs one gets the `design` label and stays open, pointing at the
plan, until the work lands.

- **File with a done-condition.** Say what command or observable would show the
  issue is resolved. An issue nobody could check is a discussion, not an issue.
- **Triage labels:** `ready` — triaged, done-condition present, anyone (human
  or agent) may pick it up; `design` — needs a design plan before work starts;
  `human-only` — needs the maintainer's judgment, the agent fleet must skip it.
  The GitHub defaults (`bug`, `enhancement`, `good first issue`, …) keep their
  usual meanings.
- **Issues close on evidence, not narration** — the same rule the kernel
  enforces on its agents. The normal close is a fixing commit or PR carrying
  `Fixes #N` in its body: GitHub closes the issue when it lands on `master`.
  A manual close happens only with the evidence attached (the maintainers use
  [`.claude/skills/issue-verify/`](.claude/skills/issue-verify/SKILL.md) for
  this).

## Maintenance reality

This is a young project with a very small maintainer set (currently one). That means:

- **Be patient** — review may take a while.
- **Open an issue before a large PR** so we can agree on the layer and approach before
  you spend the effort. A 50-line kernel PR that respects the layering is far more
  likely to merge than a 500-line one that blurs it.
- **The bar is correctness and layering, not volume.** A small, well-placed, tested
  change is the contribution this project wants most.

## Releasing

Releases are cut by the maintainer via the `/release` and `/stable-release` skills
(dev tooling that operates *on* the package — see `CLAUDE.md`). Contributors don't
need to touch versioning; just keep the suite green.

## License

By contributing, you agree your contributions are licensed under the repository's
license (see [`LICENSE`](LICENSE)).
