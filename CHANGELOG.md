# Changelog

All notable changes to this project will be documented in this file. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/) and the project follows [Semantic Versioning](https://semver.org/) once it reaches 1.0.0.

## [0.1.0-alpha] — 2026-05-08

First implementation release. Phase 1 from the [v3 design spec](docs/specs/2026-04-23-mcp-qlikview-design.md): metadata tools work end-to-end against real QVW files; data extraction is deferred to Phase 2.

### Added

- **Container parser** (`parser/container.py`): 23-byte header magic check, zlib stream scan via `bytes.find()`, kind-hint heuristics (`metadata` / `data_chunk` / `unknown`), EXEX trailer validation. Bounded against zlib-bomb input by `MAX_DECOMPRESSED_BLOCK_SIZE` (64 MB) and `MAX_BLOCKS_PER_FILE` (100 000).
- **Block decoders**:
  - `parser/blocks/strings.py` — shared tag-prefixed string-list (used by dictionary + tables blocks).
  - `parser/blocks/dictionary.py` — global field-name list (block 1).
  - `parser/blocks/tables.py` — global table-name list (block 2).
  - `parser/blocks/script.py` — load-script extraction with §4.3 encoding chain (UTF-8 → chardet → cp1252-replace) returning `ScriptDecodeResult`.
  - `parser/blocks/symbols.py` — `0x01..0x06` dual-value entry decoder (foundation for Phase 2 data extraction).
  - `parser/sources.py` — regex-based extraction of LIB/ODBC/OLEDB connections + file-path LOADs from the load script.
- **`-prj` fast-path** (`parser/prj.py`): when a sibling `<basename>-prj/LoadScript.txt` exists, prefer it over binary script extraction. Avoids encoding-fallback ambiguity for legacy Qlik exports.
- **MCP server** (`server.py`): stdio transport with 8 metadata tools — `list_files`, `list_tables`, `get_script`, `get_variables` (Phase 1 stub), `get_sheets` (stub), `get_data_sources`, `reload`, `search` (`scripts`+`variables` scopes; `fields`+`tables` enabled but return zero hits in Phase 1). Degraded mode on bad config: server stays alive, every tool call returns a structured `ErrorEnvelope`.
- **`MetadataStore`** (`store.py`): `OrderedDict`-backed LRU with default 16 entries, `Config.max_file_bytes` pre-flight gate (default 2 GiB), `qvw_too_large` error code per spec §5.1.1.
- **Configuration** (`config.py`): `pydantic-settings` env-driven config with all eight `MCP_QVW_*` variables plus `QVW_DIR`. Validates that `QVW_DIR` is a readable directory before booting.
- **`FileIndex` builder** (`index.py`): SQL-safe basename sanitisation with collision suffix, ISO-8601 mtime, `has_prj` detection.
- **Wire-type models** (`models.py`): full §4.3 schemas for `FileIndex`, `TableSummary`, `ScriptBundle`, `VariablesBundle`, `Sheet`, `DataSource`, `SearchResult`, `SkippedQvw`, `ReloadResult`, `ErrorEnvelope`.
- **88 unit tests** + **10 golden tests** on real QVW fixtures + **17 end-to-end tests** through real MCP stdio. Pytest, ruff, mypy strict — 0 errors.

### Security (post-adversarial review)

A BMAD-style adversarial review of the v1 implementation found 20 issues; 18 fixed in this release:

- **Path traversal** — absolute `qvw` paths must resolve inside `QVW_DIR`. Default deny; opt-in via `MCP_QVW_ALLOW_OUTSIDE_DIR=true`. Symlink-based escapes are rejected after `Path.resolve()`.
- **Zlib bomb** — bounded `zlib.decompressobj.decompress(max_length=...)` + per-file block-count cap.
- **Unbounded cache** — `OrderedDict` LRU eviction; the v1 implementation had no eviction at all.
- **Hardcoded encoding** — `ScriptBundle.script_encoding` and `decode_replacements` were always `"utf-8"`/`0` regardless of what the encoding chain actually did. Now wired through `ScriptDecodeResult` so consumers see the truth (e.g. on the LTV reference, encoding is reported as `cp1252` with 6039 replacements).
- **Regex flag parsing** — `/pattern/i` syntax now works (v1 stripped only the outer slashes, breaking documented examples). 1024-character pattern length cap as a soft ReDoS guard.
- **`field_count` over-reporting** — v1 returned the global dictionary size for every table; v0.1.0 returns `0` with `parse_status="pending"` to honestly signal "not yet known".
- **`PermissionError` in boot** — caught and translated to `qvw_dir_unreadable` per spec §6.1.
- **Container scan perf** — switched from byte-by-byte Python loop to `bytes.find()` (~50× faster on real files).
- **String-list count sanity bound** (1 000 000) against malformed/malicious blocks.
- **`MemoryError` / `KeyboardInterrupt` / `SystemExit`** are re-raised cleanly instead of being swallowed as a tool-level error.
- **`asyncio.to_thread`** wraps the synchronous parse so the 135 s cold parse on 141 MB doesn't block the MCP event loop for other concurrent calls.

Two issues are deferred to Phase 1.5: NFC unicode normalisation in cross-platform schema-name sort, and deep-frozen Pydantic models. Neither affects correctness or security.

### Deferred / not yet shipped

- **Data extraction tools** (`query`, `describe_table`, `export_table`) — Phase 2.
- **Filesystem watcher** — Phase 3.
- **PyPI publication** — Phase 3 (install via `uvx --from git+https://...` for now).
- **GitHub Actions CI** — Phase 1.5.

See [`LIMITATIONS.md`](LIMITATIONS.md) for the full picture.
