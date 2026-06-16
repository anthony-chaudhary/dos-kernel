# `dos_ext` — a copy-me example DOS extension

This is the "getting started" skeleton for hacking on DOS. Copy this directory,
rename it, and you have a working extension that:

1. **Adds a custom block reason** — purely as data, in `dos.toml` (no Python).
2. **Adds two custom renderers** — as code, via Python `entry_points`: `terse` (a
   coder's status bar) and `friendly` (a product's own non-coder verdict surface,
   overriding the built-in `plain`).
3. **Adds a custom admission predicate** — a safety hook, via a Python `entry_point`.
4. **Adds a custom judge** — an adjudicator for the JUDGE rung, via a Python `entry_point`.

None of these edits the `dos` package. That is the whole point: the kernel carries
the *mechanism*; your extension carries the *policy + presentation + adjudication*.

> **Install:** the package is `dos-kernel`, not `dos` (the bare `dos` name on PyPI
> is an unrelated squatter). The import + CLI stay `dos`; only the pip name differs.

## 1. The data axis — a block reason in `dos.toml`

`dos.toml` here declares one extra reason, `LANE_PARKED_FOR_BUDGET`. Run:

```bash
dos man wedge --workspace examples/dos_ext            # lists it alongside the built-ins
dos man wedge LANE_PARKED_FOR_BUDGET --workspace examples/dos_ext
```

It is now **emittable** (a producer may write `reason_class=LANE_PARKED_FOR_BUDGET`),
**verifiable** (the picker oracle resolves it to its category), **refusable**
(`is_refusal` honors its `refusal = true`), and **documented** (the man page
projects its `summary` / `fix` / `see_also`). You added a first-class kernel
concept by editing four lines of TOML.

## 2. The code axis — a renderer via `entry_points`

`dos_ext/renderer.py` defines `TerseRenderer` — a tiny alternative TUI output.
This directory is a real installable package: its `pyproject.toml` registers the
renderer under the `dos.renderers` entry-point group, so installing it makes
`dos --output terse` resolve it without the `dos` package importing it.

```toml
# pyproject.toml (already here)
[project.entry-points."dos.renderers"]
terse = "dos_ext.renderer:TerseRenderer"
```

```bash
pip install -e examples/dos_ext                       # registers `terse`
dos verify    --output terse PLAN PHASE               # the one-line terse form
dos arbitrate --output terse --lane main --kind cluster --leases '[]'
```

`TerseRenderer` implements the two required surfaces (`render_decision` /
`render_verdict`) plus **one optional surface** (`render_timeline`) to show the
partial-implementation pattern — a renderer overrides only what it cares about,
and `dos` falls back to the built-in `text` form for the surfaces it omits
(`render_man` / `render_decisions` here). The built-in `text`/`json` renderers
can never be shadowed, and an unknown `--output` fails loud. See
`docs/HACKING.md` §4 for the full contract.

### 2b. A renderer for a *non-coder* — customizing the verdict surface

DOS ships a **built-in** `plain` renderer — the zero-plugin non-coder floor:

```bash
dos verify --output plain  <PLAN> <PHASE>     # built-in, NO install needed
# -> "Not yet: 'P' isn't in what was built. The agent may have said it was done,
#     but it isn't in the project yet. Ask it to actually add 'P', then check again."
```

`dos_ext/friendly_renderer.py` is the next step: a dev team's **own** non-coder
surface (`--output friendly`) that *overrides* the built-in with their product's
exact wording and feature titles. It renders the **same `ShipVerdict`**; only the
words change. This is the worked example behind the strategy claim that the non-coder
verdict surface is just a `dos.renderers` plugin a dev team writes (no fork, kernel
unaware of it).

```bash
pip install -e examples/dos_ext
dos verify --output friendly  <PLAN> <PHASE>  # your product's own wording
```

Where `terse` prints `not-shipped P 1 [none]` for a coder's status bar, both `plain`
(built-in) and `friendly` (this example) render the same not-shipped verdict as a
plain end-user sentence. The relationship: `text`/`json`/`plain` are the
always-available built-ins (developer / machine / non-coder); `friendly` is the
*customized* non-coder variant a product ships.

Both encode the three disciplines that separate a trustworthy non-coder surface from
a confident-lie machine — and they are policy the **host** owns, not the kernel:

1. **Contrast, not the bare accusation.** A bare `NOT_SHIPPED (via none)` reads as
   an accusation or a broken tool; `plain` states the result and attaches a *way
   forward*, so "no" is a next step.
2. **Presence, not correctness.** A `plain` "yes" says *"it's in what was built"* and
   pointedly does **not** say *"it works"* — `dos verify`'s rung is presence, not
   goal (Wall §3). The kernel guarantees the verdict is honest about its own rung;
   the renderer must not over-claim past it.
3. **Hedge the weak rung.** When the verdict was reached only via a commit *subject*
   mentioning the phase (`source == "grep-subject"` — a known sharp edge where the
   deliverable may not really be built), `plain` lowers its confidence and says so,
   instead of passing a soft yes off as a hard one.

The division of labor is the framework one: the **kernel** computes a verdict that
is truthful about its evidence rung; the **host** (this plugin) chooses the words
for its audience. Swap in a real product's feature titles and tone, and this is the
surface a non-coder actually meets.

## 3. The judge axis — an adjudicator via `entry_points` (the JUDGE rung)

`dos_ext/judge.py` defines `KeywordJudge` — a trivial, zero-dependency occupant of
the **JUDGE rung** of DOS's trust ladder (ORACLE → JUDGE → HUMAN; see
`docs/87_the-adjudicator-trust-ladder.md`). It rules on a generic `Claim`
(`agree`/`disagree`/`abstain`) from the evidence alone — no model needed — to show
that a judge is just `rule(claim, config) -> JudgeVerdict`. The `pyproject.toml`
registers it under the `dos.judges` group:

```toml
[project.entry-points."dos.judges"]
keyword = "dos_ext.judge:KeywordJudge"
```

```bash
pip install -e examples/dos_ext                       # registers `keyword`
dos doctor --workspace examples/dos_ext               # lists it under "judges (JUDGE rung)"
# score it against the bundled labelled claims — the research instrument:
dos judge-eval --judge keyword --cases examples/dos_ext/cases.jsonl
```

The report's headline is the **false-clear rate** — of the claims the judge cleared,
how many were actually false (the dangerous cell). `KeywordJudge` is conservative
(it only AGREES on a positive artifact match), so its false-clear rate stays low.
A real judge — a debate, a learned verifier, a build/test oracle — replaces the body
of `rule`; the *shape* and the **fail-to-abstain** discipline are what to copy. See
`docs/HACKING.md` §6 for the full contract.

## 4. The host-policy axis — a whole DRIVER via `entry_points` (Axis 1)

`dos_ext/driver.py` defines `acme_config` — a host-policy DRIVER, the *whole config*
(its lanes, paths, facts) a host supplies, not just one axis. Where the renderer/predicate/
judge each plug into ONE seam, a driver assembles the entire `SubstrateConfig`. This was
the one seam that used to require forking the kernel (it loaded only via a hardcoded
`dos.drivers.<name>` import); now `acme` ships HERE, in a third-party package, registered
under the `dos.drivers` group:

```toml
[project.entry-points."dos.drivers"]
acme = "dos_ext.driver:acme_config"
```

```bash
pip install -e examples/dos_ext                       # registers `acme`
dos --driver acme --workspace /some/repo doctor       # resolves the acme lanes (mobile/cloud/ship)
dos plugins                                           # lists `acme` under dos.drivers (third-party)
```

In-tree packs (`workshop`/`job`) resolve FIRST and are unshadowable — a third-party `acme`
is found only after the in-tree miss, the same built-in-first contract every seam shares.
The factory gathers workspace facts so the SELF_MODIFY guard is workspace-scoped (the
kernel also backfills this, but a well-formed pack gathers its own). See `docs/HACKING.md`
§"The driver itself".

## 5. The MCP-surface axis — a tool/verb via `entry_points` (`dos.mcp_tools`)

`dos_ext/mcp_tools.py` defines `register(mcp)` — it ADDS an MCP tool (`acme_lane_hint`) to
the `dos` MCP server a host talks to, with no fork. `register` is handed the running server
and calls `mcp.tool()` itself, so its tool gets the same call-deadline + deep-answer
wrapping the built-in `dos_*` tools get. Registered under the `dos.mcp_tools` group:

```toml
[project.entry-points."dos.mcp_tools"]
acme = "dos_ext.mcp_tools:register"
```

```bash
pip install -e "examples/dos_ext" && pip install "dos-kernel[mcp]"   # the server extra
# the `dos` MCP server now exposes `acme_lane_hint` alongside the built-in dos_* tools
```

The seam is ADDITIVE — a plugin adds a verb, never replaces a built-in — and a broken
plugin is skipped, never crashing the server.

## The rule of thumb

- **Data** (reasons, lanes, paths) → declare in `dos.toml`.
- **Behavior** (renderers, admission predicates) → ship as code via `entry_points`.
- **Adjudication** (judges, the JUDGE rung) → ship as code via `entry_points`
  (`dos.judges`); measure it with `dos judge-eval`.
- **A whole host pack** (lanes/paths/facts) → ship as a `dos.drivers` driver
  (`dos --driver <name>`); in-tree packs are unshadowable.
- **An MCP tool/verb** → ship as a `dos.mcp_tools` `register(mcp)` plugin.

Run `dos plugins` to see every seam, its built-in floor, and what you've installed; run
`dos plugin new <seam> --name <yours>` to scaffold any of them.
