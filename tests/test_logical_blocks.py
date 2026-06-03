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


def test_data_chunk_run_absorbs_trailing_remainder_block() -> None:
    # Confirmed on the LTV reference (probe 2026-06-03): a field's symbol
    # table = N full 256KB data_chunks + a trailing partial block that
    # completes it. The trailing block MUST be part of the symbol_group.
    chunk = zlib.compress(b"\x00" * (256 * 1024))
    gap = b"\xff\xff\x00\x00"
    remainder = zlib.compress(b"remainder-tail")
    raw = _envelope(chunk, gap, chunk, gap, remainder)
    logical = iter_logical_blocks(parse_bytes(raw))
    # 2 data chunks + the trailing remainder = ONE symbol_group.
    assert [lb.kind for lb in logical] == ["symbol_group"]
    assert logical[0].first_index == 0
    assert logical[0].last_index == 2
    # Payload = two 256KB chunks + remainder; the 4-byte gaps are excluded.
    assert len(logical[0].payload) == 2 * 256 * 1024 + len(b"remainder-tail")
    assert logical[0].payload.endswith(b"remainder-tail")


def test_data_run_at_eof_without_remainder_is_symbol_group() -> None:
    # Defensive: a run ending at EOF (table on an exact 256KB boundary).
    chunk = zlib.compress(b"\x00" * (256 * 1024))
    gap = b"\x00\x00\x00\x00"
    raw = _envelope(chunk, gap, chunk)
    logical = iter_logical_blocks(parse_bytes(raw))
    assert [lb.kind for lb in logical] == ["symbol_group"]
    assert logical[0].first_index == 0 and logical[0].last_index == 1
    assert len(logical[0].payload) == 2 * 256 * 1024


def test_metadata_then_data_then_metadata_keeps_boundaries() -> None:
    # meta1 is independent (precedes any data run); the block after the data
    # run is the run's remainder (absorbed); a later standalone block is single.
    meta1 = zlib.compress(b"first-meta")
    chunk = zlib.compress(b"\x00" * (256 * 1024))
    gap = b"\x00\x01\x02\x03"
    remainder = zlib.compress(b"remainder")
    standalone = zlib.compress(b"small-field-table")
    raw = _envelope(meta1, chunk, gap, chunk, gap, remainder, standalone)
    logical = iter_logical_blocks(parse_bytes(raw))
    assert [lb.kind for lb in logical] == ["single", "symbol_group", "single"]
    assert logical[0].payload == b"first-meta"
    # symbol_group = blocks 1,2 (chunks) + block 3 (remainder).
    assert logical[1].first_index == 1 and logical[1].last_index == 3
    assert logical[1].payload.endswith(b"remainder")
    assert logical[2].payload == b"small-field-table"


def test_no_data_chunks_returns_singles_only() -> None:
    raw = _envelope(zlib.compress(b"a"), zlib.compress(b"b"), zlib.compress(b"c"))
    logical = iter_logical_blocks(parse_bytes(raw))
    assert all(lb.kind == "single" for lb in logical)
    assert [lb.payload for lb in logical] == [b"a", b"b", b"c"]
