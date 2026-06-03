"""Tests for parser.blocks.symbols — dual-value symbol-table decoder.

The probe report (§2.4) confirmed the wire format on real LTV blocks. Each
symbol block contains::

    [ 4 zero bytes     ]
    [ LE u32 count     ]
    [ N entries:       ]
    [   flag (1 byte)  ]
    [   <flag-specific payload> ]

Flag → payload mapping (QVW container, observed):

| flag | payload                                        | semantics            |
|------|------------------------------------------------|----------------------|
| 0x01 | 4-byte LE int                                  | int only             |
| 0x02 | 8-byte LE IEEE-754 double                      | double only          |
| 0x03 | 4-byte LE int  + length-prefixed UTF-8 text    | int + text           |
| 0x04 | length-prefixed UTF-8 text                     | text only            |
| 0x05 | length-prefixed ASCII text + 4-byte LE int     | text + int (numeric) |
| 0x06 | length-prefixed ASCII text + 8-byte LE double  | text + double        |

Length-prefix is a single byte (max 255); QVW symbol values seen in the
3 reference files all fit comfortably under that bound.
"""

from __future__ import annotations

import struct

import pytest

from mcp_qlikview.parser.blocks.symbols import (
    InvalidSymbolBlockError,
    SymbolEntry,
    decode_symbol_block,
)


def _enc_count_header(count: int) -> bytes:
    return b"\x00\x00\x00\x00" + struct.pack("<I", count)


class TestFlag04TextOnly:
    """Flag 0x04 — pure text. Same wire format as the dictionary/tables blocks."""

    def test_decodes_single_text_entry(self) -> None:
        # Probe block 1 first entry: 04 0e idCustomer3LTV
        block = _enc_count_header(1) + b"\x04\x0e" + b"idCustomer3LTV"
        entries = decode_symbol_block(block)
        assert entries == [SymbolEntry(flag=0x04, text="idCustomer3LTV", numeric=None)]


class TestLongStringEscape:
    """Length byte 0xFF escapes to a 4-byte LE u32 length (probe 2026-06-03,
    LTV group 143-159: a 256-byte route string broke the 1-byte decoder)."""

    def test_flag04_long_text(self) -> None:
        long_s = ("Київ - Харків : " * 30).encode("utf-8")
        assert len(long_s) > 255
        block = _enc_count_header(1) + b"\x04\xff" + struct.pack("<I", len(long_s)) + long_s
        entries = decode_symbol_block(block)
        assert entries == [SymbolEntry(flag=0x04, text=long_s.decode("utf-8"), numeric=None)]

    def test_flag05_long_text_then_int(self) -> None:
        long_s = b"r" * 300
        block = (
            _enc_count_header(1)
            + b"\x05\xff" + struct.pack("<I", len(long_s)) + long_s + struct.pack("<i", 7)
        )
        entries = decode_symbol_block(block)
        assert entries == [SymbolEntry(flag=0x05, text="r" * 300, numeric=7)]

    def test_truncated_u32_escape_raises(self) -> None:
        block = _enc_count_header(1) + b"\x04\xff\x00\x01"  # u32 length cut short
        with pytest.raises(InvalidSymbolBlockError):
            decode_symbol_block(block)


class TestFlag01IntOnly:
    def test_decodes_int_only(self) -> None:
        # 4-byte LE int = 42
        block = _enc_count_header(1) + b"\x01" + struct.pack("<i", 42)
        entries = decode_symbol_block(block)
        assert entries == [SymbolEntry(flag=0x01, text=None, numeric=42)]

    def test_decodes_negative_int(self) -> None:
        block = _enc_count_header(1) + b"\x01" + struct.pack("<i", -7)
        entries = decode_symbol_block(block)
        assert entries == [SymbolEntry(flag=0x01, text=None, numeric=-7)]


class TestFlag02DoubleOnly:
    def test_decodes_double_only(self) -> None:
        # 8-byte LE IEEE-754 double = 3.14
        block = _enc_count_header(1) + b"\x02" + struct.pack("<d", 3.14)
        entries = decode_symbol_block(block)
        assert len(entries) == 1
        assert entries[0].flag == 0x02
        assert entries[0].text is None
        assert isinstance(entries[0].numeric, float)
        assert abs(entries[0].numeric - 3.14) < 1e-9


class TestFlag05TextAndInt:
    """Probe-observed in LTV block 4: 05 06 "698590" <int>"""

    def test_decodes_real_block4_entry(self) -> None:
        # Probe-confirmed bytes from LTV block 4 entry 1.
        block = _enc_count_header(1) + bytes.fromhex(
            "0506" + "363938353930" + "dea80a00"
        )
        entries = decode_symbol_block(block)
        assert entries == [SymbolEntry(flag=0x05, text="698590", numeric=698590)]

    def test_decodes_three_entries(self) -> None:
        # Concatenate 3 probe block-4 entries.
        block = _enc_count_header(3) + bytes.fromhex(
            "0506363938353930dea80a00"
            "0506353335353530fe2b0800"
            "0506343738393933114f0700"
        )
        entries = decode_symbol_block(block)
        assert len(entries) == 3
        assert entries[0].text == "698590"
        assert entries[0].numeric == 698590
        assert entries[1].text == "535550"
        assert entries[1].numeric == 535550
        assert entries[2].text == "478993"
        assert entries[2].numeric == 478993


class TestFlag06TextAndDouble:
    """Probe-observed in LTV block 8 head: 06 08 "+0000000" <double 0.0>."""

    def test_decodes_real_block8_first_entry(self) -> None:
        block = _enc_count_header(1) + bytes.fromhex(
            "0608" + "2b30303030303030" + "0000000000000000"
        )
        entries = decode_symbol_block(block)
        assert len(entries) == 1
        assert entries[0].flag == 0x06
        assert entries[0].text == "+0000000"
        assert entries[0].numeric == 0.0

    def test_decodes_block8_second_entry(self) -> None:
        # Entry 2 from probe head: 06 05 "+0004" <double 4.0>
        block = _enc_count_header(1) + bytes.fromhex(
            "0605" + "2b30303034" + "0000000000001040"
        )
        entries = decode_symbol_block(block)
        assert entries[0].text == "+0004"
        assert entries[0].numeric == 4.0


class TestFlag03IntAndText:
    """0x03 is rare; fall-back to length-prefixed text after the 4-byte int."""

    def test_decodes_int_plus_text(self) -> None:
        # 4-byte int = 100, then length-prefixed text "label"
        block = _enc_count_header(1) + b"\x03" + struct.pack("<i", 100) + b"\x05label"
        entries = decode_symbol_block(block)
        assert entries == [SymbolEntry(flag=0x03, text="label", numeric=100)]


class TestErrorPaths:
    def test_rejects_too_short_buffer(self) -> None:
        with pytest.raises(InvalidSymbolBlockError):
            decode_symbol_block(b"\x00\x00\x00\x00")

    def test_rejects_unknown_flag(self) -> None:
        block = _enc_count_header(1) + b"\xff\x00"
        with pytest.raises(InvalidSymbolBlockError) as exc:
            decode_symbol_block(block)
        assert "flag" in str(exc.value).lower()

    def test_rejects_truncated_text(self) -> None:
        # length byte says 10, only 2 follow.
        block = _enc_count_header(1) + b"\x04\x0aab"
        with pytest.raises(InvalidSymbolBlockError):
            decode_symbol_block(block)

    def test_rejects_count_above_sanity_bound(self) -> None:
        # Re-uses the strings.py bound (1M).
        block = _enc_count_header(2_000_000_000) + b"\x04\x03foo"
        with pytest.raises(InvalidSymbolBlockError):
            decode_symbol_block(block)

    def test_rejects_truncated_int_payload(self) -> None:
        # flag=01 needs 4 bytes, only 2 present.
        block = _enc_count_header(1) + b"\x01\x05\x00"
        with pytest.raises(InvalidSymbolBlockError):
            decode_symbol_block(block)


class TestMixedSymbolBlock:
    """Real symbol blocks may mix flags within a single block."""

    def test_decodes_mixed_flags_in_order(self) -> None:
        # int + text-only + text+int
        block = (
            _enc_count_header(3)
            + b"\x01"
            + struct.pack("<i", 42)
            + b"\x04\x03foo"
            + b"\x05\x03bar"
            + struct.pack("<i", 7)
        )
        entries = decode_symbol_block(block)
        assert [e.flag for e in entries] == [0x01, 0x04, 0x05]
        assert entries[0].numeric == 42
        assert entries[1].text == "foo"
        assert entries[2].text == "bar" and entries[2].numeric == 7
