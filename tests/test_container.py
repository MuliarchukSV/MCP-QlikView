"""Unit tests for parser.container — file-level QVW envelope.

The probe report (`docs/probes/2026-05-07-qvw-framing.md`) established three
invariants the container layer must enforce:

1. The first 12 bytes of any valid QVW are a fixed magic+version signature.
2. Bytes 12..23 vary per file (header tail) and are passed through but not
   interpreted in v1.
3. The last 4 bytes of the file are the ASCII trailer ``b"EXEX"``.

We hand-craft minimal byte fixtures (no real QVW required) so unit tests run
in CI without the production reference files. Real-QVW assertions live in the
golden suite (see ``tests/test_golden.py``).
"""

from __future__ import annotations

import zlib

import pytest

from mcp_qlikview.parser.container import (
    QVW_MAGIC_PREFIX,
    InvalidQvwError,
    parse_bytes,
    validate_envelope,
)


def _make_minimal_qvw(magic: bytes = QVW_MAGIC_PREFIX, body: bytes = b"", trailer: bytes = b"EXEX") -> bytes:
    """Build the smallest byte sequence that satisfies the envelope rules.

    23-byte header = 12 magic + 11 per-file tail. Per-file tail can be any
    bytes for unit-test purposes; the container layer treats them as opaque.
    """
    per_file_tail = b"\x00" * 11
    return magic + per_file_tail + body + trailer


class TestHeaderMagic:
    def test_accepts_known_magic_prefix(self) -> None:
        raw = _make_minimal_qvw()
        # No exception = pass. validate_envelope returns the per-file header
        # tail and the body slice between header and trailer.
        validate_envelope(raw)

    def test_rejects_wrong_magic(self) -> None:
        raw = _make_minimal_qvw(magic=b"\x00" * 12)
        with pytest.raises(InvalidQvwError) as exc:
            validate_envelope(raw)
        assert "magic" in str(exc.value).lower()

    def test_rejects_truncated_header(self) -> None:
        raw = b"\x70\x17\x01\x00"  # only 4 bytes — header incomplete
        with pytest.raises(InvalidQvwError) as exc:
            validate_envelope(raw)
        assert "header" in str(exc.value).lower() or "too short" in str(exc.value).lower()

    def test_magic_prefix_constant_matches_probe_finding(self) -> None:
        # The probe established this exact 12-byte signature on 3 reference
        # QVWs. If a future probe updates the constant, tests must follow.
        assert bytes.fromhex("70170100c106000002000000") == QVW_MAGIC_PREFIX


class TestExexTrailer:
    def test_accepts_correct_trailer(self) -> None:
        raw = _make_minimal_qvw(trailer=b"EXEX")
        validate_envelope(raw)

    def test_rejects_missing_trailer(self) -> None:
        # Header is fine, but file ends right after — no EXEX.
        raw = QVW_MAGIC_PREFIX + b"\x00" * 11
        with pytest.raises(InvalidQvwError) as exc:
            validate_envelope(raw)
        assert "trailer" in str(exc.value).lower() or "exex" in str(exc.value).lower()

    def test_rejects_corrupt_trailer(self) -> None:
        raw = _make_minimal_qvw(trailer=b"XXXX")
        with pytest.raises(InvalidQvwError) as exc:
            validate_envelope(raw)
        assert "trailer" in str(exc.value).lower() or "exex" in str(exc.value).lower()

    def test_rejects_truncated_trailer(self) -> None:
        # 23-byte header + 2 bytes — too short to even hold a trailer.
        raw = QVW_MAGIC_PREFIX + b"\x00" * 11 + b"EX"
        with pytest.raises(InvalidQvwError):
            validate_envelope(raw)


def _wrap(*streams_and_gaps: bytes, header_tail: bytes = b"\x00" * 11) -> bytes:
    """Build a complete fake QVW from already-encoded body bytes."""
    return QVW_MAGIC_PREFIX + header_tail + b"".join(streams_and_gaps) + b"EXEX"


class TestZlibScan:
    def test_finds_single_stream(self) -> None:
        payload = b"hello world"
        raw = _wrap(zlib.compress(payload))
        container = parse_bytes(raw)
        assert len(container.blocks) == 1
        assert container.blocks[0].index == 0
        assert container.blocks[0].decompressed == payload

    def test_finds_multiple_streams_in_order(self) -> None:
        payloads = [b"first", b"second", b"third"]
        raw = _wrap(*[zlib.compress(p) for p in payloads])
        container = parse_bytes(raw)
        assert [b.decompressed for b in container.blocks] == payloads
        assert [b.index for b in container.blocks] == [0, 1, 2]

    def test_records_compressed_offset(self) -> None:
        s1 = zlib.compress(b"a")
        s2 = zlib.compress(b"b")
        raw = _wrap(s1, s2)
        container = parse_bytes(raw)
        # First stream starts right after the 23-byte header.
        assert container.blocks[0].offset == 23
        assert container.blocks[1].offset == 23 + len(s1)

    def test_preserves_header_tail(self) -> None:
        # Real LTV_analisys.qvw per-file tail per probe report §1.1:
        # offset 0x0c-0x0f: ba 1d 00 56
        # offset 0x10-0x13: 5b 0f 00 a5
        # offset 0x14-0x16: 2f 05 00 (3 bytes)
        # → 11 bytes total: ba 1d 00 56 5b 0f 00 a5 2f 05 00
        custom_tail = bytes.fromhex("ba1d00565b0f00a52f0500")
        assert len(custom_tail) == 11
        raw = _wrap(zlib.compress(b"x"), header_tail=custom_tail)
        container = parse_bytes(raw)
        assert container.header_tail == custom_tail

    def test_kind_hint_data_chunk_for_256kb_block_with_4byte_gap(self) -> None:
        data_chunk = zlib.compress(b"\x00" * (256 * 1024))
        gap = b"\x00\x00\x01\x00"  # 4-byte length-prefix as observed in probe
        tail = zlib.compress(b"meta")
        raw = _wrap(data_chunk, gap, tail)
        container = parse_bytes(raw)
        assert container.blocks[0].kind_hint == "data_chunk"
        assert container.blocks[0].gap_after == 4

    def test_kind_hint_metadata_for_long_gap(self) -> None:
        s1 = zlib.compress(b"metadata block")
        # ~169 bytes of inter-block padding observed in probe (Class A metadata).
        gap = b"\x01\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00" * 11
        s2 = zlib.compress(b"next")
        raw = _wrap(s1, gap, s2)
        container = parse_bytes(raw)
        assert container.blocks[0].kind_hint == "metadata"
        assert container.blocks[0].gap_after >= 100

    def test_kind_hint_unknown_for_atypical_gap(self) -> None:
        s1 = zlib.compress(b"x")
        gap = b"\x42" * 17
        s2 = zlib.compress(b"y")
        raw = _wrap(s1, gap, s2)
        container = parse_bytes(raw)
        # 17-byte gap matches neither the 4-byte data_chunk pattern nor the
        # ~169-byte metadata pattern — flagged as unknown for human review.
        assert container.blocks[0].kind_hint == "unknown"

    def test_last_block_gap_excludes_trailer(self) -> None:
        s = zlib.compress(b"only")
        raw = _wrap(s)
        container = parse_bytes(raw)
        # Last block is followed only by the EXEX trailer — no real gap.
        assert container.blocks[-1].gap_after == 0

    def test_validates_envelope_first(self) -> None:
        # parse_bytes must reject bad envelopes before scanning.
        with pytest.raises(InvalidQvwError):
            parse_bytes(b"\x00" * 50)

    def test_handles_zero_streams(self) -> None:
        # A QVW with header + trailer but no blocks (edge case).
        raw = QVW_MAGIC_PREFIX + b"\x00" * 11 + b"EXEX"
        container = parse_bytes(raw)
        assert container.blocks == []


class TestZlibBombProtection:
    """Hardening against adversarial QVW inputs (review #2)."""

    def test_oversized_block_raises_zlib_bomb(self) -> None:
        # Compress a 65 MB payload of zeros — exceeds the 64 MB per-block cap.
        from mcp_qlikview.parser.container import (
            MAX_DECOMPRESSED_BLOCK_SIZE,
            ZlibBombError,
        )

        oversized = b"\x00" * (MAX_DECOMPRESSED_BLOCK_SIZE + 1)
        compressed = zlib.compress(oversized)
        raw = _wrap(compressed)
        with pytest.raises(ZlibBombError):
            parse_bytes(raw)

    def test_too_many_blocks_raises_zlib_bomb(self) -> None:
        # Synthesize MAX_BLOCKS_PER_FILE + 1 tiny streams.
        from mcp_qlikview.parser.container import MAX_BLOCKS_PER_FILE, ZlibBombError

        streams = [zlib.compress(b"x")] * (MAX_BLOCKS_PER_FILE + 1)
        raw = _wrap(*streams)
        with pytest.raises(ZlibBombError):
            parse_bytes(raw)
