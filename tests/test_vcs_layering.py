"""The VCS-read litmus (docs/360): the kernel reads VCS only through `dos.vcs`.

The seam analogue of `test_home_layering` / the judge bulkhead: a kernel module that
shells `git` directly re-fuses the kernel to one VCS and bypasses the swappable
`VcsBackend`. This AST-walks every `src/dos/*.py` and asserts NONE of them (outside the
three sanctioned homes) contains a `subprocess.run(["git", …])`-shaped call:

  * ``vcs.py`` — the ONE legal home for the git subprocess (GitBackend lives here).
  * ``drivers/*`` — the vendor-allowed layer (a driver MAY name git; the bulkhead
    forbids non-git VENDORS in the kernel, not git, which is the ground-truth
    substrate). Alternative backends live here too.
  * ``cli.py`` — the layer-3 CLI boundary shell, where the architecture sanctions
    direct I/O (the `dos demo` walkthrough echoes literal `git` commands it runs).

Every other kernel module — the evidence gatherers `git_delta` / `phase_shipped` /
`oracle` / `commit_audit` / `resume_evidence` / `env_print` / `health` / `preflight` /
`verdict_cli` — must route through `active_vcs(...)`. This test fails the moment one
re-introduces a raw `["git", …]` subprocess.
"""
from __future__ import annotations

import ast
from pathlib import Path

import dos


# The only kernel modules allowed to name `git` in a subprocess call.
_EXEMPT_FILES = {"vcs.py", "cli.py"}
_EXEMPT_DIRS = {"drivers"}


def _kernel_py_files() -> list[Path]:
    root = Path(dos.__file__).parent
    out: list[Path] = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)
        if rel.parts[0] in _EXEMPT_DIRS:
            continue
        if py.name in _EXEMPT_FILES:
            continue
        out.append(py)
    return out


def _calls_git_subprocess(py: Path) -> list[int]:
    """Line numbers of any `subprocess.run/Popen/call/check_output([...,"git",...])`
    whose first list element is the string literal "git"."""
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    hits: list[int] = []
    _SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # match subprocess.run(...) / sp.run(...) / _sp.run(...) — any attr named run/…
        if not (isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.List) or not first.elts:
            continue
        head = first.elts[0]
        if isinstance(head, ast.Constant) and head.value == "git":
            hits.append(node.lineno)
    return hits


def test_no_kernel_module_shells_git_directly():
    """No kernel module (outside vcs.py / drivers / cli.py) calls git via subprocess."""
    offenders: dict[str, list[int]] = {}
    for py in _kernel_py_files():
        hits = _calls_git_subprocess(py)
        if hits:
            offenders[py.name] = hits
    assert offenders == {}, (
        "kernel modules shell `git` directly instead of through the dos.vcs seam "
        f"(docs/360): {offenders}. Route the read through active_vcs(root=…)."
    )


def test_vcs_module_is_the_one_git_home():
    """Sanity: vcs.py DOES contain the git subprocess (the home is real, the litmus
    is testing something). If this fails, GitBackend stopped shelling git."""
    vcs_py = Path(dos.__file__).parent / "vcs.py"
    assert _calls_git_subprocess(vcs_py), "vcs.py should be the home of the git subprocess"


def test_vcs_imports_only_config_and_stdlib():
    """vcs.py is layer-1 kernel: it imports only `dos.config` / `dos` (+ stdlib).

    A module-level import of any other `dos.*` would risk the load-order cycle the
    lazy `active_vcs` config import exists to avoid. (Lazy, function-local imports of
    dos.config inside a function body are fine — this checks MODULE-level imports.)
    """
    vcs_py = Path(dos.__file__).parent / "vcs.py"
    tree = ast.parse(vcs_py.read_text(encoding="utf-8"), filename=str(vcs_py))
    allowed = {"dos", "dos.config"}
    offenders: list[str] = []
    for node in tree.body:  # MODULE-level only (not ast.walk — lazy imports are fine)
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names
                          if (a.name == "dos" or a.name.startswith("dos.")) and a.name not in allowed]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if (node.module == "dos" or node.module.startswith("dos.")) and node.module not in allowed:
                offenders.append(node.module)
    assert offenders == [], f"vcs.py has disallowed module-level dos imports: {offenders}"


def test_no_kernel_module_imports_a_vcs_backend_implementation():
    """The bulkhead extended (docs/360): a non-git VCS backend (a Mercurial / Sapling /
    remote-API reader) is a DRIVER, resolved by name under the `dos.vcs` entry-point
    group — never imported by a kernel module, exactly like a ruling judge. So no kernel
    module (outside drivers) may `import dos.drivers.*` for a vcs backend.

    We check the structural invariant (no driver import) rather than substring-matching
    vendor NAMES — the latter false-fires on honest docstrings that explain the seam
    ("a workspace on Mercurial / Sapling supplies its own backend"). The existing
    `test_judge`/bulkhead litmus already forbids `from dos.drivers` in the kernel; this
    asserts the same holds after the vcs work (a regression guard, vcs-scoped).
    """
    root = Path(dos.__file__).parent
    offenders: dict[str, list[str]] = {}
    for py in root.rglob("*.py"):
        if py.relative_to(root).parts[0] in _EXEMPT_DIRS:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        bad: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    node.module.startswith("dos.drivers"):
                bad.append(node.module)
            elif isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.startswith("dos.drivers")]
        if bad:
            offenders[py.name] = bad
    assert offenders == {}, (
        f"kernel modules import a driver (incl. a vcs backend impl): {offenders}. "
        "An alternative backend belongs in drivers/, resolved by name via `dos.vcs`."
    )
