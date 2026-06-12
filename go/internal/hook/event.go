package hook

import (
	"sort"
	"strings"
)

// Event is the parsed CC PreToolUse event. Only the fields the PRE decider reads
// are typed; the rest of the JSON is ignored. `raw` retains the decoded top-level
// map so the structural PRE guard can test for the presence of result keys
// (`tool_response`/`tool_output`) regardless of their type.
type Event struct {
	HookEventName string
	SessionID     string
	Cwd           string
	ToolName      string
	ToolInput     map[string]any
	raw           map[string]any
}

// resultKeys are the CC PostToolUse result keys whose ABSENCE marks a PRE event —
// `dos.pretool_sensor._RESULT_KEYS`.
var resultKeys = []string{"tool_response", "tool_output"}

// pathArgKeys are the tool_input keys naming a filesystem path —
// `dos.pretool_sensor._PATH_ARG_KEYS`.
var pathArgKeys = []string{"file_path", "path", "notebook_path"}

// readOnlyTools never take an admission tree — `dos.pretool_sensor._READ_ONLY_TOOLS`.
var readOnlyTools = map[string]struct{}{
	"Read": {}, "Grep": {}, "Glob": {}, "LS": {},
	"NotebookRead": {}, "WebFetch": {}, "WebSearch": {},
}

// writeTools are the generic CC edit/write tools — `dos.pretool_sensor._WRITE_TOOLS`.
var writeTools = map[string]struct{}{
	"Write": {}, "Edit": {}, "MultiEdit": {}, "NotebookEdit": {},
}

// isPreEvent reports whether this looks like a PreToolUse event we should act on —
// port of `dos.pretool_sensor.is_pre_event`. The structural PRE marker: a
// tool_name present AND no tool RESULT key. A hook_event_name other than
// "PreToolUse" (when present) disqualifies; its absence does not.
func (e *Event) isPreEvent() bool {
	if e.ToolName == "" {
		return false
	}
	if e.HookEventName != "" && e.HookEventName != "PreToolUse" {
		return false
	}
	for _, k := range resultKeys {
		if v, ok := e.raw[k]; ok && v != nil {
			return false
		}
	}
	return true
}

// treeFromEvent returns the admission tree for the proposed call + whether the
// tree is KNOWN — port of `dos.pretool_sensor._tree_from_event`.
//
//   - read-only tool -> ((), true)  (empty-known: a read takes no tree, admits).
//   - write/edit tool with a path arg -> ((repoRel(path),), true).
//   - Bash with path-shaped tokens -> (those paths repo-relative, true).
//   - any other (write tool with no path, Bash with no path, unrecognized tool)
//     -> ((), false)  (UNKNOWN tree — conservative blast radius).
func (e *Event) treeFromEvent() (tree []string, known bool) {
	tn := e.ToolName
	if tn == "" {
		return nil, false
	}
	if _, ro := readOnlyTools[tn]; ro {
		return nil, true // known-empty: a read takes no tree
	}
	ti := e.ToolInput
	// A direct path arg (Write/Edit/NotebookEdit and the like).
	for _, k := range pathArgKeys {
		if v, ok := ti[k]; ok {
			if s, isStr := v.(string); isStr && strings.TrimSpace(s) != "" {
				return []string{e.repoRelative(strings.TrimSpace(s))}, true
			}
		}
	}
	if tn == "Bash" {
		if v, ok := ti["command"]; ok {
			if cmd, isStr := v.(string); isStr && strings.TrimSpace(cmd) != "" {
				if commandHasNoWriteFootprint(cmd) {
					return nil, true // known-empty: the invoked programs cannot write a path
				}
				paths := pathsFromCommand(cmd)
				if len(paths) > 0 {
					out := make([]string, 0, len(paths))
					for _, p := range paths {
						out = append(out, e.repoRelative(p))
					}
					return out, true
				}
			}
		}
		return nil, false // unknown command footprint
	}
	if _, w := writeTools[tn]; w {
		return nil, false // a write tool with no resolvable path
	}
	// An unrecognized (possibly mutating MCP) tool -> unknown tree.
	return nil, false
}

// repoRelative is the best-effort repo-relative POSIX form of a path — port of
// `dos.pretool_sensor._repo_relative`. Normalizes separators and, when the path is
// under the event's cwd, strips that prefix; otherwise the POSIX-normalized form
// with leading slashes stripped.
func (e *Event) repoRelative(path string) string {
	p := strings.ReplaceAll(path, "\\", "/")
	if e.Cwd != "" {
		c := strings.TrimRight(strings.ReplaceAll(e.Cwd, "\\", "/"), "/")
		if strings.HasPrefix(p, c+"/") {
			return p[len(c)+1:]
		}
	}
	return strings.TrimLeft(p, "/")
}

// noWriteFootprintPrefixes is the closed set of command invocations that cannot
// WRITE a filesystem path named in their arguments — port of
// `dos.pretool_sensor._NO_WRITE_FOOTPRINT_PREFIXES` (issue #12). Keys are the
// invocation prefix tokens joined with a single space; a one-token key admits the
// program with any arguments, a longer key admits only that subcommand. See the
// Python twin for the membership rationale and the deliberate exclusions.
var noWriteFootprintPrefixes = func() map[string]struct{} {
	out := map[string]struct{}{}
	for _, p := range []string{
		// stdout-only filters/reporters.
		"cat", "grep", "rg", "head", "tail", "wc", "ls",
		"stat", "du", "df", "pwd", "echo", "printf", "which",
		"diff", "cmp", "cut", "tr", "nl", "basename", "dirname",
		"realpath", "readlink", "md5sum", "sha1sum", "sha256sum",
	} {
		out[p] = struct{}{}
	}
	for _, sub := range []string{
		"log", "diff", "status", "show", "blame", "grep", "rev-parse", "rev-list",
		"ls-files", "ls-tree", "ls-remote", "cat-file", "describe", "shortlog",
		"name-rev", "merge-base", "check-ignore",
	} {
		out["git "+sub] = struct{}{}
	}
	for _, verb := range []string{
		"create", "list", "view", "comment", "close", "reopen", "edit", "status",
		"lock", "unlock", "pin", "unpin", "transfer",
	} {
		out["gh issue "+verb] = struct{}{}
	}
	for _, verb := range []string{
		"create", "list", "view", "diff", "checks", "status", "comment",
		"close", "reopen", "edit",
	} {
		out["gh pr "+verb] = struct{}{}
	}
	for _, p := range []string{
		"gh label", "gh search", "gh api",
		"gh run list", "gh run view",
		"gh release list", "gh release view",
		"gh repo view",
	} {
		out[p] = struct{}{}
	}
	return out
}()

// shellWriteMetachars can route bytes into a file (or run a hidden command) around
// the invoked program — port of `dos.pretool_sensor._SHELL_WRITE_METACHARS`.
var shellWriteMetachars = []string{">", "`", "$(", "<("}

// segmentSeparators join command segments; two-char operators replace before their
// one-char prefixes — port of `dos.pretool_sensor._SEGMENT_SEPARATORS`.
var segmentSeparators = []string{"&&", "||", ";", "|", "&", "\n"}

// segmentLeadTokens returns the invoked program token + up to two non-flag
// subcommand tokens — port of `dos.pretool_sensor._segment_lead_tokens`. Skips
// leading VAR=value assignments, lower-cases the program's basename, skips flags
// between program and subcommand. Wrappers (sudo/env/…) are NOT skipped: a wrapped
// command reports the wrapper, fails the lookup, and stays conservative.
func segmentLeadTokens(segment string, limit int) []string {
	var toks []string
	for _, raw := range strings.Fields(segment) {
		tok := strings.TrimSpace(raw)
		if tok == "" {
			continue
		}
		if len(toks) == 0 {
			if i := strings.Index(tok, "="); i > 0 {
				head := tok[:i]
				ok := true
				for _, c := range head {
					if !(c == '_' || ('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z') || ('0' <= c && c <= '9')) {
						ok = false
						break
					}
				}
				if ok {
					continue // a leading VAR=value assignment — skip
				}
			}
			base := strings.ReplaceAll(tok, "\\", "/")
			if i := strings.LastIndex(base, "/"); i != -1 {
				base = base[i+1:]
			}
			toks = append(toks, strings.ToLower(base))
		} else {
			if strings.HasPrefix(tok, "-") {
				continue // a flag between program and subcommand
			}
			toks = append(toks, strings.ToLower(tok))
		}
		if len(toks) >= limit {
			break
		}
	}
	return toks
}

// commandHasNoWriteFootprint reports whether EVERY segment of cmd invokes a known
// no-write-footprint program with no shell metacharacter to write around them —
// port of `dos.pretool_sensor._command_has_no_write_footprint` (issue #12). It can
// only ADMIT-MORE for commands provably unable to write; one metacharacter or one
// unrecognized segment and the caller falls back to the conservative scrape.
func commandHasNoWriteFootprint(cmd string) bool {
	for _, meta := range shellWriteMetachars {
		if strings.Contains(cmd, meta) {
			return false
		}
	}
	work := cmd
	for _, sep := range segmentSeparators {
		work = strings.ReplaceAll(work, sep, "\x00")
	}
	any := false
	for _, seg := range strings.Split(work, "\x00") {
		seg = strings.TrimSpace(seg)
		if seg == "" {
			continue
		}
		any = true
		toks := segmentLeadTokens(seg, 3)
		if len(toks) == 0 {
			return false
		}
		matched := false
		for depth := 1; depth <= len(toks); depth++ {
			if _, ok := noWriteFootprintPrefixes[strings.Join(toks[:depth], " ")]; ok {
				matched = true
				break
			}
		}
		if !matched {
			return false
		}
	}
	return any
}

// pathsFromCommand is a best-effort scrape of path-shaped tokens from a Bash
// command — port of `dos.pretool_sensor._paths_from_command`. NOT a shell parser:
// a token is path-shaped if it contains "/" and its final segment (after the last
// "/") contains "." and it does not start with "-". De-duplicated, order-preserved.
func pathsFromCommand(cmd string) []string {
	repl := strings.NewReplacer(";", " ", "|", " ", "&", " ")
	fields := strings.Fields(repl.Replace(cmd))
	seen := map[string]struct{}{}
	var out []string
	for _, raw := range fields {
		tok := strings.Trim(raw, "\"'()<>")
		if !strings.Contains(tok, "/") {
			continue
		}
		if strings.HasPrefix(tok, "-") {
			continue
		}
		last := tok
		if i := strings.LastIndex(tok, "/"); i != -1 {
			last = tok[i+1:]
		}
		if !strings.Contains(last, ".") {
			continue
		}
		if _, dup := seen[tok]; dup {
			continue
		}
		seen[tok] = struct{}{}
		out = append(out, tok)
	}
	return out
}

// isMutatingTool reports whether the proposed call mutates state — port of
// `dos.pretool_sensor.is_mutating_tool`. FAIL-OPEN: an explicit read-only tool is a
// read; everything else is treated as mutating (for the Rung-B provenance gate
// only). Currently the provenance gate runs only when a ruling handler is wired
// (see decide()); kept here for completeness and GHF2/GHF5 convergence.
func (e *Event) isMutatingTool() bool {
	if e.ToolName == "" {
		return false
	}
	_, ro := readOnlyTools[e.ToolName]
	return !ro
}

// sortedToolInputKeys returns the tool_input keys in a stable order — used only by
// diagnostics; the decision never depends on arg order.
func (e *Event) sortedToolInputKeys() []string {
	keys := make([]string, 0, len(e.ToolInput))
	for k := range e.ToolInput {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
