package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestCodexLifecycleObservationCarriesJoinIdentity(t *testing.T) {
	ws := t.TempDir()
	profile := filepath.Join(t.TempDir(), "profile")
	t.Setenv("CODEX_HOME", profile)
	payload := `{"hook_event_name":"PreToolUse","session_id":"thread-1","tool_use_id":"call-1","cwd":` + quote(ws) + `,"tool_name":"Read","tool_input":{}}`
	if code := run([]string{"pretool", "--workspace", ws}, bytes.NewBufferString(payload), &bytes.Buffer{}, &bytes.Buffer{}); code != 0 {
		t.Fatalf("run code=%d", code)
	}
	raw, err := os.ReadFile(filepath.Join(ws, ".dos", "metrics", "observations.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(raw), &got); err != nil {
		t.Fatal(err)
	}
	if got["call_id"] != "call-1" || got["session_id"] != "thread-1" || got["phase_state"] != "succeeded" || got["profile"] != profile || got["workspace"] != ws {
		t.Fatalf("observation=%v", got)
	}
}

func TestCodexLifecycleObservationTypesMalformedEnvelopeSkipped(t *testing.T) {
	ws := t.TempDir()
	if code := run([]string{"posttool", "--workspace", ws}, bytes.NewBufferString(`{}`), &bytes.Buffer{}, &bytes.Buffer{}); code != 0 {
		t.Fatalf("run code=%d", code)
	}
	raw, err := os.ReadFile(filepath.Join(ws, ".dos", "metrics", "observations.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(raw), &got); err != nil {
		t.Fatal(err)
	}
	if got["phase_state"] != "skipped" {
		t.Fatalf("observation=%v", got)
	}
}

func quote(s string) string { b, _ := json.Marshal(s); return string(b) }
