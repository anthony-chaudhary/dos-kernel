package hook

import "strings"

// Decision is the structured outcome of the PRE division — the Go analogue of the
// Python `decide()` outcome record. `Dialect` is the CC dict to emit (nil =
// passthrough, emit nothing). The rest is the forensic projection (the same fields
// the Python OP_ENFORCE journal body carries) that GHF3 gates byte-exact.
type Decision struct {
	Dialect     map[string]any // nil => passthrough
	Rung        string         // "admission" | "provenance" | "none"
	DecisionTag string         // "deny" | "warn" | "passthrough"
	ReasonClass string
	Reason      string
	TreeKnown   bool
}

// Inputs is the gathered evidence the boundary hands the pure decider: the live
// leases (folded from the WAL) and the runtime files that EXIST under the served
// workspace (stat-probed at the boundary). No I/O happens past this point.
type Inputs struct {
	LiveLeases   []lease
	RuntimeFiles []string
}

// Decide runs the PRE division on one event — port of `dos.pretool_sensor.decide`
// with the DEFAULT handler_name="observe".
//
// Rung A (admission) runs first: a structural refusal that is PROVABLE (a typed
// reason_class like SELF_MODIFY, or a KNOWN-tree collision) becomes a deny; an
// UNPROVABLE refusal (unknown tree, no reason_class — refused only because a lane
// was contended) becomes a turn-preserving WARN-and-pass. Rung B (provenance) runs
// only if Rung A admitted, and with the default observe handler it ALWAYS passes
// through (the PDP-only floor: a behavioral deny needs a wired ruling handler,
// which lives in a driver — GHF5 scope). So the default-install Go decider
// reproduces Python's default-install bytes exactly.
func Decide(e *Event, in Inputs) Decision {
	// ---- Rung A: structural admission ----
	tree, treeKnown := e.treeFromEvent()
	req := admissionRequest{
		lane: laneFor(e),
		kind: "tool-call",
		tree: tree,
	}
	av := runPredicates(req, in.LiveLeases, in.RuntimeFiles)
	if !av.admitted {
		reason := av.reason
		if reason == "" {
			reason = "DOS admission refused this call (no lane available)."
		}
		// The hook surface names only the remedies it has (issue #14): swap the
		// predicate's CLI-only `--force` tail before ANY downstream use — the
		// emitted dialect AND the journaled OP_ENFORCE record carry the same
		// hook-true guidance. Port of `pretool_sensor.hook_surface_reason`.
		if av.reasonClass == selfModifyReason {
			reason = hookSurfaceReason(reason)
		}
		// A non-admit is provable (→ deny) ONLY when we can SHOW a real collision:
		//
		//   (a) a typed `reason_class` (SELF_MODIFY) — request-absolute, proven; OR
		//   (b) a REAL region overlap: a KNOWN **and non-empty** requested tree
		//       (`treeKnown && len(tree) > 0`) that genuinely overlaps a held lease.
		//
		// The load-bearing correction (FQ-532 Defect 3): `treeKnown` ALONE is NOT
		// proof of collision. A read-only tool has a KNOWN but EMPTY tree
		// (`treeFromEvent` → `((), true)`) — it provably touches NOTHING, so it can
		// never collide — yet the disjointness predicate's empty-REQUESTED-tree rule
		// refuses it ("unknown blast radius") with no `reason_class`, and the old
		// `reason_class != "" || treeKnown` gate then escalated that CONTENTION-ONLY
		// refusal to a hard DENY for every Read/Edit while a Bash (unknown tree) only
		// WARNed. Requiring a NON-EMPTY known tree makes a contention-only refusal stay
		// ADVISORY regardless of treeKnown — a read passes through; only a parseable
		// footprint that really overlaps denies. The empty-tree refusal is contention,
		// not collision: we cannot prove the call collides, so we WARN, never deny.
		provable := av.reasonClass != "" || (treeKnown && len(tree) > 0)
		if provable {
			return Decision{
				Dialect:     denyPayload("DOS PRE-admission: "+reason, ""),
				Rung:        "admission",
				DecisionTag: "deny",
				ReasonClass: av.reasonClass,
				Reason:      reason,
				TreeKnown:   treeKnown,
			}
		}
		warn := "DOS PRE-admission (advisory): " + reason +
			" This call's footprint does not prove a collision (a read touches nothing; an unresolved write footprint is unknown), so DOS cannot prove it collides — proceeding, but if this call mutates shared state, scope it to a declared path/lane."
		return Decision{
			Dialect:     warnPayload(warn),
			Rung:        "admission",
			DecisionTag: "warn",
			ReasonClass: av.reasonClass,
			Reason:      reason,
			TreeKnown:   treeKnown,
		}
	}

	// ---- Rung B: behavioral provenance, default observe handler ----
	// With the default `observe` handler the proposal never withholds the call and
	// never carries a WARN note, so decide() returns passthrough. A ruling handler
	// (a driver) is required to reach a behavioral deny; that path is GHF5/driver
	// scope and is intentionally not in the native hot-path decider. Reads
	// short-circuit here too (a non-mutating call has nothing to gate).
	return Decision{
		Dialect:     nil,
		Rung:        rungForPassthrough(e),
		DecisionTag: "passthrough",
		Reason:      passthroughReason(e),
		TreeKnown:   treeKnown,
	}
}

// laneFor is the admission request lane — the tool name, or "tool" when absent —
// matching `str(event.get("tool_name") or "tool")`.
func laneFor(e *Event) string {
	if e.ToolName != "" {
		return e.ToolName
	}
	return "tool"
}

// rungForPassthrough mirrors the Python outcome's "rung" on a passthrough: "none"
// for a read / non-mutating call (Rung B short-circuited), else "provenance"
// (Rung B ran and observed).
func rungForPassthrough(e *Event) string {
	if !e.isMutatingTool() {
		return "none"
	}
	return "provenance"
}

func passthroughReason(e *Event) string {
	if !e.isMutatingTool() {
		return "read / non-mutating call"
	}
	return ""
}

// Render returns the bytes to print on stdout for a decision (empty = print
// nothing). The dialect is marshaled byte-identically to Python's
// `json.dumps(host_dialect, sort_keys=True)` — the GHF byte-exact contract. This is
// the Claude-Code projection (the canonical neutral form); RenderAs transcodes it
// for a non-CC host.
func (d Decision) Render() string {
	if d.Dialect == nil {
		return ""
	}
	return pyJSONDumps(d.Dialect)
}

// RenderAs returns the stdout bytes for a decision in host `dialect`'s grammar
// (docs/268). It transcodes the canonical CC dict (`d.Dialect`) into the host
// envelope — `claude-code`/`codex`/"" are byte-identical to Render(); gemini /
// antigravity / cursor are re-rendered. A passthrough (nil Dialect) stays empty for
// every dialect. Byte-matched to Python's `resolve_dialect(name).render(parse_cc(cc))`
// by parity_dialect_test.go.
func (d Decision) RenderAs(dialect string) string {
	out := transcodeCC(d.Dialect, dialect)
	if out == nil {
		return ""
	}
	return pyJSONDumps(out)
}

// The hook-surface remedy swap (issue #14) — byte-twinned with
// `dos.pretool_sensor._CLI_FORCE_TAIL` / `_HOOK_SURFACE_TAIL`. The SELF_MODIFY
// predicate's refusal names `--force`, which is real ONLY at the `dos arbitrate`
// CLI; the PreToolUse ABI deliberately gives the agent none, so the hook deny
// swaps that sentence for the remedies that exist at this surface. The predicate
// text itself (admission.go) is untouched — it stays the byte-faithful port of
// the Python predicate, exactly as Python's own predicate keeps its CLI tail.
const cliForceTail = "Pass --force only if you are deliberately editing the kernel between loop runs."
const hookSurfaceTail = "Do not retry — there is no force override at this surface, and repeated " +
	"denies raise an operator decision (dos decisions). Inspect with the " +
	"read-only tools; the edit itself is the operator's, made between loop " +
	"runs or under their armed override window (dos override status)."

// hookSurfaceReason is the Go twin of `pretool_sensor.hook_surface_reason`,
// already gated on SELF_MODIFY by the caller. ReplaceAll matches Python's
// `str.replace` semantics exactly (byte-parity over the corpus).
func hookSurfaceReason(reason string) string {
	if strings.Contains(reason, cliForceTail) {
		return strings.ReplaceAll(reason, cliForceTail, hookSurfaceTail)
	}
	return reason + " " + hookSurfaceTail
}

// trimmedReason is a small helper for diagnostics (never used in the gated path).
func trimmedReason(s string) string { return strings.TrimSpace(s) }
