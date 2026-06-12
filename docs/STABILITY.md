# Stability & deprecation policy — what you may depend on

> This file is a promise, not a description. It says which surfaces of
> `dos-kernel` you can build against, what a version number tells you about
> each, how a deprecation is announced and how long it keeps working, and a
> short list of things that will never change at any version. If a release
> contradicts this file, the release is the bug — file an issue.

DOS asks other people's agents to be held to their word. The same rule
applies to us: a consumer or plugin author should not have to read stable-so-far
behavior out of the git history; they should be able to read a stated promise.
This is that statement. (Two siblings make it enforceable rather than
aspirational: the conformance suite of docs/306 lets a plugin's own CI prove
the seam safety laws, and the litmus tests in [CLAUDE.md](../CLAUDE.md) pin
the architecture rules in this repo's CI. This file is the prose layer both
point at — docs/308, issue #67.)

## The three tiers

Every public surface is in exactly one tier:

- **Frozen** — will never change, at any version. The short list below.
- **Stable** — governed by the version number and the deprecation process.
  If it is named in the "What is Stable" section, you may depend on it.
- **Internal — no promise.** Everything not named here: any name starting
  with an underscore (`dos._tree`, a `_helper()`), a driver's internals
  behind its registered entry point, the prose docs, the skill-pack texts,
  test helpers. Internal surfaces may change in any release without notice.
  Default-internal is what keeps the Stable list honest.

## Frozen — what will never break

These hold at every version, including every 0.x. Each is enforced by a
litmus test in this repo's suite today; the promise is that the test never
leaves.

1. **An extension can only refuse MORE, never admit more.** Every plugin seam
   sits under a deterministic floor: a judge that raises or returns junk
   yields ABSTAIN, never AGREE (`run_judge`); an overlap policy is AND-ed
   under the unforgeable prefix-disjointness floor, so a lying-admit policy
   cannot admit a colliding pair (`admissible_under_floor`); admission
   predicates are conjunctive-only; a raising notifier yields a non-delivered
   result, never a crashed producer (`send_safely`); a raising plan source
   yields no rows, never a crash. A buggy or hostile plugin degrades to the
   built-in conservative behavior — it cannot loosen safety.
2. **No verdict believes the claimant.** A verdict never flips from its
   conservative outcome to its permissive outcome on bytes only the claimant
   authored. `dos verify` answers from git evidence, with no plan files and
   no registry required — that zero-config floor stays.
3. **Exit-code polarity.** Exit `0` is the clean/admitted/pass outcome. A
   refusal, block, or caught lie is never exit `0`.
4. **The dependency floor.** `pip install dos-kernel` requires Python and
   PyYAML, nothing else. Capability beyond that arrives only through opt-in
   extras (`[mcp]`, `[tui]`, …). The required set never grows.
5. **Vendor-blind adjudication.** No kernel decision path branches on which
   vendor or host is acting. A hook dialect is output formatting, chosen
   downstream of an already-decided verdict.
6. **Closed vocabularies are never re-meant.** A shipped verdict or reason
   member never silently changes meaning, and is never reused to mean
   something else. Members are added; removal goes through the deprecation
   process like any Stable break.

## What is Stable

- **The Python syscall ABI.** The verdict entry points the
  [README](../README.md) syscall table and [CLAUDE.md](../CLAUDE.md)'s
  "syscall ABI" table name (`verify`, `arbitrate`, `liveness`,
  `productivity`, `efficiency`, `improve`, `resume`, `reward`, the picker
  substrate, …): their importable locations, call signatures, and verdict
  return types. New keyword-only parameters with safe defaults may be added
  in a minor; anything else is a Stable break.
- **The closed verdict vocabularies.** The member sets of the shipped verdict
  enums (SHIPPED/NOT_SHIPPED, ADVANCING/SPINNING/STALLED, KEEP/REVERT/ESCALATE,
  …) and the base reason registry. Additive growth only, except through the
  deprecation process.
- **The CLI.** Verb names, documented flags, documented exit codes, and the
  top-level keys of every documented `--json` output. JSON output is
  additive: existing keys keep their name, type, and meaning; new keys may
  appear in any release, so a consumer must tolerate unknown keys.
- **Hook-dialect bytes.** For a given dialect and verdict, the rendered hook
  output a host runtime parses. These bytes are an interface to software we
  do not control; changing them is a Stable break even when it looks
  cosmetic.
- **The plugin seams.** For each entry-point group below: the group name, the
  contract type a registered occupant must satisfy (the Protocol's method
  names and signatures, or the spec dataclass's fields), and by-name
  resolution with the listed built-in default still serving when a named
  occupant is missing or broken. This roster is pinned against the source by
  `tests/test_stability_policy.py` — if the code grows a seam this table does
  not name, the suite goes red.

  | Entry-point group | Contract | Kernel seam module |
  |---|---|---|
  | `dos.judges` | `Judge` Protocol | `dos.judges` |
  | `dos.predicates` | `AdmissionPredicate` Protocol | `dos.admission` |
  | `dos.overlap_policies` | `OverlapPolicy` Protocol | `dos.overlap_policy` |
  | `dos.notifiers` | `Notifier` Protocol | `dos.notify` |
  | `dos.hook_dialects` | `HookDialect` Protocol | `dos.hook_dialect` |
  | `dos.renderers` | `Renderer` Protocol | `dos.render` |
  | `dos.stop_policies` | `StopPolicy` Protocol | `dos.stop_policy` |
  | `dos.plan_sources` | `PlanSource` Protocol | `dos.plan_source` |
  | `dos.exporters` | `Exporter` Protocol | `dos.exporter` |
  | `dos.evidence_sources` | `EvidenceSource` Protocol | `dos.evidence` |
  | `dos.log_sources` | `LogSource` Protocol | `dos.log_source` |
  | `dos.scope_sources` | `ScopeSource` Protocol | `dos.scope_source` |
  | `dos.enforce_handlers` | `EnforcementHandler` Protocol | `dos.enforce` |
  | `dos.hook_installs` | `HostHookSpec` spec | `dos.hook_install` |

- **The `dos.toml` schema.** A declared key keeps its meaning; new keys are
  additive; an unknown key keeps failing loud where it does today.
- **The deprecation machinery itself.** `dos.deprecation.DosDeprecationWarning`
  and `dos.deprecation.warn_deprecated` (both re-exported from `dos`).

## What the version number means

The current line is 0.x. Strict SemVer reads 0.x as "anything may change";
this policy is deliberately stronger:

- **PATCH** (`0.25.0 → 0.25.1`): never removes, renames, or re-means a Stable
  surface. Fixes and additions only.
- **MINOR** (`0.25 → 0.26`): may add Stable surface, and may REMOVE a surface
  only if its deprecation window (below) has fully elapsed.
- At **1.0+**, removal moves to MAJOR releases only; minors become purely
  additive.

**The one stated exception — safety outranks compatibility.** DOS is a trust
substrate. If a Stable surface is found to loosen a deterministic floor or
make a verdict forgeable (a Frozen guarantee at risk), the fix lands in the
next release of any size, without a window. The release notes must flag it
loudly as a safety break. This exception cannot be used to remove something
for convenience — it applies only when keeping compatibility would mean
keeping a hole in a Frozen guarantee.

## How a deprecation happens

1. **The deprecating release** keeps the surface working. Every use emits a
   `DosDeprecationWarning` — via the one sanctioned helper,
   `dos.deprecation.warn_deprecated` — whose message names the surface, the
   version that deprecated it, the earliest version that may remove it, and
   the replacement when one exists. The release notes say the same.
2. **The window:** at least **two minor releases**. A surface deprecated in
   0.25.x may be removed no earlier than 0.27.0.
3. **The removing release** names the removal in its release notes.

`DosDeprecationWarning` subclasses `DeprecationWarning`, so Python's default
visibility rules hold (quiet in production, surfaced by pytest and `-W`).
Because it is OUR subclass, you can hold us to this file mechanically:

```python
import warnings
from dos import DosDeprecationWarning

warnings.filterwarnings("error", category=DosDeprecationWarning)
# CI now fails the moment your code touches a deprecated DOS surface —
# and nothing else's deprecations can trip it.
```

## How this file stays true

A promise document rots unless something pins it. `tests/test_stability_policy.py`
pins this file's seam roster to the entry-point groups actually declared in
the source (both directions), and pins that the warning category documented
here is the real, importable one. The conformance suite (docs/306) is the
out-of-tree half: a plugin's own CI can prove the Frozen seam laws against
the `dos-kernel` version it actually installed.
