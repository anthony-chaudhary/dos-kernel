"""deprecation.py — the typed deprecation category + the one sanctioned emitter.

The kernel half of the stability promise (``docs/STABILITY.md``, docs/308,
issue #67). The promise says: when a Stable surface is deprecated, it keeps
working and every use WARNS, with a window of at least two minor releases
before removal. This module is the vehicle that makes the warning checkable:

- ``DosDeprecationWarning`` — a ``DeprecationWarning`` subclass, so Python's
  default visibility rules hold (quiet in production, surfaced by pytest and
  ``-W``), while a consumer can target exactly DOS's deprecations and nobody
  else's: ``filterwarnings("error", category=DosDeprecationWarning)``.
- ``warn_deprecated()`` — the only sanctioned way kernel code emits one. It
  composes the message shape the policy documents (subject, since-version,
  earliest removal version, the replacement), so "every DOS deprecation
  carries the documented category and names its removal version" is true by
  construction, not by review.

Layer: kernel leaf — stdlib only, no I/O, no host names. Deliberately NOT in
the T1 runtime set (it adjudicates nothing; it narrates a schedule). Pinned by
``tests/test_deprecation.py`` and ``tests/test_stability_policy.py``.
"""

from __future__ import annotations

import warnings

__all__ = ["DosDeprecationWarning", "warn_deprecated"]


class DosDeprecationWarning(DeprecationWarning):
    """A deprecated dos-kernel surface was used.

    The category ``docs/STABILITY.md`` promises: every deprecation DOS emits
    carries this class, so filtering on it catches all of ours and none of
    anyone else's. The surface keeps working for the documented window (at
    least two minor releases) before removal.
    """


def warn_deprecated(
    subject: str,
    *,
    since: str,
    remove_in: str,
    instead: str | None = None,
    stacklevel: int = 2,
) -> None:
    """Emit the documented deprecation warning for ``subject``.

    ``subject`` names the deprecated surface (``"dos.foo.bar()"``, a CLI flag,
    a ``dos.toml`` key); ``since`` is the version that deprecated it;
    ``remove_in`` is the earliest version that may remove it (per the policy:
    at least two minor releases after ``since``); ``instead`` names the
    replacement, when one exists.

    ``stacklevel`` counts frames above THIS helper, so the default ``2``
    attributes the warning to the deprecated surface's caller — the consumer's
    own line, where the fix belongs (``1`` would name the deprecated body
    itself). Pass a larger value when the deprecated surface sits deeper in a
    wrapper stack.
    """
    message = (
        f"{subject} is deprecated since dos-kernel {since} "
        f"and will be removed in {remove_in}"
    )
    if instead:
        message += f"; use {instead} instead"
    warnings.warn(message, DosDeprecationWarning, stacklevel=stacklevel + 1)
