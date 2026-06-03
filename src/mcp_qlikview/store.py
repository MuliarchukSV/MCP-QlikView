"""In-memory metadata cache for parsed QVWs (Phase 1 — no DuckDB yet).

Phase 2 will replace this with a DuckDB-backed store that holds actual data
rows; for Phase 1 we only cache the cheap-to-recompute metadata so repeated
``get_script`` / ``list_tables`` calls don't re-decompress the file.

Cache is bounded by ``max_entries`` (default 16) with LRU eviction — the
spec §5.1 LRU mechanism in concept, simplified to entry-count rather than
parsed-byte tracking for Phase 1. The store is intentionally non-thread-safe
— the MCP stdio transport is single-reader by construction, and aiorwlock
will join when Phase 2 introduces concurrent data parsing.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mcp_qlikview.parser.blocks.dictionary import extract_field_names
from mcp_qlikview.parser.blocks.layout import extract_table_field_map
from mcp_qlikview.parser.blocks.script import extract_script
from mcp_qlikview.parser.blocks.tables import extract_table_names
from mcp_qlikview.parser.blocks.values import ValueSet, extract_value_sets
from mcp_qlikview.parser.container import QvwContainer, parse_bytes
from mcp_qlikview.parser.prj import try_prj


class QvwTooLargeError(ValueError):
    """Raised when a QVW file exceeds the configured size pre-flight bound."""


def _decode_block(
    decoder: Callable[[bytes], list[str]],
    container: QvwContainer,
    index: int,
    role: str,
) -> list[str]:
    """Run ``decoder`` over ``container.blocks[index]`` with block context.

    Wraps the wire-format ``ValueError`` so a positional-drift failure reads
    as "block N (<role>) decode failed: ..." instead of a bare tag/UTF-8
    complaint that hides *which* block was wrong.
    """
    try:
        return decoder(container.blocks[index].decompressed)
    except ValueError as exc:
        raise ValueError(
            f"block {index} ({role}) decode failed: {exc}; the container's "
            "block ordering may have drifted (a zlib stream was missed or a "
            "false positive matched during the scan)"
        ) from exc


@dataclass(frozen=True)
class ParsedMetadata:
    """Cheap-to-cache QVW metadata (no row data)."""

    script: str
    """Full QlikView load script as text (UTF-8 with §4.3 fallback chain)."""

    script_encoding: str
    """Encoding actually used (``utf-8`` / detected codepage / ``cp1252``)."""

    script_decode_replacements: int
    """Bytes the decoder replaced (>0 only on cp1252 fallback)."""

    script_source: str
    """``"prj"`` when read from sibling ``-prj/LoadScript.txt``, else ``"binary"``."""

    field_names: list[str]
    """Global field-name dictionary from container block 1."""

    table_names: list[str]
    """Table-name list from container block 2."""

    block_count: int
    """Total zlib blocks decoded from the container — for diagnostics."""

    value_sets: list[ValueSet]
    """Per-field distinct-value summaries (cardinality, type, samples).
    Cheap partial decode — see :func:`extract_value_sets`."""

    table_field_map: dict[str, list[str]] | None
    """Table name → ordered field names (QVW internal layout), or ``None`` when
    the directory block can't be decoded. See :func:`extract_table_field_map`."""


_DEFAULT_MAX_ENTRIES: int = 16
"""LRU bound on the number of cached :class:`ParsedMetadata` entries.

16 entries x ~4 MB per typical metadata payload ~= 64 MB ceiling on cache
RAM, which keeps the server reasonable even when stress-tested against many
files. Override via :class:`MetadataStore` constructor for tests.
"""


class MetadataStore:
    """Lazy parse-on-demand metadata cache, keyed by absolute QVW path.

    Bounded LRU cache: when ``max_entries`` is reached, the least-recently-
    queried entry is evicted before a new entry is admitted. ``ensure_parsed``
    refreshes recency on cache hits.

    Thread-safe: cache reads/writes are guarded by a lock because handlers
    off-load :meth:`ensure_parsed` via ``asyncio.to_thread`` precisely so the
    server can service *other* requests during a parse — i.e. two parses can
    run concurrently. The (heavy) parse itself runs outside the lock; only the
    ``OrderedDict`` mutations are serialised. Concurrent misses on the same
    key may parse twice, but never corrupt the cache.
    """

    def __init__(
        self,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        max_file_size_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be ≥ 1, got {max_entries}")
        self._cache: OrderedDict[str, ParsedMetadata] = OrderedDict()
        self._max_entries = max_entries
        self._max_file_size_bytes = max_file_size_bytes
        self._lock = threading.Lock()

    def ensure_parsed(self, qvw_path: Path) -> ParsedMetadata:
        """Return the cached :class:`ParsedMetadata` for ``qvw_path``.

        Triggers a full container parse + metadata-block decode on cache
        miss. Container parsing dominates wall-time on large files (~135 s
        for the 141 MB reference); the per-call cost on cached hits is
        constant. LRU recency is refreshed on every successful lookup.

        Raises:
            QvwTooLargeError: file size exceeds ``max_file_size_bytes`` —
                surfaces as ``qvw_too_large`` (spec §5.1.1) rather than
                triggering a multi-gigabyte read.
        """
        key = str(qvw_path.resolve())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

        size = qvw_path.stat().st_size
        if size > self._max_file_size_bytes:
            raise QvwTooLargeError(
                f"QVW file size {size} bytes exceeds limit "
                f"{self._max_file_size_bytes}; raise MCP_QVW_MAX_FILE_BYTES "
                f"or split the file"
            )

        raw = qvw_path.read_bytes()
        container = parse_bytes(raw)
        if len(container.blocks) < 3:
            raise ValueError(
                f"QVW has fewer than 3 blocks ({len(container.blocks)}); "
                "expected at least script + dictionary + tables"
            )

        # Prefer the ``-prj/LoadScript.txt`` fast-path when available — the
        # plaintext file avoids encoding-fallback ambiguity that hits some
        # legacy Qlik exports (cp1251 mojibake under our UTF-8-strict-then-
        # fallback chain). Field/table names still come from the container
        # because the ``-prj`` folder doesn't contain them.
        prj = try_prj(qvw_path)
        if prj is not None:
            script_result = prj.script
            script_source = "prj"
        else:
            script_result = extract_script(container.blocks[0].decompressed)
            script_source = "binary"

        # Block ordering (0=script, 1=field dict, 2=tables) is positional per
        # the framing probe. If the zlib scan missed a stream or matched a
        # false positive, indices drift and the wrong buffer is decoded here —
        # surface that as a legible error rather than an opaque tag/UTF-8 fault.
        field_names = _decode_block(extract_field_names, container, 1, "field-name dictionary")
        table_names = _decode_block(extract_table_names, container, 2, "table-name list")

        meta = ParsedMetadata(
            script=script_result.text,
            script_encoding=script_result.encoding,
            script_decode_replacements=script_result.decode_replacements,
            script_source=script_source,
            field_names=field_names,
            table_names=table_names,
            block_count=len(container.blocks),
            value_sets=extract_value_sets(container),
            table_field_map=extract_table_field_map(container, field_names, table_names),
        )
        with self._lock:
            self._admit(key, meta)
        return meta

    def _admit(self, key: str, meta: ParsedMetadata) -> None:
        """Insert ``meta`` and evict LRU entries while over capacity.

        Caller must hold ``self._lock``.
        """
        self._cache[key] = meta
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def invalidate(self, qvw_path: Path | None = None) -> list[str]:
        """Drop ``qvw_path`` from the cache (or everything if ``None``).

        Returns the list of absolute paths whose cache entries were
        invalidated, for ``ReloadResult.invalidated``.
        """
        with self._lock:
            if qvw_path is None:
                keys = list(self._cache.keys())
                self._cache.clear()
                return keys
            key = str(qvw_path.resolve())
            if key in self._cache:
                del self._cache[key]
                return [key]
            return []
