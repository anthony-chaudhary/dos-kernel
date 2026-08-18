# Native Codex tool-hook fixtures

These fixtures preserve the native Windows Codex `PreToolUse` and `PostToolUse`
stdin envelopes captured on August 17, 2026 with Codex CLI `0.147.0`
(`openai/codex` tag `rust-v0.147.0`, commit
`be6e8eac01c42a49bff92ef356d6294cfd6d55dd`).

The session, turn, and tool-use identifiers come from the harmless live capture.
Only machine-specific filesystem values were replaced with `X:\codex-fixture\...`;
the field names, JSON types, hook names, `Bash` alias, command, and raw shell
response are unchanged. The fixtures contain no credentials or user content.

Codex sends only `{"command": ...}` as the native shell `tool_input`; the
PostToolUse `tool_response` is a JSON string containing the captured raw command
output. The DOS Codex adapter deliberately passes these bytes through unchanged
rather than introducing a second envelope mapping.

## Failure-stage attribution for fak#7212

The installed plugin's hook rows were **attempted**, not skipped, so Codex's
hash-based trust gate had already admitted them. Replaying the same benign
fixture through both the bundled native executable and the editable Python
backend returned exit 0. Replaying the installed PreToolUse `command` with
Windows PowerShell failed with a parser error before it could select either
backend. In the same profile, SessionStart and UserPromptSubmit already had
`commandWindows` overrides and completed.

That evidence assigns the failure to exactly one stage: **shell launch**. The
POSIX command string was selected on native Windows because PreToolUse and
PostToolUse lacked `commandWindows`; trust validation, executable selection,
stdin normalization, and backend policy were not the failing stage.

## Installed-profile live witness

`live-smoke.json` is a redacted projection of Codex app-server notifications
captured from the active Windows profile on August 17, 2026. The plugin was
installed through the Codex CLI from an ephemeral local marketplace whose
`claude-plugin` tree was copied byte-for-byte from published revision
`ea5a9dde58051cdeb1075c86740e143710947056`.

The installed `hooks.json` and `dos-hook-codex.ps1` SHA-256 values matched that
revision. Codex reported the plugin hooks as trusted without a trust bypass,
then emitted `preToolUse: completed`, executed the harmless PowerShell command
with exit 0, and emitted `postToolUse: completed`. Machine-specific paths and
the profile directory are represented as `$CODEX_HOME`; no credentials or hook
payload contents were retained.
