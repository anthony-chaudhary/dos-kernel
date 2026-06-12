# 308 — the stability + deprecation policy: a written promise a consumer can plan against

> Ecosystems form around STATED promises, not stable-so-far behavior (the
> trio/attrs/pluggy lesson from the 2026-06-12 repos-to-learn-from sweep). DOS
> has the contract discipline — litmus tests, closed vocabularies, conjunctive
> floors — but no published promise: a plugin author today cannot know what they
> may depend on. This plan ships the prose layer: `docs/STABILITY.md` (which
> surfaces are covered, what SemVer means for each, how a deprecation is
> announced and how long it warns, and an attrs-style list of what will never
> break), plus the one piece of kernel machinery the promise needs — a typed
> warning category (`dos.deprecation.DosDeprecationWarning`) so a consumer can
> filter, test for, or `-W error` exactly OUR deprecations and nobody else's.
> Tracking issue [#67](https://github.com/anthony-chaudhary/dos-kernel/issues/67).
> Complements [#61](https://github.com/anthony-chaudhary/dos-kernel/issues/61)
> (docs/306: the conformance suite PROVES the seam laws in a stranger's CI) and
> [#63](https://github.com/anthony-chaudhary/dos-kernel/issues/63) (the version
> handshake ENFORCES the floor at resolve time) — this is the promise both
> point at.

*Status: SHIPPING — P1–P3 below. The doc is layer-free prose; the warning
category is a new kernel leaf (`src/dos/deprecation.py`, stdlib only, pure —
not in the T1 runtime set). No existing surface is deprecated by this plan;
it installs the vehicle, it does not drive anywhere yet.*

## 0. The decisions (the issue said "the plan decides")

1. **A standalone `docs/STABILITY.md`, not a HACKING.md section.** A promise a
   consumer pins a dependency on deserves its own stable URL; HACKING.md is a
   how-to that *names* it. It is indexed in `llms.txt` (an arriving agent
   deciding "may I build on this?" needs the promise in the one-fetch story),
   which means `llms-full.txt` is rebuilt — the issue's done-condition.

2. **The covered seam roster is FOURTEEN entry-point groups, not five.** Issue
   #67 (written from #63's framing) says "the five seam Protocols"; the seam
   surface has since grown. A hand-kept five-row list would rot exactly the way
   the CLAUDE.md module roster rotted — so the doc carries the full roster and
   a test pins it against the source of truth: the `*_ENTRY_POINT_GROUP`
   constants under `src/dos/` plus the two seams that select their group
   inline (`dos.hook_dialects`, `dos.hook_installs`). Doc roster == code
   roster, both directions, or the suite is red.

3. **Three tiers, in the doc's own words:**
   - **Frozen — will never change** (the attrs-style list, valid at every
     version including 0.x): refuse-more composition (no extension seam will
     ever be able to loosen a deterministic floor); non-forgeability (no
     verdict will ever flip conservative→permissive on claimant-authored bytes
     alone); exit-code polarity (a refusal is never exit 0); the PyYAML-only
     required dependency set (capability grows only via extras); vendor-blind
     adjudication; closed-vocabulary members are never silently re-meant.
   - **Stable — SemVer-governed, the deprecation window applies**: the Python
     syscall ABI (the documented verdict entry points), the closed verdict
     vocabularies, CLI verb names + documented flags + exit codes + `--json`
     top-level keys (additive-only between breaks), hook-dialect output bytes,
     the fourteen seam Protocols (group names + method signatures), the
     `dos.toml` schema (additive), and the deprecation machinery itself.
   - **Internal — no promise**: underscore names/modules, driver internals
     behind their registered entry points, prose docs, the skill-pack texts —
     and everything not named Stable or Frozen. Default-internal is what keeps
     the Stable list honest.

4. **What SemVer means here, honestly, at 0.x.** Strict SemVer says 0.x
   promises nothing; this policy is deliberately stronger: a PATCH release
   never removes or re-means a Stable surface; a breaking change to a Stable
   surface rides the deprecation window and lands only in a MINOR (at 1.0+,
   only in a MAJOR). One stated exception, because this is a trust substrate:
   a fix required to keep the conservative direction (a floor found loosenable,
   a verdict found forgeable) may land in any release — safety outranks
   compatibility — and the release notes must flag it loudly.

5. **The deprecation process**: the deprecating release keeps the surface
   working and every use emits `DosDeprecationWarning` (via the one sanctioned
   helper, `dos.deprecation.warn_deprecated`) naming the surface, the version
   it is deprecated since, the earliest removal version, and the replacement;
   the release notes say the same. The window is **at least two minor
   releases**. The removing release names the removal in its notes.

6. **The typed category**: `class DosDeprecationWarning(DeprecationWarning)`.
   Subclassing `DeprecationWarning` keeps Python's default visibility rules
   (hidden in production, surfaced by pytest and `-W`); the subclass is what
   lets a consumer target ours precisely
   (`filterwarnings("error", category=DosDeprecationWarning)`). Re-exported
   from `dos`. `warn_deprecated()` is the only sanctioned emission path, so
   "every DOS deprecation carries the documented category and message shape"
   is true by construction — and pinned by test.

## Phase 1: the kernel leaf — `dos.deprecation`

`src/dos/deprecation.py`: `DosDeprecationWarning` + `warn_deprecated(subject,
*, since, remove_in, instead=None, stacklevel=2)` composing the message
`"<subject> is deprecated since dos-kernel <since> and will be removed in
<remove_in>[; use <instead> instead]"`. Stdlib only, no I/O, no host names.
Re-export both names from `dos/__init__.py`.

**Done when:** `tests/test_deprecation.py` pins: the category is a
`DeprecationWarning` subclass importable from both `dos` and
`dos.deprecation` (same object); `warn_deprecated` emits exactly one warning
of exactly that category; the message carries subject/since/remove_in/instead;
the default `stacklevel` attributes the warning to the deprecated surface's
CALLER (the consumer's own line — the attrs/numpy convention), not to the
deprecated body or the helper.

## Phase 2: the promise — `docs/STABILITY.md`

The doc per §0 decisions 2–6, written in plain Feynman English (the operator
directive): say what is promised before naming the mechanism, one idea at a
time. The seam-roster table carries all fourteen groups with their Protocol
and home module.

**Done when:** `tests/test_stability_policy.py` pins: the doc's `dos.*` group
roster equals the code's (the constant scan + the inline-select scan, both
directions); the doc names `dos.deprecation.DosDeprecationWarning` (the dotted
path of the real, importable category) and the two-minor-release window.

## Phase 3: name it from the front doors

README (via `docs/readme/90_extending-and-docs.md`, then rebuild), AGENTS.md
(the rules section — the promise is rule-shaped), docs/HACKING.md (the
`entry_points` plugin model — what a plugin may depend on), docs/README.md
(the guides table), and `llms.txt` (The kernel section), then rebuild
`llms-full.txt`. The naming assertions join `tests/test_stability_policy.py`.

**Done when:** each of the five entry docs names `STABILITY.md` (pinned);
`scripts/build_readme.py --check` and `scripts/build_llms_full.py --check`
pass (the existing assembly pins).
