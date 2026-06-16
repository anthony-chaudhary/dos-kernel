package hook

import "testing"

// parity_dialect_test — the Go dialect transcoder (docs/268) matches the Python
// `dos.hook_dialect` renderers byte-for-byte. This extends the docs/124 parity
// contract from "the Claude-Code projection" to "every dialect projection": for a
// representative DENY and WARN verdict, the Go `Decision.RenderAs(dialect)` bytes
// must equal Python's `json.dumps(resolve_dialect(name).render(parse_cc(cc)),
// sort_keys=True)`.
//
// The golden strings below were captured from the LIVE Python renderers (the same
// `pip install -e .` registration CI runs against):
//
//	deny = HookVerdict(PRE, DENY, reason="DOS PRE-admission: SELF_MODIFY blocked",
//	                   context="read it first")
//	warn = HookVerdict(PRE, WARN, context="scope it to a lane")
//	for name in (...): json.dumps(resolve_dialect(name).render(<v>), sort_keys=True)
//
// If a renderer's bytes ever drift from Python's, this fails loudly — the CI ratchet
// that keeps the two implementations honest (the fast Go path can never silently
// emit a different envelope than the Python verb it stands in for).

// ccDenyDict / ccWarnDict are the canonical Claude-Code dicts the decider builds
// (denyPayload / warnPayload), with the reason+context the golden bytes were taken
// over. transcodeCC reads these as the neutral form and re-renders per host.
func ccDenyDict() map[string]any {
	return denyPayload("DOS PRE-admission: SELF_MODIFY blocked", "read it first")
}

func ccWarnDict() map[string]any {
	return warnPayload("scope it to a lane")
}

func TestDialectTranscodeMatchesPythonGoldenBytes(t *testing.T) {
	denyD := Decision{Dialect: ccDenyDict()}
	warnD := Decision{Dialect: ccWarnDict()}

	cases := []struct {
		dialect  string
		wantDeny string
		wantWarn string
	}{
		{
			dialect:  "claude-code",
			wantDeny: `{"hookSpecificOutput": {"additionalContext": "read it first", "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "DOS PRE-admission: SELF_MODIFY blocked"}}`,
			wantWarn: `{"hookSpecificOutput": {"additionalContext": "scope it to a lane", "hookEventName": "PreToolUse"}}`,
		},
		{
			// codex copied CC's envelope verbatim → byte-identical to claude-code.
			dialect:  "codex",
			wantDeny: `{"hookSpecificOutput": {"additionalContext": "read it first", "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "DOS PRE-admission: SELF_MODIFY blocked"}}`,
			wantWarn: `{"hookSpecificOutput": {"additionalContext": "scope it to a lane", "hookEventName": "PreToolUse"}}`,
		},
		{
			// Claude Cowork RUNS the CC harness (docs/298) → byte-identical to claude-code.
			dialect:  "claude-cowork",
			wantDeny: `{"hookSpecificOutput": {"additionalContext": "read it first", "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "DOS PRE-admission: SELF_MODIFY blocked"}}`,
			wantWarn: `{"hookSpecificOutput": {"additionalContext": "scope it to a lane", "hookEventName": "PreToolUse"}}`,
		},
		{
			// A BeforeTool (PRE) deny stops the tool via {"continue": false, …} — the
			// field Gemini's shouldStopExecution() checks. {"decision":"deny"} here is a
			// silent fail-open (docs/268); the Go decider only emits PRE denies, so this
			// is the moment-correct envelope, byte-matched to Python's GeminiDialect.
			dialect:  "gemini",
			wantDeny: `{"continue": false, "hookSpecificOutput": {"additionalContext": "read it first"}, "stopReason": "DOS PRE-admission: SELF_MODIFY blocked"}`,
			wantWarn: `{"hookSpecificOutput": {"additionalContext": "scope it to a lane"}}`,
		},
		{
			// The motivating host (docs/269): Gemini-shaped output, context folded into reason.
			dialect:  "antigravity",
			wantDeny: `{"decision": "deny", "reason": "DOS PRE-admission: SELF_MODIFY blocked read it first"}`,
			wantWarn: `{"reason": "scope it to a lane"}`,
		},
		{
			dialect:  "cursor",
			wantDeny: `{"agent_message": "DOS PRE-admission: SELF_MODIFY blocked read it first", "permission": "deny"}`,
			wantWarn: `{"agent_message": "scope it to a lane", "permission": "allow"}`,
		},
		{
			// Hermes' shell hook: a flat {"decision":"block","reason":…} DENY (context
			// folded into reason), and the ALLOW object {} on a WARN (no non-blocking
			// add-context channel — context dropped). Also the renderer the `devin` host
			// aliases (its flat block deny output, a CC-shaped config + hermes output).
			dialect:  "hermes",
			wantDeny: `{"decision": "block", "reason": "DOS PRE-admission: SELF_MODIFY blocked read it first"}`,
			wantWarn: `{}`,
		},
	}

	for _, c := range cases {
		c := c
		t.Run(c.dialect, func(t *testing.T) {
			if got := denyD.RenderAs(c.dialect); got != c.wantDeny {
				t.Fatalf("DENY dialect %q drift:\n  py: %q\n  go: %q", c.dialect, c.wantDeny, got)
			}
			if got := warnD.RenderAs(c.dialect); got != c.wantWarn {
				t.Fatalf("WARN dialect %q drift:\n  py: %q\n  go: %q", c.dialect, c.wantWarn, got)
			}
		})
	}
}

// TestDialectTranscodePassthroughStaysEmptyEverywhere — a passthrough (nil Dialect)
// emits nothing on EVERY dialect (the fail-to-passthrough floor preserved per host).
func TestDialectTranscodePassthroughStaysEmptyEverywhere(t *testing.T) {
	d := Decision{Dialect: nil}
	for _, dialect := range []string{"", "claude-code", "codex", "gemini", "antigravity", "cursor", "claude-cowork", "hermes", "bogus"} {
		if got := d.RenderAs(dialect); got != "" {
			t.Fatalf("passthrough should be empty on dialect %q, got %q", dialect, got)
		}
	}
}

// TestDialectTranscodeUnknownDegradesToCC — an unknown dialect degrades to the CC
// bytes (the Go fail-safe: never crash on a host's argument), NOT a different
// envelope. This is the documented divergence from Python's fail-LOUD resolver: the
// hot-path binary must not die, and `dos init --hooks` only ever writes a known
// --dialect, so an unknown value never reaches here in practice.
func TestDialectTranscodeUnknownDegradesToCC(t *testing.T) {
	d := Decision{Dialect: ccDenyDict()}
	if got, cc := d.RenderAs("nope"), d.Render(); got != cc {
		t.Fatalf("unknown dialect should degrade to CC bytes:\n  cc: %q\n  go: %q", cc, got)
	}
}

// TestDialectTranscodeNeverEmitsRewriteKey — the docs/191 §4 byte-author floor, on
// the Go side too: no transcoded envelope (deny or warn) carries a tool-input rewrite
// key on any host. Cursor's preToolUse CAN return updated_input; DOS must NOT.
func TestDialectTranscodeNeverEmitsRewriteKey(t *testing.T) {
	for _, d := range []Decision{{Dialect: ccDenyDict()}, {Dialect: ccWarnDict()}} {
		for _, dialect := range []string{"claude-code", "codex", "gemini", "antigravity", "cursor", "claude-cowork", "hermes"} {
			out := d.RenderAs(dialect)
			for _, bad := range []string{"updatedInput", "updated_input", "updatedCommand"} {
				if containsStr(out, bad) {
					t.Fatalf("dialect %q emitted a rewrite key %q in %q", dialect, bad, out)
				}
			}
		}
	}
}

// containsStr is a tiny substring check (avoids importing strings just for the test).
func containsStr(haystack, needle string) bool {
	if len(needle) == 0 {
		return true
	}
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return true
		}
	}
	return false
}
