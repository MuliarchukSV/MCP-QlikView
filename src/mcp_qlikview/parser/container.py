"""Container layer: parse the QVW file envelope.

A QVW file is structured as::

    [ 23-byte header ][ N concatenated zlib streams ][ 4-byte EXEX trailer ]

The first 12 bytes of the header are a fixed magic+version signature
(established by the §14.1.1 probe across 3 reference files); bytes 12..23 vary
per-file. The trailer is ASCII ``b"EXEX"``. This module enforces those
invariants and exposes a low-level scan over the zlib streams.

Higher layers (``parser.blocks.*``) consume the decompressed bytes returned
here and decode block-specific structures.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

QVW_MAGIC_PREFIX: bytes = bytes.fromhex("70170100c106000002000000")
"""First 12 bytes of every valid QVW file. Constant across the 3 reference
files inspected during the probe; treated as the format/version handshake."""

HEADER_SIZE: int = 23
"""Total file-header length in bytes. First 12 are :data:`QVW_MAGIC_PREFIX`;
remaining 11 vary per-file and are passed through opaquely."""

EXEX_TRAILER: bytes = b"EXEX"
"""ASCII trailer marking end-of-file."""

DATA_CHUNK_DECOMPRESSED_SIZE: int = 256 * 1024
"""Probe-confirmed fixed decompressed size for Class B (data) blocks."""

DATA_CHUNK_GAP_SIZE: int = 4
"""Probe-confirmed inter-block gap after a data chunk (next-block size hint)."""

METADATA_GAP_MIN: int = 100
"""Lower bound for the ~169-byte inter-block gap observed after metadata blocks."""

MAX_DECOMPRESSED_BLOCK_SIZE: int = 64 * 1024 * 1024
"""Per-block hard cap on decompressed size (64 MB).

Defense against zlib-bomb input: real QVW blocks max out at ~5 MB metadata
or exactly 256 KB data per the probe. 64 MB leaves an order-of-magnitude
headroom for atypical files while bounding worst-case memory. A block that
exceeds this is treated as :class:`ZlibBombError` and aborts the file.
"""

MAX_BLOCKS_PER_FILE: int = 100_000
"""Per-file hard cap on number of zlib streams.

dbhDesigning.qvw — the largest reference — has 1176 blocks. 100k allows for
~80x growth before refusing the file, while bounding worst-case work to
something a constrained CI runner can complete.
"""


class InvalidQvwError(ValueError):
    """Raised when a byte sequence violates the QVW envelope invariants."""


class ZlibBombError(InvalidQvwError):
    """Raised when a block decompresses past :data:`MAX_DECOMPRESSED_BLOCK_SIZE`.

    Specialisation of :class:`InvalidQvwError` so callers can catch this
    specifically (e.g. surface a different error code in MCP responses).
    """


@dataclass(frozen=True, slots=True)
class RawBlock:
    """One zlib-compressed block recovered from the container body.

    Attributes:
        index: 0-based block ordinal in the file.
        offset: Absolute byte offset where the compressed stream starts.
        decompressed: Decompressed payload (block decoders consume this).
        gap_after: Number of bytes between the end of this stream and the
            start of the next stream (or, for the last block, the EXEX
            trailer). Used to classify the block kind.
        kind_hint: One of ``"data_chunk"``, ``"metadata"``, ``"unknown"``.
            Heuristic only — ground truth comes from inspecting the
            decompressed bytes themselves in ``parser.blocks.*``.
    """

    index: int
    offset: int
    decompressed: bytes
    gap_after: int
    kind_hint: str


@dataclass(frozen=True, slots=True)
class QvwContainer:
    """Outcome of envelope+scan over a QVW file.

    Attributes:
        header_tail: 11 bytes between the magic prefix and the body. Opaque
            in v1; preserved for forensic/debugging tools.
        blocks: All zlib streams decoded from the body, in file order.
    """

    header_tail: bytes
    blocks: list[RawBlock]


def _classify_block(decompressed_size: int, gap_after: int) -> str:
    """Heuristic kind classifier driven by probe findings.

    Class B (data) blocks are exactly 256 KB decompressed and followed by
    4 bytes (a length-prefix or checksum). Class A (metadata) blocks have a
    much longer trailing gap (~169 bytes). Anything else is flagged for
    human review rather than silently bucketed.
    """
    if decompressed_size == DATA_CHUNK_DECOMPRESSED_SIZE and gap_after == DATA_CHUNK_GAP_SIZE:
        return "data_chunk"
    if gap_after >= METADATA_GAP_MIN:
        return "metadata"
    return "unknown"


def validate_envelope(raw: bytes) -> None:
    """Verify that ``raw`` looks like a QVW envelope.

    Checks the magic prefix and minimum header length. The EXEX trailer and
    zlib body are validated by separate functions (added in subsequent TDD
    steps); this function intentionally does only what its tests require.

    Raises:
        InvalidQvwError: header is shorter than 23 bytes, or the first 12
            bytes do not match :data:`QVW_MAGIC_PREFIX`.
    """
    min_size = HEADER_SIZE + len(EXEX_TRAILER)
    if len(raw) < min_size:
        raise InvalidQvwError(
            f"file too short for QVW envelope: got {len(raw)} bytes, "
            f"need at least {min_size} (header {HEADER_SIZE} + trailer {len(EXEX_TRAILER)})"
        )
    if raw[: len(QVW_MAGIC_PREFIX)] != QVW_MAGIC_PREFIX:
        actual_hex = raw[: len(QVW_MAGIC_PREFIX)].hex()
        expected_hex = QVW_MAGIC_PREFIX.hex()
        raise InvalidQvwError(
            f"bad QVW magic: got {actual_hex}, expected {expected_hex}"
        )
    if raw[-len(EXEX_TRAILER):] != EXEX_TRAILER:
        trailer_bytes = raw[-len(EXEX_TRAILER):]
        raise InvalidQvwError(
            f"missing EXEX trailer: file ends with {trailer_bytes!r}"
        )


_ZLIB_MAGIC_PAIRS: tuple[bytes, ...] = (
    b"\x78\x9c",
    b"\x78\x01",
    b"\x78\xda",
    b"\x78\x5e",
)
"""Two-byte zlib stream prefixes (RFC 1950 §2.2 + probe observation).

Looking for these as fixed sequences via ``bytes.find`` is significantly
faster than the byte-by-byte loop the v1 implementation used — relevant
because real QVWs run this scan over hundreds of MB of body bytes.
"""


def _scan_blocks(body: bytes) -> list[tuple[int, bytes, int]]:
    """Walk ``body`` left-to-right, returning ``(offset, decompressed, compressed_len)``.

    Strategy: at each position, locate the next plausible 2-byte zlib stream
    start via :func:`bytes.find`, trial-decompress (with bounded output), and
    on success advance past the consumed bytes. ``decompressobj`` exposes
    ``unused_data`` which tells us exactly how many bytes belonged to the
    stream — more reliable than length-based scanning because zlib streams
    are variable-length and self-delimiting.

    Raises:
        ZlibBombError: when total block count exceeds
            :data:`MAX_BLOCKS_PER_FILE` or any block's decompressed size
            exceeds :data:`MAX_DECOMPRESSED_BLOCK_SIZE`.
    """
    out: list[tuple[int, bytes, int]] = []
    pos = 0
    end = len(body)
    while pos < end:
        candidate = _find_next_zlib_start(body, pos, end)
        if candidate < 0:
            break
        consumed, decompressed = _try_decompress(body, candidate, end)
        if consumed == 0:
            # Not a real stream — false positive on a 0x78 byte. Step one
            # forward and keep searching.
            pos = candidate + 1
            continue
        if len(out) >= MAX_BLOCKS_PER_FILE:
            raise ZlibBombError(
                f"QVW has more than {MAX_BLOCKS_PER_FILE} zlib streams; "
                "refusing as suspicious"
            )
        out.append((candidate, decompressed, consumed))
        pos = candidate + consumed
    return out


def _find_next_zlib_start(body: bytes, start: int, end: int) -> int:
    """Return absolute index of the earliest 2-byte zlib magic in ``body[start:end]``.

    Implementation: do four ``bytes.find`` calls (one per magic pair) and
    return the smallest non-negative result. This is C-implemented in CPython
    and runs ~50x faster than the prior Python-loop version on large bodies.
    """
    earliest = -1
    for magic in _ZLIB_MAGIC_PAIRS:
        idx = body.find(magic, start, end)
        if idx >= 0 and (earliest < 0 or idx < earliest):
            earliest = idx
    return earliest


def _try_decompress(body: bytes, offset: int, end: int) -> tuple[int, bytes]:
    """Attempt to decompress a zlib stream at ``body[offset:end]``.

    Returns ``(consumed_bytes, decompressed_payload)`` on success, or
    ``(0, b"")`` if the bytes do not form a valid complete zlib stream.

    Decompression is hard-capped at :data:`MAX_DECOMPRESSED_BLOCK_SIZE` to
    bound memory under a zlib-bomb attack: a 1 KB compressed block claiming
    to inflate to 100 GB stops cleanly here instead of OOMing the process.
    Exceeding the cap raises :class:`ZlibBombError`; a normal decompression
    that happens to not consume the entire candidate (``not obj.eof``) is
    treated as a non-stream.
    """
    obj = zlib.decompressobj()
    try:
        decompressed = obj.decompress(
            body[offset:end], max_length=MAX_DECOMPRESSED_BLOCK_SIZE
        )
    except zlib.error:
        return 0, b""
    if obj.unconsumed_tail:
        # max_length truncated output but more compressed input remains —
        # this is the zlib-bomb signature.
        raise ZlibBombError(
            f"block at offset {offset} decompresses past the "
            f"{MAX_DECOMPRESSED_BLOCK_SIZE}-byte cap; refusing as zlib bomb"
        )
    if not obj.eof:
        return 0, b""
    consumed = (end - offset) - len(obj.unused_data)
    return consumed, decompressed


def parse_bytes(raw: bytes) -> QvwContainer:
    """Validate envelope and return a :class:`QvwContainer` with all blocks.

    This is the public entry point for the container layer. Higher-level
    parsing (script extraction, schema, data) operates on the resulting
    :class:`RawBlock` list rather than on raw bytes.
    """
    validate_envelope(raw)
    body_start = HEADER_SIZE
    body_end = len(raw) - len(EXEX_TRAILER)
    body = raw[body_start:body_end]
    streams = _scan_blocks(body)

    blocks: list[RawBlock] = []
    for i, (rel_offset, decompressed, consumed) in enumerate(streams):
        next_start = streams[i + 1][0] if i + 1 < len(streams) else len(body)
        gap_after = next_start - (rel_offset + consumed)
        blocks.append(
            RawBlock(
                index=i,
                offset=body_start + rel_offset,
                decompressed=decompressed,
                gap_after=gap_after,
                kind_hint=_classify_block(len(decompressed), gap_after),
            )
        )

    return QvwContainer(header_tail=raw[len(QVW_MAGIC_PREFIX) : HEADER_SIZE], blocks=blocks)
