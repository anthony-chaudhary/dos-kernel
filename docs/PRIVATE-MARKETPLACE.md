# Adding DOS to a private company plugin marketplace

> **The 30-second version.** A company hosts its own Claude Code **plugin
> marketplace** — a git repo with a `.claude-plugin/marketplace.json` catalog —
> and lists the DOS plugin in it. Engineers run `/plugin marketplace add
> your-org/claude-plugins` then `/plugin install dos-kernel@<your-marketplace>`,
> and DOS's hooks + MCP tools + skill pack arrive in one step from a repo **you**
> control. The one DOS-specific catch: the plugin ships JSON + the native hook
> binary, but the *brains* are the `dos-kernel` **pip package**, so your image or
> onboarding must also `pip install "dos-kernel[mcp]"` into the interpreter
> Claude Code launches. This page is the end-to-end playbook for that.

This is for the team that does **not** want every engineer adding the public
`anthony-chaudhary/dos-kernel` marketplace by hand — you want DOS to come from
an **internal, reviewed, pinned** source: a private GitHub/GitLab repo, a
subdirectory of your monorepo, an internal npm registry, or a baked-in
container seed. All of those are first-class Claude Code marketplace sources;
this page maps each onto DOS and calls out the gotchas we hit.

The canonical Anthropic reference is
[Create and distribute a plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces)
and [Plugin settings](https://code.claude.com/docs/en/settings#plugin-settings).
This playbook is the DOS-specific overlay: what to put in the catalog, how to
ship the pip prerequisite, and how to verify the install with DOS itself rather
than trusting that it worked.

For the public, zero-setup install, see [INSTALL.md](INSTALL.md) (the "Claude
Code plugin" section) and [claude-plugin/README.md](../claude-plugin/README.md).
This page is only the private-registry path.

---

## Already run a company marketplace? Add DOS as one more entry

The common case is **not** a fresh marketplace — it's a company that *already*
runs one. There is already a git repo with a `.claude-plugin/marketplace.json`,
the team already ran `/plugin marketplace add <your-repo>` once, and it already
lists one or more in-house plugins. You are not creating anything. You add DOS
as **one more entry in the existing catalog**, then ship the pip package. Three
moves.

**1. Append one plugin object** to the existing `plugins` array — next to the
plugins you already ship (here `acme-internal`):

```json
{
  "name": "acme-tools",
  "owner": { "name": "Acme DevTools" },
  "plugins": [
    { "name": "acme-internal", "source": "./acme-internal" },
    {
      "name": "dos-kernel",
      "source": { "source": "github", "repo": "anthony-chaudhary/dos-kernel", "ref": "v0.27.0" },
      "description": "DOS — the trust substrate for agent fleets (hooks + MCP + skills). Requires 'pip install dos-kernel[mcp]'."
    }
  ]
}
```

Keep the plugin `name` as `dos-kernel` so the skills stay `/dos-kernel:<skill>`.
Pick the `source` per your supply-chain policy — pin this repo by a `ref`/`sha`,
vendor the bytes, sparse-clone a monorepo subdir, or an internal npm registry.
The four shapes are **Step 2** below. Validate before you push — don't trust the
JSON, check it:

```bash
claude plugin validate .       # must print 0 errors (warnings about extra fields are fine)
git commit -am "Add the DOS plugin to the Acme marketplace"
git push                       # to your internal git host
```

**2. Ship the `dos-kernel` pip package to the fleet.** This is the one step a
generic plugin playbook skips, and the one that bites (full detail in **Step 3**).
The plugin install copies *files*; the *brains* — the `verify` / `arbitrate` /
`refuse` syscalls — are the **package**, which a plugin install does not fetch.
Add it to your image / onboarding / CI:

```bash
pip install "dos-kernel[mcp]"   # into the interpreter Claude Code launches
```

**3. Each engineer installs the plugin.** They already added the marketplace, so
there is no re-subscribe — it's one verb, then a read-only confirm:

```text
/plugin install dos-kernel@acme-tools    # inside Claude Code (acme-tools = your marketplace name)
/plugin list                             # confirm dos-kernel shows enabled
/dos-kernel:dos-setup                    # read-only: confirms the package imports + what the plugin wired
```

That is the whole add. To **auto-enable** it for everyone (no one types `/plugin
install`) commit `enabledPlugins` to the repo or mandate it in managed settings —
**Step 4**. Everything below is the depth behind these three moves: the source
shapes (Step 2), the pip-rollout channels (Step 3), fleet-wide auto-enable
(Step 4), air-gapped seeding (Step 5), and verifying with DOS itself (Step 6). If
you are standing up the marketplace from scratch, start at Step 1.

---

## What you're actually distributing

The DOS plugin is the directory [`claude-plugin/`](../claude-plugin/) in this
repo. It bundles three runtime surfaces in one install — the fail-safe **hooks**
(`PreToolUse`/`PostToolUse`/`Stop`), the **MCP server** (`verify` / `arbitrate`
/ `refuse` … as tools), and the **generic skill pack** (`/dos-next-up`,
`/dos-dispatch`, `/dos-witness-claim`, …). The repo root holds the public
marketplace catalog, [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json),
whose single plugin entry points back at `./claude-plugin`.

A **private** marketplace is the same two pieces, hosted by you:

```
your-claude-plugins/                 # a repo YOUR org owns
  .claude-plugin/
    marketplace.json                 # your catalog — lists the dos-kernel plugin
  (optionally) vendored DOS plugin files, or a pointer to this repo
```

You have two honest choices for *where the plugin bytes come from*, and they
trade off freshness against control:

| Choice | The `source` you write | You get | You give up |
|---|---|---|---|
| **Point at this repo** | `git-subdir` / `github` at `anthony-chaudhary/dos-kernel`, pinned to a `ref`/`sha` | Zero vendoring; `git pull` upstream is your update | The bytes come from a repo you don't own (mitigate by pinning a `sha` you reviewed) |
| **Vendor the plugin** | a relative path `./dos-kernel` inside your marketplace repo | Full control; the bytes are reviewed and frozen in your repo | You re-vendor on every upgrade (a scripted copy of `claude-plugin/`) |

Most companies that ask for a "private registry" want the **second** posture for
the catalog (an internal repo the team trusts) and are fine with **either** for
the plugin bytes. Pick per your supply-chain policy; both are spelled out below.

---

## Step 1 — create the marketplace repo

A marketplace is just a git repo with one JSON file. Minimal:

```bash
mkdir -p your-claude-plugins/.claude-plugin
cd your-claude-plugins
git init
```

Write `.claude-plugin/marketplace.json`. The **name you choose here is what the
team types after the `@`** in `/plugin install dos-kernel@<name>`, so pick a
stable internal name (kebab-case, no spaces). Below, `acme-tools`:

```json
{
  "$schema": "https://www.schemastore.org/claude-code-marketplace.json",
  "name": "acme-tools",
  "description": "Acme's internal Claude Code plugins.",
  "owner": {
    "name": "Acme DevTools",
    "email": "devtools@acme.example"
  },
  "plugins": [
    {
      "name": "dos-kernel",
      "source": {
        "source": "github",
        "repo": "anthony-chaudhary/dos-kernel",
        "ref": "v0.27.0"
      },
      "description": "DOS — the trust substrate for agent fleets (hooks + MCP + skills). Requires 'pip install dos-kernel[mcp]'.",
      "homepage": "https://github.com/anthony-chaudhary/dos-kernel"
    }
  ]
}
```

> **Two reserved-name notes.** (1) The marketplace `name` must not collide with
> Anthropic's reserved names (`anthropic-plugins`, `claude-plugins-official`,
> etc.) — use your company name. (2) Keep the plugin `name` as `dos-kernel` so
> the namespaced skills stay `/dos-kernel:<skill>` and your onboarding docs match
> everyone else's.

Validate before you share it — don't trust the JSON, check it:

```bash
claude plugin validate .            # schema, duplicate names, path traversal, version mismatches
```

Then push to your internal host:

```bash
git add .claude-plugin/marketplace.json
git commit -m "Add Acme Claude Code marketplace with the DOS plugin"
git push                            # to github.com/acme/claude-plugins, your GitLab, etc.
```

---

## Step 2 — choose the plugin `source` (the four private shapes)

The `source` field of the `dos-kernel` entry decides where the plugin bytes are
fetched from. Every form below is private-registry-friendly. Pick one.

### A. Pin this repo by `github` + `ref`/`sha` (least work)

You own the catalog; the plugin bytes are pulled from this public repo at a
commit **you** reviewed and pinned. Pin a full 40-char `sha` for a frozen,
audit-friendly install (a `ref` alone tracks a moving branch/tag):

```json
{
  "name": "dos-kernel",
  "source": {
    "source": "github",
    "repo": "anthony-chaudhary/dos-kernel",
    "ref": "v0.27.0",
    "sha": "0000000000000000000000000000000000000000"
  }
}
```

When both `ref` and `sha` are set, the **`sha` is the effective pin** — the
install succeeds even if the tag later moves or is deleted upstream, as long as
the commit is still reachable. This is the recommended posture if your policy
allows the bytes to originate upstream: review a commit, pin its `sha`, and the
team gets exactly those bytes until you bump it.

> The DOS plugin lives at the repo **root's** `claude-plugin/` directory, but the
> *marketplace* this `github` source points at is the repo root (where DOS's own
> `.claude-plugin/marketplace.json` lives). If you instead want only the plugin
> subdirectory and not DOS's catalog, use the `git-subdir` form (C) pointed at
> `claude-plugin`.

### B. Vendor the plugin into your repo (full control)

Copy the plugin directory into your marketplace repo and reference it by a
relative path. The bytes are now frozen in a repo you own and review:

```bash
# from your marketplace repo, with a clone of dos-kernel beside it:
cp -r ../dos-kernel/claude-plugin ./dos-kernel
git add dos-kernel .claude-plugin/marketplace.json
git commit -m "Vendor the DOS plugin at v0.27.0"
```

```json
{
  "name": "dos-kernel",
  "source": "./dos-kernel"
}
```

> **Relative paths only resolve when the team adds your marketplace via git** (a
> repo clone), **not** via a bare URL to the `marketplace.json` file — a URL add
> downloads only the JSON, not the plugin files. So if you vendor, distribute the
> marketplace as a **git repo** (Step 3), not a raw file URL. Re-vendoring on
> upgrade is one `cp -r` + commit; script it next to your other dependency bumps.
> Pin the upstream version you copied in your commit message so the provenance is
> legible.

### C. `git-subdir` — vendor DOS inside your monorepo (sparse clone)

If your company keeps tools in a monorepo, drop the plugin under a subdirectory
and point at it with `git-subdir`. Claude Code does a **sparse, partial clone**
of just that path, so a huge monorepo costs little bandwidth:

```json
{
  "name": "dos-kernel",
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/acme/monorepo.git",
    "path": "tools/claude/dos-kernel",
    "ref": "main"
  }
}
```

`url` also accepts the GitHub `owner/repo` shorthand and SSH (`git@github.com:acme/monorepo.git`).
Put a copy of `claude-plugin/` at `tools/claude/dos-kernel/` and bump it the same
way as (B).

### D. `npm` from your internal registry

If your org already distributes internal tooling through a private npm registry,
publish the plugin as a package and point at your registry:

```json
{
  "name": "dos-kernel",
  "source": {
    "source": "npm",
    "package": "@acme/dos-kernel-plugin",
    "version": "^0.27.0",
    "registry": "https://npm.acme.example"
  }
}
```

This is the most work (you maintain a package), but it slots DOS into an
existing internal-registry pipeline with version ranges and your own access
control.

---

## Step 3 — ship the `dos-kernel` pip package (the DOS-specific catch)

**This is the step a generic plugin playbook skips, and the one that bites.** A
Claude Code plugin install copies **files** (JSON, markdown, the bundled native
`dos-hook` binary). It does **not** install Python packages. The DOS hooks and
MCP server invoke `python -m dos.cli …` / `python -m dos_mcp.server` — so the
`dos-kernel` package must be importable by **the same interpreter Claude Code
launches** (`python` on its PATH), or:

- the hooks **fail safe** — they emit nothing and exit 0, never breaking a turn,
  but they also do nothing; and
- the MCP server prints an install hint in `/mcp` and exposes no tools.

So your private-registry rollout has **two** halves: the plugin (Steps 1–2) and
the package. Wire the package one of these ways:

```bash
# the prerequisite, into the interpreter Claude Code uses:
pip install "dos-kernel[mcp]"
```

| Where the team runs Claude Code | How to seed `dos-kernel` |
|---|---|
| **Dev laptops** | Add `pip install "dos-kernel[mcp]"` to your onboarding script / Brewfile / `mise`/`asdf` setup. If your org mirrors PyPI internally, install from your mirror; the import name stays `dos`. |
| **Devcontainers / Docker** | Add the `pip install` to the image `Dockerfile` so it's present before Claude Code starts. Pair with `CLAUDE_CODE_PLUGIN_SEED_DIR` (Step 5) to bake the plugin in too. |
| **CI** | Install in the job before invoking `claude -p …`; pin `dos-kernel==X.Y.Z` next to the plugin's pinned `sha` so they move together. |

> **Pin the two together.** The plugin and the package have independent version
> lines; a plugin built against a newer kernel than the installed package can
> mis-resolve. Pin the plugin `ref`/`sha` and the `dos-kernel==X.Y.Z` to matching
> releases, and bump them in one change. (DOS is adding a declared
> minimum-kernel-version handshake so the kernel *refuses* a plugin built for a
> newer kernel instead of failing late — see
> [docs/331](331_plugin-manifest-version-handshake-plan.md).)

If your team would rather **not** use the plugin at all and just wants the same
hooks wired à la carte, `dos init --hooks auto` (after the pip install) detects
the runtimes your repo uses and writes the hook config directly — no marketplace
needed. The plugin is the one-step bundle; `dos init` is the unbundled path.

---

## Step 4 — distribute to the team (auto-prompt, don't ask everyone to type)

Listing the plugin is not enough; the team has to add the marketplace and
install the plugin. Two levels of automation.

### Project scope — prompt anyone who trusts the repo

Commit `extraKnownMarketplaces` + `enabledPlugins` to your **project**
`.claude/settings.json`. When an engineer opens the repo and trusts the folder,
Claude Code offers to add the marketplace and enables the plugin — no manual
`/plugin` typing:

```json
{
  "extraKnownMarketplaces": {
    "acme-tools": {
      "source": {
        "source": "github",
        "repo": "acme/claude-plugins"
      }
    }
  },
  "enabledPlugins": {
    "dos-kernel@acme-tools": true
  }
}
```

The same `claude plugin marketplace add acme/claude-plugins --scope project`
writes this for you. (`--scope project` shares it via the checked-in settings;
`user` is per-machine; `local` is per-checkout and gitignored.)

### Enterprise scope — mandate it via managed settings

For a fleet-wide mandate, put the same `extraKnownMarketplaces` /
`enabledPlugins` in the **managed** `managed-settings.json` (the
admin-controlled settings file that users cannot override — see
[settings files](https://code.claude.com/docs/en/settings#settings-files) for
its per-OS path). To **restrict** which marketplaces users may add at all — the
literal "private registry only" lockdown — set `strictKnownMarketplaces` in
managed settings:

```json
{
  "extraKnownMarketplaces": {
    "acme-tools": { "source": { "source": "github", "repo": "acme/claude-plugins" } }
  },
  "enabledPlugins": { "dos-kernel@acme-tools": true },
  "strictKnownMarketplaces": [
    { "source": "github", "repo": "acme/claude-plugins" }
  ]
}
```

`strictKnownMarketplaces` options, from the Anthropic docs:

| Value | Behavior |
|---|---|
| undefined (default) | no restriction — users add any marketplace |
| `[]` | complete lockdown — no new marketplaces at all |
| list of sources | allowlist — only exactly-matching sources may be added |

For a self-hosted GitHub Enterprise / GitLab, prefer a `hostPattern` (regex on
the host) over a literal URL, because exact matching does **not** normalize a
trailing slash, `.git` suffix, or `ssh://` vs `https://`:

```json
{ "strictKnownMarketplaces": [ { "source": "hostPattern", "hostPattern": "^git\\.acme\\.example$" } ] }
```

`strictKnownMarketplaces` only *restricts*; pair it with `extraKnownMarketplaces`
in the same managed file so the allowed marketplace is also auto-registered.

---

## Step 5 — air-gapped / container fleets (seed the cache, clone nothing at runtime)

If your agents run in containers or an air-gapped network where a runtime
`git clone` of the marketplace would fail, **pre-populate** the plugin cache at
image-build time and point `CLAUDE_CODE_PLUGIN_SEED_DIR` at it. Claude Code then
reads the marketplace and plugin from the seed without cloning:

```bash
# during image build — install directly into a seed path:
CLAUDE_CODE_PLUGIN_CACHE_DIR=/opt/claude-seed \
  claude plugin marketplace add acme/claude-plugins
CLAUDE_CODE_PLUGIN_CACHE_DIR=/opt/claude-seed \
  claude plugin install dos-kernel@acme-tools

# and the DOS package, same image:
pip install "dos-kernel[mcp]"
```

Then at runtime set `CLAUDE_CODE_PLUGIN_SEED_DIR=/opt/claude-seed`. The seed is
**read-only** (auto-updates are disabled for it — you rebuild the image to
upgrade), seed entries take precedence over user config, and `/plugin
marketplace update`/`remove` against a seed-managed marketplace correctly refuse
with "ask your administrator to update the seed image."

Two more env vars worth knowing for locked-down networks:

- `CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1` — keep the last-known-good
  marketplace clone when a `git pull` fails (offline), instead of wiping it.
- `CLAUDE_CODE_PLUGIN_GIT_TIMEOUT_MS=300000` — raise the 120 s git timeout for
  slow internal mirrors.

---

## Per-`CLAUDE_CONFIG_DIR` profile pools (account rotation)

Steps 1–5 distribute DOS to many *machines*. A different topology distributes it
to many *profiles on one machine*: a fleet that rotates across several isolated
Claude Code logins, each pinned to its own `CLAUDE_CONFIG_DIR`, so no single
usage window saturates. DOS ships the decision core for exactly this — the `dos
accounts` seat-pool seam (see [the `dos-goal-fleet`
skill](../src/dos/skills/dos-goal-fleet/SKILL.md)).

**Plugin install state is strictly per-config-dir.** Claude Code reads it from
`$CLAUDE_CONFIG_DIR/plugins/` (`known_marketplaces.json`,
`installed_plugins.json`, `cache/`) — never from `~/.claude`. So a plugin you
installed in your default profile is **absent** from every rotated profile, and a
launcher that points `CLAUDE_CONFIG_DIR` at a pool member runs that worker with
**no DOS hooks, no DOS MCP, no DOS skills** — the trust substrate missing from
the very fleet it exists to govern. Confirm per profile, don't assume:

```bash
CLAUDE_CONFIG_DIR=~/.claude-pool-a claude plugin list   # "No plugins installed" until you install it THERE
```

**Seeding `settings.json` is not enough.** Writing `extraKnownMarketplaces` +
`enabledPlugins` into a fresh config dir's `settings.json` *declares* the
marketplace and *enables* the plugin, but does **not** fetch or cache the plugin
bytes — `claude plugin list` against that dir still says "No plugins installed".
`enabledPlugins` toggles an *already-installed* plugin; it is not an installer.

**Provision each config dir explicitly** — the same two verbs, once per profile:

```bash
for d in ~/.claude-pool-a ~/.claude-pool-b ~/.claude-pool-c; do
  CLAUDE_CONFIG_DIR="$d" claude plugin marketplace add anthony-chaudhary/dos-kernel
  CLAUDE_CONFIG_DIR="$d" claude plugin install dos-kernel@dos --scope user
done
```

Both verbs run non-interactively (a fresh `add` raises no trust prompt). Point
`add` at a **local checkout** (`claude plugin marketplace add /path/to/dos-kernel`)
to install from the working tree you dogfood — the lightest cache (~25 MB/dir, no
marketplace clone) and the way to test an unreleased change across the whole
pool. `--scope user` writes the install into that dir's own `plugins/`, so it
survives every session that profile launches.

**Make it part of enrollment, not an afterthought.** If your switcher scaffolds
new config dirs, fold the two verbs above into that step. DOS's own `dos accounts
enroll` already seeds each new account's `settings.json` from the roster
`defaults` (model, effort, permissions); the plugin install is the missing
companion — seeding settings carries your *preferences*, only `claude plugin
install` carries the *substrate*. A freshly-enrolled account should be born with
DOS, not silently rotated into the fleet without it.

---

## Step 6 — verify with DOS, not with the installer's say-so

The whole point of DOS is that a tool's "it worked" is a self-report. So confirm
the install the DOS way — ask the kernel:

```bash
# 1. is the package importable by THIS interpreter? (the half a plugin can't provide)
dos doctor --workspace .            # prints the RESOLVED dos path + version + workspace facts
dos doctor | head -1                # → "DOS vX.Y.Z"  (there is no `dos --version` flag)
```

Inside Claude Code, after the plugin installs:

```text
/dos-kernel:dos-setup               # read-only: confirms the package imports + reports what the plugin wired
/mcp                                # the `dos` MCP server should be running with its tools listed (not an install hint)
```

If `/mcp` shows the server failing or `dos-setup` reports the package missing,
the **plugin** is installed but the **package** (Step 3) is not in the
interpreter Claude Code launches. That split is the single most common failure
of a private rollout; fix it by installing `dos-kernel[mcp]` into the right
interpreter, not by reinstalling the plugin.

A `dos doctor` that prints an unexpected source path means a stale `dos` shim is
shadowing the install (the multi-worktree trap) — run `python install.py
fix-shadowing`, or `uv tool install --force dos-kernel`.

---

## Step 7 — upgrades

| To upgrade | Do |
|---|---|
| **The plugin (catalog points upstream)** | bump the `ref`/`sha` in your `marketplace.json`, commit, push; team runs `/plugin marketplace update` (or it auto-updates if their git auth is wired) |
| **The plugin (vendored)** | re-`cp -r` the new `claude-plugin/` into your repo at the new version, commit, push; team `/plugin marketplace update` |
| **The package** | bump `dos-kernel==X.Y.Z` in your onboarding/image and `pip install --upgrade "dos-kernel[mcp]"` |
| **A seed image** | rebuild the image with the new plugin + package baked in (seeds are read-only by design) |

Keep the plugin pin and the package pin **in lockstep** — bump both in one
change. A two-channel "stable / latest" split (a `stable` ref for most, a
`latest` ref for early adopters, assigned to user groups via managed settings) is
supported; see the
[release-channels section](https://code.claude.com/docs/en/plugin-marketplaces#version-resolution-and-release-channels)
of the Anthropic docs.

---

## Private-repo authentication — the gotcha we hit

Claude Code installs from private repos using your **existing git credential
helpers** for manual `add`/`update` (HTTPS via `gh auth login` / Keychain /
`git-credential-store`; SSH if the host is in `known_hosts` and the key is in
`ssh-agent`). For **background auto-updates** (which run at startup without
prompting), set a token in the environment:

| Provider | Env var |
|---|---|
| GitHub | `GITHUB_TOKEN` or `GH_TOKEN` (needs `repo` scope for private) |
| GitLab | `GITLAB_TOKEN` or `GL_TOKEN` (needs `read_repository`) |
| Bitbucket | `BITBUCKET_TOKEN` |

> **Real-world caveat (2026).** Token-based access to *private* marketplaces has
> been flaky in practice — see
> [anthropics/claude-code#17201](https://github.com/anthropics/claude-code/issues/17201).
> If `/plugin marketplace add <private-repo>` fails despite a configured token,
> the proven workaround is to **clone to a local path and add that**:
>
> ```bash
> git clone https://x-access-token:${GITHUB_TOKEN}@github.com/acme/claude-plugins.git ~/.acme/claude-plugins
> # inside Claude Code:
> #   /plugin marketplace add ~/.acme/claude-plugins
> ```
>
> A local-path add sidesteps the in-process auth entirely. For container fleets,
> the seed-dir approach (Step 5) avoids runtime auth altogether and is the more
> robust answer. Re-check #17201 — the first-class private path may have landed
> since.

---

## Why route DOS through your own registry at all

Three reasons a company pins DOS internally instead of using the public
marketplace directly, each of which DOS is built to reward:

1. **Provenance you control.** A pinned `sha` in a repo you review means the
   trust substrate itself arrives from bytes you vetted — fitting for the tool
   whose whole thesis is "don't trust unverified claims."
2. **One lockstep version across the fleet.** `enabledPlugins` +
   `strictKnownMarketplaces` + a pinned package give every engineer and CI job
   the *same* DOS, so a verdict on one machine means the same thing on another.
3. **Air-gap / compliance.** The seed-dir path ships DOS into networks that can't
   reach PyPI or GitHub at runtime, with auto-update correctly disabled.

---

## See also

- [INSTALL.md](INSTALL.md) — every install channel (uv, pip, the public plugin); the private path is the section that points here.
- [claude-plugin/README.md](../claude-plugin/README.md) — what the bundle contains and why it shells `python -m`, not the console scripts.
- [docs/331](331_plugin-manifest-version-handshake-plan.md) — the plugin↔kernel version handshake (refuse a plugin built for a kernel you don't have).
- [docs/298](298_claude-cowork-the-sixth-host-shared-surface.md) — the plugin in Claude Cowork (MCP + skills work; hooks dormant until Cowork fires hooks).
- [SECURITY.md](../SECURITY.md) — "Supply chain": the distribution name is `dos-kernel`, never the bare `dos` squatter.
- [Create and distribute a plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces) and [Plugin settings](https://code.claude.com/docs/en/settings#plugin-settings) — the authoritative Anthropic references this overlays.
