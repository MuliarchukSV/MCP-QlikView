"""Dual-value symbol-table decoder (probe-confirmed wire format).

QlikView symbol tables are uniformly framed::

    [ 4 zero bytes ]
    [ LE u32 count ]
    [ N entries:   ]
    [   flag (1 byte) ]
    [   <flag-specific payload> ]

Flag-specific payloads:

| flag | payload                                          |
|------|--------------------------------------------------|
| 0x01 | 4-byte LE signed int                             |
| 0x02 | 8-byte LE IEEE-754 double                        |
| 0x03 | 4-byte LE int + length-prefixed UTF-8 text       |
| 0x04 | length-prefixed UTF-8 text                       |
| 0x05 | length-prefixed ASCII text + 4-byte LE int       |
| 0x06 | length-prefixed ASCII text + 8-byte LE double    |

This module is the inner-decoder reused across blocks 1, 2, and 4..N.
The dictionary/tables decoders in :mod:`.strings` predate this generic
implementation and remain in place because they only ever expect flag 0x04;
this module exists for the per-table symbol tables that the data layer
(Phase 2) will consume.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from mcp_qlikview.parser.blocks.strings import MAX_REASONABLE_STRING_COUNT


class InvalidSymbolBlockError(ValueError):
    """Raised when a buffer cannot be decoded as a symbol table."""


@dataclass(frozen=True)
class SymbolEntry:
    """One row in a QVW symbol table.

    Attributes:
        flag: One of ``0x01..0x06``; identifies the payload shape.
        text: Text face (face-value), present for flags 0x03, 0x04, 0x05, 0x06.
        numeric: Numeric value (int or float), present for flags 0x01, 0x02,
            0x03, 0x05, 0x06. ``int`` for flags 0x01, 0x03, 0x05; ``float``
            for flags 0x02, 0x06.
    """

    flag: int
    text: str | None
    numeric: int | float | None


def decode_symbol_block(buf: bytes) -> list[SymbolEntry]:
    """Decode a QVW symbol-table block to a list of :class:`SymbolEntry`.

    Raises:
        InvalidSymbolBlockError: any wire-format invariant is violated
            (truncation, unknown flag, count overrun).
    """
    if len(buf) < 8:
        raise InvalidSymbolBlockError(
            f"buffer too short for symbol-block header: {len(buf)} bytes"
        )
    (count,) = struct.unpack_from("<I", buf, 4)
    if count > MAX_REASONABLE_STRING_COUNT:
        raise InvalidSymbolBlockError(
            f"declared symbol count {count} exceeds sanity bound "
            f"{MAX_REASONABLE_STRING_COUNT}"
        )

    pos = 8
    out: list[SymbolEntry] = []
    for i in range(count):
        if pos >= len(buf):
            raise InvalidSymbolBlockError(
                f"truncated stream at entry {i + 1}/{count} (offset {pos})"
            )
        flag = buf[pos]
        pos += 1
        try:
            entry, pos = _decode_entry(flag, buf, pos)
        except (struct.error, IndexError) as exc:
            raise InvalidSymbolBlockError(
                f"truncated payload for entry {i + 1}/{count} "
                f"(flag 0x{flag:02x} at offset {pos - 1}): {exc}"
            ) from exc
        out.append(entry)
    return out


def _decode_entry(flag: int, buf: bytes, pos: int) -> tuple[SymbolEntry, int]:
    """Dispatch decoding of one entry by its leading flag byte.

    Returns the decoded :class:`SymbolEntry` and the new cursor position.
    """
    if flag == 0x01:
        (value,) = struct.unpack_from("<i", buf, pos)
        return SymbolEntry(flag=0x01, text=None, numeric=value), pos + 4

    if flag == 0x02:
        (value,) = struct.unpack_from("<d", buf, pos)
        return SymbolEntry(flag=0x02, text=None, numeric=value), pos + 8

    if flag == 0x03:
        (int_value,) = struct.unpack_from("<i", buf, pos)
        text, new_pos = _read_length_prefixed_text(buf, pos + 4)
        return SymbolEntry(flag=0x03, text=text, numeric=int_value), new_pos

    if flag == 0x04:
        text, new_pos = _read_length_prefixed_text(buf, pos)
        return SymbolEntry(flag=0x04, text=text, numeric=None), new_pos

    if flag == 0x05:
        text, after_text = _read_length_prefixed_text(buf, pos)
        (int_value,) = struct.unpack_from("<i", buf, after_text)
        return SymbolEntry(flag=0x05, text=text, numeric=int_value), after_text + 4

    if flag == 0x06:
        text, after_text = _read_length_prefixed_text(buf, pos)
        (double_value,) = struct.unpack_from("<d", buf, after_text)
        return SymbolEntry(flag=0x06, text=text, numeric=double_value), after_text + 8

    raise InvalidSymbolBlockError(
        f"unknown symbol flag 0x{flag:02x} at offset {pos - 1}"
    )


def _read_length_prefixed_text(buf: bytes, pos: int) -> tuple[str, int]:
    """Read length-prefixed UTF-8 text from ``buf`` starting at ``pos``.

    The length is a single byte, except the sentinel ``0xFF`` which escapes to
    a 4-byte little-endian u32 length that follows it — required for strings
    longer than 255 bytes (probe 2026-06-03: LTV group 143-159 carries 256-byte
    route descriptions encoded this way).
    """
    if pos >= len(buf):
        raise InvalidSymbolBlockError(
            f"truncated length byte at offset {pos}"
        )
    length = buf[pos]
    if length == 0xFF:
        if pos + 5 > len(buf):
            raise InvalidSymbolBlockError(
                f"truncated 0xFF-escaped u32 length at offset {pos}"
            )
        (length,) = struct.unpack_from("<I", buf, pos + 1)
        start = pos + 5
    else:
        start = pos + 1
    end = start + length
    if end > len(buf):
        raise InvalidSymbolBlockError(
            f"truncated text: length {length} at offset {pos} but only "
            f"{len(buf) - start} bytes available"
        )
    try:
        text = buf[start:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidSymbolBlockError(
            f"non-UTF-8 bytes at offset {pos}: {exc}"
        ) from exc
    return text, end
