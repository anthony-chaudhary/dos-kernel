package hook

import "fmt"

// admissionVerdict is one predicate's (or the conjunction's) answer — port of
// `dos.admission.AdmissionVerdict`. Two-valued by construction: there is no
// "force admit" — a predicate can only refuse (the conjunctive-only invariant).
type admissionVerdict struct {
	admitted    bool
	reason      string
	reasonClass string
}

func admitVerdict() admissionVerdict { return admissionVerdict{admitted: true} }

func refuseVerdict(reason, reasonClass string) admissionVerdict {
	return admissionVerdict{admitted: false, reason: reason, reasonClass: reasonClass}
}

// lease is the live-lease shape the disjointness check reads — the subset of the
// WAL lease row the pretool decider needs: the lane name + its file tree, plus the
// holder's `run_id` (issue #188 — the lineage join key, so a subagent can resolve a
// lease an ANCESTOR holds and not be hard-denied for an in-lane edit). `runID` is the
// WAL `run_id` field; "" when the lease carried none (a pre-#137 lease).
type lease struct {
	lane  string
	tree  []string
	runID string
	mode  string
}

// admissionRequest is the requested lease as the pure datum a predicate sees —
// port of `dos.admission.AdmissionRequest`. `command`/`argValues` are the call's
// agent-authored argument bytes (docs/364) the DeclaredCallShapePredicate reads —
// FOOTPRINT content, not identity (the vendor-agnostic litmus still holds: the
// predicates that ignore them, disjointness/self-modify, are unaffected). Both
// empty for a non-Bash tool with no string args.
type admissionRequest struct {
	lane      string
	kind      string
	tree      []string
	mode      string
	command   string
	argValues []string
}

// disjointnessVerdict is the DisjointnessPredicate against ONE live lease — port
// of `dos.admission.DisjointnessPredicate.__call__`, with the both-known default
// case delegated through the sound lock-mode path (S/S compatible, anything with
// X conflicts on intersection).
//
// The empty-tree asymmetry (owned by the predicate, never the scorer):
//   - empty LEASE tree -> admit (a lease naming no blast radius cannot conflict).
//   - empty REQUESTED tree vs a KNOWN lease tree -> refuse (unknown blast radius
//     is never safe to admit concurrently).
//   - both known -> lock-mode compatibility over the intersecting region.
func disjointnessVerdict(req admissionRequest, lz lease) admissionVerdict {
	leaseTree := lz.tree
	if len(leaseTree) == 0 {
		return admitVerdict()
	}
	if len(req.tree) == 0 {
		return refuseVerdict(fmt.Sprintf(
			"lane %s has an EMPTY tree (unknown blast radius) and cannot share live lane %s — unknown blast radius is never safe to admit concurrently.",
			pyRepr(req.lane), pyRepr(lz.lane)), "")
	}
	ov := lockModeDecision(req.tree, leaseTree, req.mode, lz.mode)
	if ov.admissible() {
		return admitVerdict()
	}
	return refuseVerdict(fmt.Sprintf(
		"lane %s cannot share live lane %s: %s.",
		pyRepr(req.lane), pyRepr(lz.lane), ov.reason), "")
}

// selfModifyVerdict is the SelfModifyPredicate — request-absolute, ignores the
// lease. Port of `dos.self_modify.SelfModifyPredicate.__call__`.
func selfModifyVerdict(req admissionRequest, runtimeFiles []string) admissionVerdict {
	hits := treeTouchesRuntime(req.tree, runtimeFiles)
	if len(hits) == 0 {
		return admitVerdict()
	}
	shown := joinPreview(hits, 3)
	return refuseVerdict(fmt.Sprintf(
		"lane %s would edit the orchestrator's own running code (%s) — refusing to let a live loop rewrite the kernel that is adjudicating it (SELF_MODIFY). Pass --force only if you are deliberately editing the kernel between loop runs.",
		pyRepr(req.lane), shown), selfModifyReason)
}

// runPredicates runs the conjunction: every predicate against every live lease,
// returning the FIRST refusal (lease-outer, predicate-inner — disjointness THEN
// self-modify THEN call-shape), else admit. Port of `dos.admission.run_predicates`
// with the built-in conjunction [DisjointnessPredicate, SelfModifyPredicate,
// DeclaredCallShapePredicate].
//
// With no live leases the conjunction still runs ONCE against a synthetic empty
// lease, so the request-absolute SelfModifyPredicate AND DeclaredCallShapePredicate
// fire on an idle repo (the closed idle-repo gap), while DisjointnessPredicate
// sees the empty lease, hits its "empty lease tree -> admit" branch, and
// contributes nothing.
//
// call-shape is appended LAST (after disjointness + self-modify), so it can only
// ADD a refusal, never displace the two structural guards — and an empty ruleset
// (the OFF-by-default / generic-workspace case) short-circuits to admit before
// touching any bytes, so the conjunction stays byte-identical in VERDICT to the
// prior two-predicate list (docs/364). Byte-twinned with the Python
// `built_in_predicates` order.
func runPredicates(req admissionRequest, liveLeases []lease, runtimeFiles []string, callShape CallShapeRuleset) admissionVerdict {
	leases := liveLeases
	if len(leases) == 0 {
		leases = []lease{{}} // the synthetic empty-lease sentinel
	}
	for _, lz := range leases {
		if v := disjointnessVerdict(req, lz); !v.admitted {
			return v
		}
		if v := selfModifyVerdict(req, runtimeFiles); !v.admitted {
			return v
		}
		if v := callShapeVerdict(req, callShape); !v.admitted {
			return v
		}
	}
	return admitVerdict()
}
