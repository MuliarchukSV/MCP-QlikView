"""Tests for :func:`iter_logical_blocks` — symbol-group merging.

The probe report ``docs/probes/2026-05-08-spanning-symbol-tables.md``
established that consecutive ``data_chunk`` blocks form one logical
payload that the block decoders consume as a unit.
"""

from __future__ import annotations

import zlib

from mcp_qlikview.parser.container import (
    QVW_MAGIC_PREFIX,
    iter_logical_blocks,
    parse_bytes,
)


def _envelope(*streams: bytes) -> bytes:
    return QVW_MAGIC_PREFIX + b"\x00" * 11 + b"".join(streams) + b"EXEX"


def test_single_metadata_block_yields_one_logical_block() -> None:
    raw = _envelope(zlib.compress(b"meta"))
    logical = iter_logical_blocks(parse_bytes(raw))
    assert len(logical) == 1
    assert logical[0].kind == "single"
    assert logical[0].payload == b"meta"
    assert logical[0].first_index == logical[0].last_index == 0


def test_data_chunks_with_4byte_gaps_merge_into_symbol_group() -> None:
    chunk = zlib.compress(b"\x00" * (256 * 1024))
    gap = b"\xff\xff\x00\x00"
    last = zlib.compress(b"end-meta")
    raw = _envelope(chunk, gap, chunk, gap, last)
    logical = iter_logical_blocks(parse_bytes(raw))
    # 2 data chunks merged + 1 single metadata.
    assert [lb.kind for lb in logical] == ["symbol_group", "single"]
    assert logical[0].first_index == 0
    assert logical[0].last_index == 1
    # Payload is the two 256KB chunks joined; the 4-byte gap is excluded.
    assert len(logical[0].payload) == 2 * 256 * 1024
    assert logical[1].payload == b"end-meta"


def test_metadata_then_data_then_metadata_keeps_boundaries() -> None:
    meta1 = zlib.compress(b"first-meta")
    chunk = zlib.compress(b"\x00" * (256 * 1024))
    gap = b"\x00\x01\x02\x03"
    meta2 = zlib.compress(b"second-meta")
    raw = _envelope(meta1, chunk, gap, chunk, gap, meta2)
    logical = iter_logical_blocks(parse_bytes(raw))
    assert [lb.kind for lb in logical] == ["single", "symbol_group", "single"]
    assert logical[0].payload == b"first-meta"
    assert logical[1].first_index == 1 and logical[1].last_index == 2
    assert logical[2].payload == b"second-meta"


def test_no_data_chunks_returns_singles_only() -> None:
    raw = _envelope(zlib.compress(b"a"), zlib.compress(b"b"), zlib.compress(b"c"))
    logical = iter_logical_blocks(parse_bytes(raw))
    assert all(lb.kind == "single" for lb in logical)
    assert [lb.payload for lb in logical] == [b"a", b"b", b"c"]
