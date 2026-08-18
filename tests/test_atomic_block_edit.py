from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.atomic_block_edit import BlockEditRefused, main, replace_block


def test_replace_block_publishes_exactly_one_literal(tmp_path: Path):
    target = tmp_path / "shared.txt"
    target.write_bytes(b"before\nBEGIN\nold\nEND\nafter\n")

    result = replace_block(target, b"BEGIN\nold\nEND", b"BEGIN\nnew\nEND")

    assert target.read_bytes() == b"before\nBEGIN\nnew\nEND\nafter\n"
    assert result["before_sha256"] != result["after_sha256"]


def test_replace_block_refuses_ambiguous_match_without_writing(tmp_path: Path):
    target = tmp_path / "shared.txt"
    original = b"same\nsame\n"
    target.write_bytes(original)

    with pytest.raises(BlockEditRefused, match="found 2"):
        replace_block(target, b"same", b"new")

    assert target.read_bytes() == original


def test_expected_digest_refuses_stale_read_contract(tmp_path: Path):
    target = tmp_path / "shared.txt"
    target.write_bytes(b"peer rewrite")

    with pytest.raises(BlockEditRefused, match="CONTENT_CHANGED"):
        replace_block(target, b"peer", b"our", expected_sha256="0" * 64)

    assert target.read_bytes() == b"peer rewrite"


def test_replace_failure_preserves_original_and_cleans_temp(tmp_path: Path, monkeypatch):
    target = tmp_path / "shared.txt"
    target.write_bytes(b"old")

    def fail_replace(_source, _target):
        raise OSError("injected publication failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        replace_block(target, b"old", b"new")

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.atomic-edit")) == []


def test_cli_reads_blocks_from_files(tmp_path: Path, capsys):
    target = tmp_path / "shared.txt"
    old = tmp_path / "old.block"
    new = tmp_path / "new.block"
    target.write_bytes(b"a OLD z")
    old.write_bytes(b"OLD")
    new.write_bytes(b"NEW")

    assert main([str(target), "--old-file", str(old), "--new-file", str(new)]) == 0
    assert target.read_bytes() == b"a NEW z"
    assert "REPLACED" in capsys.readouterr().out
