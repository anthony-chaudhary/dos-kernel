package hook

import (
	"fmt"
	"strings"
)

// overlapRatioMax is the kernel default soft-overlap tolerance (⅓) —
// `dos.lane_overlap.OVERLAP_RATIO_MAX`. The pretool decider uses the kernel
// default: the WAL carries no per-workspace ratio override for the hot path, and
// the deterministic floor (`admissible_under_floor`) is computed at ⅓ regardless.
const overlapRatioMax = 1.0 / 3.0

// overlapVerdict is the verdict category, mirroring `dos.lane_overlap.Verdict`.
type overlapVerdict string

const (
	admitDisjoint      overlapVerdict = "admit_disjoint"
	admitSoft          overlapVerdict = "admit_soft"
	admitShared        overlapVerdict = "admit_shared"
	refuseOverlap      overlapVerdict = "refuse_overlap"
	refuseExactGlob    overlapVerdict = "refuse_exact_glob"
	refuseLockConflict overlapVerdict = "refuse_lock_conflict"
)

// overlapDecision is the typed result of the disjointness scorer
// (`dos.lane_overlap.OverlapDecision`).
type overlapDecision struct {
	verdict   overlapVerdict
	shared    int
	requested int
	reason    string
}

func (d overlapDecision) admissible() bool {
	return d.verdict == admitDisjoint || d.verdict == admitSoft || d.verdict == admitShared
}

// exactGlobCollisions returns the requested entries whose normalized prefix
// EXACTLY equals a lease entry's — the hard-collision detector the ratio test
// cannot see. Port of `dos.lane_overlap._exact_glob_collisions`. The universal
// empty prefix (a bare "**/*" that normalizes to "") is excluded — a whole-repo
// glob is handled by the ratio path, not treated as an "exact glob".
func exactGlobCollisions(reqTree, leaseTree []string) []string {
	if len(reqTree) == 0 || len(leaseTree) == 0 {
		return nil
	}
	leaseExact := map[string]struct{}{}
	for _, p := range leaseTree {
		if p == "" {
			continue
		}
		n := normalizeEntry(p)
		if n != "" {
			leaseExact[n] = struct{}{}
		}
	}
	if len(leaseExact) == 0 {
		return nil
	}
	seen := map[string]struct{}{}
	var hits []string
	for _, r := range reqTree {
		if r == "" {
			continue
		}
		nr := normalizeEntry(r)
		if nr == "" {
			continue
		}
		if _, ok := leaseExact[nr]; !ok {
			continue
		}
		if _, dup := seen[nr]; dup {
			continue
		}
		seen[nr] = struct{}{}
		hits = append(hits, r)
	}
	return hits
}

// sharedCount counts requested entries that prefix-collide with any lease entry.
// Each requested entry counts at most once. Port of
// `dos.lane_overlap._shared_count`. The empty prefix (from a leading glob) is
// KEPT — a whole-repo glob collides with every entry; only a literally blank
// entry is filtered.
func sharedCount(reqTree, leaseTree []string) int {
	if len(reqTree) == 0 || len(leaseTree) == 0 {
		return 0
	}
	var leasePrefixes []string
	for _, p := range leaseTree {
		if p != "" {
			leasePrefixes = append(leasePrefixes, normalizeEntry(p))
		}
	}
	if len(leasePrefixes) == 0 {
		return 0
	}
	shared := 0
	for _, r := range reqTree {
		if r == "" {
			continue
		}
		nr := normalizeEntry(r)
		for _, nl := range leasePrefixes {
			if prefixesCollide(nr, nl) {
				shared++
				break
			}
		}
	}
	return shared
}

// computeOverlap decides whether a known-tree lane can run alongside a known-tree
// lease. Port of `dos.lane_overlap.overlap_verdict` at the kernel default ratio.
//
// Decision order (byte-faithful): exact-glob hard floor first, then disjoint,
// then the ratio compare. The DECISION (admissible or not) is what GHF gates
// byte-exact; the `reason` prose carries the percentage (a float-formatted string,
// the docs/124 §1.1 hazard) and is NOT part of the gated projection.
func computeOverlap(reqTree, leaseTree []string) overlapDecision {
	exact := exactGlobCollisions(reqTree, leaseTree)
	if len(exact) > 0 {
		sharedAll := sharedCount(reqTree, leaseTree)
		preview := joinPreview(exact, 3)
		return overlapDecision{
			verdict:   refuseExactGlob,
			shared:    sharedAll,
			requested: len(reqTree),
			reason: fmt.Sprintf(
				"exact-glob overlap: identical glob claimed by both lanes (%d: %s) — same write region, hard collision regardless of ratio",
				len(exact), preview),
		}
	}
	requested := len(reqTree)
	if requested < 1 {
		requested = 1
	}
	shared := sharedCount(reqTree, leaseTree)
	if shared == 0 {
		return overlapDecision{
			verdict:   admitDisjoint,
			shared:    shared,
			requested: len(reqTree),
			reason:    "no shared prefixes — fully disjoint",
		}
	}
	ratio := float64(shared) / float64(requested)
	if ratio > overlapRatioMax {
		return overlapDecision{
			verdict:   refuseOverlap,
			shared:    shared,
			requested: len(reqTree),
			reason: fmt.Sprintf(
				"overlap too large (%d/%d = %s of requested tree shared, threshold %s)",
				shared, len(reqTree), pct0(ratio), pct0(overlapRatioMax)),
		}
	}
	return overlapDecision{
		verdict:   admitSoft,
		shared:    shared,
		requested: len(reqTree),
		reason: fmt.Sprintf(
			"soft-overlap admit — %d/%d = %s of requested tree shared (≤%s)",
			shared, len(reqTree), pct0(ratio), pct0(overlapRatioMax)),
	}
}

const lockModeShared = "shared"
const lockModeExclusive = "exclusive"

func coerceLockMode(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case lockModeShared:
		return lockModeShared
	case lockModeExclusive:
		return lockModeExclusive
	default:
		return lockModeExclusive
	}
}

func modesCompatible(a, b string) bool {
	return a == lockModeShared && b == lockModeShared
}

func treesIntersectKnown(reqTree, leaseTree []string) bool {
	if len(reqTree) == 0 || len(leaseTree) == 0 {
		return false
	}
	var reqPrefixes, leasePrefixes []string
	for _, p := range reqTree {
		if p != "" {
			reqPrefixes = append(reqPrefixes, normalizeEntry(p))
		}
	}
	for _, p := range leaseTree {
		if p != "" {
			leasePrefixes = append(leasePrefixes, normalizeEntry(p))
		}
	}
	if len(reqPrefixes) == 0 || len(leasePrefixes) == 0 {
		return false
	}
	for _, nr := range reqPrefixes {
		for _, nl := range leasePrefixes {
			if prefixesCollide(nr, nl) {
				return true
			}
		}
	}
	return false
}

func lockModeDecision(reqTree, leaseTree []string, reqModeRaw, leaseModeRaw string) overlapDecision {
	reqMode := coerceLockMode(reqModeRaw)
	leaseMode := coerceLockMode(leaseModeRaw)
	requested := len(reqTree)
	if !treesIntersectKnown(reqTree, leaseTree) {
		return overlapDecision{
			verdict:   admitDisjoint,
			shared:    0,
			requested: requested,
			reason:    "no shared prefixes — fully disjoint",
		}
	}
	if modesCompatible(reqMode, leaseMode) {
		return overlapDecision{
			verdict:   admitShared,
			shared:    1,
			requested: requested,
			reason: fmt.Sprintf(
				"shared lock-compatible region — %s/%s modes may coexist",
				reqMode, leaseMode),
		}
	}
	modeNote := "exclusive/exclusive"
	if reqMode == lockModeShared || leaseMode == lockModeShared {
		modeNote = reqMode + "/" + leaseMode
	}
	return overlapDecision{
		verdict:   refuseLockConflict,
		shared:    1,
		requested: requested,
		reason: fmt.Sprintf(
			"lock-mode conflict: intersecting region with %s modes requires zero-tolerance refusal",
			modeNote),
	}
}

// joinPreview renders the first n items joined by ", " with a "…" if truncated —
// the shape Python's `", ".join(xs[:n]) + ("…" if len(xs) > n else "")` produces.
func joinPreview(xs []string, n int) string {
	if len(xs) <= n {
		return joinComma(xs)
	}
	return joinComma(xs[:n]) + "…"
}

func joinComma(xs []string) string {
	out := ""
	for i, x := range xs {
		if i > 0 {
			out += ", "
		}
		out += x
	}
	return out
}
