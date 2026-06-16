# How do I add the DOS plugin to a private company Claude Code marketplace?

> Host your own marketplace — a git repo with a `.claude-plugin/marketplace.json`
> catalog — and list the DOS plugin in it with a `source` pinned to a commit you
> reviewed. Your team runs `/plugin marketplace add your-org/claude-plugins` then
> `/plugin install dos-kernel@<your-marketplace>`, and DOS's hooks + MCP tools +
> skills arrive from a repo **you** control. The one DOS-specific catch: a plugin
> install copies files, not Python packages, so your image or onboarding must
> also `pip install "dos-kernel[mcp]"` into the interpreter Claude Code launches.
> The package is `dos-kernel` (the bare `dos` on PyPI is an unrelated squatter).

## The short answer

A Claude Code **plugin marketplace** is just a git repository with one file:
`.claude-plugin/marketplace.json`, a catalog that lists plugins and where to
fetch each one. To put DOS in your company's private registry, you create that
repo, add a `dos-kernel` entry whose `source` points at a version you've
reviewed, and distribute it to the team through `extraKnownMarketplaces` in
settings. Nothing about DOS is special here **except** one thing worth saying
twice: the plugin ships JSON, markdown, and the native hook binary, but the
*brains* — the `verify` / `arbitrate` / `refuse` syscalls the hooks and MCP
server call — are the **`dos-kernel` pip package**. A plugin install does not
install pip packages, so you wire the package separately (onboarding script,
Docker image, or CI step). If you skip that, the hooks fail safe (do nothing,
exit 0) and the MCP server shows an install hint instead of its tools.

The full step-by-step — all four private `source` shapes (a pinned `github`
ref, a vendored relative path, a monorepo `git-subdir` sparse clone, or an
internal `npm` registry), the `strictKnownMarketplaces` lockdown, the
air-gapped seed-dir path, and the private-repo auth gotcha — is the dedicated
playbook: [Adding DOS to a private company plugin marketplace](../PRIVATE-MARKETPLACE.md).

## The minimal catalog

```json
{
  "name": "acme-tools",
  "owner": { "name": "Acme DevTools" },
  "plugins": [
    {
      "name": "dos-kernel",
      "source": { "source": "github", "repo": "anthony-chaudhary/dos-kernel", "ref": "v0.26.0" },
      "description": "DOS — trust substrate for agent fleets. Requires 'pip install dos-kernel[mcp]'."
    }
  ]
}
```

Validate it (`claude plugin validate .`), push it to your internal git host,
and ship the package alongside:

```bash
pip install "dos-kernel[mcp]"        # into the interpreter Claude Code launches
```

Then verify the install the DOS way — don't trust the installer, ask the
kernel:

```bash
dos doctor --workspace .             # prints the RESOLVED dos path + version + workspace facts
```

```text
/dos-kernel:dos-setup                # inside Claude Code: confirms the package imports + what the plugin wired
```

A `/mcp` that shows the `dos` server failing means the **plugin** installed but
the **package** is missing from the interpreter Claude Code uses — the single
most common private-rollout failure, and it's fixed by installing
`dos-kernel[mcp]`, not by reinstalling the plugin.

## Why pin DOS through your own registry

The point of DOS is that a tool's "it worked" is a self-report you shouldn't
trust. Routing the trust substrate itself through a registry you control is the
consistent move: a pinned `sha` in a repo you reviewed means DOS arrives from
bytes you vetted; `enabledPlugins` + a pinned `dos-kernel==X.Y.Z` give every
engineer and CI job the *same* DOS, so a verdict means the same thing on every
machine; and the seed-dir path ships it into air-gapped networks that can't
reach PyPI at runtime.

| What | Number / fact | Witness (you didn't author it) | Source |
|---|---|---|---|
| The plugin bundles all three runtime surfaces in one install | hooks (`PreToolUse`/`PostToolUse`/`Stop`) + MCP server + skill pack, one `/plugin install` | the plugin manifest + bundle the repo ships | [`claude-plugin/README.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/claude-plugin/README.md) |
| The catalog `/plugin marketplace add` reads | one plugin entry, `source: ./claude-plugin` | the checked-in marketplace JSON | [`.claude-plugin/marketplace.json`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/.claude-plugin/marketplace.json) |
| The kernel will refuse a plugin built for a kernel you don't have | declared minimum-kernel-version handshake (planned) — a checked claim, not a self-report | the version-compat leaf + lint sweep design | [`docs/331`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/331_plugin-manifest-version-handshake-plan.md) |
| The install is verified by the kernel, not the installer | `dos doctor` prints the resolved source path + version, not "the command exists" | git + the resolved interpreter, read by `dos doctor` | [`docs/INSTALL.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/INSTALL.md) |

## Related

- The full playbook: [Adding DOS to a private company plugin marketplace](../PRIVATE-MARKETPLACE.md)
- Every install channel (uv, pip, the public plugin): [INSTALL.md](../INSTALL.md)
- No plugin system at all? Wire a `dos` verb's exit code instead: [how to add a guardrail to a coding agent with no plugin system](how-to-add-a-guardrail-to-a-coding-agent-with-no-plugin-system.md)
- The bundle's contents and the `python -m` choice: [claude-plugin/README.md](../../claude-plugin/README.md)

## Also asked as

- add the DOS plugin to a private company Claude Code marketplace
- install DOS in an internal plugin marketplace
- deploy the dos-kernel plugin to a company gallery
- private marketplace setup for the DOS plugin
- ship DOS to my org's internal Claude Code marketplace
- host the DOS plugin in a private company registry
- deploy dos-kernel to a company plugin gallery
- host the DOS plugin in a private registry
- ship DOS to our org's Claude Code marketplace
