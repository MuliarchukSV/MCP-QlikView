"""Tests for the tag-prefixed string-list decoder shared by blocks 1 and 2.

Block 1 (field-name dictionary) and block 2 (table list) use the same wire
format observed in the probe::

    00 00 00 00       4 zero bytes (padding)
    NN NN NN NN       LE u32 string count
    04 LL <utf8>      repeated count times: tag 0x04, length byte, raw bytes

The decoder is shared because the format is identical; only the semantic role
differs. Tests live in this single file to keep the wire-format invariants in
one place.
"""

from __future__ import annotations

import struct

import pytest

from mcp_qlikview.parser.blocks.dictionary import extract_field_names
from mcp_qlikview.parser.blocks.strings import (
    InvalidStringListError,
    decode_tagged_string_list,
)
from mcp_qlikview.parser.blocks.tables import extract_table_names


def _encode_string_list(strings: list[bytes]) -> bytes:
    """Build a probe-shaped buffer: 4-zero pad + LE u32 count + tagged strings."""
    parts = [b"\x00\x00\x00\x00", struct.pack("<I", len(strings))]
    for s in strings:
        assert len(s) <= 0xFF, "test fixture restricted to length-byte format"
        parts.append(b"\x04" + bytes([len(s)]) + s)
    return b"".join(parts)


def _encode_long_string_entry(s: bytes) -> bytes:
    """Tag 0x04 + 0xFF escape + LE u32 length + bytes (probe 2026-06-03)."""
    return b"\x04\xff" + struct.pack("<I", len(s)) + s


class TestLongStringEscape:
    """A length byte of 0xFF escapes to a 4-byte LE u32 length — confirmed on
    LTV group 143-159 where a 256-byte route string broke the 1-byte decoder."""

    def test_decodes_string_longer_than_255_bytes(self) -> None:
        long_s = ("1310/014 м. Косів - " * 20).encode("utf-8")
        assert len(long_s) > 255
        buf = b"\x00\x00\x00\x00" + struct.pack("<I", 1) + _encode_long_string_entry(long_s)
        out = decode_tagged_string_list(buf)
        assert out == [long_s.decode("utf-8")]

    def test_mixed_short_and_long(self) -> None:
        short = b"abc"
        long_s = b"x" * 300
        buf = (
            b"\x00\x00\x00\x00"
            + struct.pack("<I", 3)
            + b"\x04" + bytes([len(short)]) + short
            + _encode_long_string_entry(long_s)
            + b"\x04" + bytes([2]) + b"yz"
        )
        assert decode_tagged_string_list(buf) == ["abc", "x" * 300, "yz"]

    def test_truncated_u32_length_raises(self) -> None:
        buf = b"\x00\x00\x00\x00" + struct.pack("<I", 1) + b"\x04\xff\x00\x01"  # u32 cut short
        with pytest.raises(InvalidStringListError):
            decode_tagged_string_list(buf)


class TestStringList:
    def test_decodes_empty_list(self) -> None:
        buf = _encode_string_list([])
        assert decode_tagged_string_list(buf) == []

    def test_decodes_ascii_strings(self) -> None:
        buf = _encode_string_list([b"foo", b"bar", b"baz"])
        assert decode_tagged_string_list(buf) == ["foo", "bar", "baz"]

    def test_decodes_utf8_cyrillic(self) -> None:
        # Probe found Cyrillic field names like "Год-Месяц_Sale4LTV". Decoder
        # must preserve them as Python str via UTF-8 decode.
        cyrillic = "Год-Месяц".encode()
        buf = _encode_string_list([cyrillic])
        assert decode_tagged_string_list(buf) == ["Год-Месяц"]

    def test_rejects_unknown_tag(self) -> None:
        # Replace the 0x04 string tag with something else — should be rejected
        # rather than silently misinterpreted.
        buf = _encode_string_list([b"foo"])
        bad = bytearray(buf)
        # First tag byte sits at offset 8 (4-zero pad + 4-byte count).
        bad[8] = 0x09
        with pytest.raises(InvalidStringListError):
            decode_tagged_string_list(bytes(bad))

    def test_rejects_count_exceeding_buffer(self) -> None:
        # Claim 999 strings but provide bytes for only one.
        buf = b"\x00\x00\x00\x00" + struct.pack("<I", 999) + b"\x04\x03foo"
        with pytest.raises(InvalidStringListError):
            decode_tagged_string_list(buf)

    def test_rejects_truncated_string(self) -> None:
        # length byte says 10, but only 2 bytes follow.
        buf = b"\x00\x00\x00\x00" + struct.pack("<I", 1) + b"\x04\x0aab"
        with pytest.raises(InvalidStringListError):
            decode_tagged_string_list(buf)


class TestExtractFieldNames:
    """``extract_field_names`` is the public façade for block 1."""

    def test_returns_field_names_in_order(self) -> None:
        block = _encode_string_list([b"idCustomer", b"DateSale", b"Region"])
        assert extract_field_names(block) == ["idCustomer", "DateSale", "Region"]


class TestExtractTableNames:
    """``extract_table_names`` is the public façade for block 2."""

    def test_returns_table_names_in_order(self) -> None:
        # Probe block 2 in LTV_analisys: "DataLTV", "filter4LTV", ...
        block = _encode_string_list([b"DataLTV", b"filter4LTV", b"Tab4Filter4LTV"])
        assert extract_table_names(block) == [
            "DataLTV",
            "filter4LTV",
            "Tab4Filter4LTV",
        ]
