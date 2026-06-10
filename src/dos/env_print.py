"""The environment print — a content-addressed record of *under what* a verdict ran (docs/115 §2).

> **DOS records *who did what* (the run-id spine, the lease WAL, the intent
> ledger, git ancestry). It records nothing about *under what* — which kernel,
> which Python, which OS, which toolchain adjudicated or produced the record. So
> two runs in different environments can reach different verdicts on the same
> input with no trace of the divergence in the fossil. An `EnvPrint` is the
> missing fact: gathered ONCE at the build boundary (a `WorkspaceFacts` sibling),
> frozen as data on the `SubstrateConfig`, and stamped onto the durable surfaces
> so every adjudication is contestable — recompute it under the recorded print
> and see if the verdict holds.**

This is the object for docs/115 primitive 1. It is deliberately the
``run_id``/``WorkspaceFacts`` shape, not a new pattern:

  * **A pure, frozen dataclass** (`EnvPrint`) that round-trips through a JSONL line
    (`to_dict`/`from_dict`, the `RunId.to_dict` idiom). Constructible with NO I/O —
    a hand-built print for a unit test never shells `git` (the
    ``WorkspaceFacts(root=…)`` test-construction rule).
  * **One boundary gatherer** (`gather_env_print`) — the ONLY function here that
    touches `sys`/`platform`/`git`/the declared tool binaries. Called by the config
    BUILDERS (`default_config`/`job_config`/`load_workspace_config`), the same
    boundary `gather_workspace_facts` runs at, never inside a pure verdict (the
    "I/O at the boundary, data to the pure core" discipline — cf.
    `git_delta`/`journal_delta` → `liveness.classify`).
  * **A content-addressed `digest`** — a short, stable hash over the print's fields
    (Crockford base32, the run-id token alphabet). Two environments with the same
    `digest` are interchangeable *by declaration*; the kernel does NOT assert they
    are behaviorally identical (the model-id caveat — a pinned weight set is not a
    pinned behavior — applies to the whole print). The `digest` is the *`EnvId`*:
    the cheap key a WAL entry carries, and the value docs/115 primitive 3's
    `FLEET_ENV_MISMATCH` arbiter gate compares against a declared pin.

What an `EnvPrint` is NOT (docs/115 §2):

  * **Not a sandbox manager.** DOS does not create, snapshot, or enforce
    environments — that is the host's container/Nix/devcontainer layer (the docs/99
    actuation boundary: the kernel RECORDS and REFUSES, it does not ACTUATE). This
    module records the *print* of whatever environment it was run in.
  * **Not a behavioral guarantee.** A matching `digest` means "the same declared
    inputs," never "the same output" (the temp-0-nondeterminism + model-id-drift
    caveats forbid that claim). The print is evidence FOR a reproduction attempt,
    not a proof OF reproducibility.
  * **Not mandatory on the pure core.** A `SubstrateConfig` built without gathering
    (the test path) carries ``env=None``; every consumer treats ``None`` as "not
    recorded," exactly as ``WorkspaceFacts=None`` is treated. A pure verdict is
    handed a print to STAMP, the way it is handed a clock — it never REQUIRES one.

The `tools` set is DECLARED (``dos.toml [env] tools = ["git", "node"]``), not an
open probe of everything on PATH: the kernel records only what a workspace says
matters, keeping the print small, stable, and free of ambient noise (the
closed-set-as-data discipline `reasons`/`stamp` ride, applied to the env axis).

Every `EnvPrint` carries a `durable_schema` family (``"env-print"``, version 1)
like every other durable record, so a print a newer kernel wrote is
refused-don't-guessed at read, not misparsed (docs/115 primitive 4 closes the loop
on the print itself).

Pure stdlib + `dos.durable_schema` (a leaf) — no third-party imports. The git read
is a guarded `subprocess` confined to `gather_env_print`, fail-safe to ``None`` on
any failure (no git, timeout, non-git dir), the `git_delta` posture.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from dos import durable_schema as _schema

# The durable-schema family + version every env-print record carries (§6/docs/115).
# Bumped ONLY on a NON-additive shape change; a new optional field (a new declared
# tool, say) is additive and does NOT bump it. A print tagged higher than this is
# REFUSED at read (`durable_schema.classify`), never guessed.
SCHEMA_FAMILY = "env-print"
ENV_PRINT_SCHEMA = 1

# The digest alphabet — Crockford base32, the same human-safe, case-folding set the
# run-id token uses (no I/O/O confusion, sortable). The digest is a fixed-width
# slice of a SHA-256 over the print's canonical fields, so it is short enough to
# eyeball in a WAL entry and stable across processes/platforms (a hash, not a
# Python `hash()` — which is salted per-process and would not match across runs).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DIGEST_WIDTH = 12  # 12 base32 chars ≈ 60 bits — ample for an interchangeability key


@dataclass(frozen=True)
class ToolVersion:
    """One declared tool and the version string probed for it. Pure data.

    ``name`` — the tool a workspace declared it cares about (``"git"``, ``"node"``).
    ``version`` — the version string `gather_env_print` probed, or ``""`` when the
        tool was declared but not found / did not answer (recorded as absent, not
        dropped — "git was declared and missing" is itself a fact a reproduction
        attempt needs, distinct from "git was never declared").
    """

    name: str
    version: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version}

    @classmethod
    def from_obj(cls, obj: Any) -> "ToolVersion | None":
        if not isinstance(obj, Mapping):
            return None
        name = obj.get("name")
        if not isinstance(name, str) or not name:
            return None
        ver = obj.get("version")
        return cls(name=name, version=ver if isinstance(ver, str) else "")


@dataclass(frozen=True)
class EnvPrint:
    """A content-addressed record of the environment a verdict was computed in.

    The `WorkspaceFacts` sibling on `SubstrateConfig` (`.env`): a frozen set of
    facts about the *runtime*, gathered once at the build boundary and stamped onto
    the durable surfaces. Pure — constructible with no I/O for a test.

      kernel_version — `dos.__version__` (e.g. ``"0.8.0"``); the pip/dist version.
      kernel_sha     — the git SHA of the KERNEL's own source tree (HEAD), or
                       ``None`` when it cannot be determined (not a git checkout, a
                       wheel install). The one fact that catches the stale-editable-
                       `.pth` hazard directly: two worktrees at the same
                       `kernel_version` but different commits print different SHAs,
                       so a verdict from the wrong tree is self-evident in the fossil.
      python         — the Python version (``"3.13.1"``), `sys.version_info` joined,
                       NOT the full multi-line `sys.version` banner (stable across
                       builds of the same x.y.z).
      platform       — ``"<system>-<machine>"`` (``"win32-AMD64"`` / ``"linux-x86_64"``).
      tools          — the DECLARED tool versions (`ToolVersion`s), in declaration
                       order. Empty when a workspace declared none.

    The `digest` (a property, not a stored field) is the `EnvId`: a stable hash over
    (kernel_version, kernel_sha, python, platform, tools). Computed, never stored, so
    it can never drift out of sync with the fields it summarizes — a record reads the
    fields back and recomputes; a stored digest that disagreed with its fields would
    be the exact silent-drift the kernel forbids.
    """

    kernel_version: str
    kernel_sha: str | None = None
    python: str = ""
    platform: str = ""
    tools: tuple[ToolVersion, ...] = ()

    @property
    def digest(self) -> str:
        """The content-addressed `EnvId` — a stable base32 hash over the fields.

        Deterministic across processes and platforms (a SHA-256 over a canonical
        string, NOT Python's per-process-salted `hash()`), so the same environment
        always prints the same digest and `FLEET_ENV_MISMATCH` (docs/115 §5) can
        compare a worker's digest to a declared pin by equality. The tool set is
        sorted into the canonical string so declaration ORDER does not change the
        digest (two configs that declare ``["git","node"]`` vs ``["node","git"]``
        describe the same environment and must hash alike).
        """
        tools_canon = ",".join(
            f"{t.name}={t.version}" for t in sorted(self.tools, key=lambda t: t.name)
        )
        canon = "\x1f".join((
            self.kernel_version,
            self.kernel_sha or "",
            self.python,
            self.platform,
            tools_canon,
        ))
        h = int.from_bytes(hashlib.sha256(canon.encode("utf-8")).digest(), "big")
        out = []
        for _ in range(_DIGEST_WIDTH):
            out.append(_CROCKFORD[h & 0x1F])
            h >>= 5
        return "".join(reversed(out))

    def to_dict(self) -> dict:
        """The shape stamped onto a durable record (carries the schema tag).

        Includes the computed `digest` as a convenience for a `--json` reader that
        wants the key without recomputing — but `from_dict` RECOMPUTES it from the
        fields and ignores any stored value, so a tampered/stale `digest` in a
        record can never be believed (the field is authoritative, the stored digest
        is a courtesy). The `durable_schema` tag rides here so a stamped print
        self-declares its format (the `intent_entry` idiom).
        """
        return {
            **_schema.tag(SCHEMA_FAMILY, ENV_PRINT_SCHEMA),
            "kernel_version": self.kernel_version,
            "kernel_sha": self.kernel_sha,
            "python": self.python,
            "platform": self.platform,
            "tools": [t.to_dict() for t in self.tools],
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, obj: Mapping[str, Any]) -> "EnvPrint | None":
        """Parse an `EnvPrint` from a stamped record. None if absent/malformed.

        Tolerant the way `SchemaTag.from_obj` is — a missing/garbled print yields
        ``None``, not a crash (a fossil written by a kernel that did not stamp prints
        simply has no print to read). The `digest` is RECOMPUTED from the parsed
        fields; any stored ``"digest"`` is ignored, so the key can never disagree
        with the data it summarizes.
        """
        if not isinstance(obj, Mapping):
            return None
        kv = obj.get("kernel_version")
        if not isinstance(kv, str) or not kv:
            return None
        sha = obj.get("kernel_sha")
        tools_raw = obj.get("tools")
        tools: list[ToolVersion] = []
        if isinstance(tools_raw, Iterable) and not isinstance(tools_raw, (str, bytes)):
            for t in tools_raw:
                tv = ToolVersion.from_obj(t)
                if tv is not None:
                    tools.append(tv)
        return cls(
            kernel_version=kv,
            kernel_sha=sha if isinstance(sha, str) and sha else None,
            python=obj.get("python") if isinstance(obj.get("python"), str) else "",
            platform=obj.get("platform") if isinstance(obj.get("platform"), str) else "",
            tools=tuple(tools),
        )


# ---------------------------------------------------------------------------
# The boundary gatherer — the ONE I/O home (the `gather_workspace_facts` rule).
# Everything above is pure; everything that touches sys/platform/git is here.
# ---------------------------------------------------------------------------

_GIT_TIMEOUT_S = 10  # the `git_delta` cap — a hung git never blocks a config build


def _python_version() -> str:
    """``"3.13.1"`` — the x.y.z, not the full `sys.version` banner."""
    vi = sys.version_info
    return f"{vi.major}.{vi.minor}.{vi.micro}"


def _platform_tag() -> str:
    """``"<system>-<machine>"`` — ``"linux-x86_64"`` / ``"win32-AMD64"``.

    `sys.platform` for the OS (matches the value DOS already reports in `doctor`'s
    environment block and the `_filelock` win32 branch keys on) + `platform.machine`
    for the arch, so a print distinguishes the same OS on different CPUs.
    """
    machine = platform.machine() or "unknown"
    return f"{sys.platform}-{machine}"


def _kernel_sha(kernel_root: Path | None) -> str | None:
    """The git HEAD SHA of the kernel's OWN tree, or ``None``. Guarded `subprocess`.

    Anchored on the kernel package's own location (the directory `dos/` lives in),
    NOT the served workspace — the question is "which commit of DOS is running,"
    which is a property of the installed kernel, not of the repo it is adjudicating.
    Fail-safe to ``None`` on every failure (no git, not a checkout, timeout) — a
    wheel-installed kernel has no SHA and that is a recorded fact, not an error (the
    `git_delta` returns-[] posture, lifted to "returns None").
    """
    root = kernel_root or Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            # Never inherit the caller's stdin: inside a long-lived stdio server
            # (dos-mcp) it is the live transport pipe, and a git child holding it
            # wedges on Windows — the docs/295 stall. An evidence probe reads no
            # stdin, so it declares that.
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha or None


def _tool_version(name: str) -> str:
    """Probe ``<name> --version`` → its first non-empty output line, or ``""``.

    Guarded `subprocess`, fail-safe to ``""`` (declared-but-absent is a fact, not an
    error). Returns the raw first line the tool prints — the kernel does not parse a
    semantic version out of it (that would be tool-specific policy); the print
    records what the tool SAID, and two runs of the same tool print the same line.
    """
    try:
        out = subprocess.run(
            [name, "--version"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,  # docs/295 — never leak the caller's stdin
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    for line in (out.stdout or out.stderr or "").splitlines():
        s = line.strip()
        if s:
            return s
    return ""


# Per-process memo of gathered prints, keyed by (tools, kernel_root). The print
# describes the RUNTIME — kernel version + kernel-SHA + Python + OS/arch + the
# declared tools' versions — none of which change while the process lives (the
# kernel SHA cannot move under a running server; `platform.machine()` is itself
# CPython-cached). So the docstring's "probe the runtime ONCE" is literally true
# per process: the FIRST gather pays the `git rev-parse` subprocess + the WMI
# platform query (~tens of ms on Windows); every later gather returns the frozen
# print for free. This is the single biggest cost on the MCP server's per-tool-call
# config build (`load_workspace_config` → `default_config` → here), which used to
# re-spawn `git` on every call. Cleared by `_clear_env_print_cache()` for the rare
# test that wants to force a re-probe.
_GATHER_CACHE: "dict[tuple, EnvPrint]" = {}


def _clear_env_print_cache() -> None:
    """Drop the per-process gather memo (test hook; a real process never needs it)."""
    _GATHER_CACHE.clear()


def gather_env_print(
    *,
    tools: Iterable[str] = (),
    kernel_root: Path | None = None,
) -> EnvPrint:
    """Probe the runtime once and freeze the discovered `EnvPrint`. The I/O HOME.

    Called by the config BUILDERS (the boundary already allowed to touch the disk),
    never by a pure verdict — the `gather_workspace_facts` discipline. `tools` is the
    workspace's DECLARED tool list (from ``dos.toml [env] tools``); each is probed
    via ``<name> --version`` and recorded (present or absent). `kernel_root` overrides
    where the kernel-SHA git read is anchored (tests / an oddly-installed kernel);
    defaults to this module's own directory.

    Memoized per process on ``(tools, kernel_root)`` (see `_GATHER_CACHE`): the print
    is a property of the running KERNEL, constant for the process's lifetime, so the
    git subprocess + platform probe run ONCE and every later call is free. This is the
    "gathered once at the build boundary" contract made literal — and what keeps a
    long-lived server (the MCP server builds a config per tool call) from re-spawning
    `git rev-parse` on every call.

    `dos.__version__` is read lazily here (not at module import) to avoid a circular
    import — `dos/__init__.py` imports `config`, which will import this; reaching back
    up to the package at import time would cycle. At CALL time the package is fully
    loaded, the same lazy-resolve `gather_workspace_facts` uses for `self_modify`.
    """
    # Materialize `tools` ONCE — it is typed `Iterable[str]`, so a one-shot
    # generator is legal, and we both key the cache on it and (on a miss) iterate
    # it to probe; reusing the same tuple keeps a generator caller correct.
    tool_names = tuple(tools)
    key = (tool_names, kernel_root)
    cached = _GATHER_CACHE.get(key)
    if cached is not None:
        return cached

    from dos import __version__ as kernel_version  # noqa: PLC0415 — lazy, anti-cycle

    probed = tuple(ToolVersion(name=n, version=_tool_version(n)) for n in tool_names)
    print_ = EnvPrint(
        kernel_version=kernel_version,
        kernel_sha=_kernel_sha(kernel_root),
        python=_python_version(),
        platform=_platform_tag(),
        tools=probed,
    )
    _GATHER_CACHE[key] = print_
    return print_
