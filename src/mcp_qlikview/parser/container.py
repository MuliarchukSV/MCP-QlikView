"""
QVW container parser — reads the binary block structure of a .qvw file.

File layout (from probe findings 2026-04-23):
  [15-byte file header]
  [8-byte block header: uncompressed_size(4B LE) + compressed_size(4B LE)]
  [zlib-compressed block data]
  ... repeated; uncompressed "gap" sections may appear between blocks
"""

from __future__ import annotations

import zlib
import struct
from dataclasses import dataclass, field
from pathlib import Path


FILE_HEADER_SIZE = 15
BLOCK_HEADER_SIZE = 8  # 4B uncompressed_size + 4B compressed_size
ZLIB_MAGIC = b"\x78\x9c"

# Sanity limits
MAX_BLOCK_COMPRESSED = 200_000_000   # 200 MB per block
MAX_BLOCKS = 10_000


class QvwParseError(Exception):
    """Raised when the QVW file cannot be parsed."""


class QvwEncryptedError(QvwParseError):
    """Raised when block 0 cannot be decompressed (likely encrypted)."""


@dataclass
class QvwBlock:
    index: int
    offset: int           # offset of the 8-byte block header in the file
    uncompressed_size: int
    compressed_size: int
    data: bytes           # decompressed content


@dataclass
class QvwContainer:
    path: Path
    blocks: list[QvwBlock] = field(default_factory=list)
    is_encrypted: bool = False
    file_header: bytes = b""


def _try_decompress(raw: bytes, start: int, length: int) -> bytes:
    """Decompress a zlib block. Raises QvwEncryptedError on failure."""
    chunk = raw[start:start + length]
    try:
        return zlib.decompress(chunk)
    except zlib.error:
        pass
    # Try raw deflate (no zlib wrapper)
    try:
        return zlib.decompress(chunk, -15)
    except zlib.error as exc:
        raise QvwEncryptedError(
            f"Failed to decompress block at offset {start}: {exc}"
        ) from exc


def parse(path: Path) -> QvwContainer:
    """
    Read a QVW file and decompress all blocks.

    Raises:
        FileNotFoundError: if the file does not exist.
        QvwEncryptedError: if block 0 cannot be decompressed.
        QvwParseError: if the file structure is unrecognisable.
    """
    raw = path.read_bytes()
    if len(raw) < FILE_HEADER_SIZE + BLOCK_HEADER_SIZE:
        raise QvwParseError(f"{path.name}: file too small ({len(raw)} bytes)")

    container = QvwContainer(
        path=path,
        file_header=raw[:FILE_HEADER_SIZE],
    )

    offset = FILE_HEADER_SIZE
    block_index = 0

    while offset + BLOCK_HEADER_SIZE <= len(raw) and block_index < MAX_BLOCKS:
        uncompressed_size, compressed_size = struct.unpack_from("<II", raw, offset)

        data_start = offset + BLOCK_HEADER_SIZE

        # Sanity check: sizes must be plausible AND zlib magic must be present
        valid_size = (
            0 < compressed_size <= MAX_BLOCK_COMPRESSED
            and 0 < uncompressed_size <= MAX_BLOCK_COMPRESSED
            and data_start + compressed_size <= len(raw)
        )
        valid_magic = raw[data_start:data_start + 2] in (
            b"\x78\x9c", b"\x78\xda", b"\x78\x01", b"\x78\x5e"
        )

        if not valid_size or not valid_magic:
            # Gap section or unrecognised header — scan forward for next block
            scan_from = data_start if valid_size else offset
            next_zlib = _find_next_block(raw, scan_from, len(raw))
            if next_zlib is None:
                break
            offset = next_zlib - BLOCK_HEADER_SIZE
            if offset < FILE_HEADER_SIZE:
                break
            continue

        data_end = data_start + compressed_size

        try:
            decompressed = _try_decompress(raw, data_start, compressed_size)
        except QvwEncryptedError:
            if block_index == 0:
                container.is_encrypted = True
                return container
            # Non-fatal for subsequent blocks — stop scanning
            break

        container.blocks.append(
            QvwBlock(
                index=block_index,
                offset=offset,
                uncompressed_size=uncompressed_size,
                compressed_size=compressed_size,
                data=decompressed,
            )
        )

        offset = data_end
        block_index += 1

    return container


def _find_next_block(raw: bytes, start: int, end: int) -> int | None:
    """
    Scan forward from `start` for the next zlib magic bytes preceded by
    a plausible 8-byte block header.
    """
    magics = (b"\x78\x9c", b"\x78\xda", b"\x78\x01", b"\x78\x5e")
    pos = start
    while pos < end - BLOCK_HEADER_SIZE - 2:
        for magic in magics:
            idx = raw.find(magic, pos)
            if idx == -1:
                continue
            # Check that the 8 bytes before this position look like a valid header
            hdr_start = idx - BLOCK_HEADER_SIZE
            if hdr_start < FILE_HEADER_SIZE:
                pos = idx + 2
                continue
            uncompressed_size, compressed_size = struct.unpack_from("<II", raw, hdr_start)
            if (1 <= compressed_size <= MAX_BLOCK_COMPRESSED
                    and 1 <= uncompressed_size <= MAX_BLOCK_COMPRESSED
                    and hdr_start + BLOCK_HEADER_SIZE + compressed_size <= len(raw)):
                return idx
            pos = idx + 2
            break
        else:
            break
    return None
