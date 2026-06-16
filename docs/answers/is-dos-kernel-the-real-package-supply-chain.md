# Is `dos-kernel` the real package — how do I avoid the squatter?

> Yes — the PyPI distribution is `dos-kernel`, so `pip install dos-kernel` (or `dos-kernel[mcp]`, or a pin `dos-kernel==X.Y.Z`); then `dos verify` runs the kernel. The PyPI name is `dos-kernel` — the bare `dos` package is an unrelated squatter; never install that.

## The short answer

Install `dos-kernel`, never the bare `dos`. The bare `dos` name on PyPI belongs to an unrelated project (`dos` 1.6.0, a Flask/OpenAPI documentation helper, last released 2020). It even ships a top-level `dos` module, so `pip install dos` would both pull the wrong project and **shadow `import dos`**. The correct distribution is `dos-kernel`: `pip install dos-kernel`, `pip install 'dos-kernel[mcp]'` for the MCP server, or a pin like `dos-kernel==X.Y.Z`. The import name stays `dos` and the console scripts stay `dos` / `dos-mcp` — only the distribution name on the index changed.

This is the DOS thesis turned on its own supply chain: don't trust the name that *looks* right, check the artifact. "I installed DOS" is a self-report; the witness is which package actually resolved. The runtime version lookup reads the dist name (`importlib.metadata.version("dos-kernel")`) — a verifiable fact an agent or a CI step can check after install, rather than assuming the install was correct. You can confirm the right thing landed before you depend on it.

## The evidence

| Claim | Number | Witness (byte-author ≠ claimant) | Source |
|---|---|---|---|
| The PyPI distribution name is `dos-kernel`, not `dos` | one canonical dist name; the bare `dos` is an unrelated 2020 package | the PyPI index entry and the project's own `[project].name`, not the name you typed | [`SECURITY.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/SECURITY.md) |
| `pip install dos` shadows `import dos` with the squatter's module | both top-level packages own the `dos` module name | the resolved `dos.__file__` on disk, which the installer cannot fake | [`SECURITY.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/SECURITY.md) |
| The version lookup uses the dist name, so a wrong install is detectable | `importlib.metadata.version("dos-kernel")` reads our metadata; `"dos"` would miss it | the package metadata recorded at install, not a self-report | [`docs/FAQ.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/FAQ.md) |

## The one command

```bash
pip install dos-kernel
dos verify --workspace . PLAN PHASE
```

```text
NOT_SHIPPED  PLAN/PHASE  (source=none)
# exit code 1 — no commit backs the claim
```

A `SHIPPED` verdict exits `0`; `NOT_SHIPPED` exits `1`. The verb running at all is the cheapest possible confirmation that you installed the real `dos-kernel` and not the squatter — the bare `dos` package ships no such command.

## What this does — and does not — certify

Installing `dos-kernel` certifies you have the kernel, not that any particular work is correct. `dos verify` adjudicates "did this effect leave a checkable trace?" (a commit, a registry row, a merge ancestry) — never the correctness of a judgment that leaves no artifact, and never an agent's narration. The name guard is narrow too: it tells you which distribution resolved, not that the resolved bytes are unmodified — pin a version and use your normal supply-chain controls (hashes, a lockfile) for that. A green verdict means "this provably happened," not "this was the right thing to do."

## Sources / reproduce

- [Trust substrate for a fleet of autonomous agents](trust-substrate-for-a-fleet-of-autonomous-agents.md) — what the kernel is, in one page
- [How to verify an AI agent actually did the work](how-to-verify-an-ai-agent-actually-did-the-work.md) — the same distrust thesis, applied to a claim of done
- [`SECURITY.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/SECURITY.md) — the supply-chain section: `dos-kernel`, not `dos`
- [`docs/FAQ.md`](https://github.com/anthony-chaudhary/dos-kernel/blob/master/docs/FAQ.md) — the install disambiguation
- [FAQ](../FAQ.md) — the questions people ask before installing

## Also asked as

- Is the `dos` package on PyPI the real DOS kernel?
- `pip install dos` vs `pip install dos-kernel` — which one?
- Why does `pip install dos` give me a 2020 Flask helper?
- Which package is the Dispatch Operating System on PyPI?
- How do I avoid the `dos` typosquatter / name-collision package?
- What is the correct PyPI name for dos-kernel?
- Does installing `dos` shadow `import dos`?
- How do I pin the real dos-kernel in my requirements file?

> The kernel is the part that doesn't believe the agents.
