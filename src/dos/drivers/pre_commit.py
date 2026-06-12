"""dos × pre-commit — the range adapter for the pre-push hook (docs/304).

The pre-commit framework (pre-commit.com) runs a pre-push hook with the pushed
range in two env vars — ``PRE_COMMIT_FROM_REF`` (the remote tip, or the
all-zeros SHA for a new branch) and ``PRE_COMMIT_TO_REF`` (the local tip) —
and its ``entry:`` line is exec'd WITHOUT a shell, so ``$PRE_COMMIT_FROM_REF``
never expands. This driver is that one missing line of glue: read the env
vars, mint the ``FROM..TO`` range, delegate to the unchanged ``dos
commit-audit`` CLI, and pass its exit code through untouched — the verdict IS
the exit code, so pre-commit's pass/fail is the kernel's verdict with no
further logic.

Driver, not kernel: it names a vendor's env contract (pre-commit's), so it
lives under ``dos/drivers`` per the hook-dialect litmus. It contains zero
adjudication — every verdict byte comes from ``dos.cli`` (a driver may import
the CLI: the established consumer→consumer edge, cf. ``drivers/watchdog.py``).

Ref resolution, most specific first:

- both env vars present and FROM is a real ref → audit the range ``FROM..TO``
  (exactly the commits this push would publish);
- FROM absent or the all-zeros SHA (a new branch — git's pre-push contract
  has no old tip to diff against) → audit ``TO`` only, the conservative
  fallback: the tip is judged, the back-history is not re-litigated;
- no env at all (the post-commit hook, or a hand run) → audit ``HEAD``.

Anything in ``argv`` is passed through to ``dos commit-audit`` verbatim
(``--warn-only``, ``--json``, ``--docs-ok``, ``--sweep``); pass flags only —
the positional ref is this module's job.
"""

from __future__ import annotations

import os
import sys

__all__ = ["main", "resolve_ref"]


def _absent(ref: str | None) -> bool:
    """No usable ref: unset, empty, or git's all-zeros 'no old tip' SHA
    (40 hex zeros for sha1 repos, 64 for sha256 — any all-zero string)."""
    return ref is None or ref == "" or set(ref) == {"0"}


def resolve_ref(env: dict[str, str] | None = None) -> str:
    """Map pre-commit's env contract onto one commit-audit ref argument."""
    e = os.environ if env is None else env
    from_ref = e.get("PRE_COMMIT_FROM_REF")
    to_ref = e.get("PRE_COMMIT_TO_REF")
    if not _absent(to_ref):
        if not _absent(from_ref):
            return f"{from_ref}..{to_ref}"
        return str(to_ref)
    return "HEAD"


def main(argv: list[str] | None = None) -> int:
    """Delegate ``dos commit-audit <resolved-ref> *argv`` and return its exit."""
    from dos import cli  # consumer→consumer import (a driver may import the CLI)

    extra = list(sys.argv[1:] if argv is None else argv)
    return cli.main(["commit-audit", resolve_ref(), *extra])


if __name__ == "__main__":  # pragma: no cover — the `python -m` entry
    raise SystemExit(main())
