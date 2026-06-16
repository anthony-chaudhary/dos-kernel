// Package hook is the native port of the PRE-moment tool-call decider
// (`dos.pretool_sensor` + the kernel leaves it stands on). It is a PURE decider:
// it receives a CC PreToolUse event + gathered evidence (the live leases, the
// existing runtime files) and returns the exact CC dialect to emit. All I/O
// (reading the WAL, stat-ing the runtime files) is gathered at the boundary
// (cmd/dos-hook), never inside a verdict — the same "I/O at the edge, data to the
// pure core" rule the Python kernel follows.
package hook

import "strings"

// normTreePrefix normalizes one tree entry to a comparable directory prefix.
//
// Byte-for-byte port of `dos._tree.norm_tree_prefix`:
//   - "\\" -> "/", then strip surrounding whitespace, then casefold.
//   - truncate at the first glob metacharacter "*", "?" or "[" (everything after
//     is a wildcard). `?`/`[` are wildcards exactly as `*` is; truncating only at
//     `*` left them as literals and false-disjointed overlapping regions (#144).
//   - a leading-glob entry ("**/*", "*.py", "?x", "[ab]/c") truncates to the EMPTY
//     prefix "" — the UNIVERSAL prefix that matches every path. Callers must not drop it.
//
// Case is folded UNCONDITIONALLY (DOS's documented primary platform is Windows,
// a case-insensitive FS) so the prefix algebra collides case-variants of one real
// file. Go has no exact `str.casefold()`, but for the ASCII path identifiers a
// lane tree carries, `strings.ToLower` is identical; the few non-ASCII casefold
// edge cases (ß, İ) do not occur in repo-relative POSIX paths, and treating an
// unexpected case-variant as colliding is the harmless over-refusal direction
// `_tree` already documents. (The parity corpus pins this for the real inputs.)
func normTreePrefix(p string) string {
	p = strings.ReplaceAll(p, "\\", "/")
	p = strings.TrimSpace(p)
	p = strings.ToLower(p)
	// Earliest of the three glob metacharacters; IndexByte returns -1 when absent.
	cut := -1
	for _, mc := range []byte{'*', '?', '['} {
		if i := strings.IndexByte(p, mc); i != -1 && (cut == -1 || i < cut) {
			cut = i
		}
	}
	if cut != -1 {
		return p[:cut]
	}
	return p
}

// prefixesCollide reports whether two normalized prefixes can name the same file.
//
// Port of `dos._tree.prefixes_collide`: two prefixes collide when one is a prefix
// of the other. The EMPTY prefix (from a leading-glob like "**/*") is universal —
// it collides with everything, including another empty prefix — because
// "".HasPrefix(x) is only true for x=="" but x.HasPrefix("") is true for all x.
func prefixesCollide(a, b string) bool {
	return strings.HasPrefix(a, b) || strings.HasPrefix(b, a)
}

// laneTreesDisjoint reports whether two lane file trees cannot edit the same file.
//
// Port of `dos._tree.lane_trees_disjoint`. CONSERVATIVE BY DESIGN: an empty tree
// is an UNKNOWN (not zero) blast radius, so this returns false (overlapping) when
// either tree is empty, or when every entry normalizes away. Only when both trees
// have a real prefix and no pair collides is it disjoint.
//
// Currently unused by the pretool decider (the disjointness predicate uses the
// overlap ratio, not this boolean), but ported alongside its siblings so the tree
// algebra is complete and the parity corpus can pin it. Kept for GHF5 convergence.
func laneTreesDisjoint(treeA, treeB []string) bool {
	if len(treeA) == 0 || len(treeB) == 0 {
		return false
	}
	var normA, normB []string
	for _, p := range treeA {
		if p != "" {
			normA = append(normA, normTreePrefix(p))
		}
	}
	for _, p := range treeB {
		if p != "" {
			normB = append(normB, normTreePrefix(p))
		}
	}
	if len(normA) == 0 || len(normB) == 0 {
		return false
	}
	for _, na := range normA {
		for _, nb := range normB {
			if strings.HasPrefix(na, nb) || strings.HasPrefix(nb, na) {
				return false
			}
		}
	}
	return true
}
