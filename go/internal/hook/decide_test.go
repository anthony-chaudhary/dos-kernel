package hook

import (
	"strings"
	"testing"
	"time"
)

// eventFor builds an *Event the way parseEvent would from a decoded map, for the
// unit tests that exercise the pure decider without the stdin/JSON layer.
func eventFor(tool, cwd string, input map[string]any) *Event {
	raw := map[string]any{"tool_name": tool, "cwd": cwd}
	if input != nil {
		raw["tool_input"] = input
	}
	return &Event{
		HookEventName: "PreToolUse",
		Cwd:           cwd,
		ToolName:      tool,
		ToolInput:     input,
		raw:           raw,
	}
}

// dosRuntimeFiles is the full static runtime set as if every file existed under
// the workspace — the conservative ExistingRuntimeFiles("") result. The decider
// tests use this so the self-modify rung is fully armed regardless of the test FS.
var dosRuntimeFiles = dispatchRuntimeFiles

func TestSelfModifyDeny(t *testing.T) {
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.DecisionTag != "deny" {
		t.Fatalf("want deny, got %q", d.DecisionTag)
	}
	if d.ReasonClass != "SELF_MODIFY" {
		t.Fatalf("want SELF_MODIFY reason_class, got %q", d.ReasonClass)
	}
	out := d.Render()
	if !strings.Contains(out, `"permissionDecision": "deny"`) {
		t.Fatalf("deny dialect missing permissionDecision: %s", out)
	}
	if !strings.Contains(out, "SELF_MODIFY") {
		t.Fatalf("deny reason missing SELF_MODIFY: %s", out)
	}
}

func TestReadOfRuntimeFilePassesThrough(t *testing.T) {
	// A read takes no tree — it is never gated, even on a runtime file.
	e := eventFor("Read", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.Render() != "" {
		t.Fatalf("read should pass through, got %q", d.Render())
	}
}

func TestEditDisjointDocPassesThrough(t *testing.T) {
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "docs/notes.md"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.Render() != "" {
		t.Fatalf("disjoint doc edit should pass through, got %q", d.Render())
	}
}

func TestBashNonRuntimeFilePassesThrough(t *testing.T) {
	// cli.py is NOT in the runtime set — a Bash write to it is not a self-modify.
	e := eventFor("Bash", "/work/workspace", map[string]any{"command": "echo hi > src/dos/cli.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.Render() != "" {
		t.Fatalf("bash to non-runtime file should pass through, got %q", d.Render())
	}
}

func TestBashRuntimeFileDeny(t *testing.T) {
	e := eventFor("Bash", "/work/workspace", map[string]any{"command": "rm src/dos/_tree.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.DecisionTag != "deny" {
		t.Fatalf("want deny on rm of a runtime file, got %q (%s)", d.DecisionTag, d.Render())
	}
}

func TestDisjointnessCollisionDeny(t *testing.T) {
	// A held `src/**` lease + an Edit to src/dos/cli.py (a known tree) -> exact
	// ratio 100% -> REFUSE_OVERLAP -> provable -> deny with the float-prose reason.
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/cli.py"})
	in := Inputs{
		LiveLeases:   []lease{{lane: "src", tree: []string{"src/**"}}},
		RuntimeFiles: dosRuntimeFiles,
	}
	d := Decide(e, in)
	if d.DecisionTag != "deny" {
		t.Fatalf("want deny on collision, got %q (%s)", d.DecisionTag, d.Render())
	}
	if !strings.Contains(d.Render(), "100% of requested tree shared, threshold 33%") {
		t.Fatalf("collision reason float-prose mismatch: %s", d.Render())
	}
}

func TestOperatorSessionCollisionWarnsNotDenies(t *testing.T) {
	// The operator-session softening: the SAME `src/**` lease + Edit to
	// src/dos/cli.py that a dispatch loop is hard-DENIED on (TestDisjointnessCollisionDeny)
	// must DOWNGRADE to an advisory WARN for an INTERACTIVE operator. A held lane's
	// declared region is a defensive claim, not proof the holder is writing this
	// exact file; the human-in-command owns their own blast radius (the `--force`
	// principle). Contention-only refusal (no reason_class) + OperatorSession -> warn.
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/cli.py"})
	in := Inputs{
		LiveLeases:      []lease{{lane: "src", tree: []string{"src/**"}}},
		RuntimeFiles:    dosRuntimeFiles,
		OperatorSession: true,
	}
	d := Decide(e, in)
	if d.DecisionTag != "warn" {
		t.Fatalf("operator-session collision must WARN, got %q (%s)", d.DecisionTag, d.Render())
	}
	if strings.Contains(d.Render(), "permissionDecision") {
		t.Fatalf("operator-session WARN must not carry permissionDecision (must pass through): %s", d.Render())
	}
}

func TestLoopSessionCollisionStillDenies(t *testing.T) {
	// The negative control: WITHOUT OperatorSession (a dispatch loop / cron / headless
	// run — the field defaults false), the identical collision stays a hard DENY. The
	// softening must NOT leak to automated lanes: sibling loops race and a declared
	// collision is the only safe-to-arbitrate signal they have.
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/cli.py"})
	in := Inputs{
		LiveLeases:      []lease{{lane: "src", tree: []string{"src/**"}}},
		RuntimeFiles:    dosRuntimeFiles,
		OperatorSession: false,
	}
	d := Decide(e, in)
	if d.DecisionTag != "deny" {
		t.Fatalf("loop-session collision must still DENY, got %q (%s)", d.DecisionTag, d.Render())
	}
}

func TestSubagentInLaneCollisionWarnsNotDenies(t *testing.T) {
	// issue #188 (the Go parity twin): a dispatched SUBAGENT editing INSIDE the lane
	// its parent leased must NOT be hard-DENIED. The same `src/**` lease + Edit to
	// src/dos/cli.py a sibling loop is denied on DOWNGRADES to an advisory WARN when
	// SubagentInLane is set — the boundary resolved that this run's lineage ties it to
	// the lease holder AND the write is contained. The pure decider's contract: a
	// contention-only refusal (no reason_class) + SubagentInLane -> warn, even though
	// OperatorSession is false (a subagent is NOT an interactive operator).
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/cli.py"})
	in := Inputs{
		LiveLeases:      []lease{{lane: "src", tree: []string{"src/**"}}},
		RuntimeFiles:    dosRuntimeFiles,
		OperatorSession: false, // a subagent is a dispatch loop, not an operator
		SubagentInLane:  true,
	}
	d := Decide(e, in)
	if d.DecisionTag != "warn" {
		t.Fatalf("in-lane subagent collision must WARN, got %q (%s)", d.DecisionTag, d.Render())
	}
	if strings.Contains(d.Render(), "permissionDecision") {
		t.Fatalf("in-lane subagent WARN must not carry permissionDecision (must pass through): %s", d.Render())
	}
}

func TestSubagentInLaneDoesNotSoftenSelfModify(t *testing.T) {
	// The same safety invariant as the operator case: SubagentInLane softens a
	// CONTENTION refusal only. Editing the live kernel stays a hard DENY for a subagent
	// too — a child must not rewrite the kernel adjudicating its parent's fleet.
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": dosRuntimeFiles[0]})
	in := Inputs{
		RuntimeFiles:   dosRuntimeFiles,
		SubagentInLane: true,
	}
	d := Decide(e, in)
	if d.DecisionTag != "deny" {
		t.Fatalf("SELF_MODIFY must DENY even for an in-lane subagent, got %q (%s)", d.DecisionTag, d.Render())
	}
	if d.ReasonClass != selfModifyReason {
		t.Fatalf("want SELF_MODIFY reason_class %q, got %q", selfModifyReason, d.ReasonClass)
	}
}

func TestSubagentInLaneBoundaryResolution(t *testing.T) {
	// The boundary helper `subagentInLane`: a child whose CID_ROOT_ID matches a live
	// lease's run_id AND whose CONTAINED write resolves true; an ESCAPE resolves false
	// (so the hard deny stands); an UNRELATED lineage resolves false; and the child's
	// OWN id (no ancestor lineage) resolves false (own-lease is the gate's concern).
	parent := []lease{{lane: "src", tree: []string{"src/**"}, runID: "RID-PARENT"}}

	t.Run("contained in-lane child -> true", func(t *testing.T) {
		t.Setenv("CID_RUN_ID", "RID-CHILD")
		t.Setenv("CID_ROOT_ID", "RID-PARENT")
		t.Setenv("CID_PARENT_ID", "RID-PARENT")
		e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/cli.py"})
		if !subagentInLane(e, parent) {
			t.Fatalf("a child contained in the parent's leased tree must resolve in-lane")
		}
	})
	t.Run("escape child -> false", func(t *testing.T) {
		t.Setenv("CID_RUN_ID", "RID-CHILD")
		t.Setenv("CID_ROOT_ID", "RID-PARENT")
		t.Setenv("CID_PARENT_ID", "RID-PARENT")
		e := eventFor("Write", "/work/workspace", map[string]any{"file_path": "docs/escape.md", "content": "x"})
		if subagentInLane(e, parent) {
			t.Fatalf("a child write that ESCAPES the parent's tree must NOT resolve in-lane")
		}
	})
	t.Run("unrelated lineage -> false", func(t *testing.T) {
		t.Setenv("CID_RUN_ID", "RID-OTHER")
		t.Setenv("CID_ROOT_ID", "RID-UNRELATED")
		t.Setenv("CID_PARENT_ID", "RID-UNRELATED")
		e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/cli.py"})
		if subagentInLane(e, parent) {
			t.Fatalf("a run whose lineage does NOT match the holder must NOT resolve in-lane")
		}
	})
	t.Run("own id only (operator root) -> false", func(t *testing.T) {
		t.Setenv("CID_RUN_ID", "RID-PARENT") // this run IS the holder, no ancestor
		t.Setenv("CID_ROOT_ID", "")
		t.Setenv("CID_PARENT_ID", "")
		e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/cli.py"})
		if subagentInLane(e, parent) {
			t.Fatalf("an own-lease write (no ancestor lineage) is not this rung's concern")
		}
	})
}

func TestOperatorSessionDoesNotSoftenSelfModify(t *testing.T) {
	// The safety invariant: OperatorSession softens CONTENTION refusals only. A
	// SELF_MODIFY refusal carries a reason_class and is request-absolute — editing the
	// live kernel that is adjudicating the fleet stays a hard DENY for EVERYONE,
	// operator or not.
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": dosRuntimeFiles[0]})
	in := Inputs{
		RuntimeFiles:    dosRuntimeFiles,
		OperatorSession: true,
	}
	d := Decide(e, in)
	if d.DecisionTag != "deny" {
		t.Fatalf("SELF_MODIFY must DENY even for an operator session, got %q (%s)", d.DecisionTag, d.Render())
	}
	if d.ReasonClass != selfModifyReason {
		t.Fatalf("want SELF_MODIFY reason_class %q, got %q", selfModifyReason, d.ReasonClass)
	}
}

func TestReadAgainstContendedLanePassesClean(t *testing.T) {
	// A proven no-footprint READ passes CLEAN against a contended lane (issue #46).
	// A read-only tool has a KNOWN but EMPTY tree — it provably touches nothing, so
	// it can NEVER collide with any live lease. It never denied (FQ-532 Defect 3
	// fixed the phantom-lane DENY), but it still emitted a PRE-admission ADVISORY on
	// every read while an unrelated lane was leased — ambient noise. Now a proven
	// no-footprint refusal passes CLEAN: a nil dialect, a `passthrough` tag, no
	// advisory bytes. The advisory is reserved for the genuinely-unknown footprint
	// (TestUnknownTreeContendedWarns), where "scope it to a path" is real guidance.
	for _, tool := range []string{"Read", "Grep"} {
		e := eventFor(tool, "/work/workspace", map[string]any{"file_path": "docs/x.md"})
		in := Inputs{
			LiveLeases:   []lease{{lane: "src", tree: []string{"src/**"}}},
			RuntimeFiles: dosRuntimeFiles,
		}
		d := Decide(e, in)
		if d.DecisionTag != "passthrough" {
			t.Fatalf("%s against a contended lane must pass CLEAN (a read cannot collide), got %q (%s)",
				tool, d.DecisionTag, d.Render())
		}
		if d.Render() != "" {
			t.Fatalf("%s proven no-footprint pass must emit NOTHING (no advisory): %s", tool, d.Render())
		}
	}
}

func TestUnknownTreeContendedWarns(t *testing.T) {
	// A held `src/**` lease + `make build` (unknown tree — not a known no-write
	// program, nothing path-shaped) -> disjointness refuses (empty requested tree
	// vs known lease) with no reason_class, tree unknown -> WARN-and-pass
	// (additionalContext only, no permissionDecision).
	e := eventFor("Bash", "/work/workspace", map[string]any{"command": "make build"})
	in := Inputs{
		LiveLeases:   []lease{{lane: "src", tree: []string{"src/**"}}},
		RuntimeFiles: dosRuntimeFiles,
	}
	d := Decide(e, in)
	if d.DecisionTag != "warn" {
		t.Fatalf("want warn, got %q (%s)", d.DecisionTag, d.Render())
	}
	out := d.Render()
	if strings.Contains(out, "permissionDecision") {
		t.Fatalf("WARN must not carry permissionDecision: %s", out)
	}
	if !strings.Contains(out, "additionalContext") {
		t.Fatalf("WARN must carry additionalContext: %s", out)
	}
}

func TestMentionIsNotMutation(t *testing.T) {
	// Issue #12: a Bash command whose invoked program provably cannot write
	// (`gh issue create`) gets the read-only posture — a kernel runtime path inside
	// an ARGUMENT is prose, not a write footprint, so SELF_MODIFY must not deny.
	e := eventFor("Bash", "/work/workspace",
		map[string]any{"command": `gh issue create --body "see src/dos/arbiter.py"`})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.DecisionTag == "deny" {
		t.Fatalf("a path MENTION in a no-write command must not deny, got %s", d.Render())
	}
}

func TestRedirectDefeatsMentionAllowance(t *testing.T) {
	// The conservative direction is preserved: a `>` can write around even an
	// allowed program, so the allowance is vetoed and the scrape still denies.
	e := eventFor("Bash", "/work/workspace",
		map[string]any{"command": "git log > src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.DecisionTag != "deny" {
		t.Fatalf("redirect into a runtime file must still deny, got %q (%s)", d.DecisionTag, d.Render())
	}
}

func TestPostToolEventDeclined(t *testing.T) {
	// A mis-routed PostToolUse event (carries tool_response) is not a PRE event.
	e := &Event{
		HookEventName: "PostToolUse",
		ToolName:      "Read",
		raw:           map[string]any{"tool_name": "Read", "tool_response": "data"},
	}
	if e.isPreEvent() {
		t.Fatal("event with tool_response must not be a PRE event")
	}
}

func TestEmptyTreeKnownnessSemantics(t *testing.T) {
	// Write with no path -> unknown tree; with no leases -> admits -> passthrough.
	e := eventFor("Write", "/work/workspace", map[string]any{})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles})
	if d.Render() != "" {
		t.Fatalf("write-no-path with no leases should pass through, got %q", d.Render())
	}
}

func TestForeignRepoNoRuntimeFilesAdmitsWholeRepoGlob(t *testing.T) {
	// In a foreign repo (no runtime files exist), a `**/*`-ish Bash edit is not a
	// self-modify (the existence probe yields ()).
	e := eventFor("Bash", "/some/foreign", map[string]any{"command": "rm -rf src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: nil}) // no runtime files present
	if d.DecisionTag == "deny" {
		t.Fatalf("foreign repo must not self-modify-deny, got %s", d.Render())
	}
}

// --- docs/296 operator-armed SELF_MODIFY override (Py↔Go parity, issue #186) ---
//
// These pin the native fast-path's override-admit branch directly (no corpus): an
// armed window in time disposes a SELF_MODIFY deny to an allow-with-note; an expired
// or out-of-scope window leaves the deny standing; nil facts is byte-unchanged from
// the pre-#186 behavior (back-compat). The corpus parity gate (TestParityCorpus)
// proves the EMITTED BYTES match Python; these prove the DECISION wiring in isolation.

// armedWindow is a fixed in-window clock + facts pair for the override unit tests:
// now (17:30) sits before until (18:00), so an unscoped window admits.
func armedWindow() (*OverrideFacts, time.Time) {
	until := time.Date(2026, 6, 15, 18, 0, 0, 0, time.UTC)
	now := time.Date(2026, 6, 15, 17, 30, 0, 0, time.UTC)
	return &OverrideFacts{Until: until, Reason: "parity fix #186"}, now
}

func TestSelfModifyOverrideArmedInWindowAdmits(t *testing.T) {
	facts, now := armedWindow()
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles, OverrideFacts: facts, Now: now})
	if d.DecisionTag != "override-admit" {
		t.Fatalf("armed window must override-admit, got %q (%s)", d.DecisionTag, d.Render())
	}
	if d.ReasonClass != selfModifyReason {
		t.Fatalf("override-admit must carry the SELF_MODIFY reason_class, got %q", d.ReasonClass)
	}
	out := d.Render()
	if !strings.Contains(out, "operator override armed until") || !strings.Contains(out, "additionalContext") {
		t.Fatalf("override-admit dialect missing the allow-with-note: %s", out)
	}
	if strings.Contains(out, `"permissionDecision": "deny"`) {
		t.Fatalf("override-admit must NOT be a deny: %s", out)
	}
}

func TestSelfModifyOverrideExpiredDenies(t *testing.T) {
	facts, _ := armedWindow()
	now := facts.Until.Add(30 * time.Minute) // past the deadline
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles, OverrideFacts: facts, Now: now})
	if d.DecisionTag != "deny" {
		t.Fatalf("expired window must DENY, got %q (%s)", d.DecisionTag, d.Render())
	}
}

func TestSelfModifyOverrideScopeMissDenies(t *testing.T) {
	until := time.Date(2026, 6, 15, 18, 0, 0, 0, time.UTC)
	now := time.Date(2026, 6, 15, 17, 30, 0, 0, time.UTC)
	// Window scoped to _tree.py only; the edit targets arbiter.py → out of scope.
	facts := &OverrideFacts{Until: until, Reason: "scoped to _tree", Scope: []string{normOverridePath("src/dos/_tree.py")}}
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles, OverrideFacts: facts, Now: now})
	if d.DecisionTag != "deny" {
		t.Fatalf("scope-miss must DENY, got %q (%s)", d.DecisionTag, d.Render())
	}
}

func TestSelfModifyOverrideScopeHitAdmits(t *testing.T) {
	until := time.Date(2026, 6, 15, 18, 0, 0, 0, time.UTC)
	now := time.Date(2026, 6, 15, 17, 30, 0, 0, time.UTC)
	facts := &OverrideFacts{Until: until, Reason: "scoped to _tree", Scope: []string{normOverridePath("src/dos/_tree.py")}}
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/_tree.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles, OverrideFacts: facts, Now: now})
	if d.DecisionTag != "override-admit" {
		t.Fatalf("scope-hit in window must override-admit, got %q (%s)", d.DecisionTag, d.Render())
	}
}

func TestSelfModifyNilOverrideDeniesBackCompat(t *testing.T) {
	// No facts (disarmed / pre-#186): the deny is byte-unchanged from today.
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "src/dos/arbiter.py"})
	d := Decide(e, Inputs{RuntimeFiles: dosRuntimeFiles}) // OverrideFacts nil, Now zero
	if d.DecisionTag != "deny" {
		t.Fatalf("nil override must DENY (back-compat), got %q (%s)", d.DecisionTag, d.Render())
	}
}

func TestOverrideNeverWavesThroughCollision(t *testing.T) {
	// The arm file is a SELF_MODIFY instrument only: a plain lease COLLISION (no
	// reason_class) must NOT be converted even inside an armed window.
	facts, now := armedWindow()
	held := []lease{{lane: "docs", tree: []string{"docs/**"}}}
	e := eventFor("Edit", "/work/workspace", map[string]any{"file_path": "docs/ARCHITECTURE.md"})
	d := Decide(e, Inputs{LiveLeases: held, OverrideFacts: facts, Now: now})
	if d.DecisionTag == "override-admit" {
		t.Fatalf("a collision deny must never be override-admitted, got %s", d.Render())
	}
}
