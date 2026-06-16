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
}

// admissionRequest is the requested lease as the pure datum a predicate sees —
// port of `dos.admission.AdmissionRequest`.
type admissionRequest struct {
	lane string
	kind string
	tree []string
}

// disjointnessVerdict is the DisjointnessPredicate against ONE live lease — port
// of `dos.admission.DisjointnessPredicate.__call__`, with the both-known case
// delegated through the deterministic floor (`admissible_under_floor` with the
// built-in prefix policy, which reproduces `overlap_verdict` exactly).
//
// The empty-tree asymmetry (owned by the predicate, never the scorer):
//   - empty LEASE tree -> admit (a lease naming no blast radius cannot conflict).
//   - empty REQUESTED tree vs a KNOWN lease tree -> refuse (unknown blast radius
//     is never safe to admit concurrently).
//   - both known -> the overlap scorer under the floor.
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
	// admissible_under_floor with the built-in prefix policy == the floor itself ==
	// computeOverlap (the default-policy path is byte-identical to the floor).
	ov := computeOverlap(req.tree, leaseTree)
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
// self-modify), else admit. Port of `dos.admission.run_predicates` with the
// built-in conjunction [DisjointnessPredicate, SelfModifyPredicate].
//
// With no live leases the conjunction still runs ONCE against a synthetic empty
// lease, so the request-absolute SelfModifyPredicate fires on an idle repo (the
// closed idle-repo gap), while DisjointnessPredicate sees the empty lease, hits
// its "empty lease tree -> admit" branch, and contributes nothing.
func runPredicates(req admissionRequest, liveLeases []lease, runtimeFiles []string) admissionVerdict {
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
	}
	return admitVerdict()
}
