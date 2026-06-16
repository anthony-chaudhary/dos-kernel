# 367 — DOS-verified is W2, not W3: the Goodhart guardrail for the training-substrate claim

> **One sentence.** DOS-verified means "a real artifact landed and its claim
> matches its diff" — it does not mean "the artifact is correct," and labeling
> a trajectory training-grade on W2 evidence alone risks teaching models to
> satisfy the witness rather than do the work.

**Status:** design note — no new syscall. **Date:** 2026-06-16.
**Closes:** issue #197.
**Builds on:** docs/204 §3 (presence-not-goal wall), docs/214 (commit-audit),
docs/288 (TWV), docs/318 (keep-gate ablation), docs/332 (verified data).

---

## 1. What "DOS-verified" confers — exactly

A trajectory earns the DOS-verified label when two independent artifact checks
pass.

**W1 — env acknowledgment.** The tool executor accepted the call. A 200 OK or
a successful git commit exists. The agent did not merely say it ran; the env
confirmed receipt.

**W2 — presence.** `verify()` found the named file in git ancestry. "A commit
touched the path." The file is there. This is tamper-evident; you cannot forge
a commit hash (docs/204 §3, docs/214 §3).

**Claim-vs-diff consistency.** `commit-audit` found no contradiction between
the commit subject and the diff. The subject says `fix`; the diff touches
source; the rung is `diff-witnessed`. Not a heuristic — a one-sided check on
non-forgeable bytes (docs/214 §2).

That is the full W2 bundle. It is real. It is hard to fake. It is not
correctness.

---

## 2. What "DOS-verified" does NOT confer

**W3 — goal correctness.** "The change is right." DOS does not witness this.

`verify()` reads `git log -- <path>`. It records which SHA touched the path. It
does not read the file's content. It does not compare the diff to an expected
gold (docs/204 §3, `phase_shipped.py:1329`). A commit that introduces a subtle
logic bug, a wrong formula, or a plausible-but-broken algorithm passes the W2
rung perfectly.

`commit-audit` checks that a code claim is backed by a code diff. An agent
that writes *real* source changes — wrong ones — and a *truthful* subject gets
`OK / diff-witnessed`. The rung is sound for what it measures. It does not
measure correctness (docs/214 §2, point 1).

The TWV (`dos test-witness`, docs/288) goes further: it requires a test that
fails on the baseline and passes on the candidate. But even `DISCRIMINATES`
only proves the test discriminates; it does not prove the test asserts the
right thing (docs/288 §6). A test that checks `os.path.exists("new_file.py")`
is `DISCRIMINATES` and says nothing about whether the file does what it should.

The honest picture, in order:

| Rung | What it witnesses | What it does NOT witness |
|---|---|---|
| W1 env-ack | the tool ran and was received | the action had the right effect |
| W2 presence | the file changed | the change is correct |
| claim-vs-diff | the subject matches the diff shape | the logic inside the diff is right |
| TWV DISCRIMINATES | this test did not pass before the change | the test asserts the intended behavior |
| W3 correctness | *not witnessed by any kernel rung today* | — |

W3 is the job of a content-diff rung against a gold, a passing integration
test against real env state, or JUDGE/HUMAN — all of which are either
unavailable at scale or advisory (docs/204 §3, docs/192).

---

## 3. The Goodhart risk — stated plainly

docs/332's central claim is that DOS-verified trajectories are the next
generation of training data because their label is *author-disjoint*: the
witness byte was not written by the agent.

That claim is true at the W2 level. The Goodhart risk is what happens when you
train a model on W2-labeled data and call it a W3-correct substrate.

**The classic Goodhart pattern:** when a measure becomes a target, it ceases
to be a good measure. A model trained on trajectories that "passed the DOS
witness" will, under optimization pressure, learn to produce trajectories that
pass the DOS witness. Passing the DOS witness means landing an artifact and
making the commit subject match the diff. It does not require the artifact to
be correct.

The failure mode is quiet: the model gets better at W2 compliance — real
commits, truthful subjects, green suites of vacuous tests — while the
underlying logic gets no better or gets worse. The training signal rewards the
*shape* of good work, not the work.

This is the exact failure class DOS exists to oppose at runtime. Letting it
back in through the data-foundry framing would be the kernel being used as a
tool to build its own blind spot.

**Why it is more dangerous here than at runtime.** At runtime, a DOS-verified
but wrong trajectory gets one bad action. In a training dataset, the same
trajectory is the lesson — taught to every copy of the model trained on it,
with gradient applied. A runtime lie is a single event; a training lie is a
systematic update.

---

## 4. The guardrail — require a W3 witness before training-grade labeling

The fix is not to raise the bar on W2 (W2 is already honest and sound). The
fix is to add a W3 gate *before* calling a trajectory training-grade.

**The rule:** a trajectory is training-grade only when it carries at least one
W3 witness — an author-disjoint check that the change had the *right effect*,
not just *some* effect.

Three W3 witnesses exist today in the kernel or in draft:

1. **DB-state hash / env-state invariant** (docs/332 §7, docs/228). For
   write tasks where the env has a checkable post-state — a reservation, a
   balance, a database row — the env's own hash is a W3 correctness witness.
   The agent authored zero bytes of it. This is the strongest currently
   available W3 witness, and it works only where a checkable invariant exists
   (~62% of frontier goals, docs/204 §3).

2. **TWV `DISCRIMINATES`** (docs/288). A test that was red before the change
   and green after provides a partial W3 witness. It is partial because test
   adequacy is not checked. But a commit whose new test `DISCRIMINATES` is
   meaningfully stronger than one whose new test is `VACUOUS` or absent —
   the vacuous-test shape (docs/288 §3) is exactly what W2-only admission
   banks as assurance. TWV rejects the vacuous test; that is the W3 lift it
   provides.

3. **Content-diff rung against a gold** (proposed, not yet shipped). Where a
   gold diff exists — a reference implementation, a verified prior commit, a
   formal spec — a content-addressed diff comparison is a W3 witness. This is
   the P(b) design in docs/204 §3.

The guardrail in practice: before adding a trajectory to a training corpus,
require at least one of these three. If none is available, the trajectory may
still be useful for other purposes (fine-tuning narration style, improving
formatting discipline) but should not be labeled "DOS-verified training data"
in the W3 sense. The label should name the level: `W2-clean` vs
`W3-witnessed`.

The composition with docs/332's four axes: a W3-witnessed trajectory has
stronger *distillability* (the irreducible residue is smaller — you have
checked goal-correctness, not just presence) and is genuinely in the
"next-generation" tier the doc claims. A W2-only trajectory is real and
valuable but belongs in the honest middle tier, not the top one.

---

## 5. The falsifiable probe

**Hypothesis.** A meaningful fraction of W2-clean trajectories are W3-wrong:
the artifact landed, the claim is consistent with the diff, but the change does
not accomplish the stated goal.

**The corpus.** Construct a corpus of commits that are:
- W2-clean: `dos verify` shows the file was touched, `commit-audit` returns
  `OK / diff-witnessed`.
- W3-wrong: the change either (a) leaves the bug it claimed to fix present,
  (b) has a new test that is `VACUOUS` (passes on the baseline tree), or
  (c) produces wrong output on a known input.

This corpus is not hypothetical. The tau2 benchmark (docs/228) already contains
rows where the agent committed a change, the commit passed artifact checks, and
the env-state hash said the DB row was wrong. The `commit-audit` sweep on 458
DOS commits (docs/214 §4b) found a 0.4% drift rate on a meticulously-maintained
repo; agent fleets in the wild will have higher rates (the E3 forgery corpus,
docs/206, shows the forgeable shape is common).

**The measurement.**

```bash
# Step 1: collect W2-clean commits from an agent-fleet run
dos commit-audit --sweep <fleet-branch>..HEAD --json > w2_clean.json

# Step 2: for each W2-clean commit that claims a test was added or a fix was made,
# run TWV against the baseline tree
# (requires the P2 two-tree gather driver, docs/288 §7 — see runbook below)

# Step 3: count the VACUOUS fraction
python scripts/probe_w2_w3_gap.py --commits w2_clean.json --twv-results twv_results.json
```

**The runbook (P2 driver not yet shipped).**

Until docs/288 P2 (the two-tree gather) ships, the probe is manual but
executable:

1. For each commit `C` in the W2-clean corpus that adds a test file `T`:
   - Check out `C^` (parent) into a temp worktree.
   - Run `python -m pytest -x <T>` on the parent worktree.
   - Run `python -m pytest -x <T>` on `C`.
   - Record `(baseline_result, candidate_result)`.
   - Feed to `dos test-witness --baseline <b> --candidate <c>`.
2. Count `VACUOUS` verdicts.

**The falsification criterion.**

If fewer than 10% of W2-clean, test-claiming commits are `VACUOUS`, the W2
label is a reasonable proxy for W3 quality in practice, and the substrate claim
is defensible (with the caveat stated). If more than 30% are `VACUOUS`, the
substrate claim is overstated: W2 admission is letting a large fraction of
hollow trajectories through, and the training signal is polluted. The 10–30%
range is a gray zone where honest qualification is required.

**The expected direction.** The keep-gate ablation (docs/318) found that a
self-certifying gate kept **122 over-claims** vs the witnessed gate's **5**
over 10 seeded runs — a 24× difference. W2-only admission is not as bad as
full self-certification (the artifact check is real), but it does not close
the correctness gap. The prior from docs/318 suggests the VACUOUS fraction
will be meaningfully above zero.

**Script stub** (`scripts/probe_w2_w3_gap.py` — runbook version, no
two-tree runner).

```python
"""
Probe the W2/W3 gap: given a JSON list of commit-audit results and a JSON list
of TWV verdicts for the same commits, compute the VACUOUS fraction.

Usage:
    python scripts/probe_w2_w3_gap.py \
        --commits w2_clean.json \
        --twv-results twv_results.json

Input formats:
  w2_clean.json: list of {"sha": str, "verdict": "OK", "rung": "diff-witnessed", ...}
  twv_results.json: list of {"sha": str, "test": str, "verdict": "VACUOUS"|"DISCRIMINATES"|...}

Output: summary counts + fraction, exit 0.
"""
import json, argparse, sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commits", required=True)
    ap.add_argument("--twv-results", required=True)
    args = ap.parse_args()

    commits = {c["sha"]: c for c in json.load(open(args.commits))}
    twv = json.load(open(args.twv_results))

    checkable = [r for r in twv if r["sha"] in commits]
    vacuous   = [r for r in checkable if r["verdict"] == "VACUOUS"]
    disc      = [r for r in checkable if r["verdict"] == "DISCRIMINATES"]

    total = len(checkable)
    if total == 0:
        print("No checkable rows — corpus may be empty or sha keys mismatched.")
        sys.exit(0)

    frac = len(vacuous) / total
    print(f"W2-clean commits with a test claim: {total}")
    print(f"  VACUOUS (test witnesses nothing): {len(vacuous)}  ({frac:.1%})")
    print(f"  DISCRIMINATES (real witness):     {len(disc)}")
    print(f"  other (UNSATISFIED/REGRESSIVE/ABSTAIN): {total - len(vacuous) - len(disc)}")
    print()
    if frac > 0.30:
        print("RESULT: fraction > 30% — substrate claim OVERSTATED; W2 label is not a "
              "reliable proxy for W3 quality.")
    elif frac > 0.10:
        print("RESULT: fraction 10–30% — gray zone; honest qualification required.")
    else:
        print("RESULT: fraction < 10% — W2 label is a defensible proxy for W3 quality "
              "(with caveats stated in docs/367).")

if __name__ == "__main__":
    main()
```

---

## 6. Summary — what to say, and what not to say

**Say:** "This trajectory is W2-verified: the artifact landed, and the commit
claim is consistent with the diff. It passed every author-disjoint artifact
check DOS runs today."

**Do not say:** "This trajectory is correct" or "This is training-grade data
for correctness tasks" unless a W3 witness is also present.

**The guardrail in one sentence:** require a W3 witness — TWV `DISCRIMINATES`,
an env-state hash, or a content-diff against a gold — before calling a
trajectory training-grade. Without one, label it `W2-clean` and use it for
what W2 can support: format, narration style, tool-call shape. Not goal
correctness.

The distinction matters because the failure mode of ignoring it is Goodhart
in the exact domain DOS was built to resist.
