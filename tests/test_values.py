"""Tests for parser.blocks.values — value-set extraction (Phase 2a)."""

from __future__ import annotations

import struct
import zlib

from mcp_qlikview.parser.blocks.values import ValueSet, extract_value_sets
from mcp_qlikview.parser.container import QVW_MAGIC_PREFIX, parse_bytes


def _symtab(entries: bytes, count: int) -> bytes:
    return b"\x00\x00\x00\x00" + struct.pack("<I", count) + entries


def _text_entry(s: str) -> bytes:
    b = s.encode("utf-8")
    return b"\x04" + bytes([len(b)]) + b


def _string_list(names: list[str]) -> bytes:
    parts = [b"\x00\x00\x00\x00", struct.pack("<I", len(names))]
    for n in names:
        parts.append(_text_entry(n))
    return b"".join(parts)


def _envelope(*payloads: bytes) -> bytes:
    body = b"".join(zlib.compress(p) for p in payloads)
    return QVW_MAGIC_PREFIX + b"\x00" * 11 + body + b"EXEX"


def _container(*payloads: bytes):
    return parse_bytes(_envelope(*payloads))


def test_skips_script_dict_tables_blocks() -> None:
    # blocks 0,1,2 = script-ish / field dict / table list; not value-sets.
    script = b"///$tab Main\nLOAD 1;"
    fields = _string_list(["A", "B"])
    tables = _string_list(["T"])
    field_a = _symtab(_text_entry("x") + _text_entry("y"), 2)
    c = _container(script, fields, tables, field_a)
    vs = extract_value_sets(c)
    assert len(vs) == 1
    assert vs[0].first_block == 3
    assert vs[0].cardinality == 2
    assert vs[0].value_type == "text"
    assert vs[0].samples == ["x", "y"]


def test_value_type_and_samples_limit() -> None:
    script, fields, tables = b"///$tab Main\n", _string_list(["A"]), _string_list(["T"])
    ints = b"".join(b"\x01" + struct.pack("<i", i) for i in range(10))
    c = _container(script, fields, tables, _symtab(ints, 10))
    vs = extract_value_sets(c, max_samples=3)
    assert len(vs) == 1
    assert vs[0].cardinality == 10
    assert vs[0].value_type == "int"
    assert vs[0].samples == ["0", "1", "2"]  # limited to 3


def test_skips_non_symbol_blocks() -> None:
    # A high-entropy block that is not a symbol table must be skipped, not crash.
    script, fields, tables = b"///$tab Main\n", _string_list(["A"]), _string_list(["T"])
    good = _symtab(_text_entry("v"), 1)
    junk = bytes(range(200, 256)) * 40  # no leading 4 zero bytes, bad flag
    c = _container(script, fields, tables, good, junk)
    vs = extract_value_sets(c)
    assert [v.first_block for v in vs] == [3]  # junk (block 4) skipped


def test_returns_value_set_dataclass() -> None:
    script, fields, tables = b"///$tab Main\n", _string_list(["A"]), _string_list(["T"])
    c = _container(script, fields, tables, _symtab(_text_entry("only"), 1))
    vs = extract_value_sets(c)
    assert isinstance(vs[0], ValueSet)
    assert vs[0].samples == ["only"]
