"""Tests for parser.blocks.layout — table→field directory decode."""

from __future__ import annotations

import zlib

from mcp_qlikview.parser.blocks.layout import extract_table_field_map
from mcp_qlikview.parser.container import QVW_MAGIC_PREFIX, parse_bytes


def _envelope(*payloads: bytes) -> bytes:
    body = b"".join(zlib.compress(p) for p in payloads)
    return QVW_MAGIC_PREFIX + b"\x00" * 11 + body + b"EXEX"


def _container(*payloads: bytes):
    return parse_bytes(_envelope(*payloads))


def test_decodes_table_field_ranges() -> None:
    # directory: 3 tables starting at field 0, 2, 5 (pairs index,offset).
    directory = bytes([0, 0, 1, 2, 2, 5])
    c = _container(b"x", directory, b"y")
    fields = [f"f{i}" for i in range(8)]
    tables = ["T0", "T1", "T2"]
    m = extract_table_field_map(c, fields, tables)
    assert m == {
        "T0": ["f0", "f1"],
        "T1": ["f2", "f3", "f4"],
        "T2": ["f5", "f6", "f7"],
    }


def test_returns_none_when_no_directory_block() -> None:
    c = _container(b"only-one-block-here")
    assert extract_table_field_map(c, ["a", "b"], ["T0"]) is None


def test_rejects_non_monotonic_offsets() -> None:
    # right size (2 tables) but offsets not increasing → reject (coincidence).
    bad = bytes([0, 0, 1, 0])
    c = _container(bad)
    assert extract_table_field_map(c, ["a", "b", "c"], ["T0", "T1"]) is None


def test_rejects_offset_past_field_count() -> None:
    bad = bytes([0, 0, 1, 9])  # offset 9 >= 3 fields
    c = _container(bad)
    assert extract_table_field_map(c, ["a", "b", "c"], ["T0", "T1"]) is None


def test_none_when_too_many_fields() -> None:
    c = _container(bytes([0, 0]))
    assert extract_table_field_map(c, [f"f{i}" for i in range(300)], ["T0"]) is None
