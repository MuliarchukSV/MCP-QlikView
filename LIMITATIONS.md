# Limitations — v0.1.0

A frank list of what this server **does not** do yet, organised by category. Phase 2 and 1.5 work will close most of these; intractable items are flagged.

## Data extraction (Phase 2 — biggest gap)

The following spec §4.1 tools are **not implemented** in v0.1.0:

- `query(sql, qvw?, max_rows?)` — would execute SQL via DuckDB. Returns `unsupported` error today.
- `describe_table(table, qvw?, include_distinct_count?)` — would return `TableDetail` with field-level types, sample values, and distinct counts. Not callable.
- `export_table(table, format, qvw?, out_dir?)` — would stream parquet/CSV/JSONL. Not callable.

`list_tables` works but reports `field_count=0` and `parse_status="pending"` for every table because the per-table schema decoder lives in Phase 2.

`search` accepts the `fields` and `tables` scopes per spec §4.1 but returns zero hits in those scopes — they're documented as Phase 2 work.

The foundational `parser/blocks/symbols.py` shipped in v0.1.0 (decodes the `0x01..0x06` dual-value entries on top of which Phase 2 builds), but the table-level data layer (bit-stuffed index decode + DuckDB ingest) is the next ~3-6 dev-days of work. Estimate is risky because the QVW container is custom and PyQvd applies only to the inner symbol decoder, not the table framing — see the [framing probe](docs/probes/2026-05-07-qvw-framing.md) for the format details.

## Encoding accuracy

The §4.3 chain (UTF-8 → chardet → cp1252-replace) is implemented and the actual encoding used is reported in `ScriptBundle.script_encoding`. However:

- **Cyrillic detection is not always precise.** The 141 MB `dbhDesigning.qvw` reference triggers cp1252 fallback with several thousand replacements even though much of the script is human-readable. Probably the file mixes UTF-8 and Windows-1251 bytes per `SET MoneyFormat` directive originating on a Windows-1251 host. We log the encoding used and the replacement count so consumers can see when output is degraded; we do not yet auto-detect Cyrillic specifically.
- **Workaround:** if your QVW exports a `-prj` sibling folder, the server prefers `LoadScript.txt` from there — that path doesn't go through fallback at all, so encoding is whatever your file system stored. Future work: per-block charset hints from QVW metadata (probe noted these exist but did not characterise them).

## Encrypted / section-access QVWs

Probe §3 noted no encrypted reference file was available, so the `encrypted_unsupported` detection is a "best-effort raise on first decompression failure that looks like ciphertext after zlib" placeholder. We do not actively detect the encryption header flag because we have not seen it. Files with section access will likely fail with `malformed_qvw` rather than the spec'd `encrypted_unsupported`. PRs welcome with sample files.

## Performance

- **Cold parse on 141 MB is ~135 s** on a typical laptop. Within the 180 s spec budget but tight on slow CI runners. The spec mentions a Rust-fallback (`qvd-utils`) as a stretch optimisation; not in scope for v0.1.0.
- **Memory: ~640 MB peak** during parse of `dbhDesigning.qvw` (141 MB compressed → ~377 MB decompressed in container blocks). Cache is bounded to 16 entries (~64 MB metadata payload) post-parse.
- **No filesystem watcher.** `MCP_QVW_WATCH=true` is accepted as config but no watching happens; `reload` is the only invalidation path. Phase 3 deliverable.

## Scope-related gaps

- **`-prj` fast-path: script only.** The directory may also contain XML files for variables and sheets; we don't read them yet. Variables/sheets tools return empty mappings until Phase 1.5 implements the XML parser.
- **`get_data_sources` regex misses several patterns:** `REST CONNECTOR`, `CUSTOM CONNECT`, inline `[]` `LOAD` blocks, dynamic `$(vSource)` interpolation. Captures the four most common (LIB CONNECT, ODBC CONNECT, OLEDB CONNECT, FROM '<file>' / FROM [<file>]). Phase 1.5 candidate.
- **Synthetic-key tables are not surfaced as views** (Phase 2 deliverable); they will appear in `list_tables` once Phase 2 lands.
- **Cross-QVW dependencies are not resolved.** Two QVWs that share the same field name in `LIB CONNECT` won't be linked across schemas; that's also Phase 2.

## Cross-platform quirks

- **NFC unicode normalisation in schema-name sort is not yet applied.** Two macOS-stored QVWs with NFD-normalised filenames may collide differently than the same files on Linux. Edge case — none of the reference files exercise it.
- **Windows path separators in `QVW_DIR` are not extensively tested.** The codebase uses `pathlib.Path` throughout and should be Windows-clean, but no contributor has run the test suite on Windows yet.

## Distribution gaps

- **PyPI publication is deferred** until Phase 2 closes. Until then, install via `uvx --from git+https://...` or `pipx install git+...` (see [README](README.md)).
- **Smithery / MCP-registry submission** has not happened. Same reasoning.

## What this server **will not** do (intentional)

- **Run QlikView load scripts.** This is a read-only viewer. The original Qlik ETL keeps running unchanged; we never write back to the `.qvw` file.
- **Replace QlikView Server.** Performance is best-effort, not parity. Treat the server as a "backstage door" for Claude Code to ask questions over the data without spinning up the desktop client.
- **Support `.qvd` or Qlik Sense `.qvf` files.** QVW only. The container framing differs and the design intentionally scopes to the format the author needs.
- **Surface PNG sheet thumbnails** that the probe found embedded in real files. Not useful in a stdio MCP context; explicitly out of scope per spec §13.
