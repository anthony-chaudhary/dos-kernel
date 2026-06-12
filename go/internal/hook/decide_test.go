package hook

import (
	"strings"
	"testing"
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

func TestReadAgainstContendedLaneWarnsNotDenies(t *testing.T) {
	// FQ-532 Defect 3: a READ-ONLY tool has a KNOWN but EMPTY tree — it provably
	// touches nothing, so it can NEVER collide. Against a live `src` lease the
	// disjointness predicate refuses it ("empty requested tree vs known lease")
	// with NO reason_class, and the OLD `reason_class || treeKnown` gate escalated
	// that contention-only refusal to a hard DENY for every Read/Grep — the
	// phantom-lane denial that blocked real agent reads. The fix keeps a
	// contention-only refusal ADVISORY (WARN, no permissionDecision) regardless of
	// treeKnown, so the read passes through. A Bash (unknown tree) already WARNed;
	// this closes the Read/Edit-vs-Bash asymmetry.
	for _, tool := range []string{"Read", "Grep"} {
		e := eventFor(tool, "/work/workspace", map[string]any{"file_path": "docs/x.md"})
		in := Inputs{
			LiveLeases:   []lease{{lane: "src", tree: []string{"src/**"}}},
			RuntimeFiles: dosRuntimeFiles,
		}
		d := Decide(e, in)
		if d.DecisionTag != "warn" {
			t.Fatalf("%s against a contended lane must WARN (a read cannot collide), got %q (%s)",
				tool, d.DecisionTag, d.Render())
		}
		if strings.Contains(d.Render(), "permissionDecision") {
			t.Fatalf("%s WARN must not carry permissionDecision (must pass through): %s", tool, d.Render())
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
