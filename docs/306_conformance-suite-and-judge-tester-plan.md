# 306 — `dos.testing`: the conformance suite + `JudgeTester` — the seam safety laws, provable in a stranger's CI

> A third-party plugin meets the kernel's safety laws only at runtime today.
> The laws are real — `run_judge` turns any judge failure into ABSTAIN, the
> overlap floor ANDs every scorer under unforgeable prefix disjointness,
> `send_safely` turns any transport raise into a non-delivered result — but a
> plugin author has no way to PROVE, in their own CI, that their occupant
> composes under them. This plan ships `dos.testing`: an importable conformance
> suite (subclass one class, override one factory, pytest does the rest) plus a
> `JudgeTester` micro-harness (write (claim, expected-stance) tables, get the
> hostile cases auto-run for free). The suite runs the KERNEL's invariants
> inside the PLUGIN's checkout — this repo never sees their code. SQLAlchemy
> solved exactly this for dialects (`from sqlalchemy.testing.suite import *`);
> ESLint's `RuleTester` is the authorship-bootstrap half. Tracking issue
> [#61](https://github.com/anthony-chaudhary/dos-kernel/issues/61), from the
> 2026-06-12 repos-to-learn-from sweep.

*Status: SHIPPING — P1–P3 below. Kernel-layer leaf (`src/dos/testing/`), pure
stdlib + sibling kernel imports; no host names, no vendor names, no pytest
import (plain classes + `assert`, so any test runner collects them and the
kernel's dependency set stays PyYAML-only).*

## 0. The laws the suite makes provable

Each seam already enforces its law for in-tree occupants; the suite re-states
each as a test a stranger's CI can run against THEIR occupant and THEIR
installed `dos-kernel` version:

| Seam | The law | Enforced by | Suite class |
|---|---|---|---|
| `dos.judges` (docs/86) | fail-to-ABSTAIN, never fail-to-AGREE: a judge that raises or returns junk cannot clear a claim | `run_judge` | `JudgeConformance` |
| `dos.overlap_policies` (docs/113) | a scorer can only refuse-MORE: `admit ⟺ floor.admissible AND policy.admissible`, so a lying-admit policy cannot admit a floor-refused pair — including through the real `arbiter.arbitrate` | `admissible_under_floor` | `OverlapPolicyConformance` |
| `dos.notifiers` (docs/225) | fail-SOFT: a raising transport yields a non-delivered `NotifyResult`, never a crashed producer | `send_safely` | `NotifierConformance` |

Two kinds of test per class, deliberately:

1. **Laws about THEIR occupant** — it names itself, satisfies the Protocol,
   returns the kernel's verdict type on benign input, and never escapes the
   kernel's safety wrapper on a hostile-input battery (huge strings, control
   characters, empty fields).
2. **Laws about the INSTALLED KERNEL, proven in their environment** — the
   suite ships hostile doubles (`RaisingJudge`, `JunkReturnJudge`,
   `LyingAdmitPolicy`, `RaisingOverlapPolicy`, `JunkReturnOverlapPolicy`,
   `RaisingNotifier`, `JunkReturnNotifier`) and auto-runs them through
   `run_judge` / `admissible_under_floor` / `arbitrate` / `send_safely`. This
   is the SQLAlchemy value: if the plugin pins a `dos-kernel` version whose
   floor were ever broken, THEIR CI catches it — version drift between the
   contract's prose and the installed wheel becomes a red build, not a
   runtime surprise.

The junk-return doubles are traps on purpose: `JunkReturnJudge` returns an
object whose `.agreed` is `True` but which is NOT a `JudgeVerdict` — pinning
that the kernel never reads a foreign object's attributes (the
"no false-clear through a wrong return type" line in `run_judge`).

## 1. The shape — subclass, override one factory

```python
# their_plugin/tests/test_conformance.py
from dos.testing.suite import JudgeConformance
from their_plugin import TheirJudge

class TestTheirJudgeConformance(JudgeConformance):
    def make_judge(self):
        return TheirJudge()
```

The base classes are NOT named `Test*`, so importing them collects nothing;
the plugin's `Test*` subclass is what pytest runs. Each base has exactly one
required factory (`make_judge` / `make_policy` / `make_notifier`) and one
optional hook (`make_config`, default `None` — every built-in ignores it; a
judge that reads real config overrides it). A notifier factory should return
the occupant in its unconfigured / dry-run form — conformance sends synthetic
notifications and must not deliver anywhere real.

`JudgeTester` is the table half:

```python
from dos.judges import Claim
from dos.testing import JudgeTester

JudgeTester(TheirJudge()).run(
    agree=[Claim("phase P1 shipped", evidence=("commit abc1234",))],
    disagree=[Claim("phase P2 shipped", evidence=("",))],
    abstain=["no evidence either way"],          # a bare str is claim_text
)
```

`run()` adjudicates every table row through `run_judge` (the supported call
path — so a judge that raises on a row expected AGREE fails the table as
ABSTAIN, the honest report), then auto-runs the hostile battery, and raises
ONE `AssertionError` listing every failed case. Plain `AssertionError`, no
pytest types — it works under any runner.

## 2. Where it sits in the layering

Kernel layer (row 1): `src/dos/testing/` imports stdlib + sibling kernel
modules (`judges`, `overlap_policy`, `notify`, `lane_overlap`, `admission`,
`self_modify`, `arbiter`, `config`) and nothing else — no host, no vendor, no
I/O at law-check time (the one boundary touch is `default_config(".")` inside
the arbiter-level case, which is test-time, not verdict-time). Nothing under
`src/dos/` imports `dos.testing`; the arrow points the usual way (consumers
import it). The doubles are NOT registered under any entry-point group — they
are importable fixtures, never discoverable occupants, so they can never leak
into a real resolver walk.

Out of scope, deliberately: conformance classes for `dos.predicates`,
`dos.hook_dialects`, `dos.evidence_sources`, `dos.exporters`, `dos.renderers`,
`dos.plan_sources`, `dos.stop_policies` — the same pattern extends to each,
but #61's done-condition names the judge/policy/notifier trio, and a smaller
shipped surface beats a wider unshipped one. A follow-up issue per seam when
demand shows up.

## 3. Phases

### Phase 1 — the `dos.testing` package + the in-tree pin

`src/dos/testing/{__init__,doubles,suite,tester}.py` as designed above, plus
`tests/test_conformance_suite.py`: the suite run against the built-in
occupants (`AbstainJudge`, the shipped `llm` judge — which abstains with no
provider wired, `PrefixOverlapPolicy`, `NullNotifier`), the `JudgeTester`
table/failure-message pins, the three done-condition bullets as named tests,
and an AST hygiene check that no module under `src/dos/testing/` imports
pytest or any third-party package (the near-stdlib promise, pinned for the
new subpackage).

**Done when:** the new test file is green and the full kernel suite stays
green.

### Phase 2 — the scratch-plugin trio + the out-of-tree proof

`examples/conformance_plugins/` — three minimal, installable plugin packages
(one per kind: an evidence-count judge, a basename-strictness overlap policy,
a collecting notifier), each with its own `pyproject.toml` (depending on
`dos-kernel`, registering its entry point) and its own
`tests/test_conformance.py` that subclasses the suite AND asserts by-name
resolver discovery (`resolve_judge("evidence-count")` etc.). They are the
copy-paste starting point for a real plugin author.

The proof itself runs OUTSIDE this repo (the issue's done-condition): copy
the trio to a scratch directory, create a fresh venv, `pip install` the
kernel + pytest, `pip install -e` each plugin, run pytest in each plugin's
own checkout. Evidence (the three green pytest runs) recorded on #61.

**Done when:** all three out-of-tree pytest runs pass with every conformance
test collected and green.

### Phase 3 — the docs pointer

A short section in `docs/HACKING.md` (the plugin-author surface) naming the
suite, the subclass pattern, and `JudgeTester`, with the 60-second example;
`llms-full.txt` rebuilt (HACKING.md is on the llms.txt roster, and the
assembly gate pins byte-equality).

**Done when:** `tests/test_llms_full.py` is green over the rebuilt artifact.
