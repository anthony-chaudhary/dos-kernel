package hook

import (
	"fmt"
	"strings"
	"time"
)

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
// OperatorSession is true when the calling session is an INTERACTIVE operator
// turn, not a dispatch loop / cron / headless run — derived at the boundary from
// the ABSENCE of any loop-context env (DOS_LOOP / CID_RUN_ID / DISPATCH_LOOP_TS).
// It softens a CONTENTION-only disjointness refusal from a hard DENY to an
// advisory WARN: the human-in-command owns the blast radius of their own
// deliberate edit (the same principle the CLI `--force` flag carries), so a fleet
// lane's broad defensive glob must not HARD-BLOCK an operator's narrow edit to a
// file that lane is not actually writing. A dispatch loop (OperatorSession=false)
// still gets the hard, arbitrated DENY — loop-vs-loop collisions are real and must
// be refused. The SELF_MODIFY refusal (which carries a reason_class) is NEVER
// softened by this flag: editing the running kernel stays a hard deny for everyone.
type Inputs struct {
	LiveLeases      []lease
	RuntimeFiles    []string
	OperatorSession bool
	// OverrideFacts is the operator's armed SELF_MODIFY window (docs/296), read at
	// the boundary (`ReadOverride`) or nil when disarmed/absent. When present and in
	// its window, a SELF_MODIFY deny is DISPOSED to an allow-with-note — the Go twin
	// of the Python `pretool_sensor` override path, so the native fast-path honors an
	// armed window byte-identically. nil ⇒ the deny stands (today's behavior).
	OverrideFacts *OverrideFacts
	// Now is the clock for the override window check, injected at the boundary
	// (time.Now().UTC()) so the pure decider stays testable/hermetic.
	Now time.Time
	// SubagentInLane is true when THIS call is a dispatched SUBAGENT whose lineage
	// (`CID_PARENT_ID` / `CID_ROOT_ID`) ties it to the holder of a live lease AND whose
	// write footprint is CONTAINED in that ancestor's declared tree (issue #188). A
	// subagent inherits its parent's lineage but mints its OWN `CID_RUN_ID`, so its
	// in-lane edit otherwise reads as a 100% sibling collision against the parent's held
	// lane — and, classified as a dispatch loop by `CID_RUN_ID`, the OperatorSession
	// softening is skipped, yielding a hard DENY of a squarely-in-scope edit. When this
	// flag is set, `Decide` softens a CONTENTION-only DENY to a WARN exactly as it does
	// for an operator session: a child editing INSIDE the lane its parent leased owns
	// that blast radius as much as the parent does. Computed at the boundary
	// (`SubagentInLaneFromEnv`) — it requires only a CONTAINED write; an ESCAPE leaves
	// the flag false, so the hard deny stands. The SELF_MODIFY refusal is NEVER softened.
	SubagentInLane bool
	// CallShape is the workspace's declared [call_shape] policy (docs/364, OWASP
	// ASI02), read at the boundary off `dos.toml` or the zero value when the
	// workspace declares none. The zero CallShapeRuleset isEmpty() and the
	// predicate short-circuits to admit before touching any bytes, so the
	// default-install decider stays byte-identical. A FORBIDDEN_CALL_SHAPE refusal
	// is a hard deny for EVERYONE — it carries a reason_class that is neither
	// SELF_MODIFY nor "", so none of the softening/override branches (all keyed on
	// those two) ever convert it, exactly mirroring the Python leaf.
	CallShape CallShapeRuleset
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

	// docs/296 — the override-arm PERIMETER, before admission and never subject to
	// the disposition below: a write whose KNOWN footprint touches the operator's arm
	// file (`.dos/override/`) is denied outright. Arming is the operator's hand on the
	// file by design (there is no arm verb), and a window must not be able to extend
	// itself. Byte-twinned with `pretool_sensor.decide`'s perimeter (the same
	// SELF_MODIFY-classed deny). A read (known-empty tree) cannot reach here.
	if treeKnown && len(tree) > 0 && touchesArmPath(tree) {
		reason := "this call would write the operator's SELF_MODIFY override arm file " +
			"(" + armRelPath + ") — only the operator arms a window, by hand " +
			"(docs/296). `dos override status` reports it; `dos override disarm` " +
			"is always allowed."
		return Decision{
			Dialect:     denyPayload("DOS PRE-admission: "+reason, ""),
			Rung:        "admission",
			DecisionTag: "deny",
			ReasonClass: selfModifyReason,
			Reason:      reason,
			TreeKnown:   treeKnown,
		}
	}

	cmd, argValues := callShapeInputs(e)
	req := admissionRequest{
		lane:      laneFor(e),
		kind:      "tool-call",
		tree:      tree,
		command:   cmd,
		argValues: argValues,
	}
	av := runPredicates(req, in.LiveLeases, in.RuntimeFiles, in.CallShape)
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
		// Operator-session softening (issue: a fleet lane's broad defensive glob
		// hard-blocks an interactive operator's narrow edit). A refusal with NO
		// reason_class is a CONTENTION refusal — the call overlaps a held lane's
		// DECLARED region, but a declared region is a defensive claim, not proof the
		// holder is actually writing this exact file. A dispatch loop must still be
		// refused (sibling loops race and a declared collision is the only
		// safe-to-arbitrate signal they have). But an INTERACTIVE operator is the
		// human-in-command, not a competing automaton: they own the blast radius of
		// their own deliberate edit (exactly the `--force` semantics), so a fleet
		// lane's broad glob DOWNGRADES to an advisory WARN for them, never a hard
		// block. The SELF_MODIFY refusal (reason_class != "") is request-absolute and
		// is NOT softened — editing the live kernel stays a hard deny for everyone.
		if provable && in.OperatorSession && av.reasonClass == "" {
			warn := "DOS PRE-admission (advisory, operator session): " + reason +
				" A held lane's DECLARED region overlaps this edit, but you are an interactive operator (not a dispatch loop) — you own the blast radius of your own change, so DOS warns instead of blocking. If a fleet loop is actively writing this exact file, coordinate before saving."
			return Decision{
				Dialect:     warnPayload(warn),
				Rung:        "admission",
				DecisionTag: "warn",
				ReasonClass: av.reasonClass,
				Reason:      reason,
				TreeKnown:   treeKnown,
			}
		}
		// issue #188 — a dispatched SUBAGENT editing INSIDE the lane its PARENT leased.
		// A child inherits its parent's lineage but mints its own CID_RUN_ID, so its
		// in-lane edit reads as a 100% sibling collision against the parent's held lane
		// AND is classified as a dispatch loop (OperatorSession=false), missing the
		// softening above → a hard DENY of a squarely-in-scope edit. The boundary
		// resolved (lineage ties this run to a lease holder AND the write is CONTAINED
		// in that ancestor's tree) → soften the CONTENTION-only DENY to a WARN: a child
		// owns the blast radius of the lane its ancestor holds. An ESCAPE leaves
		// SubagentInLane false (the boundary's containment check fails), so a child
		// writing OUTSIDE the ancestor's tree still hard-denies. SELF_MODIFY
		// (reason_class != "") is never softened.
		if provable && in.SubagentInLane && av.reasonClass == "" {
			warn := "DOS PRE-admission (advisory, in-lane subagent): " + reason +
				" This call is a dispatched subagent editing INSIDE the lane its parent leased (lineage CID_ROOT_ID/CID_PARENT_ID ties it to the holder, and the write is contained in that lane's tree) — so DOS warns instead of blocking an in-scope child edit. A write that ESCAPES the parent's lane still denies."
			return Decision{
				Dialect:     warnPayload(warn),
				Rung:        "admission",
				DecisionTag: "warn",
				ReasonClass: av.reasonClass,
				Reason:      reason,
				TreeKnown:   treeKnown,
			}
		}
		// docs/355 — the SELF_MODIFY middle ground: soften the interactive,
		// NO-LOOP case to an advisory WARN. The verdict still says SELF_MODIFY,
		// but the mid-flight-rewrite HAZARD needs a live dispatch loop — a packet
		// rewriting arbiter.py between two admission checks only matters if there
		// IS a next packet. An interactive operator (OperatorSession, i.e. no loop
		// env) is the human editing the kernel BETWEEN loop runs, the case the
		// guard's own TYPICAL-FIX calls safe; for them the deny downgrades to a
		// WARN (proceeds, hazard named, no arm ritual). A LOOP session is NOT
		// softened — it falls through to the `dispose` window branch and then the
		// hard deny. Runs BEFORE `dispose`: if we soften for the no-loop human
		// there is nothing for the arm file to convert. Byte-twinned with
		// `pretool_sensor.decide`'s docs/355 operator-session SELF_MODIFY branch.
		if provable && av.reasonClass == selfModifyReason && in.OperatorSession {
			warn := "DOS PRE-admission (advisory, operator session): " + reason +
				" You are editing the live kernel, but NO dispatch loop is in flight (the mid-flight-rewrite hazard needs a live loop) — you own the blast radius of your own deliberate edit, so DOS warns instead of blocking. A dispatch loop carries the loop env and still gets the hard deny; arm a window (dos override status) to edit under a live loop."
			return Decision{
				Dialect:     warnPayload(warn),
				Rung:        "admission",
				DecisionTag: "warn",
				ReasonClass: av.reasonClass,
				Reason:      reason,
				TreeKnown:   treeKnown,
			}
		}
		// docs/296 — the operator's armed override window, consulted at the
		// ENFORCEMENT boundary only (the verdict above is unchanged and still says
		// SELF_MODIFY). The boundary handed us the parsed facts + clock; `dispose`
		// is pure and fail-closed, and ONLY a SELF_MODIFY refusal is ever converted
		// (a collision/budget deny is never waved through). The admit is emitted as
		// ALLOW-with-note, never a silent pass — byte-twinned with
		// `pretool_sensor.decide`'s override-admit branch. Reached only by a LOOP
		// session now (the no-loop human softened above) — the arm window's
		// loop-time-only job, post docs/355.
		expiredNote := "" // issue #159 — set when an armed window has LAPSED
		if provable && av.reasonClass == selfModifyReason && in.OverrideFacts != nil {
			if note := dispose(av.reasonClass, tree, in.OverrideFacts, in.Now); note != "" {
				return Decision{
					Dialect:     warnPayload("DOS PRE-admission (operator override): " + note + " [the refused verdict was: " + reason + "]"),
					Rung:        "admission",
					DecisionTag: "override-admit",
					ReasonClass: av.reasonClass,
					Reason:      reason,
					TreeKnown:   treeKnown,
				}
			}
			// issue #159 — an EXPIRED window denies identically to NO window
			// unless we say so. `dispose` declined; if the arm file parsed AND
			// its deadline is now in the past, the cause is LAPSE, not absence —
			// tell the operator to re-arm. Byte-twinned with `pretool_sensor`'s
			// expired-note branch (the same minute math + phrasing).
			if in.Now.After(in.OverrideFacts.Until) {
				mins := int(in.Now.Sub(in.OverrideFacts.Until).Minutes())
				ago := "less than a min ago"
				if mins >= 1 {
					ago = fmt.Sprintf("%d min ago", mins)
				}
				expiredNote = " An operator override window WAS armed but EXPIRED at " +
					isoZ(in.OverrideFacts.Until) + " (" + ago + ") — it lapsed, it was " +
					"never absent. Re-arm to edit: dos override status."
			}
		}
		if provable {
			return Decision{
				Dialect:     denyPayload("DOS PRE-admission: "+reason+expiredNote, ""),
				Rung:        "admission",
				DecisionTag: "deny",
				ReasonClass: av.reasonClass,
				Reason:      reason,
				TreeKnown:   treeKnown,
			}
		}
		// PROVEN no-footprint (issue #46): a KNOWN-and-EMPTY tree with no
		// reason_class is a read (Read/Grep/Glob, a no-write Bash, a read-only MCP
		// tool) — it provably touches NOTHING, so it cannot collide with ANY live
		// lease. Firing the advisory on every read is ambient noise; reserve it for
		// the genuinely-unknown footprint (treeKnown == false), where "scope it to a
		// path" is real guidance. A proven no-footprint call passes CLEAN. Only the
		// OUTPUT changes (WARN was already pass-with-context) — no decision flips.
		if treeKnown && len(tree) == 0 {
			return Decision{
				Dialect:     nil,
				Rung:        "admission",
				DecisionTag: "passthrough",
				ReasonClass: "",
				Reason:      "proven no-footprint call (a read touches nothing) — cannot collide with any live lease",
				TreeKnown:   treeKnown,
			}
		}
		warn := "DOS PRE-admission (advisory): " + reason +
			" This call's footprint does not prove a collision (an unresolved write footprint is unknown), so DOS cannot prove it collides — proceeding, but if this call mutates shared state, scope it to a declared path/lane."
		return Decision{
			Dialect:     warnPayload(warn),
			Rung:        "admission",
			DecisionTag: "warn",
			ReasonClass: nonEmpty(av.reasonClass, "UNRESOLVED_WRITE_FOOTPRINT"),
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
