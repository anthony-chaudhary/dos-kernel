package hook

import "testing"

func TestShellWriteFootprintBenignRedirections(t *testing.T) {
	for _, cmd := range []string{
		`Get-Content -Raw AGENTS.md 2>$null`,
		`rg -n TODO . 2>&1`,
		`git status 2>/dev/null`,
	} {
		if !commandHasNoWriteFootprint(cmd) {
			t.Errorf("benign stream/null redirection must remain no-file-write: %q", cmd)
		}
	}
}

func TestShellWriteFootprintNullDevicePrefixesRemainWrites(t *testing.T) {
	for _, cmd := range []string{
		`git status > nul.txt`,
		`git status > nul/out.txt`,
		`git status > /dev/null.log`,
		`git status > $null.log`,
		`git status 2>$null`,
		`git status 2>nul`,
	} {
		if commandHasNoWriteFootprint(cmd) {
			t.Errorf("null-device prefix must not hide a file write: %q", cmd)
		}
	}
}

func TestShellWriteFootprintPowerShellSuccessBlock(t *testing.T) {
	for _, cmd := range []string{
		`Get-Command git; if ($?) { git status }`,
		`git status; if ($?) { Get-ChildItem -File }`,
	} {
		if !commandHasNoWriteFootprint(cmd) {
			t.Errorf("read-only PowerShell success block must remain no-file-write: %q", cmd)
		}
	}

	for _, cmd := range []string{
		`git status; if ($?) { rm src/dos/arbiter.py }`,
		`git status; if ($?) { echo x > out.txt }`,
		`Get-Command missing; if ($?) { git status } else { rm victim.txt }`,
		`Get-Command missing; if ($?) { git status } elseif ($?) { rm victim.txt }`,
	} {
		if commandHasNoWriteFootprint(cmd) {
			t.Errorf("mutating PowerShell success block must stay unresolved/write-shaped: %q", cmd)
		}
	}
}

func TestBenignRedirectReadPassesCleanAgainstContendedLane(t *testing.T) {
	e := eventFor("Bash", "/work/workspace", map[string]any{
		"command": `Get-Content -Raw AGENTS.md 2>$null`,
	})
	d := Decide(e, Inputs{LiveLeases: []lease{{lane: "ops", tree: []string{"platform/ops/**"}}}})
	if d.DecisionTag != "passthrough" || !d.TreeKnown || d.Render() != "" {
		t.Fatalf("benign redirected read must pass clean, got %#v render=%q", d, d.Render())
	}
}

func TestUnknownMutatorsStillWarn(t *testing.T) {
	for _, tc := range []struct {
		name  string
		tool  string
		input map[string]any
	}{
		{name: "unknown shell command", tool: "Bash", input: map[string]any{"command": "npm test"}},
		{name: "pathless write", tool: "Write", input: map[string]any{"content": "x"}},
		{name: "unknown MCP mutation", tool: "mcp__svc__do", input: map[string]any{"q": "1"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := eventFor(tc.tool, "/work/workspace", tc.input)
			d := Decide(e, Inputs{LiveLeases: []lease{{lane: "ops", tree: []string{"platform/ops/**"}}}})
			if d.DecisionTag != "warn" || d.TreeKnown || d.ReasonClass != "UNRESOLVED_WRITE_FOOTPRINT" {
				t.Fatalf("unknown mutator must retain advisory scope guidance, got %#v", d)
			}
		})
	}
}
