# 331 — the plugin version handshake: refuse a plugin built for a kernel you don't have

> A plugin says "I extend DOS." Today the kernel takes it at its word. It loads
> the plugin the first time someone resolves it, and if the plugin was written
> against a newer kernel — a method the old kernel never calls, a verdict shape
> the old kernel can't read — the failure shows up late, as a crash or a silent
> wrong answer, at the worst possible moment: inside a resolve the operator
> asked for. The proven fix, used by pre-commit (`minimum_pre_commit_version`)
> and Sphinx (`needs_extensions`), is a **declared minimum host version the host
> checks before it trusts the plugin**. This plan gives DOS that check, plus the
> matching pre-flight sweep (pluggy's `check_pending`): `dos lint` loads every
> registered plugin across the seam roster and validates it against its seam
> Protocol, so a "registered but broken" plugin is found at lint time, not at
> first resolve. Tracking issue
> [#63](https://github.com/anthony-chaudhary/dos-kernel/issues/63). It enforces,
> at resolve time, the floor that [docs/308](308_stability-and-deprecation-policy-plan.md)
> (the stability promise) and [#61](https://github.com/anthony-chaudhary/dos-kernel/issues/61)
> (docs/306, the conformance suite) only state and prove.

*Status: PLAN — not yet implemented. Lane: src (the seam resolvers) + a new
kernel leaf. The version-compat check is a new pure leaf (`src/dos/seam_compat.py`,
stdlib only); the per-seam resolvers gain one call to it. The resolvers
(`judges.py`, `admission.py`, `notify.py`, …) are NOT in the T1 runtime set, but
`admission.py` IS — so the predicate-seam wiring (Phase 2) needs the operator's
SELF_MODIFY override window, and is split into its own phase for that reason.*

## 0. The decisions (the issue said "the plan decides")

1. **The roster is whatever the code declares, never a hardcoded "five."** The
   issue and #67 say "the five entry-point seams"; that count is already stale —
   [docs/308 §2](308_stability-and-deprecation-policy-plan.md) pins the live seam
   roster at fourteen-plus, derived from the `*_ENTRY_POINT_GROUP` constants
   under `src/dos/` (thirteen today: `dos.predicates`, `dos.judges`,
   `dos.notifiers`, `dos.overlap_policies`, `dos.renderers`, `dos.exporters`,
   `dos.evidence_sources`, `dos.enforce_handlers`, `dos.memory_stores`,
   `dos.log_sources`, `dos.plan_sources`, `dos.scope_sources`,
   `dos.stop_policies`) plus the inline-select seams (`dos.hook_dialects`,
   `dos.hook_installs`). This plan touches every seam that resolves a plugin by
   name, and a test pins the handshake's covered-group list against the SAME
   source of truth docs/308 uses (the constant scan), so the roster cannot rot.
   No phase writes the literal string "five."

2. **The declaration lives in the plugin's distribution metadata, not in a new
   manifest file.** A plugin already ships a `pyproject.toml` whose
   `[project.entry-points."dos.<seam>"]` registers it. The minimum-kernel-version
   floor rides the same distribution: the plugin sets
   `[project.optional-dependencies]`-style intent as a metadata key the kernel
   reads via `importlib.metadata` — concretely, the plugin declares
   `Requires-Dist: dos-kernel>=X` (the standard, already-enforced-by-pip way) AND,
   for the kernel's own resolve-time check, an entry-point-adjacent
   `dos-min-kernel = "X.Y.Z"` read from the distribution's metadata. We do NOT
   invent a `gemini-extension.json`-style sidecar: the cheapest declaration is the
   one the author already maintains. (The capability-bool layer the issue
   mentions as "later" stays out of v1 — §0.6.)

3. **A mismatch is a typed ABSTAIN-shaped outcome, never a crash and never silent
   acceptance.** The kernel's whole posture is "an unannounced substitution is a
   refusal." So when a plugin declares a minimum kernel version ABOVE the running
   kernel, the resolver does not load it: it skips it the way
   `_discover_entry_point_judges` already skips a plugin that fails to load (a
   one-line stderr note), and `resolve_<seam>(name)` for that name falls to the
   **built-in default** (`abstain` for judges, the disjointness-only predicate set
   for admission, the text renderer, …). The caller gets the conservative,
   trusted fallback — the same outcome as "no such plugin" — plus a typed reason
   it can read, never a half-initialized plugin object.

4. **The refusal carries a reason from the closed vocabulary.** The skip is not
   free-text. It declares a new `reason_class` — `SEAM_VERSION_FLOOR` — in the
   `dos.toml [reasons]`-shaped registry (the seam-data layer), so a downstream
   reader routes "this plugin was refused because it needs a newer kernel"
   distinctly from "this plugin is broken" or "no such plugin." `dos man wedge
   SEAM_VERSION_FLOOR` explains it and names the fix (upgrade the kernel, or pin
   the plugin to a compatible release).

5. **The pre-flight sweep is a `dos lint` finding, not a new verb.** `dos lint`
   already loads the config and emits typed `Finding`s (`config_lint.lint`). The
   new finding — kind `seam-plugin`, severity `warn` (a broken plugin does not
   break the kernel, it just won't serve) — loads every REGISTERED occupant across
   the roster and checks two things: (a) its declared minimum kernel version is
   satisfied; (b) it satisfies its seam's `Protocol` (the methods the seam calls
   exist with the right shape — `runtime_checkable` `isinstance` plus a
   signature spot-check). A registered-but-broken plugin becomes a lint finding
   instead of a first-resolve surprise. `dos lint --json` carries it in the
   existing `findings` array.

6. **What v1 does NOT ship (stated, so the floor stays honest).** No capability
   bools (the Sphinx `needs_extensions`-style feature flags) — the min-version
   field is the cheapest 80%; bools are a later issue once a real
   forward-incompatible method lands. No MAXIMUM version pin (a plugin claiming
   "I only work up to kernel X" is the plugin author over-constraining; we honor a
   floor, not a ceiling, matching pip's `>=` convention). No auto-upgrade
   suggestion beyond the wedge text. These are named here so a future reader does
   not mistake their absence for an oversight.

## Phase 1: the kernel leaf — `dos.seam_compat`

`src/dos/seam_compat.py`: one pure module the resolvers and the lint sweep both
call.

- `kernel_version() -> str` — the running kernel version (`dos.__version__`),
  wrapped so a test can inject a version.
- `read_min_kernel(dist_name) -> str | None` — boundary I/O: read the
  `dos-min-kernel` declaration from a distribution's metadata via
  `importlib.metadata`, or None (no declaration = no floor = always compatible,
  the conservative default — an un-versioned plugin behaves exactly as today).
- `satisfies(min_required, running) -> bool` — PURE version compare, tolerant of
  the PEP 440 spellings a hand-typed floor takes (`1.2`, `1.2.0`, `1.2.0rc1`);
  an unparseable floor fails CLOSED (treated as "cannot prove compatible" →
  refuse), never crashes.
- `compat_verdict(dist_name, *, running=None) -> SeamCompatVerdict` — folds the
  three: `{compatible: bool, min_required: str|None, running: str, reason:
  str}`. `compatible=True` with `min_required=None` is the un-versioned plugin;
  `compatible=False` carries the `SEAM_VERSION_FLOOR` reason.

Stdlib only (`importlib.metadata`, `packaging.version` is already a transitive
dep via pip; if absent, a vendored tuple-split compare is the fallback). No host
names, no I/O outside `read_min_kernel`. The verdict is `classify(evidence,
policy)` — version facts in, a typed verdict out — the kernel's own shape.

**Done when:** `tests/test_seam_compat.py` pins: a plugin declaring a floor at or
below the running kernel is `compatible`; a floor above is incompatible with the
`SEAM_VERSION_FLOOR` reason; a plugin with NO declaration is compatible
(min_required None); an unparseable floor fails closed (incompatible, not a
crash); `satisfies` agrees with `packaging` on the PEP 440 spellings.

## Phase 2: wire the check into the by-name resolvers

Each seam's `_discover_entry_point_<X>()` already iterates entry points and skips
a load failure with a stderr note. Add ONE step before `ep.load()`: resolve the
entry point's distribution, call `seam_compat.compat_verdict(dist)`, and if
incompatible, skip with the typed `SEAM_VERSION_FLOOR` note INSTEAD of loading —
so `resolve_<X>(name)` falls to the built-in default exactly as it does for an
unknown name. The discovery helpers are the single seam to touch; `resolve_<X>`
and `active_<X>` are unchanged (they consume the filtered list).

Two sub-phases, because one resolver lives in a T1 file:

- **2a (non-T1, ships first):** every seam whose resolver is NOT in
  `_DISPATCH_RUNTIME_FILES` — `judges.py`, `notify.py`, `overlap_policy.py`,
  `render.py`, `exporter.py`, `evidence.py`, `enforce.py`, `memory_stores.py`,
  `log_source.py`, `plan_source.py`, `scope_source.py`, `stop_policy.py`, and the
  inline-select hook-dialect/install seams. A shared helper in `seam_compat`
  (`filter_compatible(eps) -> (kept, skipped)`) keeps the per-resolver edit to one
  line, so the thirteen edits are mechanical and identical.
- **2b (T1, needs the operator's override window):** `admission.py`'s
  `_discover_entry_point_predicates` is in the T1 runtime set (the admission
  kernel). Its one-line edit is the same shape as 2a's, but the SELF_MODIFY hook
  denies it inside a live loop — so it lands in its own commit under an
  operator-armed `.dos/override/self-modify.toml` window (docs/296), exactly the
  gate #118 hit. Splitting it out keeps 2a shippable without the override.

**Done when:** `tests/test_seam_compat_wiring.py` pins, for at least the judge
seam (the canonical JUDGE rung) and one more non-T1 seam: a registered scratch
plugin declaring a minimum kernel version ABOVE the installed kernel resolves to
the **built-in default** (the plugin does NOT serve), with the
`SEAM_VERSION_FLOOR` note on stderr; a plugin declaring a floor AT or BELOW the
kernel still resolves and serves; an un-versioned plugin is unaffected. 2b adds
the admission-seam assertion once its commit lands.

## Phase 3: the pre-flight sweep — `dos lint` finds the registered-but-broken plugin

A new check in `config_lint` (or a thin sibling it calls) that loads every
registered occupant across the seam roster and emits a `Finding` per problem:

- kind `seam-plugin`, severity `warn`;
- `subject` = the `group:name` of the plugin;
- `detail` = either "declares min kernel X > running Y (SEAM_VERSION_FLOOR)" or
  "does not satisfy the <group> Protocol (missing/mis-shaped <method>)";
- `fix` = "upgrade dos-kernel to >=X" or "the plugin must implement <method>".

The Protocol check uses each seam's `runtime_checkable` Protocol
(`isinstance(obj, SeamProtocol)`) plus a signature spot-check of the seam's one
or two called methods — pluggy's `check_pending`, adapted: validate every
REGISTERED hook against its spec up front. The roster the sweep walks is the
docs/308 source-of-truth scan, so adding a seam automatically extends the sweep.

**Done when:** `tests/test_lint_seam_plugins.py` pins: `dos lint --json` reports a
`seam-plugin` finding for a registered scratch entry point that fails Protocol
validation (a class missing the seam's required method); a finding for one whose
declared min kernel exceeds the running version; NO finding for a well-formed
compatible plugin; the finding rides the existing `findings` array and `counts`
(so no `--json` schema break — additive only, per docs/308's Stable promise).

## Phase 4: name it from the contract docs

The handshake is a promise a plugin author depends on, so it joins the stability
surface: `docs/HACKING.md` (the `entry_points` plugin model — "declare
`dos-min-kernel` and the kernel will refuse to load you against an older host"),
`docs/STABILITY.md` (the version-handshake row, once docs/308 ships it), and the
`SEAM_VERSION_FLOOR` wedge text (`dos man wedge`). The seam roster reference
points at the docs/308 table rather than restating it (cross-link by filename,
never duplicate — the CLAUDE.md privacy/duplication rule).

**Done when:** `dos man wedge SEAM_VERSION_FLOOR` renders the reason and its fix;
`docs/HACKING.md` names the declaration; `scripts/build_readme.py --check` and
the llms assembly checks pass if any indexed doc changed.

## Why this is the right shape (the one-paragraph defense)

The kernel never trusts a worker's self-report; a plugin is a worker, and "I am
compatible" is a self-report. The handshake turns it into a CHECKED claim:
declared floor in, version compare, typed refuse-to-built-in-default out — the
same `classify(evidence, policy)` the rest of the kernel is. It refuses MORE
(an incompatible plugin no longer loads) and never less, so it cannot widen what
a plugin may do. It names no vendor and hardcodes no count. And it fails the safe
way at every branch: no declaration → compatible (today's behavior); unparseable
floor → refuse; broken Protocol → a warn finding, not a crash. The expensive part
(loading + validating every plugin) lives in `dos lint`, off the hot resolve
path; the hot path gains one metadata read per discovered entry point, cached by
`importlib.metadata`.
