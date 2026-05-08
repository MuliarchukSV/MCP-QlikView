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

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from mcp_qlikview.parser.blocks.dictionary import extract_field_names
from mcp_qlikview.parser.blocks.script import extract_script
from mcp_qlikview.parser.blocks.tables import extract_table_names
from mcp_qlikview.parser.container import parse_bytes
from mcp_qlikview.parser.prj import try_prj


class QvwTooLargeError(ValueError):
    """Raised when a QVW file exceeds the configured size pre-flight bound."""


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
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        size = qvw_path.stat().st_size
        if size > self._max_file_size_bytes:
            raise QvwTooLargeError(
                f"QVW file size {size} bytes exceeds limit "
                f"{self._max_file_size_bytes}; raise MCP_QVW_MAX_FILE_SIZE_BYTES "
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

        meta = ParsedMetadata(
            script=script_result.text,
            script_encoding=script_result.encoding,
            script_decode_replacements=script_result.decode_replacements,
            script_source=script_source,
            field_names=extract_field_names(container.blocks[1].decompressed),
            table_names=extract_table_names(container.blocks[2].decompressed),
            block_count=len(container.blocks),
        )
        self._admit(key, meta)
        return meta

    def _admit(self, key: str, meta: ParsedMetadata) -> None:
        """Insert ``meta`` and evict LRU entries while over capacity."""
        self._cache[key] = meta
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def invalidate(self, qvw_path: Path | None = None) -> list[str]:
        """Drop ``qvw_path`` from the cache (or everything if ``None``).

        Returns the list of absolute paths whose cache entries were
        invalidated, for ``ReloadResult.invalidated``.
        """
        if qvw_path is None:
            keys = list(self._cache.keys())
            self._cache.clear()
            return keys
        key = str(qvw_path.resolve())
        if key in self._cache:
            del self._cache[key]
            return [key]
        return []
