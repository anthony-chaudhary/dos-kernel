package hook

// EffectKind preserves non-filesystem host effects after the file-footprint
// axis has proved empty. A tool absent from the closed host map has no classified
// non-file effect; its FILE footprint independently remains UNKNOWN.
type EffectKind string

const (
	EffectNone         EffectKind = "none"
	EffectSpawn        EffectKind = "spawn"
	EffectCoordination EffectKind = "coordination"
	EffectCapability   EffectKind = "capability"
)

// knownEmptyToolEffects proves only an exact tool's repository-file footprint
// is empty. EffectKind separately keeps its non-file effect visible.
var knownEmptyToolEffects = map[string]EffectKind{
	"Agent":      EffectSpawn,
	"Task":       EffectCoordination,
	"TaskCreate": EffectCoordination,
	"TaskUpdate": EffectCoordination,
	"ToolSearch": EffectCapability,

	"collaborationspawn_agent":               EffectSpawn,
	"collaborationsend_message":              EffectCoordination,
	"collaborationfollowup_task":             EffectCoordination,
	"collaborationinterrupt_agent":           EffectCoordination,
	"create_goal":                            EffectCoordination,
	"update_goal":                            EffectCoordination,
	"mcp__codex_app__send_message_to_thread": EffectCoordination,
	"collaborationwait_agent":                EffectNone,
	"collaborationlist_agents":               EffectNone,
	"get_goal":                               EffectNone,
	"clocksleep":                             EffectNone,
	"clockcurr_time":                         EffectNone,
	"mcp__codex_app__wait_threads":           EffectNone,
	"mcp__codex_app__read_thread":            EffectNone,
	"mcp__codex_app__list_threads":           EffectNone,
	"mcp__dos__dos_answer":                   EffectNone,
	"mcp__dos__dos_arbitrate":                EffectNone,
	"mcp__dos__dos_check_reason":             EffectNone,
	"mcp__dos__dos_citation_resolve":         EffectNone,
	"mcp__dos__dos_commit_audit":             EffectNone,
	"mcp__dos__dos_doctor":                   EffectNone,
	"mcp__dos__dos_recall":                   EffectNone,
	"mcp__dos__dos_refuse_reasons":           EffectNone,
	"mcp__dos__dos_review":                   EffectNone,
	"mcp__dos__dos_status":                   EffectNone,
	"mcp__dos__dos_verify":                   EffectNone,
}

func (e *Event) effectKind() EffectKind {
	if e == nil {
		return EffectNone
	}
	if kind, ok := knownEmptyToolEffects[e.ToolName]; ok {
		return kind
	}
	return EffectNone
}

func knownEmptyTool(name string) bool {
	_, ok := knownEmptyToolEffects[name]
	return ok
}
