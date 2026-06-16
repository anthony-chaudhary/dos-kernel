package hook

// dialect_transcode — render the canonical Claude-Code hook dict into the bytes a
// NON-Claude-Code host honors (docs/268, the Go side of the docs/217 dialect seam).
//
// The Go fast-path used to emit the Claude-Code envelope unconditionally, silently
// ignoring `--dialect`. For a host whose deny grammar is NOT CC's nested
// `permissionDecision` (Gemini, Cursor, Antigravity), that is a fail-OPEN: the host
// receives bytes it does not parse, finds no refusal, and proceeds — a correctly
// computed DENY (e.g. SELF_MODIFY) is dropped. This closes that gap.
//
// The design mirrors the Python kernel exactly (`dos.hook_dialect`): the CC dict the
// decider already builds (`denyPayload`/`warnPayload`/`postWarnPayload`) is the
// dialect-NEUTRAL lingua franca, and a host renderer TRANSCODES it. So:
//
//   - the decision logic stays vendor-blind (the verdict is computed once, in CC
//     shape — no branch on which host is acting);
//   - `--dialect` selects an OUTPUT transform, strictly downstream of the decided
//     verdict (exactly where the vendor-agnostic-kernel litmus says a vendor name
//     belongs — the render step, never the adjudication);
//   - `Decision.Dialect` and the durable journal record stay CC byte-for-byte, so
//     every existing parity test (which gates the CC projection) is untouched.
//
// Unlike the Python side, Go needs no entry-point plugin machinery: the non-CC
// dialects are kernel-known closed data that ship in the binary anyway, so a plain
// switch is the whole mechanism (~a few small transforms, not a framework). The
// built-in `claude-code` stays the unshadowable default (return the dict as-is).
//
// Parity contract (docs/124, extended from "the CC projection" to "every dialect
// projection"): for every (verdict, dialect) the Go bytes must equal Python's
// `resolve_dialect(name).render(parse_cc(cc))` bytes. Pinned by parity_dialect_test.go.

const defaultDialect = "claude-code"

// transcodeCC reads the canonical Claude-Code hook dict `cc` (or nil = passthrough)
// and returns the dict to actually emit under `dialect`. A nil input stays nil
// (passthrough, emit nothing) for every dialect — the fail-to-passthrough floor.
//
// An UNKNOWN dialect returns the CC dict unchanged. NOTE this differs from Python's
// `resolve_dialect`, which RAISES on an unknown name. The Go binary is a fail-safe
// hot-path decider that must never die on a host's argument (parseFlags drops unknown
// flags by the same rule), so it degrades to the CC default rather than crashing — and
// the wiring that selects a non-CC dialect is `dos init --hooks <host>`, which only
// ever writes a KNOWN `--dialect`, so an unknown value never reaches here in practice.
// The honest guard against a typo is still the Python resolver at install time.
func transcodeCC(cc map[string]any, dialect string) map[string]any {
	if cc == nil {
		return nil
	}
	switch dialect {
	case "", defaultDialect, "codex", "claude-cowork":
		// claude-code is the neutral form; codex copied CC's envelope verbatim, and
		// Claude Cowork RUNS the CC harness (docs/298) — so both are byte-identical
		// and emit the CC dict unchanged (matching the Python CodexDialect /
		// ClaudeCoworkDialect, which delegate to ClaudeCodeDialect).
		return cc
	case "gemini":
		return renderGemini(parseCC(cc))
	case "antigravity":
		return renderAntigravity(parseCC(cc))
	case "cursor":
		return renderCursor(parseCC(cc))
	case "hermes":
		return renderHermes(parseCC(cc))
	case "copilot-cli":
		return renderCopilotCli(parseCC(cc))
	default:
		// Unknown dialect — degrade to CC (see the doc comment above). Never crash.
		return cc
	}
}

// hookVerdict is the dialect-neutral decision read out of the CC dict — the Go
// analogue of `dos.hook_dialect.HookVerdict` (the fields a renderer needs). `action`
// is "deny" | "warn" | "pass".
type hookVerdict struct {
	action  string
	reason  string // operator-facing why (DENY) — CC's permissionDecisionReason
	context string // a fact to re-surface (WARN payload, or a deny's additionalContext)
}

// parseCC reads a Claude-Code hook dict into the neutral verdict — the Go port of
// `dos.hook_dialect.parse_cc`. A deny → action=deny (reason + any additionalContext);
// an additionalContext with no deny → action=warn (context = that text); anything
// else → action=pass. Total: a malformed shape degrades to pass.
func parseCC(cc map[string]any) hookVerdict {
	hso, ok := cc["hookSpecificOutput"].(map[string]any)
	if !ok {
		return hookVerdict{action: "pass"}
	}
	context, _ := hso["additionalContext"].(string)
	if dec, _ := hso["permissionDecision"].(string); dec == "deny" {
		reason, _ := hso["permissionDecisionReason"].(string)
		return hookVerdict{action: "deny", reason: reason, context: context}
	}
	if context != "" {
		return hookVerdict{action: "warn", context: context}
	}
	return hookVerdict{action: "pass"}
}

// renderGemini emits Gemini CLI's grammar. The DENY envelope is the MOMENT-correct
// one for a BeforeTool hook (which is the ONLY moment the Go decider produces — its
// deny/warn are PreToolUse): Gemini 0.45.x gates the tool-execution path on
// `shouldStopExecution()` (`return this.continue === false`), which does NOT consult
// `decision`. So a PRE deny must emit {"continue": false, "stopReason": …}; a
// {"decision":"deny"} here is a SILENT FAIL-OPEN (the tool runs anyway) — the exact
// gap docs/268 found. The why rides `stopReason` (what `getEffectiveReason()`
// surfaces) and the corrective fact rides hookSpecificOutput.additionalContext.
// Byte-for-byte port of `drivers.hook_dialects.GeminiDialect.render` for the PRE
// moment (the stop-moment {"decision":"block"} branch has no Go caller — the Go
// fast-path is the pretool/posttool decider). Pinned by parity_dialect_test.go.
func renderGemini(v hookVerdict) map[string]any {
	if v.action == "pass" {
		return nil
	}
	if v.action == "deny" {
		out := map[string]any{"continue": false, "stopReason": v.reason}
		if v.context != "" {
			out["hookSpecificOutput"] = map[string]any{"additionalContext": v.context}
		}
		return out
	}
	// warn — additionalContext only (no decision), turn-preserving.
	return map[string]any{"hookSpecificOutput": map[string]any{"additionalContext": v.context}}
}

// renderAntigravity emits Google Antigravity's grammar — a top-level
// {"decision":"deny","reason":…} (Gemini-shaped output, even though its hook CONFIG is
// Claude-Code-shaped, docs/269). Antigravity documents only decision/reason (no
// separate context channel), so a deny's corrective fact is folded into `reason`
// (space-joined); a warn is a bare {"reason":…} with no decision (inert to the
// allow/deny gate). Port of `drivers.hook_dialects.AntigravityDialect`.
func renderAntigravity(v hookVerdict) map[string]any {
	if v.action == "pass" {
		return nil
	}
	if v.action == "deny" {
		out := map[string]any{"decision": "deny"}
		reason := joinNonEmpty(v.reason, v.context)
		if reason != "" {
			out["reason"] = reason
		}
		return out
	}
	return map[string]any{"reason": v.context}
}

// renderCursor emits Cursor's grammar — a top-level {"permission":"deny"} with the
// agent-facing message on agent_message; a warn → {"permission":"allow","agent_message":…}
// (Cursor's only turn-preserving "add context" path). NEVER emits updated_input (the
// byte-author floor). Port of `drivers.hook_dialects.CursorDialect`.
func renderCursor(v hookVerdict) map[string]any {
	if v.action == "pass" {
		return nil
	}
	if v.action == "deny" {
		out := map[string]any{"permission": "deny"}
		msg := v.reason
		if v.context != "" {
			msg = joinNonEmpty(msg, v.context)
		}
		if msg != "" {
			out["agent_message"] = msg
		}
		return out
	}
	return map[string]any{"permission": "allow", "agent_message": v.context}
}

// renderHermes emits Hermes' shell-hook grammar — a flat top-level
// {"decision":"block","reason":…} on a DENY (the canonical block shape Hermes' hook
// parser reads), and the ALLOW object {} on a WARN (Hermes' shell hook has NO
// non-blocking add-context channel, so a turn-preserving verdict can only PASS there;
// the context is dropped — a host coverage limit, surfaced honestly, not smuggled onto
// a field Hermes does not read). The reason + any corrective fact are space-joined into
// the one `reason` field Hermes surfaces. Byte-for-byte port of
// `drivers.hook_dialects.HermesDialect.render`; moment-agnostic (Hermes' pre/post shell
// hooks read the same decision field). Also the renderer the `devin` host aliases
// (its flat {"decision":"block"} deny output, docs: a CC-shaped config + hermes output).
// Pinned by parity_dialect_test.go.
func renderHermes(v hookVerdict) map[string]any {
	if v.action == "pass" {
		return nil
	}
	if v.action == "deny" {
		out := map[string]any{"decision": "block"}
		reason := joinNonEmpty(v.reason, v.context)
		if reason != "" {
			out["reason"] = reason
		}
		return out
	}
	// warn — the ALLOW object {}. No non-blocking context channel; context dropped.
	return map[string]any{}
}

// renderCopilotCli emits the GitHub Copilot CLI grammar — a FLAT top-level
// {"permissionDecision":"deny","permissionDecisionReason":…} on a DENY (the CC field
// names, but un-nested — the Copilot CLI does NOT read CC's hookSpecificOutput wrapper,
// so emitting the nested form would be a silent fail-open), and a turn-preserving
// {"permissionDecision":"allow","permissionDecisionReason":…} on a WARN (the context
// rides the reason; allow never blocks). The reason + any corrective fact are
// space-joined into permissionDecisionReason. Byte-for-byte port of
// drivers.hook_dialects.CopilotCliDialect.render. Pinned by parity_dialect_test.go.
func renderCopilotCli(v hookVerdict) map[string]any {
	if v.action == "pass" {
		return nil
	}
	if v.action == "deny" {
		out := map[string]any{"permissionDecision": "deny"}
		reason := joinNonEmpty(v.reason, v.context)
		if reason != "" {
			out["permissionDecisionReason"] = reason
		}
		return out
	}
	// warn — allow with the context on the reason (turn-preserving, never blocks).
	out := map[string]any{"permissionDecision": "allow"}
	if v.context != "" {
		out["permissionDecisionReason"] = v.context
	}
	return out
}

// joinNonEmpty space-joins the non-empty parts (the Antigravity/Cursor reason fold),
// trimming so neither half is left dangling — matching the Python
// `" ".join(p for p in (...) if p).strip()`.
func joinNonEmpty(parts ...string) string {
	out := ""
	for _, p := range parts {
		if p == "" {
			continue
		}
		if out == "" {
			out = p
		} else {
			out = out + " " + p
		}
	}
	return out
}
