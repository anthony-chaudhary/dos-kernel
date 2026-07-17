package hook

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeCallShapeToml(t *testing.T, ws, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(ws, "dos.toml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func pretoolEventBytes(t *testing.T, ws, tool string, input map[string]any) []byte {
	t.Helper()
	ev := map[string]any{
		"hook_event_name": "PreToolUse",
		"session_id":      "s1",
		"cwd":             ws,
		"tool_name":       tool,
		"tool_input":      input,
	}
	out, err := json.Marshal(ev)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func quietPretoolEnv(t *testing.T) {
	t.Helper()
	t.Setenv("DISPATCH_LANE_JOURNAL_PATH", "")
	t.Setenv("JOB_LANE_JOURNAL_PATH", "")
	t.Setenv("DOS_LOOP", "")
	t.Setenv("CID_RUN_ID", "")
	t.Setenv("DISPATCH_LOOP_TS", "")
}

func TestReadCallShapeAbsentTableIsEmpty(t *testing.T) {
	ws := t.TempDir()
	writeCallShapeToml(t, ws, "[lanes]\n")
	rs, err := ReadCallShape(ws)
	if err != nil {
		t.Fatalf("absent [call_shape] must not error: %v", err)
	}
	if !rs.isEmpty() {
		t.Fatalf("absent [call_shape] must produce an empty ruleset: %#v", rs)
	}
}

func TestReadCallShapeWorkspaceWideAndPerLaneUnion(t *testing.T) {
	ws := t.TempDir()
	writeCallShapeToml(t, ws, `
[call_shape]
forbidden_command_prefixes = ["Curl", "Git Push"]
forbidden_arg_patterns = ["@evil.example"]
forbidden_path_globs = [" **/.env "]

[call_shape.Bash]
forbidden_command_prefixes = ["Scp"]
`)
	rs, err := ReadCallShape(ws)
	if err != nil {
		t.Fatalf("read call_shape: %v", err)
	}

	base := rs.policyFor("Write")
	if got := base.ForbiddenCommandPrefixes; len(got) != 2 ||
		!equalTokens(got[0], []string{"curl"}) ||
		!equalTokens(got[1], []string{"git", "push"}) {
		t.Fatalf("workspace command prefixes not tokenized/lowercased: %#v", got)
	}
	if got := base.ForbiddenPathGlobs; len(got) != 1 || got[0] != "**/.env" {
		t.Fatalf("path globs must be trimmed, got %#v", got)
	}

	bash := rs.policyFor("Bash")
	if len(bash.ForbiddenCommandPrefixes) != 3 ||
		!equalTokens(bash.ForbiddenCommandPrefixes[0], []string{"curl"}) ||
		!equalTokens(bash.ForbiddenCommandPrefixes[2], []string{"scp"}) {
		t.Fatalf("per-lane policy must union with workspace floor, got %#v", bash.ForbiddenCommandPrefixes)
	}
}

func TestReadCallShapeMalformedReturnsError(t *testing.T) {
	ws := t.TempDir()
	writeCallShapeToml(t, ws, `
[call_shape]
forbidden_arg_patterns = "not-a-list"
`)
	if _, err := ReadCallShape(ws); err == nil {
		t.Fatalf("malformed declared call_shape must return an error")
	}
}

func TestReadCallShapeUnknownKeyReturnsError(t *testing.T) {
	ws := t.TempDir()
	writeCallShapeToml(t, ws, `
[call_shape]
forbidden_typo = ["curl"]
`)
	if _, err := ReadCallShape(ws); err == nil {
		t.Fatalf("unknown call_shape key must return an error")
	}
}

func TestReadCallShapeArrayOfTableReturnsError(t *testing.T) {
	ws := t.TempDir()
	writeCallShapeToml(t, ws, `
[[call_shape]]
forbidden_command_prefixes = ["curl"]
`)
	if _, err := ReadCallShape(ws); err == nil {
		t.Fatalf("array-of-table call_shape declarations must return an error")
	}
}

func TestDecidePretoolReadsCallShapeFromDosToml(t *testing.T) {
	quietPretoolEnv(t)
	ws := t.TempDir()
	writeCallShapeToml(t, ws, `
[call_shape]
forbidden_command_prefixes = ["curl"]
`)
	stdin := pretoolEventBytes(t, ws, "Bash", map[string]any{"command": "curl https://example.invalid/data"})
	res := DecidePretool(stdin, ws, "", nil)

	if res.Decision.DecisionTag != "deny" {
		t.Fatalf("declared forbidden command must deny, got %q (%s)", res.Decision.DecisionTag, res.Stdout)
	}
	if res.Decision.ReasonClass != forbiddenCallShapeReason {
		t.Fatalf("want %s reason_class, got %q", forbiddenCallShapeReason, res.Decision.ReasonClass)
	}
	if !strings.Contains(res.Stdout, `"permissionDecision": "deny"`) ||
		!strings.Contains(res.Stdout, "FORBIDDEN_CALL_SHAPE") {
		t.Fatalf("deny stdout must surface the forbidden call shape: %s", res.Stdout)
	}
}

func TestDecidePretoolReadsPerLaneUnionFromDosToml(t *testing.T) {
	quietPretoolEnv(t)
	ws := t.TempDir()
	writeCallShapeToml(t, ws, `
[call_shape]
forbidden_command_prefixes = ["git push"]

[call_shape.Bash]
forbidden_command_prefixes = ["scp"]
`)

	for _, command := range []string{"git push origin main", "scp file host:/tmp"} {
		stdin := pretoolEventBytes(t, ws, "Bash", map[string]any{"command": command})
		res := DecidePretool(stdin, ws, "", nil)
		if res.Decision.DecisionTag != "deny" {
			t.Fatalf("%q must deny from disk-loaded workspace/lane union, got %q (%s)",
				command, res.Decision.DecisionTag, res.Stdout)
		}
		if res.Decision.ReasonClass != forbiddenCallShapeReason {
			t.Fatalf("%q want %s reason_class, got %q", command, forbiddenCallShapeReason, res.Decision.ReasonClass)
		}
	}
}

func TestDecidePretoolMalformedCallShapeFailsClosed(t *testing.T) {
	quietPretoolEnv(t)
	ws := t.TempDir()
	writeCallShapeToml(t, ws, `
[call_shape]
forbidden_arg_patterns = "not-a-list"
`)
	stdin := pretoolEventBytes(t, ws, "Bash", map[string]any{"command": "echo harmless"})
	res := DecidePretool(stdin, ws, "", nil)

	if res.Decision.DecisionTag != "deny" {
		t.Fatalf("malformed declared call_shape must fail closed, got %q (%s)", res.Decision.DecisionTag, res.Stdout)
	}
	if res.Decision.ReasonClass != forbiddenCallShapeReason {
		t.Fatalf("malformed call_shape deny must carry %s, got %q", forbiddenCallShapeReason, res.Decision.ReasonClass)
	}
	if !strings.Contains(res.Stdout, "malformed [call_shape]") ||
		!strings.Contains(res.Stdout, "FORBIDDEN_CALL_SHAPE") {
		t.Fatalf("malformed config deny must surface the parse fault, got: %s", res.Stdout)
	}
}
