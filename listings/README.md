# Listing-venue artifacts (source of truth)

Each subdirectory here is the **in-tree source of truth** for an artifact DOS
submits to a third-party listing venue (the `verify-action/` / `gitlab-ci/`
pattern: the artifact lives here, the upstream submission is a copy). Tracking
them here means a venue's copy can always be regenerated or diffed against the
canonical version.

Authored for issue [#102](https://github.com/anthony-chaudhary/dos-kernel/issues/102)
(artifact-gated listing contributions).

| Path | Venue | Artifact |
|------|-------|----------|
| `logo.png` / `make_logo.py` | [Cline MCP marketplace](https://github.com/cline/mcp-marketplace) | 400×400 PNG server icon (reproducible via Pillow) |
| `antigravity-skill/SKILL.md` | [sickn33/antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills) | a cross-host skill that shells `dos verify` / `dos commit-audit` |
| `aitmpl-hook/dos-verify-gate.{json,sh}` | [davila7/claude-code-templates](https://github.com/davila7/claude-code-templates) | a Stop-hook component that audits the last commit's claim vs its diff |
| `cursorrules-mdc/dos-verify-done-claims-cursorrules-prompt-file.mdc` | [PatrickJS/awesome-cursorrules](https://github.com/PatrickJS/awesome-cursorrules) | a Cursor Project Rule gating done-claims through DOS |

The repo-root [`llms-install.md`](../llms-install.md) is the agent-driven
install guide the Cline marketplace reads (and any MCP host can use).

All four point at the SAME shipped surface every other distribution manifest
names: the `dos` CLI (`dos verify`, `dos commit-audit`) and the `dos-kernel[mcp]`
MCP server. Nothing here is host-specific kernel logic — these are distribution
adapters.
