"""test_deprecation.py — pin the typed deprecation category + the emitter.

The docs/308 P1 pins (issue #67's done-condition: "a test pinning that
deprecation warnings carry the documented category"). The contract under test
is the one ``docs/STABILITY.md`` documents:

- the category is ``dos.deprecation.DosDeprecationWarning``, a
  ``DeprecationWarning`` subclass, re-exported from ``dos`` (same object);
- ``warn_deprecated`` emits exactly one warning of exactly that category,
  whose message names the surface, the since-version, the earliest removal
  version, and (when given) the replacement;
- the default ``stacklevel`` attributes the warning to the deprecated
  surface's CALLER — the consumer's own line, where the fix belongs — not to
  the deprecated body and not to the helper.
"""

from __future__ import annotations

import inspect
import warnings

import dos
from dos.deprecation import DosDeprecationWarning, warn_deprecated


def test_category_is_a_deprecation_warning_subclass() -> None:
    assert issubclass(DosDeprecationWarning, DeprecationWarning)
    # And not == DeprecationWarning itself: the subclass is what lets a
    # consumer target DOS's deprecations and nobody else's.
    assert DosDeprecationWarning is not DeprecationWarning


def test_category_reexported_from_dos_is_the_same_object() -> None:
    assert dos.DosDeprecationWarning is DosDeprecationWarning
    assert dos.warn_deprecated is warn_deprecated
    assert "DosDeprecationWarning" in dos.__all__
    assert "warn_deprecated" in dos.__all__


def test_warn_deprecated_emits_exactly_the_documented_category() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_deprecated(
            "dos.example.old()",
            since="0.25",
            remove_in="0.27",
            instead="dos.example.new()",
        )
    assert len(caught) == 1
    assert caught[0].category is DosDeprecationWarning


def test_message_names_subject_since_removal_and_replacement() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_deprecated(
            "dos.example.old()",
            since="0.25",
            remove_in="0.27",
            instead="dos.example.new()",
        )
    message = str(caught[0].message)
    assert message == (
        "dos.example.old() is deprecated since dos-kernel 0.25 "
        "and will be removed in 0.27; use dos.example.new() instead"
    )


def test_message_without_replacement_omits_the_instead_clause() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_deprecated("--old-flag", since="0.25", remove_in="0.27")
    message = str(caught[0].message)
    assert message == (
        "--old-flag is deprecated since dos-kernel 0.25 "
        "and will be removed in 0.27"
    )
    assert "instead" not in message


def test_consumer_can_escalate_exactly_dos_deprecations_to_errors() -> None:
    # The filtering story the policy sells: -W error on OUR category only.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        warnings.filterwarnings("error", category=DosDeprecationWarning)
        try:
            warn_deprecated("dos.example.old()", since="0.25", remove_in="0.27")
        except DosDeprecationWarning:
            pass
        else:  # pragma: no cover - the assertion message is the point
            raise AssertionError("filterwarnings(error) did not catch ours")
        # A foreign DeprecationWarning is untouched by that filter.
        warnings.warn("someone else's", DeprecationWarning, stacklevel=2)


def _deprecated_surface() -> None:
    """A stand-in deprecated function: the body emits with the default level."""
    warn_deprecated("_deprecated_surface()", since="0.25", remove_in="0.27")


def test_default_stacklevel_points_at_the_deprecated_surfaces_caller() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        frame = inspect.currentframe()
        assert frame is not None
        expected_line = frame.f_lineno + 1
        _deprecated_surface()
    assert caught[0].filename == __file__
    assert caught[0].lineno == expected_line
