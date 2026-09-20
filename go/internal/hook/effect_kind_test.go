package hook

import (
	"strings"
	"testing"
)

func TestKnownEmptyHostToolsPreserveEffects(t *testing.T) {
	cases := map[string]EffectKind{
		"Agent": EffectSpawn, "Task": EffectCoordination,
		"TaskCreate": EffectCoordination, "TaskUpdate": EffectCoordination,
		"ToolSearch":                   EffectCapability,
		"collaborationspawn_agent":     EffectSpawn,
		"collaborationsend_message":    EffectCoordination,
		"collaborationfollowup_task":   EffectCoordination,
		"collaborationinterrupt_agent": EffectCoordination,
		"create_goal":                  EffectCoordination, "update_goal": EffectCoordination,
		"mcp__codex_app__send_message_to_thread": EffectCoordination,
		"collaborationwait_agent":                EffectNone, "collaborationlist_agents": EffectNone,
		"get_goal": EffectNone, "clocksleep": EffectNone, "clockcurr_time": EffectNone,
		"mcp__codex_app__wait_threads": EffectNone,
		"mcp__codex_app__read_thread":  EffectNone,
		"mcp__codex_app__list_threads": EffectNone,
	}
	for tool, want := range cases {
		t.Run(tool, func(t *testing.T) {
			e := eventFor(tool, "/work/workspace", map[string]any{"message": "hello"})
			tree, known := e.treeFromEvent()
			if !known || len(tree) != 0 || e.isMutatingTool() {
				t.Fatalf("file axis = (%v, %v, mutating=%v), want known-empty/non-mutating", tree, known, e.isMutatingTool())
			}
			d := Decide(e, Inputs{LiveLeases: []lease{{lane: "held", tree: []string{"**"}}}})
			if d.DecisionTag != "passthrough" || d.Render() != "" || d.EffectKind != want {
				t.Fatalf("decision=(%s, %q, %q), want clean passthrough effect %q", d.DecisionTag, d.Render(), d.EffectKind, want)
			}
		})
	}
}

func TestUnsafeAndUnknownToolsKeepUnknownWriteFootprint(t *testing.T) {
	for _, tool := range []string{
		"apply_patch", "mcp__codex_app__automation_update", "webrun", "mcp__dos__dos_arbitrate", "mcp__unknown__mutate",
	} {
		t.Run(tool, func(t *testing.T) {
			e := eventFor(tool, "/work/workspace", map[string]any{"path": "docs/x.md"})
			if tree, known := e.treeFromEvent(); known || len(tree) != 0 {
				t.Fatalf("tree=(%v, %v), want unknown", tree, known)
			}
			d := Decide(e, Inputs{LiveLeases: []lease{{lane: "held", tree: []string{"**"}}}})
			if d.DecisionTag != "warn" || d.ReasonClass != "UNRESOLVED_WRITE_FOOTPRINT" || d.TreeKnown {
				t.Fatalf("decision=%+v, want unresolved-footprint warning", d)
			}
		})
	}
}

func TestKnownEmptyEffectToolStillHonorsCallShape(t *testing.T) {
	e := eventFor("collaborationsend_message", "/work/workspace", map[string]any{"message": "forbidden payload"})
	d := Decide(e, Inputs{CallShape: CallShapeRuleset{WorkspaceWide: CallShapePolicy{
		ForbiddenArgPatterns: []string{"forbidden"},
	}}})
	if d.DecisionTag != "deny" || d.ReasonClass != forbiddenCallShapeReason {
		t.Fatalf("decision=%+v, want call-shape deny", d)
	}
	if d.EffectKind != EffectCoordination {
		t.Fatalf("effect=%q, want coordination", d.EffectKind)
	}
}

func TestEffectKindTelemetryAndEnforceProjection(t *testing.T) {
	e := eventFor("collaborationspawn_agent", "/work/workspace", map[string]any{})
	d := Decide(e, Inputs{})
	entry := (Observation{Verb: "pretool", Outcome: d.DecisionTag, EffectKind: d.EffectKind}).toEntry()
	if entry["effect_kind"] != string(EffectSpawn) {
		t.Fatalf("observation effect_kind=%v, want spawn", entry["effect_kind"])
	}
	d.DecisionTag, d.Rung, d.Reason = "warn", "admission", "test"
	body := enforceBody(e, d)
	enforce := enforceEntry(e, d, body)
	if body["effect_kind"] != string(EffectSpawn) || enforce["effect_kind"] != string(EffectSpawn) {
		t.Fatalf("ENFORCE projections lost effect: body=%v entry=%v", body, enforce)
	}

	none := (Observation{Verb: "pretool", Outcome: "passthrough", EffectKind: EffectNone}).toEntry()
	if _, ok := none["effect_kind"]; ok {
		t.Fatalf("none effect must remain additive-optional: %s", strings.TrimSpace(pyJSONDumps(none)))
	}
}

func TestEffectKindStatsFold(t *testing.T) {
	t.Setenv("DOS_HOOK_METRICS", "1")
	workspace := t.TempDir()
	recordObservation(workspace, false, Observation{
		Verb:       "pretool",
		Outcome:    "passthrough",
		EffectKind: EffectSpawn,
	})

	agg := foldObservations(obsLogPath(workspace))
	if got := agg.ByEffect[string(EffectSpawn)]; got != 1 {
		t.Fatalf("spawn count=%d, want 1", got)
	}
	if out := renderStatsJSON(agg); !strings.Contains(out, `"by_effect_kind": {"spawn": 1}`) {
		t.Fatalf("stats JSON lost effect kind: %s", out)
	}
}
