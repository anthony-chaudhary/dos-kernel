package hook

// selfModifyReason is the typed reason a SELF_MODIFY refusal carries —
// `dos.self_modify.SELF_MODIFY_REASON`. It is the structural reason_class the
// admission verdict carries, and it is what makes a SELF_MODIFY refusal a
// "provable" deny at PRE (see decide()).
const selfModifyReason = "SELF_MODIFY"

// dispatchRuntimeFiles is the T1 runtime set — the kernel modules in a LIVE
// dispatch loop's own decision path. Editing any mid-flight changes the logic
// that admits the NEXT packet. Repo-relative POSIX paths, in DECLARATION ORDER
// (load-bearing: the refusal names the first ≤3 hits in this order). Port of
// `dos.self_modify._DISPATCH_RUNTIME_FILES` — must stay byte-identical to it, so
// the parity corpus pins the list. If a file is added/removed there, mirror it
// here (the GHF3 gate fails loudly otherwise).
//
// docs/355 — TRIMMED to the mid-flight-critical core (six files). The
// loop-internal classifiers (gate_classify, loop_decide, tokens) and the reason
// DATA/prose (reasons, wedge_reason) were DROPPED: edited far more often than
// mid-flight-critical, still reached by a live loop via the opt-in apply-gate,
// and the guard now softens to a WARN for an interactive no-loop human. Must
// stay byte-identical to the trimmed `_DISPATCH_RUNTIME_FILES` (the GHF3 gate).
var dispatchRuntimeFiles = []string{
	"src/dos/arbiter.py",
	"src/dos/admission.py",
	"src/dos/self_modify.py",
	"src/dos/lane_overlap.py",
	"src/dos/_tree.py",
	"src/dos/config.py",
}

// treeTouchesRuntime returns the runtime files a requested tree would touch, in
// declaration order (empty = none). Port of
// `dos.self_modify._tree_touches_runtime`.
//
// Prefix-collision in BOTH directions (a requested "src/dos/" glob contains
// "src/dos/arbiter.py"; a requested "src/dos/arbiter.py" IS a runtime file). A
// leading-glob request ("**/*") normalizes to the empty universal prefix and
// collides with EVERY runtime file. Only literally-blank requested entries are
// filtered.
//
// runtimeFiles is the set that actually EXISTS under the served workspace
// (gathered at the boundary, the `existing_runtime_files` analogue) — so a "**/*"
// lane in a FOREIGN repo (no src/dos/*.py) touches nothing and admits, while the
// same lane in the DOS repo is refused.
func treeTouchesRuntime(requestedTree, runtimeFiles []string) []string {
	var reqPrefixes []string
	for _, p := range requestedTree {
		if p != "" {
			reqPrefixes = append(reqPrefixes, normTreePrefix(p))
		}
	}
	if len(reqPrefixes) == 0 {
		return nil
	}
	var hits []string
	for _, original := range runtimeFiles {
		rp := normTreePrefix(original)
		for _, nr := range reqPrefixes {
			if prefixesCollide(nr, rp) {
				hits = append(hits, original)
				break
			}
		}
	}
	return hits
}
