"""Tag-prefixed string-list decoder.

Used by block 1 (field-name dictionary) and block 2 (table list); the wire
format is identical, only the semantic role differs.

Wire format (probe-confirmed)::

    [ 4 bytes : zero padding              ]
    [ 4 bytes : little-endian u32 count   ]
    [ for each string:                    ]
    [   1 byte  : tag (always 0x04)       ]
    [   1 byte  : length L                ]
    [   L bytes : UTF-8 payload           ]

The length byte tops out at 255. Probe data shows all field/table names in
the 3 reference QVWs are well under that bound; if a future QVW uses a
different tag byte (e.g. 0x05 for >255-byte strings), it surfaces here as
:class:`InvalidStringListError` with the offending tag value, so adding
support is a localised change.
"""

from __future__ import annotations

import struct

STRING_TAG: int = 0x04
"""Single-byte tag preceding each entry in the string list."""

MAX_REASONABLE_STRING_COUNT: int = 1_000_000
"""Sanity bound on the declared u32 entry count.

Probe data shows real dictionaries top out at 64 entries; this leaves four
orders of magnitude headroom while bounding worst-case work under a crafted
or corrupt block claiming ``count = 4_000_000_000``.
"""


class InvalidStringListError(ValueError):
    """Raised when a buffer cannot be decoded as a tagged string list."""


def decode_tagged_string_list(buf: bytes) -> list[str]:
    """Decode a tag-prefixed string list from ``buf``.

    Args:
        buf: Decompressed contents of a metadata block expected to be a
            string list (block 1 or 2 in the QVW container, per probe).

    Returns:
        The strings in declaration order, decoded as UTF-8.

    Raises:
        InvalidStringListError: any wire-format invariant is violated
            (truncation, wrong tag byte, count overruns the buffer).
    """
    if len(buf) < 8:
        raise InvalidStringListError(
            f"buffer too short for string-list header: {len(buf)} bytes"
        )

    (count,) = struct.unpack_from("<I", buf, 4)
    if count > MAX_REASONABLE_STRING_COUNT:
        raise InvalidStringListError(
            f"declared string count {count} exceeds sanity bound "
            f"{MAX_REASONABLE_STRING_COUNT}; refusing as malformed/malicious"
        )
    pos = 8
    out: list[str] = []

    for i in range(count):
        if pos + 2 > len(buf):
            raise InvalidStringListError(
                f"truncated header for entry {i + 1}/{count} at offset {pos}"
            )
        tag = buf[pos]
        if tag != STRING_TAG:
            raise InvalidStringListError(
                f"unknown string tag 0x{tag:02x} at offset {pos} (expected 0x{STRING_TAG:02x})"
            )
        length = buf[pos + 1]
        if length == 0xFF:
            # Sentinel: real length is the following LE u32 (strings > 255 bytes).
            if pos + 6 > len(buf):
                raise InvalidStringListError(
                    f"truncated 0xFF-escaped u32 length at entry {i + 1}/{count} (offset {pos})"
                )
            (length,) = struct.unpack_from("<I", buf, pos + 2)
            start = pos + 6
        else:
            start = pos + 2
        end = start + length
        if end > len(buf):
            raise InvalidStringListError(
                f"truncated string at entry {i + 1}/{count}: "
                f"declared length {length}, only {len(buf) - start} bytes available"
            )
        try:
            out.append(buf[start:end].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise InvalidStringListError(
                f"non-UTF-8 bytes at entry {i + 1}/{count}: {exc}"
            ) from exc
        pos = end

    return out
