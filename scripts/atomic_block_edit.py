"""Atomically replace one literal block in a shared-tree text file.

This is a contributor helper, not kernel code.  It collapses the usual agent
read -> tool-call -> write race into one process and publishes the complete new
file with os.replace, so readers see either the old bytes or the new bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile


class BlockEditRefused(ValueError):
    """The requested edit was ambiguous or its precondition no longer held."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_block(
    path: Path,
    old: bytes,
    new: bytes,
    *,
    expected_sha256: str | None = None,
) -> dict[str, object]:
    """Replace exactly one occurrence of *old* and atomically publish *new*.

    ``expected_sha256`` is an optional stronger read contract: when supplied,
    refuse unless the on-disk file still has that digest. The temporary file is
    created beside the target so ``os.replace`` stays on one filesystem.
    """
    path = Path(path)
    if not old:
        raise BlockEditRefused("OLD_EMPTY: the old block must not be empty")
    if not path.is_file():
        raise BlockEditRefused(f"PATH_NOT_FILE: {path}")
    if path.is_symlink():
        raise BlockEditRefused(f"PATH_SYMLINK: refusing to replace symlink {path}")

    original = path.read_bytes()
    before_sha = _digest(original)
    if expected_sha256 is not None and before_sha.lower() != expected_sha256.lower():
        raise BlockEditRefused(
            f"CONTENT_CHANGED: expected sha256 {expected_sha256}, found {before_sha}"
        )
    occurrences = original.count(old)
    if occurrences != 1:
        raise BlockEditRefused(
            f"BLOCK_COUNT: expected exactly one old block, found {occurrences}"
        )

    updated = original.replace(old, new, 1)
    mode = stat.S_IMODE(path.stat().st_mode)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".atomic-edit",
            dir=path.parent, delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)

        # Re-read immediately before publication. This catches ordinary sibling
        # rewrites during preparation; os.replace then makes our publication a
        # single filesystem operation rather than a torn truncate/write.
        current = path.read_bytes()
        if current != original:
            raise BlockEditRefused(
                f"CONTENT_CHANGED: {path} changed while preparing the edit"
            )
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    return {
        "path": str(path),
        "before_sha256": before_sha,
        "after_sha256": _digest(updated),
        "bytes_before": len(original),
        "bytes_after": len(updated),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="atomically replace exactly one literal block in a file"
    )
    parser.add_argument("path", type=Path, help="target file")
    parser.add_argument("--old-file", required=True, type=Path,
                        help="file containing the exact bytes to replace")
    parser.add_argument("--new-file", required=True, type=Path,
                        help="file containing replacement bytes")
    parser.add_argument("--expected-sha256",
                        help="refuse if PATH no longer has this SHA-256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = replace_block(
            args.path,
            args.old_file.read_bytes(),
            args.new_file.read_bytes(),
            expected_sha256=args.expected_sha256,
        )
    except (BlockEditRefused, OSError) as exc:
        print(f"atomic-block-edit: REFUSED {exc}", file=sys.stderr)
        return 1
    print(
        "atomic-block-edit: REPLACED "
        f"{result['path']} {result['before_sha256']} -> {result['after_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
