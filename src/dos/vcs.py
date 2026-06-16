"""The VCS seam — pluggable evidence-gathering, so git stops being *assumed*.

Why this exists
===============

DOS adjudicates ground truth from **non-forgeable evidence**, and today that
evidence is git: commit ancestry, the "N commits since start" forward delta, the
ship-stamp subjects `verify` greps, the files a commit touched. Every one of those
reads is a `subprocess.run(["git", …])` hardcoded at a caller boundary
(`git_delta`, `phase_shipped._git_log`, `commit_audit._git`, `oracle`). That is the
correct *shape* — I/O at the boundary, a pure `classify(evidence, policy)` core —
but it **fuses the kernel to one VCS**: a workspace on jj, Sapling, Mercurial, or no
VCS at all cannot supply evidence, and the graceful no-git degrade is an accident of
git's exit codes rather than a first-class, testable thing.

This module is the seam that breaks that fusion. A `VcsBackend` is the one object the
kernel asks for VCS evidence, the way `judges.py` is the one seam an adjudicator plugs
into. The built-in `GitBackend` is the default — the existing `git log`/`git show`
reads, lifted verbatim — and ships **in the kernel** beside the protocol, exactly as
`AbstainJudge` (the conservative default) ships in `judges.py`. That is deliberate:
git is NOT a vendor the bulkhead forbids. The kernel names git all over (CLAUDE.md:
"git ancestry + stamp grammar"); git is the kernel's *ground-truth substrate*. What the
seam adds is the ability to swap it — an *alternative* backend (Mercurial, Sapling, a
remote-API reader) is the open set, and those live in `drivers/vcs_*.py`, resolved by
name, never imported by a kernel module (the `drivers/` one-way rule).

`NullVcs` is the honest-empty backend: every read returns `[]` / `None` / `False`.
A workspace with no VCS, or a `dos.toml` that declares `[vcs] backend = "null"`,
yields exactly the evidence `git_delta` yields today in a non-git dir — liveness reads
"0 commits" (the honest floor), `verify` resolves `via none` (the evidence horizon,
not a lie). The seam does not *invent* graceful no-git degradation; it **names and
centralizes** the degradation the kernel already performs, and makes "this workspace
has no VCS" a first-class, testable backend rather than a returncode accident — the
same move `AbstainJudge` makes for "no adjudicator wired."

The contract (what keeps a swappable evidence source honest)
============================================================

A `VcsBackend` is an **evidence reader**, not an adjudicator. So its disciplines differ
from a judge's in one deliberate way — the *direction* of safe failure:

  1. **Thin & policy-free.** A backend returns raw facts — a list of `{sha, subject}`,
     a list of paths, a `bool`/`str`/`None` — and **never interprets them**. It does
     not parse a subject against the ship grammar (that is `dos.stamp`'s job), does not
     decide whether an ancestry result means "trust this anchor" (that is the caller's
     policy). `log_subjects` hands back opaque subject strings; the moment a backend
     pre-filtered them to "ships" it would re-entangle the read with the grammar.
  2. **Fail-to-EMPTY, never fail-to-anything-richer.** A read that cannot answer — git
     missing, a non-VCS dir, a timeout, an unknown ref — returns the *empty* shape
     (`[]` / `None` / `False`-on-`is_ancestor`→`None`), never raises out of the backend.
     This is the predicate-side direction (a safety read that cannot answer fails to
     the conservative value), the INVERSE of `judges.py`'s fail-to-ABSTAIN. The reason
     is the same: a failure must never become a richer claim than the evidence supports.
     Critically, the backend returns the empty shape and the **caller keeps its own
     failsafe policy** on top — `git_delta` reads `[]` as "no forward delta,"
     `resume_evidence` reads a `None` ancestry as "don't anchor here" (its `False`),
     `commit_audit` may re-raise. The backend never imposes one interpretation.
  3. **Three-valued where the truth is.** `is_ancestor` returns `bool | None`: `None`
     means "could not resolve" (unknown sha, shallow clone, no VCS), distinct from a
     definite `False` ("resolved — not an ancestor"). Collapsing unknown into `False`
     would regress `memory_recall`'s abstention (it must say UNKNOWN, not "stale"). The
     same three-valued honesty is why `files_in_commit` / `head_sha` return `… | None`.

Optional capabilities
---------------------

The seven methods on `VcsBackend` are the *core* — the reads the kernel's verdict
machinery needs, and the set `NullVcs` can honestly answer (with empties). Two richer
reads — `read_blob` (the raw committed bytes of one path, for a content diff) and
`history_search` (git's pickaxe: `log -S`, `--diff-filter=D`, `--all -- path`) — are
**optional**: the base protocol declares them returning `None`/`NotImplemented`, a
backend overrides them only if it can, and a caller that needs them keeps its existing
abstention path when the backend cannot. Keeping the core at seven is what lets
`NullVcs` stay honest and a non-git backend stay implementable.

Purity & layering
=================

This is **layer-1 kernel** beside `judges`/`render`/`admission`: a Protocol, two
value types, the built-in `GitBackend` + `NullVcs`, and resolver/runner helpers. It
imports only stdlib + `dos.config` (the seam it reads for the configured backend
name) + `dos`. Entry-point discovery (the one bit of I/O) happens at the call boundary
in `active_vcs`, exactly as `active_judges` / renderer discovery do — never inside a
verdict. A backend with provider/network surface (a remote-API reader) lives in a
`drivers/*` module or an installed plugin; the kernel points to it by name and never
imports it.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# Cap every VCS subprocess so a pathological repo can't hang an evidence-gather.
# Matches the 10s bound `git_delta` / `timeline._git_log` have always used.
_GIT_TIMEOUT_S = 10


@dataclass(frozen=True)
class Commit:
    """The domain-neutral unit a VCS read returns: a commit's identity + subject.

    Deliberately minimal — `sha` (whatever the backend's identity token is: a git
    short hash, a Mercurial node, a jj change id) and `subject` (the first line of
    the commit message). `body` is populated only by reads that asked for it
    (`log_subjects(..., bodies=True)`); `files` only by reads that asked for the
    touched-file set. Both default empty so the common `{sha, subject}` read stays
    cheap and a consumer never has to special-case their absence. This mirrors how
    `git_delta` has always returned `{"sha": …, "subject": …}` dicts — `to_dict`
    yields exactly that shape so existing callers are byte-unchanged.
    """

    sha: str
    subject: str = ""
    body: str = ""
    files: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, str]:
        """The `{sha, subject}` dict shape `git_delta` callers have always read."""
        return {"sha": self.sha, "subject": self.subject}


@dataclass(frozen=True)
class FileDelta:
    """One file's line-delta in a commit: ``(added, removed, path)``.

    The unit `commit_diffstat` returns. ``added``/``removed`` are ``-1`` for a binary
    file (git prints ``-`` in numstat — no countable line delta), so a caller counting
    test-line churn skips binaries by checking for the ``-1`` sentinel, exactly as it
    skipped git's ``-`` marker before the seam.
    """

    added: int
    removed: int
    path: str


@dataclass(frozen=True)
class WorkingTree:
    """The uncommitted working-tree state: ``head`` + modified + untracked paths.

    What `working_changes` returns. ``head`` is the current HEAD sha (short, or ``""``
    if unborn). ``modified`` carries each changed-but-tracked path with its 2-char
    status tag (``"M  src/x.py"``), ``untracked`` the new paths — the exact split
    `preflight`'s dirty-tree snapshot reports. A non-git backend that cannot read a
    working tree returns ``None`` from `working_changes`, never an empty `WorkingTree`
    (which would falsely read as "clean").
    """

    head: str = ""
    modified: tuple[str, ...] = field(default_factory=tuple)
    untracked: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class VcsBackend(Protocol):
    """The contract a workspace implements to supply VCS evidence to the kernel.

    ``name`` is the token `dos.toml [vcs] backend` selects and `dos doctor` lists.
    The seven core methods are the reads the kernel's evidence-gatherers need; every
    one returns the EMPTY shape (``[]`` / ``None`` / ``False``) when it cannot answer,
    and NONE of them interpret what they read (the thin-&-policy-free rule). A backend
    MAY do I/O inside these methods (shell out, hit a network) — like a judge and
    unlike a predicate, the VCS rung is where read I/O is allowed; the discipline that
    keeps it honest is fail-to-empty (a backend that raises is wrapped — see callers),
    not purity.
    """

    name: str

    def commits_since(self, start: str, *, limit: int | None = None) -> list[Commit]:
        """Commits on the served tree since ``start`` (exclusive), newest-first.

        The forward-progress delta: how many commits landed since a run began. ``[]``
        for an empty/unknown ``start``, a non-VCS tree, or any read failure. A special
        case of ``commits_in_range(f"{start}..HEAD")``.
        """
        ...

    def commits_in_range(
        self, spec: str, *, limit: int | None = None, full_sha: bool = False
    ) -> list[Commit]:
        """Commits in an arbitrary range ``spec`` (e.g. ``origin/main..HEAD``),
        newest-first, with full 40-char shas iff ``full_sha``.

        The general form ``commits_since`` is a special case of — `commit_audit`'s
        range audit needs an arbitrary ``A..B`` enumeration. The range syntax is the
        backend's to interpret (a non-git backend maps its own equivalent). ``[]`` for
        an empty/unresolvable ``spec`` or any read failure.
        """
        ...

    def recent_commits(self, n: int) -> list[Commit]:
        """The last ``n`` commits, newest-first — the *unanchored* sibling of
        ``commits_since`` (what landed lately, period). ``[]`` on any failure or n<=0.
        """
        ...

    def log_subjects(
        self,
        *,
        limit: int,
        paths: tuple[str, ...] = (),
        bodies: bool = False,
    ) -> list[Commit]:
        """Up to ``limit`` commits' subjects (and bodies iff ``bodies``), newest-first,
        optionally restricted to commits touching any of ``paths``.

        The read the ship-stamp grep rung consumes: it hands back opaque subject
        strings and the kernel's `dos.stamp` grammar decides which are ships. The
        backend MUST NOT parse them. ``[]`` on any failure.
        """
        ...

    def files_in_commit(self, sha: str) -> list[str] | None:
        """The repo-relative paths commit ``sha`` touched, or ``None`` if unresolvable.

        ``None`` (unknown sha, shallow clone, no VCS) is distinct from ``[]`` (an
        empty/`--allow-empty` commit that touched no files) — the caller's footprint
        checks rely on that distinction.
        """
        ...

    def is_ancestor(self, sha: str, of: str = "HEAD") -> bool | None:
        """Is ``sha`` an ancestor of ``of`` (default HEAD)? ``True``/``False``, or
        ``None`` when it cannot be resolved (unknown sha, no VCS, timeout).

        Three-valued on purpose: a caller anchoring a resume point treats ``None`` as
        "don't trust" (its `False`), while `memory_recall` treats ``None`` as UNKNOWN
        (abstain) — collapsing ``None`` into ``False`` here would regress that.
        """
        ...

    def head_sha(self, *, short: bool = False) -> str | None:
        """The current HEAD commit id (short form iff ``short``), or ``None`` if there
        is no HEAD (unborn branch, no VCS, read failure)."""
        ...

    def commit_meta(self, ref: str) -> Commit | None:
        """The ``{sha, subject}`` for one ``ref``, or ``None`` if it does not resolve."""
        ...

    def diff_names(self, base: str, head: str) -> list[str] | None:
        """The repo-relative paths that differ between ``base`` and ``head``, or
        ``None`` if the range cannot be resolved (unknown ref, no VCS).

        The two-ref footprint `verdict_cli`'s scope check needs — distinct from
        `files_in_commit` (one commit) and `commits_in_range` (the commit list). ``[]``
        means "resolved, no difference"; ``None`` means "could not resolve" — the
        caller's empty-footprint-vs-unknown distinction rides on it."""
        ...

    def working_changes(self) -> "WorkingTree | None":
        """The UNCOMMITTED working-tree state — modified + untracked paths + HEAD — or
        ``None`` if it cannot be read (no VCS, read failure).

        The one read that is NOT about history: `preflight`'s dirty-tree snapshot needs
        to know what is changed-but-uncommitted right now. A non-git backend that has
        no working-tree concept returns ``None`` and the caller treats the tree as
        "unknown" (it never fabricates a clean tree)."""
        ...

    def log_records(
        self,
        *,
        limit: int,
        paths: tuple[str, ...] = (),
        with_files: bool = False,
        with_body: bool = False,
    ) -> list[Commit]:
        """Up to ``limit`` commits as full ``Commit`` records, newest-first, merges
        excluded, optionally restricted to commits touching any of ``paths``.

        The structured read the ship-stamp grep rung (`phase_shipped`) consumes: each
        record carries ``sha`` + ``subject`` always, ``body`` iff ``with_body``, and
        the touched-file tuple ``files`` iff ``with_files`` (so the rung associates a
        commit with the load-bearing files it touched without re-parsing raw git
        output). The backend hands back opaque subjects/bodies — the kernel's
        `dos.stamp` grammar decides which are ships; the backend MUST NOT parse them.
        ``[]`` on any failure. This is the typed replacement for the heterogeneous
        ``git log`` shapes the rung used to assemble by hand (docs/360)."""
        ...

    # --- optional capabilities (a backend overrides only if it can) -------------

    def read_blob(self, sha: str, path: str) -> bytes | None:  # pragma: no cover - default
        """The raw committed bytes of ``path`` at ``sha``, or ``None`` if unsupported
        / unresolvable. Optional: the base contract returns ``None`` so a content-diff
        caller falls back to its existing abstention when a backend can't serve it."""
        return None

    def commit_diffstat(self, sha: str) -> "list[FileDelta] | None":  # pragma: no cover - default
        """Per-file ``(added, removed, path)`` line deltas for commit ``sha``, or
        ``None`` if unresolvable. Optional: `commit_audit` uses it to count test-line
        churn; a backend that cannot produce a numstat returns ``None`` and the caller
        falls back to a files-only read. ``added``/``removed`` are ``-1`` for a binary
        file (no countable line delta), matching git's ``-`` numstat marker."""
        return None

    def log_lines(self, args: tuple[str, ...]) -> list[str] | None:  # pragma: no cover - default
        """Raw ``git log <args>`` output lines, or ``None`` if unsupported.

        The escape hatch for the **ship-stamp grep rung** (`phase_shipped`) — the one
        consumer whose job IS parsing raw VCS-log output against the stamp grammar, in
        formats (``--name-only`` blocks, custom ``%h%n%B`` bodies) too git-shaped to
        reduce to the typed `log_records` without reserialization drift. The backend
        runs the log and returns the lines verbatim; the rung does ALL parsing (it
        never asks the backend to interpret a subject). ``None`` (the base default)
        means "this backend cannot serve raw git-arg logs" — a non-git backend; the
        rung then falls back to `log_records` or reports no evidence. Returning a
        FILTERED capability rather than a core method keeps the git-arg grammar out of
        the stable seven and honest about which backends can answer it (docs/360)."""
        return None

    def history_search(self, **kwargs: object) -> list[Commit] | None:  # pragma: no cover - default
        """A VCS-semantic history search (git's pickaxe: ``-S<literal>``,
        ``--diff-filter=D``, ``--all -- path``), or ``None`` if unsupported.

        Optional: `memory_recall`'s archaeology reads route here, and a backend that
        cannot answer returns ``None`` so the caller keeps its UNKNOWN abstention. The
        kwargs are intentionally open — this is the one method whose shape is the
        caller's and the backend's to agree on, NOT part of the stable core."""
        return None


def _run_git(args: list[str], *, root: Path | str) -> "subprocess.CompletedProcess | None":
    """One git subprocess with the kernel's standing safety envelope, or ``None``.

    The single home for the `["git", …]` call the whole kernel used to scatter:
    capped at ``_GIT_TIMEOUT_S``, ``stdin=DEVNULL`` (docs/295 — never leak the
    caller's stdin into a long-lived stdio server), utf-8 with replacement so a
    commit subject's `→`/`—` can't raise. Returns ``None`` for a missing git binary
    or a timeout (the OSError/TimeoutExpired class) — the caller maps that, and a
    non-zero exit, to its empty shape. ``root`` is always explicit, never a
    process-global, so a server fielding several workspaces reads the right tree.
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _parse_tab_log(stdout: str) -> list[Commit]:
    """`%h\\t%s` lines → `[Commit, …]`. The shared parse `git_delta` always did."""
    out: list[Commit] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        out.append(Commit(sha=parts[0], subject=parts[1]))
    return out


class GitBackend:
    """The built-in default: the kernel's existing git reads, behind the seam.

    Every method body is the subprocess the kernel ran before this seam existed,
    moved here verbatim (the `git_delta` / `phase_shipped._git_log` / `oracle`
    `git show` reads), with the same fail-to-empty contract. It ships in the kernel —
    not `drivers/` — because git is the kernel's ground-truth substrate, not a vendor
    (see the module docstring); the `drivers/` home is for *alternative* backends.
    ``root`` is bound at construction so the backend is a workspace-scoped object the
    resolver hands out, exactly as a judge is.
    """

    name = "git"

    def __init__(self, *, root: Path | str) -> None:
        self._root = root

    def commits_since(self, start: str, *, limit: int | None = None) -> list[Commit]:
        if not start:
            return []
        return self.commits_in_range(f"{start}..HEAD", limit=limit)

    def commits_in_range(
        self, spec: str, *, limit: int | None = None, full_sha: bool = False
    ) -> list[Commit]:
        if not spec:
            return []
        fmt = "%H%x09%s" if full_sha else "%h%x09%s"
        args = ["log", spec, f"--pretty=format:{fmt}"]
        if limit is not None and limit > 0:
            args.insert(1, f"-{int(limit)}")
        res = _run_git(args, root=self._root)
        if res is None or res.returncode != 0:
            return []
        return _parse_tab_log(res.stdout)

    def recent_commits(self, n: int) -> list[Commit]:
        if n <= 0:
            return []
        res = _run_git(
            ["log", f"-{int(n)}", "--pretty=format:%h%x09%s"], root=self._root
        )
        if res is None or res.returncode != 0:
            return []
        return _parse_tab_log(res.stdout)

    def log_subjects(
        self, *, limit: int, paths: tuple[str, ...] = (), bodies: bool = False
    ) -> list[Commit]:
        if limit <= 0:
            return []
        if bodies:
            # `%h\n%B\n--END--` blocks — body may span lines, so split on the marker.
            args = ["log", f"-{int(limit)}", "--format=%h%n%B%n--END--"]
            if paths:
                args += ["--", *paths]
            res = _run_git(args, root=self._root)
            if res is None or res.returncode != 0:
                return []
            out: list[Commit] = []
            for block in res.stdout.split("\n--END--"):
                block = block.strip("\n")
                if not block.strip():
                    continue
                head, _, body = block.partition("\n")
                out.append(Commit(sha=head.strip(), subject=body.partition("\n")[0],
                                  body=body))
            return out
        args = ["log", "--oneline", "--no-merges", f"-{int(limit)}"]
        if paths:
            args += ["--", *paths]
        res = _run_git(args, root=self._root)
        if res is None or res.returncode != 0:
            return []
        out2: list[Commit] = []
        for line in res.stdout.splitlines():
            parts = line.split(None, 1)
            if not parts:
                continue
            out2.append(Commit(sha=parts[0], subject=parts[1] if len(parts) > 1 else ""))
        return out2

    def files_in_commit(self, sha: str) -> list[str] | None:
        s = (sha or "").strip()
        if not s:
            return None
        res = _run_git(["show", "--name-only", "--format=", s], root=self._root)
        if res is None or res.returncode != 0:
            return None
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    def is_ancestor(self, sha: str, of: str = "HEAD") -> bool | None:
        s = (sha or "").strip()
        if not s:
            return None
        res = _run_git(["merge-base", "--is-ancestor", s, of], root=self._root)
        if res is None:
            return None  # git missing / timeout — unresolvable, not "not an ancestor"
        if res.returncode == 0:
            return True
        if res.returncode == 1:
            return False
        return None  # rc 128 etc. (unknown sha, non-git dir) — unresolvable

    def head_sha(self, *, short: bool = False) -> str | None:
        args = ["rev-parse"] + (["--short"] if short else []) + ["HEAD"]
        res = _run_git(args, root=self._root)
        if res is None or res.returncode != 0:
            return None
        out = res.stdout.strip()
        return out or None

    def commit_meta(self, ref: str) -> Commit | None:
        r = (ref or "").strip()
        if not r:
            return None
        res = _run_git(["log", "-1", "--pretty=format:%h%x09%s", r], root=self._root)
        if res is None or res.returncode != 0:
            return None
        rows = _parse_tab_log(res.stdout)
        return rows[0] if rows else None

    def diff_names(self, base: str, head: str) -> list[str] | None:
        if not base or not head:
            return None
        res = _run_git(["diff", "--name-only", f"{base}..{head}"], root=self._root)
        if res is None or res.returncode != 0:
            return None
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    def working_changes(self) -> "WorkingTree | None":
        head_res = _run_git(["rev-parse", "--short", "HEAD"], root=self._root)
        head = head_res.stdout.strip() if (head_res and head_res.returncode == 0) else ""
        st = _run_git(["status", "--short"], root=self._root)
        if st is None or st.returncode != 0:
            # No working tree readable (no VCS / read failure) AND no HEAD → unknown.
            # If HEAD resolved we still can't honestly report the dirty set, so a status
            # failure is the deciding signal: return None (never a false "clean").
            return None
        modified: list[str] = []
        untracked: list[str] = []
        for line in st.stdout.splitlines():
            if not line.strip():
                continue
            tag = line[:2]
            path = line[3:].strip()
            if tag.startswith("??"):
                untracked.append(path)
            else:
                modified.append(f"{tag} {path}")
        return WorkingTree(head=head, modified=tuple(modified), untracked=tuple(untracked))

    def log_records(
        self,
        *,
        limit: int,
        paths: tuple[str, ...] = (),
        with_files: bool = False,
        with_body: bool = False,
    ) -> list[Commit]:
        if limit <= 0:
            return []
        # NUL-delimited header so a subject/body containing a tab or newline can't
        # be mis-split: each commit starts `\x00<sha>\x00<subject>` (the exact format
        # phase_shipped's union scan used), then `--name-only` emits one path per line
        # under it, and (for bodies) the raw `%b` body follows the header. We assemble
        # the records by scanning for the `\x00` header marker, byte-faithful to the
        # rung's own former parse.
        fmt = "%x00%h%x00%s" + ("%n%b" if with_body else "")
        args = ["log", "--no-merges", f"-{int(limit)}", f"--format={fmt}"]
        if with_files:
            args.append("--name-only")
        if paths:
            args += ["--", *paths]
        res = _run_git(args, root=self._root)
        if res is None or res.returncode != 0:
            return []
        out: list[Commit] = []
        cur_sha = cur_subj = ""
        body_lines: list[str] = []
        file_lines: list[str] = []

        def _flush() -> None:
            if not cur_sha:
                return
            out.append(Commit(
                sha=cur_sha,
                subject=cur_subj,
                body="\n".join(body_lines).strip() if with_body else "",
                files=tuple(file_lines) if with_files else (),
            ))

        for line in res.stdout.splitlines():
            if line.startswith("\x00"):
                _flush()
                _, _, rest = line.partition("\x00")
                cur_sha, _, cur_subj = rest.partition("\x00")
                body_lines = []
                file_lines = []
                continue
            if not cur_sha:
                continue
            stripped = line.strip()
            if with_files and stripped:
                file_lines.append(stripped)
            if with_body:
                body_lines.append(line)
        _flush()
        return out

    def log_lines(self, args: tuple[str, ...]) -> list[str] | None:
        # The grep rung's raw passthrough: `git log <args>`, lines verbatim. Returns
        # None on a non-zero exit / missing git so the caller degrades exactly as the
        # rung's old `except RuntimeError` did (it caught the raise and used []).
        res = _run_git(["log", *args], root=self._root)
        if res is None or res.returncode != 0:
            return None
        return res.stdout.splitlines()

    def history_search(self, **kwargs: object) -> list[Commit] | None:
        # The pickaxe / path-archaeology reads `memory_recall` needs (docs/360). Three
        # modes, each newest-match-first, restricted to one `path`:
        #   mode="pickaxe", literal=<str>  → `git log -S<literal> -- <path>`
        #   mode="deleted"                 → `git log --diff-filter=D -- <path>`
        #   mode="tracked"                 → `git log --all -- <path>`
        # Returns `[Commit]` (possibly empty) or None on a read failure. A non-git
        # backend leaves the base `history_search` returning None → the caller keeps
        # its UNKNOWN abstention, never a false verdict.
        mode = str(kwargs.get("mode", ""))
        path = str(kwargs.get("path", ""))
        limit = int(kwargs.get("limit", 1) or 1)
        if not mode or not path:
            return None
        if mode == "pickaxe":
            literal = str(kwargs.get("literal", ""))
            if not literal:
                return None
            args = ["log", "-S", literal, "-n", str(limit),
                    "--pretty=format:%h%x09%s", "--", path]
        elif mode == "deleted":
            args = ["log", "--diff-filter=D", "-n", str(limit),
                    "--pretty=format:%h%x09%s", "--", path]
        elif mode == "tracked":
            args = ["log", "--all", "-n", str(limit),
                    "--pretty=format:%h%x09%s", "--", path]
        else:
            return None
        res = _run_git(args, root=self._root)
        if res is None or res.returncode != 0:
            return None
        return _parse_tab_log(res.stdout)

    def read_blob(self, sha: str, path: str) -> bytes | None:
        s = (sha or "").strip()
        if not s or not path:
            return None
        try:
            res = subprocess.run(
                ["git", "show", f"{s}:{path}"],
                cwd=str(self._root),
                capture_output=True,  # bytes — no text decode for a blob
                check=False,
                timeout=_GIT_TIMEOUT_S,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if res.returncode != 0:
            return None
        return res.stdout

    def commit_diffstat(self, sha: str) -> "list[FileDelta] | None":
        s = (sha or "").strip()
        if not s:
            return None
        res = _run_git(
            ["show", "--numstat", "--format=", "--no-renames", s], root=self._root
        )
        if res is None or res.returncode != 0:
            return None
        out: list[FileDelta] = []
        for line in res.stdout.splitlines():
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added_s, removed_s, path = parts
            path = path.strip()
            if not path:
                continue
            # git prints `-` for a binary file's added/removed — map to the -1 sentinel.
            try:
                added = int(added_s) if added_s != "-" else -1
                removed = int(removed_s) if removed_s != "-" else -1
            except ValueError:
                added = removed = -1
            out.append(FileDelta(added=added, removed=removed, path=path))
        return out


class NullVcs:
    """The honest-empty backend: every read returns the empty shape.

    The `AbstainJudge` analogue — the always-available, unshadowable baseline a
    workspace gets when it has no VCS, declares `[vcs] backend = "null"`, or its git
    binary is gone. It makes "this workspace has no VCS" a FIRST-CLASS, testable
    backend rather than an accident of git's exit codes: liveness reads "0 commits"
    (the honest floor), `verify` resolves `via none` (the evidence horizon). Nothing
    here can manufacture evidence — that is the point.
    """

    name = "null"

    def __init__(self, *, root: Path | str | None = None) -> None:
        # Accept (and ignore) `root` so the resolver constructs every built-in the
        # same way — `_BUILT_IN_VCS[name](root=root)`. A no-VCS backend has no tree.
        self._root = root

    def commits_since(self, start: str, *, limit: int | None = None) -> list[Commit]:
        return []

    def commits_in_range(
        self, spec: str, *, limit: int | None = None, full_sha: bool = False
    ) -> list[Commit]:
        return []

    def recent_commits(self, n: int) -> list[Commit]:
        return []

    def log_subjects(
        self, *, limit: int, paths: tuple[str, ...] = (), bodies: bool = False
    ) -> list[Commit]:
        return []

    def files_in_commit(self, sha: str) -> list[str] | None:
        return None

    def is_ancestor(self, sha: str, of: str = "HEAD") -> bool | None:
        return None

    def head_sha(self, *, short: bool = False) -> str | None:
        return None

    def commit_meta(self, ref: str) -> Commit | None:
        return None

    def diff_names(self, base: str, head: str) -> list[str] | None:
        return None

    def working_changes(self) -> "WorkingTree | None":
        return None

    def log_records(
        self,
        *,
        limit: int,
        paths: tuple[str, ...] = (),
        with_files: bool = False,
        with_body: bool = False,
    ) -> list[Commit]:
        return []

    def read_blob(self, sha: str, path: str) -> bytes | None:
        return None

    def commit_diffstat(self, sha: str) -> "list[FileDelta] | None":
        return None

    def log_lines(self, args: tuple[str, ...]) -> list[str] | None:
        return None

    def history_search(self, **kwargs: object) -> list[Commit] | None:
        return None


# ---------------------------------------------------------------------------
# Resolution — built-in first, then the `dos.vcs` entry-point group. Byte-for-byte
# the `judges.py` resolver shape: built-ins unshadowable, unknown name fails loud,
# discovery I/O at the call boundary only.
# ---------------------------------------------------------------------------

VCS_ENTRY_POINT_GROUP = "dos.vcs"

# The built-in backends, resolvable by name and UNSHADOWABLE by a plugin (a plugin
# registering `git` cannot displace this one — built-ins resolve first). Each is a
# class taking `root=` at construction, so the resolver binds it to a workspace.
_BUILT_IN_VCS: dict[str, type] = {
    GitBackend.name: GitBackend,
    NullVcs.name: NullVcs,
}


def _discover_entry_point_vcs(*, _stderr=None) -> list[tuple[str, type]]:
    """Find VCS backends registered under the `dos.vcs` entry-point group.

    A plugin registers ``name = "pkg.module:BackendClass"`` in its
    ``[project.entry-points."dos.vcs"]``. We load each as a CLASS (the resolver binds
    ``root=`` per workspace), returning ``(entry_point_name, cls)`` sorted by name. A
    plugin that fails to load is skipped with a one-line stderr note — the same
    posture judge/predicate/renderer discovery take (a broken third-party plugin is
    the operator's to fix, not a kernel fault).
    """
    stderr = _stderr if _stderr is not None else sys.stderr
    out: list[tuple[str, type]] = []
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - importlib.metadata always present py3.11+
        return out
    try:
        eps = entry_points(group=VCS_ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - py<3.10 selectable-API fallback
        eps = entry_points().get(VCS_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive: never let discovery crash a call
        return out
    for ep in sorted(eps, key=lambda e: e.name):
        try:
            obj = ep.load()
        except Exception as e:  # pragma: no cover - depends on third-party plugin
            print(
                f"warning: vcs plugin {ep.name!r} failed to load ({e}); skipping",
                file=stderr,
            )
            continue
        out.append((ep.name, obj))
    return out


def resolve_vcs(name: str, *, root: Path | str, _stderr=None) -> VcsBackend:
    """Resolve a backend by name, bound to ``root``: built-ins first, then plugins.

    Built-ins (`git`, `null`) resolve FIRST and cannot be shadowed by a plugin of the
    same name — the trusted-fallback guarantee, identical to `resolve_judge` /
    `resolve_renderer`. An unknown name fails LOUD with the known list (never silently
    degrades to `git` or `null`, which would hide a typo'd `[vcs] backend`): the host
    asked for a specific backend and getting a different one silently is exactly the
    unannounced substitution the kernel refuses.
    """
    if name in _BUILT_IN_VCS:
        return _BUILT_IN_VCS[name](root=root)
    discovered = dict(_discover_entry_point_vcs(_stderr=_stderr))
    if name in discovered:
        cls = discovered[name]
        try:
            return cls(root=root)  # type: ignore[call-arg]
        except TypeError:
            return cls()  # a backend that ignores root (a remote reader) — allowed
    known = sorted(set(_BUILT_IN_VCS) | set(discovered))
    raise ValueError(f"unknown vcs backend {name!r}; known: {', '.join(known)}")


def active_vcs(*, root: Path | str, cfg: object | None = None) -> VcsBackend:
    """The call-boundary resolver: the backend the active config selects, bound to
    ``root``.

    Reads ``cfg.vcs_backend`` (default ``"git"``) — pass an explicit ``cfg`` (the
    long-lived-server discipline) or let it fall back to the process-active config.
    Does ENTRY-POINT DISCOVERY (I/O) when the name isn't a built-in, so it is a
    call-boundary helper, never called inside a verdict — exactly as `active_judges`
    is. The common path (`vcs_backend == "git"`) hits the built-in map with no
    discovery I/O at all.
    """
    name = "git"
    if cfg is not None:
        name = getattr(cfg, "vcs_backend", "git") or "git"
    else:
        try:
            from dos import config as _config

            name = getattr(_config.active(), "vcs_backend", "git") or "git"
        except Exception:  # pragma: no cover - never let config-resolution crash a read
            name = "git"
    return resolve_vcs(name, root=root)


def active_vcs_names(*, _stderr=None) -> list[str]:
    """The names of every resolvable backend (built-ins + discovered) — what
    `dos doctor` lists so an operator can see which VCS evidence sources are wired."""
    discovered = [n for n, _cls in _discover_entry_point_vcs(_stderr=_stderr)]
    # Built-ins first, then any plugin names not shadowing a built-in.
    return list(_BUILT_IN_VCS) + [n for n in discovered if n not in _BUILT_IN_VCS]
