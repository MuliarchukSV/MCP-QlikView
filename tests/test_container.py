"""Unit tests for the QVW container parser."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from mcp_qlikview.parser.container import (
    FILE_HEADER_SIZE,
    QvwContainer,
    QvwParseError,
    parse,
)


class TestSyntheticContainer:
    def test_block_count(self, synthetic_container: QvwContainer):
        # fixture has 3 blocks: script + schema + table-index
        assert len(synthetic_container.blocks) == 3

    def test_not_encrypted(self, synthetic_container: QvwContainer):
        assert synthetic_container.is_encrypted is False

    def test_block_indices(self, synthetic_container: QvwContainer):
        for i, blk in enumerate(synthetic_container.blocks):
            assert blk.index == i

    def test_block_data_nonempty(self, synthetic_container: QvwContainer):
        for blk in synthetic_container.blocks:
            assert len(blk.data) > 0

    def test_block0_contains_script_marker(self, synthetic_container: QvwContainer):
        assert b"///" in synthetic_container.blocks[0].data

    def test_block1_contains_field_bytes(self, synthetic_container: QvwContainer):
        # block 1 is the schema block — must contain 0x04 type byte
        assert b"\x04" in synthetic_container.blocks[1].data

    def test_path_preserved(self, synthetic_container: QvwContainer, synthetic_path: Path):
        assert synthetic_container.path == synthetic_path


class TestEdgeCases:
    def test_file_too_small_raises(self, tmp_path: Path):
        tiny = tmp_path / "tiny.qvw"
        tiny.write_bytes(b"\x00" * 10)
        with pytest.raises(QvwParseError):
            parse(tiny)

    def test_file_not_found_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            parse(tmp_path / "nonexistent.qvw")

    def test_encrypted_detected(self, tmp_path: Path):
        # Build a file with valid zlib magic but garbage body so decompress fails.
        # The parser only sets is_encrypted when it CAN find the block (valid magic +
        # plausible sizes) but fails to decompress block 0.
        header = b"QVW_TEST_000000"
        fake_compressed = b"\x78\x9c" + b"\xAB" * 100  # valid magic, invalid body
        block_hdr = struct.pack("<II", 200, len(fake_compressed))
        enc = tmp_path / "enc.qvw"
        enc.write_bytes(header + block_hdr + fake_compressed)
        c = parse(enc)
        assert c.is_encrypted is True
        assert len(c.blocks) == 0

    def test_single_valid_block(self, tmp_path: Path):
        data = b"Hello QVW test block"
        compressed = zlib.compress(data)
        header = b"QVW_TEST_000000"
        block_hdr = struct.pack("<II", len(data), len(compressed))
        f = tmp_path / "single.qvw"
        f.write_bytes(header + block_hdr + compressed)
        c = parse(f)
        assert len(c.blocks) == 1
        assert c.blocks[0].data == data
