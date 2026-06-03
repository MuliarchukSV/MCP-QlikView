# Changelog

All notable changes to this project will be documented in this file. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/) and the project follows [Semantic Versioning](https://semver.org/) once it reaches 1.0.0.

## [0.1.1] — 2026-06-03 — adversarial-review fixes (round 2)

A second BMAD-style adversarial review of the v0.1.0 code found 18 issues; all addressed here.

### Fixed

- **Store size limit ignored config** — `MetadataStore` was constructed with no arguments, keeping a hardcoded 2 GiB cap and ignoring `MCP_QVW_MAX_FILE_BYTES`. The store is now wired to `Config.max_file_bytes` at boot, so one limit governs both the `_resolve_qvw` pre-flight and the store.
- **Wrong env var in size error** — the `qvw_too_large` hint referenced `MCP_QVW_MAX_FILE_SIZE_BYTES` (read by nothing); corrected to `MCP_QVW_MAX_FILE_BYTES`.
- **`list_tables` / `search` skipped the size guard** — the index-derived paths parsed files without the pre-flight that `get_script` enforced, including full all-files scans of arbitrarily large QVWs. Both now apply `_check_size` per file and report oversized files as `parse_failed` / skipped.
- **Cache data race** — handlers offload `ensure_parsed` via `asyncio.to_thread`, so two parses can run concurrently against the non-thread-safe `OrderedDict`. Added a `threading.Lock` around all cache mutations (the heavy parse still runs outside the lock).
- **Connection-string credential leak + truncation** — ODBC/OLEDB connection strings were captured only up to the first internal `;` (dropping most of the string) and echoed verbatim. Now the full statement is captured (line-anchored regex, incl. `CONNECT32/64`) and credential values (`PWD`/`Password`/`token`/…) are masked to `***` before leaving the parser.
- **O(n²) container decompression** — `_try_decompress` sliced `body[offset:end]` (and materialised `unused_data`) per block, copying the whole tail each time — the dominant cost in the ~135 s parse. Now feeds 1 MiB windows over a zero-copy `memoryview`, bounding per-block copies.
- **`get_variables` / `get_sheets` silent empty results** — returned `{}` / `[]`, which reads as "no variables/sheets". Now return a structured `unsupported` error until the Phase 1.5 decoders land.
- **`search` reported nothing for unimplemented scopes** — `fields`/`tables`/`variables` returned zero hits indistinguishable from real misses. Added `SearchResult.not_implemented_scopes`; removed the dead `variables` branch from the Phase 1 active set.
- **Filesystem races surfaced as protocol errors** — `_call_tool` now catches `OSError` and returns a structured `ErrorEnvelope`, honouring the module's stated error contract.
- **`reload` of an oversized cached file was a silent no-op** — reload now resolves the path with the size pre-flight disabled so a file that grew past the limit can still be invalidated.
- **`log_level` config was dead** — `MCP_QVW_LOG_LEVEL` now adjusts the `mcp_qlikview` logger at boot.
- **Block-decode errors didn't say which block** — positional-drift failures now read `block N (<role>) decode failed: …`.
- **Search line numbers diverged from `line_count`** — search now splits on `\n` (matching `ScriptBundle.line_count`) instead of `splitlines()`.
- **Misc** — `/regex/x` (VERBOSE) flag documented; the regex match now runs off the event loop (ReDoS containment); replaced an `__import__("sys")` hack with a normal import; scan now logs skipped zlib-magic false positives at DEBUG.

### Tests

- New `tests/test_store.py` (size guard, env-var hint, LRU, concurrency stress, block-decode error context) and `tests/test_server_phase1.py` (not-implemented scopes, size pre-flight, unsupported variables/sheets, `\n` line numbers) run in CI without real fixtures. Plus connection-string masking tests. ruff + mypy strict clean.

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
