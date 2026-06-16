package hook

// callshape.go — the native port of `dos.call_shape` (docs/364, OWASP ASI02 Tool
// Misuse & Exploitation). The SELF_MODIFY sibling, generalized from "the write
// touches the kernel's own code" to "the call matches a shape the host DECLARED
// out-of-policy for this lane." It plugs into the admission conjunction LAST
// (after disjointness + self-modify), so a match becomes a real
// FORBIDDEN_CALL_SHAPE refusal that `Decide` renders as a permissionDecision deny.
//
// Byte-for-byte port of the matchers + predicate; the parity corpus pins it.
// Every contract rail of the Python leaf is preserved:
//   - DECLARED, never sniffed — match agent-authored bytes against a host-declared
//     set (command-prefixes / arg-substrings / path-globs); SHAPE-not-word.
//   - Conjunctive / refuse-MORE only — appended last; only ever stricter.
//   - Sound at PRE — reads only the proposed call's own bytes + the declared policy.
//   - OFF by default — an empty policy short-circuits to admit; the default
//     conjunction stays byte-identical to the two-predicate list.
//   - Fail toward ADMIT on ambiguity — an unparseable command under-matches and
//     admits; the whole body never panics (Go has no try/except, but the matchers
//     are total — they cannot panic on any string input).

import (
	"sort"
	"strings"
)

// forbiddenCallShapeReason is the typed reason a FORBIDDEN_CALL_SHAPE refusal
// carries — port of `dos.call_shape.FORBIDDEN_CALL_SHAPE_REASON`. Declared in the
// KERNEL (like selfModifyReason) because the kernel's own predicate emits it; it
// is what makes a call-shape refusal a "provable" deny at PRE (see Decide), and —
// being neither SELF_MODIFY nor "" — it is NEVER softened by the operator-session
// / subagent-in-lane / docs/355 / docs/296 branches (all keyed on those two), so a
// declared ban is a hard deny for everyone, exactly as the Python leaf is.
const forbiddenCallShapeReason = "FORBIDDEN_CALL_SHAPE"

// Note on shared helpers: `segmentSeparators` and `segmentLeadTokens` already live
// in event.go (the Go port of `pretool_sensor`'s versions). The Python kernel leaf
// `call_shape.py` DUPLICATES them because a kernel leaf cannot import upward from
// the helper layer — but in Go they are the SAME package, so this leaf REUSES the
// existing definitions (they are byte-identical in behavior to the Python leaf's
// copies, which is the whole point of the duplication-pinning comment there).

// CallShapePolicy is the forbidden shapes for ONE lane — policy as data. Port of
// `dos.call_shape.CallShapePolicy`. All three rungs optional; an all-empty policy
// isEmpty() and admits everything (the OFF-by-default contract).
//
//   - ForbiddenCommandPrefixes — leading program-token slices that are
//     out-of-policy. ["curl"] forbids the program curl; ["git","push"] forbids
//     `git push` but not `git status`. SHAPE-not-word: matched on the invoked-program
//     leading tokens, never a substring of the whole command.
//   - ForbiddenArgPatterns — LITERAL substrings (not regex) matched against each
//     string argument value AND the raw command string.
//   - ForbiddenPathGlobs — repo-relative globs matched against the proposed write
//     tree via the `tree` prefix algebra (the same self-modify uses).
type CallShapePolicy struct {
	ForbiddenCommandPrefixes [][]string
	ForbiddenArgPatterns     []string
	ForbiddenPathGlobs       []string
}

func (p CallShapePolicy) isEmpty() bool {
	return len(p.ForbiddenCommandPrefixes) == 0 &&
		len(p.ForbiddenArgPatterns) == 0 &&
		len(p.ForbiddenPathGlobs) == 0
}

// union is the additive merge — the conservative direction for a refuse-more
// predicate. Port of `CallShapePolicy.union`: a per-lane table can only ADD
// forbidden shapes onto the workspace floor, never remove one. De-dup preserves
// declaration order.
func (p CallShapePolicy) union(other CallShapePolicy) CallShapePolicy {
	return CallShapePolicy{
		ForbiddenCommandPrefixes: dedupPrefixes(append(append([][]string{},
			p.ForbiddenCommandPrefixes...), other.ForbiddenCommandPrefixes...)),
		ForbiddenArgPatterns: dedupStrings(append(append([]string{},
			p.ForbiddenArgPatterns...), other.ForbiddenArgPatterns...)),
		ForbiddenPathGlobs: dedupStrings(append(append([]string{},
			p.ForbiddenPathGlobs...), other.ForbiddenPathGlobs...)),
	}
}

func dedupStrings(seq []string) []string {
	seen := make(map[string]struct{}, len(seq))
	out := make([]string, 0, len(seq))
	for _, x := range seq {
		if _, ok := seen[x]; ok {
			continue
		}
		seen[x] = struct{}{}
		out = append(out, x)
	}
	return out
}

func dedupPrefixes(seq [][]string) [][]string {
	seen := make(map[string]struct{}, len(seq))
	out := make([][]string, 0, len(seq))
	for _, x := range seq {
		key := strings.Join(x, "\x00")
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, x)
	}
	return out
}

// CallShapeRuleset is the workspace's whole [call_shape] declaration — a per-lane
// lookup. Port of `dos.call_shape.CallShapeRuleset`. WorkspaceWide is the floor
// every lane inherits; PerLane are the additions a specific lane declares.
// policyFor(lane) returns the workspace-wide policy UNION the lane's own.
type CallShapeRuleset struct {
	WorkspaceWide CallShapePolicy
	PerLane       map[string]CallShapePolicy
}

func (r CallShapeRuleset) isEmpty() bool {
	if !r.WorkspaceWide.isEmpty() {
		return false
	}
	for _, p := range r.PerLane {
		if !p.isEmpty() {
			return false
		}
	}
	return true
}

// policyFor returns the effective policy for lane: workspace-wide UNION the lane's
// own. Port of `CallShapeRuleset.policy_for`.
func (r CallShapeRuleset) policyFor(lane string) CallShapePolicy {
	lanePol, ok := r.PerLane[lane]
	if !ok {
		return r.WorkspaceWide
	}
	return r.WorkspaceWide.union(lanePol)
}

// commandSegments splits a command on shell segment separators. Port of
// `dos.call_shape._command_segments` — PURE, not a shell parser.
func commandSegments(command string) []string {
	work := command
	for _, sep := range segmentSeparators {
		work = strings.ReplaceAll(work, sep, "\x00")
	}
	var out []string
	for _, seg := range strings.Split(work, "\x00") {
		seg = strings.TrimSpace(seg)
		if seg != "" {
			out = append(out, seg)
		}
	}
	return out
}

// commandMatchesForbiddenPrefix returns the forbidden command-prefix a command's
// leading tokens match (declaration order, first hit), or nil. Port of
// `dos.call_shape._command_matches_forbidden_prefix`.
func commandMatchesForbiddenPrefix(command string, prefixes [][]string) []string {
	if strings.TrimSpace(command) == "" || len(prefixes) == 0 {
		return nil
	}
	for _, segment := range commandSegments(command) {
		toks := segmentLeadTokens(segment, 3)
		if len(toks) == 0 {
			continue
		}
		for _, prefix := range prefixes {
			if len(prefix) == 0 {
				continue
			}
			if len(prefix) > len(toks) {
				continue
			}
			if equalTokens(toks[:len(prefix)], prefix) {
				return prefix
			}
		}
	}
	return nil
}

func equalTokens(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// argMatchesForbiddenPattern returns the first forbidden arg-substring matched
// (the pattern), or "" with ok=false. LITERAL substring match (never regex)
// against each string arg value AND the raw command string. Port of
// `dos.call_shape._arg_matches_forbidden_pattern` (we need only the pattern for
// the refusal text, so the haystack is not returned).
func argMatchesForbiddenPattern(command string, argValues, patterns []string) (string, bool) {
	if len(patterns) == 0 {
		return "", false
	}
	haystacks := make([]string, 0, len(argValues)+1)
	haystacks = append(haystacks, argValues...)
	if command != "" {
		haystacks = append(haystacks, command)
	}
	for _, pattern := range patterns {
		if pattern == "" {
			continue
		}
		for _, hay := range haystacks {
			if strings.Contains(hay, pattern) {
				return pattern, true
			}
		}
	}
	return "", false
}

// treeMatchesForbiddenGlob returns the first forbidden path-glob the proposed tree
// collides with (un-normalized), or "" with ok=false. Prefix-collision in both
// directions via the tree algebra. Port of
// `dos.call_shape._tree_matches_forbidden_glob`.
func treeMatchesForbiddenGlob(tree, globs []string) (string, bool) {
	if len(tree) == 0 || len(globs) == 0 {
		return "", false
	}
	var reqPrefixes []string
	for _, p := range tree {
		if p != "" {
			reqPrefixes = append(reqPrefixes, normTreePrefix(p))
		}
	}
	if len(reqPrefixes) == 0 {
		return "", false
	}
	for _, original := range globs {
		gp := normTreePrefix(original)
		for _, rp := range reqPrefixes {
			if prefixesCollide(rp, gp) {
				return original, true
			}
		}
	}
	return "", false
}

// callShapeInputs returns the proposed call's agent-authored argument bytes —
// (command, argValues). Port of `dos.pretool_sensor._call_shape_inputs`: the raw
// Bash command string (empty when the tool is not Bash) and the flattened
// top-level string argument values from tool_input (and string items of a
// top-level list). Deliberately shallow — a nested-dict arg is not walked.
//
// Python iterates `tool_input.values()` in insertion order; Go map order is
// random, so this walks the keys SORTED. The order of argValues is
// verdict-irrelevant — `argMatchesForbiddenPattern` iterates patterns (declaration
// order) outer and returns the matched PATTERN (the haystack it hit is discarded
// by the predicate), so which arg matched never changes the verdict or the reason
// text. Sorting only makes the corpus deterministic.
func callShapeInputs(e *Event) (string, []string) {
	if e.ToolInput == nil {
		return "", nil
	}
	command := ""
	if e.ToolName == "Bash" {
		if cmd, ok := e.ToolInput["command"].(string); ok {
			command = cmd
		}
	}
	keys := make([]string, 0, len(e.ToolInput))
	for k := range e.ToolInput {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var argValues []string
	for _, k := range keys {
		switch v := e.ToolInput[k].(type) {
		case string:
			if strings.TrimSpace(v) != "" {
				argValues = append(argValues, v)
			}
		case []any:
			for _, item := range v {
				if s, ok := item.(string); ok && strings.TrimSpace(s) != "" {
					argValues = append(argValues, s)
				}
			}
		}
	}
	return command, argValues
}

// callShapeVerdict is the DeclaredCallShapePredicate — request-absolute (ignores
// the lease), reads the effective policy for the request's lane from the ruleset.
// Port of `dos.call_shape.DeclaredCallShapePredicate.__call__`. The first check
// short-circuits to admit when the lane forbids nothing, so a generic workspace
// pays nothing and changes no verdict. The three rungs are checked in a fixed
// order (command-prefix, arg-substring, path-glob); the first hit refuses with the
// FORBIDDEN_CALL_SHAPE reason_class. The reason prose is byte-twinned with the
// Python predicate so the parity corpus gates the whole emitted line.
func callShapeVerdict(req admissionRequest, ruleset CallShapeRuleset) admissionVerdict {
	policy := ruleset.policyFor(req.lane)
	if policy.isEmpty() {
		return admitVerdict()
	}

	if hit := commandMatchesForbiddenPrefix(req.command, policy.ForbiddenCommandPrefixes); hit != nil {
		shown := strings.Join(hit, " ")
		return refuseVerdict(
			"lane "+pyRepr(req.lane)+" proposed a call invoking "+pyRepr(shown)+", "+
				"a command shape the workspace's declared [call_shape] policy "+
				"forbids (FORBIDDEN_CALL_SHAPE). Relax the declared shape, run "+
				"OUTSIDE the gated lane, or pass --force (operator override).",
			forbiddenCallShapeReason)
	}

	if pattern, ok := argMatchesForbiddenPattern(req.command, req.argValues, policy.ForbiddenArgPatterns); ok {
		return refuseVerdict(
			"lane "+pyRepr(req.lane)+" proposed a call whose arguments contain "+
				pyRepr(pattern)+", a substring the workspace's declared [call_shape] "+
				"policy forbids (FORBIDDEN_CALL_SHAPE). Relax the declared "+
				"pattern, run OUTSIDE the gated lane, or pass --force.",
			forbiddenCallShapeReason)
	}

	if glob, ok := treeMatchesForbiddenGlob(req.tree, policy.ForbiddenPathGlobs); ok {
		return refuseVerdict(
			"lane "+pyRepr(req.lane)+" proposed a write to a path matching "+
				pyRepr(glob)+", a path glob the workspace's declared [call_shape] "+
				"policy forbids (FORBIDDEN_CALL_SHAPE). Relax the declared "+
				"glob, run OUTSIDE the gated lane, or pass --force.",
			forbiddenCallShapeReason)
	}

	return admitVerdict()
}
