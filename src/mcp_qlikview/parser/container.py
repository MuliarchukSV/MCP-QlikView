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

import logging
import zlib
from dataclasses import dataclass

log = logging.getLogger("mcp_qlikview.container")

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


@dataclass(frozen=True, slots=True)
class LogicalBlock:
    """Higher-level grouping over :class:`RawBlock`.

    The §14.1.1 framing probe found that QlikView segments any decompressed
    payload longer than 256 KB into fixed-size 256 KB zlib chunks plus a
    4-byte inter-chunk length-prefix gap. The 2026-05-08 follow-up probe
    confirmed that consecutive ``data_chunk`` blocks form one logical
    payload (typically a single field's symbol table). This type exposes
    that grouping: ``payload`` is the concatenation of every contributing
    block's decompressed bytes, suitable for direct hand-off to a block
    decoder (e.g. :func:`mcp_qlikview.parser.blocks.symbols.decode_symbol_block`).

    Attributes:
        first_index: ``RawBlock.index`` of the leftmost contributing block.
        last_index: ``RawBlock.index`` of the rightmost contributing block.
            Equal to ``first_index`` for single-block groups.
        payload: Concatenated decompressed bytes from all contributing blocks.
        kind: ``"symbol_group"`` for a run of ``data_chunk`` blocks merged
            into one buffer; ``"single"`` for a metadata/unknown block that
            wasn't merged with neighbours.
    """

    first_index: int
    last_index: int
    payload: bytes
    kind: str


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
    false_positives = 0
    while pos < end:
        candidate = _find_next_zlib_start(body, pos, end)
        if candidate < 0:
            break
        consumed, decompressed = _try_decompress(body, candidate, end)
        if consumed == 0:
            # Not a real stream — false positive on a 0x78 byte. Step one
            # forward and keep searching.
            false_positives += 1
            pos = candidate + 1
            continue
        if len(out) >= MAX_BLOCKS_PER_FILE:
            raise ZlibBombError(
                f"QVW has more than {MAX_BLOCKS_PER_FILE} zlib streams; "
                "refusing as suspicious"
            )
        out.append((candidate, decompressed, consumed))
        pos = candidate + consumed
    if false_positives:
        # A high count can indicate a missed zlib variant (only four magic
        # pairs are recognised) and explains downstream block-index drift.
        log.debug(
            "scan skipped %d zlib-magic false positives across %d byte body",
            false_positives,
            end,
        )
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


_DECOMPRESS_INPUT_CHUNK: int = 1 << 20
"""Compressed-input window fed to :func:`zlib.decompressobj.decompress` per call.

Feeding the whole ``body[offset:]`` tail at once is O(n²): every block then
copies the entire remaining body (and ``unused_data`` materialises it again),
which is the dominant cost in the ~135 s parse of the 141 MB reference. Feeding
in 1 MiB windows over a zero-copy :class:`memoryview` bounds both the per-call
input slice and the post-EOF ``unused_data`` copy to this size.
"""


def _try_decompress(body: bytes, offset: int, end: int) -> tuple[int, bytes]:
    """Attempt to decompress a zlib stream at ``body[offset:end]``.

    Returns ``(consumed_bytes, decompressed_payload)`` on success, or
    ``(0, b"")`` if the bytes do not form a valid complete zlib stream.

    Input is fed in :data:`_DECOMPRESS_INPUT_CHUNK` windows over a
    :class:`memoryview` so neither the input slice nor ``unused_data`` ever
    copies more than one window — see that constant's docstring for why.

    Decompression is hard-capped at :data:`MAX_DECOMPRESSED_BLOCK_SIZE` to
    bound memory under a zlib-bomb attack: a 1 KB compressed block claiming
    to inflate to 100 GB stops cleanly here instead of OOMing the process.
    Exceeding the cap raises :class:`ZlibBombError`; input that runs out
    before the stream terminates (``not obj.eof``) is treated as a non-stream.
    """
    obj = zlib.decompressobj()
    view = memoryview(body)
    chunks: list[bytes] = []
    produced = 0
    pos = offset

    def _pump(data: bytes | memoryview) -> None:
        nonlocal produced
        piece = obj.decompress(data, MAX_DECOMPRESSED_BLOCK_SIZE - produced + 1)
        produced += len(piece)
        if piece:
            chunks.append(piece)
        if produced > MAX_DECOMPRESSED_BLOCK_SIZE:
            raise ZlibBombError(
                f"block at offset {offset} decompresses past the "
                f"{MAX_DECOMPRESSED_BLOCK_SIZE}-byte cap; refusing as zlib bomb"
            )

    try:
        while not obj.eof:
            if pos >= end:
                # Input exhausted before the stream terminated — the bytes at
                # ``offset`` are not a complete zlib stream.
                return 0, b""
            inbuf = view[pos : pos + _DECOMPRESS_INPUT_CHUNK]
            pos += len(inbuf)
            _pump(inbuf)
            # If the output cap truncated this call, zlib buffers the rest as
            # ``unconsumed_tail``; keep draining it before advancing ``pos``.
            while obj.unconsumed_tail:
                _pump(obj.unconsumed_tail)
    except zlib.error:
        return 0, b""

    consumed = (pos - offset) - len(obj.unused_data)
    return consumed, b"".join(chunks)


def iter_logical_blocks(container: QvwContainer) -> list[LogicalBlock]:
    """Group ``container.blocks`` into logical payloads (probe-driven).

    A field's symbol table larger than 256 KB is stored as a run of full
    256 KB ``data_chunk`` blocks (each with a 4-byte inter-chunk gap)
    **followed by one trailing block** that holds the remainder. That
    trailing block is < 256 KB and carries the wider ~169-byte gap, so the
    container classifies it ``metadata``/``unknown`` rather than
    ``data_chunk``. It is nonetheless part of the same symbol table —
    confirmed on the LTV reference, where the run blocks 8-48 + block 49
    decode to exactly the declared 478,993 entries (and 160-324 + 325 to
    408,260). Dropping it truncates the table just short of its count.

    Grouping rule, therefore:

    - A maximal run of ``data_chunk`` blocks **plus the single block that
      immediately follows it** merge into one ``"symbol_group"``. The
      4-byte inter-chunk gaps are container framing and stay excluded from
      the payload; the trailing block's own gap is likewise irrelevant.
    - A run that ends at EOF with no following block forms a
      ``"symbol_group"`` of just its chunks (table ended on a 256 KB
      boundary — not observed yet, handled defensively).
    - Every other block becomes its own ``"single"`` :class:`LogicalBlock`.

    The symbol-table reader (Phase 2) still treats the ``[u32 count]``
    header as the authority and validates the decoded entry count against
    it; this grouping only assembles the candidate buffer.

    The result is a flat list rather than an iterator so callers can index
    into it (e.g. block 0 = script, block 1 = field-name dict).
    """
    out: list[LogicalBlock] = []
    pending: list[RawBlock] = []

    def _flush(tail: RawBlock | None = None) -> None:
        if not pending:
            return
        members = pending + ([tail] if tail is not None else [])
        out.append(
            LogicalBlock(
                first_index=members[0].index,
                last_index=members[-1].index,
                payload=b"".join(b.decompressed for b in members),
                kind="symbol_group",
            )
        )
        pending.clear()

    for block in container.blocks:
        if block.kind_hint == "data_chunk":
            pending.append(block)
            continue
        if pending:
            # This block is the remainder that completes the preceding
            # data_chunk run's symbol table — absorb it, then it's consumed.
            _flush(tail=block)
            continue
        out.append(
            LogicalBlock(
                first_index=block.index,
                last_index=block.index,
                payload=block.decompressed,
                kind="single",
            )
        )
    _flush()
    return out


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
