# MCP-QlikView — Design Spec

**Date:** 2026-04-23
**Status:** Draft — pending user approval
**Target repo:** `github.com/MuliarchukSV/MCP-QlikView` (public)
**Distribution:** PyPI package `mcp-qlikview`, `uvx`-launched
**Owner:** Sergey Muliarchuk (personal / open source)

> This spec lives temporarily in `flipfactory-hub/docs/superpowers/specs/` until the `MCP-QlikView` repo is created. Upon scaffold, it will move to `MCP-QlikView/docs/specs/` as the canonical design record.

---

## 1. Purpose

Build an MCP (Model Context Protocol) server that reads QlikView QVW files and exposes their full contents — load scripts, metadata, and all data rows — to Claude Code via stdio, so users can query Qlik data conversationally without touching the original QlikView ETL pipeline.

**Primary use case:** Author has years of QlikView work; QVW files contain valuable production data. Wants Claude Code to ask questions over that data. Original Qlik scripts keep running unchanged.

**Secondary use case:** Others plug the same MCP server into their Claude Code via a public `uvx mcp-qlikview` install.

**Non-goals:**
- Replacing QlikView as a runtime
- Writing data back to QVW files (read-only)
- Supporting encrypted / section-access QVWs in v1
- Supporting QVD or QVF (Qlik Sense) files in v1 — QVW only

---

## 2. Success criteria

1. Installing and running: `uvx mcp-qlikview` launches the server; Claude Code connects via stdio on first use.
2. `list_files` returns the 3 reference files (`LTV_analisys.qvw`, `Monitoring.qvw`, `dbhDesigning.qvw`) from the configured `QVW_DIR`.
3. `get_script(qvw)` returns the full QlikView load script verbatim for all 3 reference files.
4. `list_tables(qvw)` returns every table defined in each QVW with its fields.
5. `query("SELECT COUNT(*) FROM LTV_analisys.DataLTV")` returns a non-zero row count matching what QlikView shows.
6. `query("SELECT * FROM LTV_analisys.DataLTV LIMIT 5")` returns real data rows that match what QlikView shows for the same table.
7. First-query cold start on the 141 MB reference file (`dbhDesigning.qvw`) under 60 seconds; subsequent queries under 1 second.
8. File change detected by watcher within 5 seconds of save; next query returns updated data.

Quality bar for data extraction: for each of the 3 reference files, at least 90% of explicitly-declared tables (non-synthetic, non-`$Syn*`) produce row counts and sample values matching QlikView. Known limitations (synthetic keys, mixed-type edge cases, Qlik-specific date packing) are documented in `LIMITATIONS.md` at ship time.

---

## 3. Architecture

### 3.1 High-level diagram

```
┌─────────────────────────────────────────┐
│             Claude Code                  │
└────────────────────┬────────────────────┘
                     │ stdio JSON-RPC (MCP)
                     ▼
┌─────────────────────────────────────────┐
│          mcp-qlikview (Python)           │
│                                          │
│  ┌───────────────────────────────────┐  │
│  │  MCP server (official mcp SDK)    │  │
│  │  10 tools, stdio transport         │  │
│  └───────────────┬───────────────────┘  │
│                  │                       │
│  ┌───────────────▼───────────────────┐  │
│  │  Store (DuckDB, schema-per-QVW)   │◄─┐
│  └───────────────┬───────────────────┘  │
│                  │                       │
│  ┌───────────────▼───────────────────┐  │
│  │  Parser                           │  │
│  │  ├─ container (zlib + EXEX)       │  │
│  │  ├─ -prj fast-path                │  │
│  │  ├─ script / vars / sheets        │  │
│  │  ├─ schema (tables + fields)      │  │
│  │  └─ data (symbol + bit-index)     │  │
│  └───────────────────────────────────┘  │
│                                          │
│  ┌───────────────────────────────────┐  │
│  │  Watcher (watchdog)               │──┘ invalidate cache
│  │  Config (QVW_DIR env + override)  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
                     ▲
                     │ filesystem
        ┌────────────┴────────────┐
        │  QVW_DIR                │
        │    LTV_analisys.qvw      │
        │    Monitoring.qvw        │
        │    dbhDesigning.qvw      │
        │    [optional]            │
        │    LTV_analisys-prj/     │ ← fast-path if present
        │      LoadScript.txt      │
        │      *.xml               │
        └─────────────────────────┘
```

### 3.2 Component responsibilities

Each component has one job, a defined interface, and is testable in isolation.

| Component | Responsibility | Key interface |
|---|---|---|
| `server.py` | MCP protocol glue: registers 10 tools, maps calls to handlers, formats responses | `run()` — starts stdio server |
| `config.py` | Load settings from env (`QVW_DIR`, `MAX_ROWS`, cache limits) | `Config` pydantic model |
| `store.py` | DuckDB connection, schema-per-QVW namespacing, lazy-load orchestration, cache invalidation | `ensure_parsed(qvw)`, `query(sql)`, `invalidate(qvw)` |
| `watcher.py` | Filesystem watch on `QVW_DIR`, emits invalidation events to store | `start(on_change)`, `stop()` |
| `parser/container.py` | Read QVW file: header → sequence of zlib blocks → EXEX trailer | `parse(path) → QvwContainer` (blocks + offsets) |
| `parser/prj.py` | If `<name>-prj/` folder sibling of `<name>.qvw` exists, read `LoadScript.txt` and XML objects | `try_prj(qvw_path) → PrjBundle \| None` |
| `parser/script.py` | Extract load script text: from `-prj` if present, else from block 0 | `extract_script(qvw) → str` |
| `parser/schema.py` | Parse table definitions and field lists (blocks 1..N metadata) | `extract_schema(qvw) → list[TableSchema]` |
| `parser/data.py` | Decode data blocks: symbol tables (flags `0x01..0x06`) + bit-stuffed index → `pyarrow.Table`. Adapted from `PyQvd`. | `extract_data(qvw, table) → pa.Table` |
| `parser/variables.py` | Parse variable XML | `extract_variables(qvw) → dict[str, str]` |
| `parser/sheets.py` | Parse sheet + chart XML | `extract_sheets(qvw) → list[Sheet]` |
| `parser/sources.py` | Regex-based extraction of `LIB CONNECT TO`, `ODBC`, `OLEDB`, file paths from script | `extract_sources(script) → list[DataSource]` |

Internals of each parser module can change without consumers noticing; the return types (Pydantic models in `models.py`) are the contract.

### 3.3 Data flow — on-demand parsing

**Granularity decision:** on first query to any table in a given QVW, **all tables** of that QVW are parsed and registered in DuckDB. Rationale (holds independent of §14.1.1 probe outcome): parsing cost is dominated by the single zlib-decompression pass over the QVW container; once decompressed, touching N tables vs 1 is marginal. Amortizing across all tables avoids re-decompressing the same container on every subsequent first-query-per-table. Also small-N: typical QVW has dozens of tables, not thousands. Exception: if a parse of an individual table fails, that table is marked `parse_failed` but the others in the QVW remain available (§6 "Partial parse success").

```
User: Claude Code calls query("SELECT * FROM LTV_analisys.DataLTV LIMIT 10")
  │
  ▼
server.query handler
  │
  ▼
store.query(sql)
  │
  ├─ parse SQL, find referenced schema "LTV_analisys"
  │
  ├─ is schema registered in DuckDB? ──yes──► execute → return
  │                                 └──no───┐
  │                                         ▼
  │                          store.ensure_parsed("LTV_analisys.qvw")  [acquires per-QVW Lock — see §3.6]
  │                                         │
  │                                         ▼
  │                          parser.container.parse(path)
  │                                         │
  │                                         ├─ check -prj folder → prj.try_prj()
  │                                         │
  │                                         ├─ parser.schema.extract_schema()
  │                                         │
  │                                         └─ for each table: parser.data.extract_data()
  │                                                 │              (all tables, not only the one in SQL — see decision above)
  │                                                 ▼
  │                          register in DuckDB schema "LTV_analisys"
  │                                                 │            [Lock released]
  │                                                 ▼
  │                          return to store; execute SQL; return rows
```

### 3.4 File changes — watcher flow

```
Watcher detects change on LTV_analisys.qvw
  │
  ▼
store.invalidate("LTV_analisys")
  │
  ├─ DROP SCHEMA LTV_analisys CASCADE
  │
  └─ mark as not_parsed
       │
       ▼
Next query to LTV_analisys triggers re-parse (cold path again)
```

### 3.5 Table namespacing

Each QVW maps to a DuckDB schema; the schema name is the QVW basename (without `.qvw`, sanitized for SQL identifiers).

- `LTV_analisys.qvw` → DuckDB schema `LTV_analisys`
- Table `DataLTV` inside → queried as `LTV_analisys.DataLTV`
- Collisions across QVWs impossible by construction
- Synthetic keys (Qlik `$Syn*`) surfaced as DuckDB views in the same schema, documented in `describe_table`

Sanitization rules (authoritative — this is the rule used, not deferred): replace any character not in `[A-Za-z0-9_]` with `_`; if the resulting identifier starts with a digit, prefix with `qvw_`. Cyrillic, accented, and other non-ASCII filename characters all collapse to `_`. Collisions after sanitization (e.g., `ЛТВ.qvw` and `LTV.qvw` both → `_LTV` / `LTV`) are handled by appending a numeric suffix (`_2`, `_3`) in registration order; `list_files` surfaces the mapping `original_path → schema_name` so users can resolve manually.

### 3.6 Concurrency model

The server runs a single asyncio event loop. The watcher runs in its own thread (watchdog's requirement); all state mutations it triggers are marshalled back onto the event loop via `asyncio.run_coroutine_threadsafe(coro, loop)`, where `loop` is the server-owned event loop handed to the watcher at `watcher.start(loop, on_change)` time.

Per-QVW **asyncio RWLock** (`aiorwlock` or equivalent) protects each schema's lifecycle. DuckDB itself does not queue DDL behind in-flight SELECTs — we provide the ordering ourselves.

- `store.query(sql)` acquires the **read** lock for each schema it touches (SQL-parsed table refs). Multiple concurrent queries hold read locks simultaneously — no contention.
- `store.ensure_parsed(qvw)` acquires the **write** lock before parsing and registering. Second concurrent caller waits; by the time it runs, the schema is already registered and the call is a cheap no-op check.
- `store.invalidate(qvw)` (triggered by watcher) acquires the same **write** lock before `DROP SCHEMA ... CASCADE`. Waits for all in-flight readers of that schema to complete; blocks new readers until the drop is done. Next query after invalidation pays the cold-parse cost via `ensure_parsed`.
- Cross-schema SQL (e.g., `JOIN` between `LTV_analisys.X` and `Monitoring.Y`) acquires read locks on both schemas before executing; lock acquisition is ordered by schema name to avoid deadlock.

DuckDB's own connection is thread-safe for read queries; the single MCP process uses one connection instance shared across handlers. All async handlers run on the main event loop; DuckDB's blocking calls are wrapped in `loop.run_in_executor` to avoid blocking the loop during long parses or queries.

---

## 4. MCP tools contract

All tool parameters and return types are Pydantic models in `src/mcp_qlikview/models.py`. The MCP layer auto-generates JSON schemas.

### 4.1 Tools

Field-level schemas for every return type live in §4.3 (authoritative). This table lists tool names, parameters, and the return-type identifier only.

| # | Tool | Parameters | Returns |
|---|---|---|---|
| 1 | `list_files` | — | `FileIndex[]` |
| 2 | `list_tables` | `qvw?` | `TableSummary[]` |
| 3 | `describe_table` | `table`, `qvw?`, `include_distinct_count?` (default auto — see §4.3) | `TableDetail` |
| 4 | `get_script` | `qvw` | `ScriptBundle` |
| 5 | `get_variables` | `qvw` | `VariablesBundle` |
| 6 | `get_sheets` | `qvw` | `Sheet[]` |
| 7 | `get_data_sources` | `qvw` | `DataSource[]` |
| 8 | `query` | `sql`, `qvw?`, `max_rows?` (default 10000) | `QueryResult` |
| 9 | `export_table` | `table`, `format` (`parquet`/`csv`/`jsonl`), `qvw?`, `out_dir?` | `ExportResult` |
| 10 | `reload` | `qvw?` (null = all) | `ReloadResult` |

All error responses use the shared `ErrorEnvelope` shape (§4.3).

### 4.2 Per-call `qvw` override

Tools that take optional `qvw` accept:
- **Omitted** → operate across all files in `QVW_DIR`
- **Basename** (e.g., `LTV_analisys`) → resolved against `QVW_DIR`
- **Absolute path** → used directly, file added to session's index even if outside `QVW_DIR`

Files referenced by absolute path outside `QVW_DIR` are **not** added to the watcher. Changes to them require explicit `reload(qvw="/abs/path")`. This keeps the watcher scope predictable and avoids ad-hoc filesystem surveillance.

### 4.3 Return model schemas

All return types are Pydantic models in `src/mcp_qlikview/models.py`. Fields below are the contract — implementations may add fields (forward-compatible) but MUST NOT remove or rename listed fields without a major version bump.

**`FileIndex`** — one entry per QVW file visible to the server.
```
path: str                    # absolute path
basename: str                # filename without extension, sanitized for SQL
schema_name: str             # DuckDB schema this QVW maps to (may differ from basename after collision suffix)
size_bytes: int
mtime: str                   # ISO-8601 UTC
status: Literal["not_parsed","cached","parse_failed"]
error: str | None            # populated when status == "parse_failed"
has_prj: bool                # True if sibling "<basename>-prj/" folder exists
```

**`TableSummary`** — one per table, across all QVWs when `qvw` omitted.
```
qvw: str                     # source QVW basename
schema: str                  # DuckDB schema name
table_name: str
field_count: int
row_count: int | None        # null until data is parsed
is_synthetic: bool           # Qlik $Syn* — represented as a DuckDB VIEW
parse_status: Literal["ok","pending","parse_failed"]
error: str | None
```

**`TableDetail`** (returned by `describe_table`) — full metadata for one table.
```
qvw: str
schema: str
table_name: str
row_count: int
is_view: bool                # true for $Syn synthetic keys
synthetic_key_fields: list[str]
fields: list[FieldDescriptor]

FieldDescriptor:
  name: str
  duckdb_type: str           # e.g. "VARCHAR", "DOUBLE", "TIMESTAMP"
  qlik_original_type: str    # e.g. "dual(INT,STRING)", "TIMESTAMP"
  nullable: bool
  distinct_count: int | None # see "distinct_count policy" below
  sample_values: list[Any]   # up to 10 values — see §4.4
```

**`distinct_count` policy:**
- For tables with `row_count <= 100_000` — always computed (`SELECT COUNT(DISTINCT field) FROM ...`).
- For larger tables — computed only when `describe_table(include_distinct_count=true)` is passed explicitly; otherwise returned as `None`. The default `include_distinct_count` is `"auto"` which enables the 100k threshold rule. Users can force skip with `false`.
- Small-table behavior for `sample_values`: if the table has fewer rows than the sample target (10), all rows returned, no padding.

**`ScriptBundle`** (returned by `get_script`).
```
qvw: str
script: str                  # full load-script text
source: Literal["prj","binary"]
line_count: int
```

**`VariablesBundle`** (returned by `get_variables`).
```
qvw: str
variables: dict[str, Variable]

Variable:
  name: str
  expression: str
  is_reserved: bool          # Qlik-provided vs user-defined
  comment: str | None
```

**`Sheet`** (one element of `get_sheets` return list).
```
id: str                      # Qlik sheet ID
title: str
order: int                   # display order in original QVW
objects: list[SheetObject]

SheetObject:
  id: str
  type: Literal["chart","table","text","button","input","other"]
  caption: str | None
  expressions: list[str]     # for charts: measure formulas; empty for static elements
  dimensions: list[str]      # fields used as dimensions; empty when not applicable
```

**`DataSource`** (one element of `get_data_sources`).
```
kind: Literal["odbc","oledb","lib","file","inline","rest"]
connection_string: str | None        # raw string from LIB CONNECT / ODBC CONNECT TO
lib_name: str | None                 # for kind="lib"
file_path: str | None                # for kind="file"
referenced_in_tables: list[str]      # which LOAD statements use this source
line_in_script: int                  # line number in the load-script
```

**`QueryResult`** (returned by `query`).
```
columns: list[ColumnMeta]
rows: list[list[Any]]        # row-oriented, parallel to columns
row_count: int               # len(rows) — what was returned
total_count: int | None      # len of full unclamped result; null if unknown
truncated: bool              # True when row_count < total_count due to max_rows cap
elapsed_ms: int

ColumnMeta:
  name: str
  duckdb_type: str
```

**`ExportResult`** (returned by `export_table`).
```
path: str                    # absolute path of written file
format: Literal["parquet","csv","jsonl"]
row_count: int
bytes_written: int
elapsed_ms: int
```

**`ReloadResult`** (returned by `reload`).
```
invalidated: list[str]       # schema names that were dropped; will re-parse on next query
```

**`ErrorEnvelope`** — all tool errors share this shape (returned instead of the normal result on failure).
```
error_code: Literal[
  "file_not_found",
  "qvw_dir_missing",
  "qvw_dir_unreadable",
  "qvw_too_large",            # single QVW > 2× cache budget — see §5.1.1
  "malformed_qvw",
  "encrypted_unsupported",
  "parse_failed",
  "sql_error",
  "table_not_found",
  "row_limit_exceeded",
  "internal",
]
message: str                 # human-readable
hint: str | None             # actionable suggestion
details: dict[str, Any] | None  # structured context (file path, SQL, etc.)
```

### 4.4 Row limits and truncation

| Tool | Limit | Behavior when exceeded |
|---|---|---|
| `query` | `max_rows` argument (default `MCP_QVW_MAX_ROWS=10000`, hard cap `MCP_QVW_HARD_MAX_ROWS=1_000_000`) | Truncate result, set `QueryResult.truncated=true`, populate `total_count` via a separate `COUNT(*)` when cheap (under 100ms); else `total_count=null`. Suggest `export_table` in the hint. |
| `list_tables` | none | Metadata is small (dozens of tables × ~100 bytes); return all. |
| `describe_table` | 10 sample values per field (fixed, not configurable) | Samples are a diverse sample (first, last, plus 8 random via `USING SAMPLE RESERVOIR`), not just head. Truncated long strings to 200 chars with ellipsis. |
| `export_table` | none (streams) | Uses DuckDB `COPY table TO 'path' (FORMAT ...)` — streaming, no in-memory materialization. OK for tables larger than RAM. |
| `list_files` | none | One entry per QVW found; count bounded by filesystem. |

### 4.5 Example request/response

Claude Code invokes `query` via MCP tool-call JSON:

```json
{
  "method": "tools/call",
  "params": {
    "name": "query",
    "arguments": {
      "sql": "SELECT idCustomer, SUM(LTV) AS total_ltv FROM LTV_analisys.DataLTV GROUP BY idCustomer ORDER BY total_ltv DESC LIMIT 5",
      "max_rows": 5
    }
  }
}
```

Response (on success):

```json
{
  "columns": [
    {"name": "idCustomer", "duckdb_type": "VARCHAR"},
    {"name": "total_ltv", "duckdb_type": "DOUBLE"}
  ],
  "rows": [
    ["C-001823", 48250.17],
    ["C-007192", 42100.40],
    ["C-000044", 39087.55],
    ["C-012309", 35500.00],
    ["C-001001", 31240.88]
  ],
  "row_count": 5,
  "total_count": 5,
  "truncated": false,
  "elapsed_ms": 42
}
```

Response (on error — e.g., schema not yet in DuckDB because QVW is encrypted):

```json
{
  "error_code": "encrypted_unsupported",
  "message": "LTV_analisys.qvw appears to be encrypted or uses section-access protection",
  "hint": "mcp-qlikview v1 does not support encrypted QVWs. Re-save the file without encryption in QlikView Desktop.",
  "details": {"qvw": "/home/user/qlikview/apps/LTV_analisys.qvw"}
}
```

---

## 5. Configuration

Environment variables (loaded via `pydantic-settings`):

| Variable | Default | Purpose |
|---|---|---|
| `QVW_DIR` | (required) | Directory scanned for `*.qvw` files |
| `MCP_QVW_MAX_ROWS` | `10000` | Default row cap for `query` |
| `MCP_QVW_HARD_MAX_ROWS` | `1000000` | Absolute cap that `max_rows` can request |
| `MCP_QVW_CACHE_MEM_MB` | `2048` | DuckDB memory budget before spill-to-disk |
| `MCP_QVW_WATCH` | `true` | Enable watcher; set `false` to disable in CI/ephemeral envs |
| `MCP_QVW_LOG_LEVEL` | `INFO` | Python logging level |

Example Claude Code config snippet for users:

```json
{
  "mcpServers": {
    "qlikview": {
      "command": "uvx",
      "args": ["mcp-qlikview"],
      "env": {
        "QVW_DIR": "/home/user/qlikview/apps"
      }
    }
  }
}
```

Alternative installs (documented in README, not enforced by spec): `pipx install mcp-qlikview` then point `command` to the resolved entry point; or `pip install mcp-qlikview` in a venv.

### 5.1 DuckDB instance lifetime and persistence

**DuckDB lives only for the MCP process lifetime.** No persistent cache across MCP restarts. Rationale: user explicitly chose on-demand parsing (no pre-warming) during design discussion; persistent cache would contradict that intent and introduce invalidation edge cases (stale cache after Qlik reload, version upgrades changing the parser).

Concrete behavior:

- DuckDB instance created in-memory at server startup: `duckdb.connect(":memory:")`
- Spill-to-disk enabled by default via `PRAGMA temp_directory = '<tempfile.gettempdir()>/mcp-qlikview-<pid>/'`. Uses Python's cross-platform `tempfile.gettempdir()` (resolves `$TMPDIR` on POSIX, `%TEMP%` on Windows). The temp dir is deleted on clean shutdown. On crash it remains; cleaned up by OS temp policy.
- Memory budget: `PRAGMA memory_limit = '<MCP_QVW_CACHE_MEM_MB>MB'`. When exceeded, DuckDB spills intermediate query state and newly-ingested tables to temp files.
- **Eviction policy when many QVWs loaded:** LRU at the DuckDB schema level. When total registered row-memory exceeds `MCP_QVW_CACHE_MEM_MB`, the least-recently-queried schema is `DROP`ped; next query to it re-parses the original QVW file. Tracked via an in-memory LRU map in `store.py`.
- First query to any given QVW after process start pays the full parse cost (observable as acceptance criterion §2.7: <60s for 141MB file). Subsequent queries are <1s. Restart → cold again.

### 5.1.1 Single QVW larger than cache budget

If a single QVW's parsed representation exceeds `MCP_QVW_CACHE_MEM_MB`, LRU eviction cannot help (there is no smaller neighbor to evict). Two-tier behavior:

- **Soft overrun** (estimated parsed size ≤ 2× `MCP_QVW_CACHE_MEM_MB`) — parse proceeds. DuckDB spills column chunks to `temp_directory` transparently; query performance degrades (disk reads on every scan) but correctness holds. This is the default path.
- **Hard overrun** (estimated parsed size > 2× `MCP_QVW_CACHE_MEM_MB`) — refuse to parse. Tool returns `ErrorEnvelope` with `error_code: "qvw_too_large"`, `hint: "raise MCP_QVW_CACHE_MEM_MB or split the QVW"`. Size estimate = uncompressed block total from the container header, not a perfect proxy but cheap to compute without parsing.

If `temp_directory` itself runs out of disk during a soft overrun, DuckDB raises `OutOfMemoryException`; we surface it as `ErrorEnvelope` with `error_code: "internal"` and the underlying message. No data corruption risk — the registration transaction is rolled back and the schema is left in `parse_failed` state.

`qvw_too_large` is added to the `ErrorEnvelope.error_code` enum in §4.3.

If users want warm starts, they run `uvx mcp-qlikview` as a long-lived process (e.g., kept alive by Claude Code between conversations); spec does not mandate a persistence strategy.

---

## 6. Error handling

### 6.1 Startup errors (server exits with non-zero)

- **`QVW_DIR` env var not set** → log to stderr `QVW_DIR environment variable is required. Set it to a directory containing .qvw files.` and exit `1`. MCP server does not start. Claude Code shows the stderr in its MCP connection error.
- **`QVW_DIR` points to non-existent path** → stderr `QVW_DIR='/foo/bar' does not exist.` and exit `2`.
- **`QVW_DIR` exists but is not a directory** (regular file, symlink to nowhere) → stderr message, exit `3`.
- **`QVW_DIR` is not readable by the MCP process** (permissions) → stderr message, exit `4`.

### 6.2 Runtime errors (tool-level `ErrorEnvelope`)

- **`QVW_DIR` is empty** (0 `*.qvw` files) → server starts normally, `list_files` returns `[]`. Explicit `qvw="/abs/path"` argument still works for out-of-dir files. No error is raised — empty dir is a valid state.
- **`file_not_found`** → tool returns `ErrorEnvelope` with hint `"check QVW_DIR or pass absolute qvw= path"`
- **`malformed_qvw`** (not zlib or missing EXEX trailer) → mark file `parse_failed` in index, return diagnostic; do not crash server
- **Partial parse success** (some tables OK, some fail) → successful tables usable via `query`; failed ones listed in `list_tables` with `parse_status: "parse_failed"` and `error` field
- **`encrypted_unsupported`** — detect via header flag or failed decompression pattern (exact signal identified in Phase 1 probe — see §14.1)
- **`sql_error`** → pass through DuckDB error message in `ErrorEnvelope.message`; `details.sql` echoes the offending SQL
- **`table_not_found`** → hint lists available schemas/tables
- **Watcher disabled / unavailable** → log warning at startup; fall back to polling-on-query (stat mtime before each `query`; re-parse if changed). No error surfaced unless polling itself fails.
- **`row_limit_exceeded`** → only used by `query` when `max_rows` exceeds `MCP_QVW_HARD_MAX_ROWS`. Normal truncation is NOT an error (uses `QueryResult.truncated` flag instead).

All runtime errors are structured `ErrorEnvelope` JSON returned to Claude Code (no plaintext crashes, no stack traces leaked).

---

## 7. Build strategy — phased implementation

Estimates are **targets, not commitments**. No public QVW data-body parser exists (§12) — Phase 2 in particular carries genuine unknown-unknowns. Phase gates are defined by acceptance criteria, not dates. If Phase 2 reveals framing that diverges significantly from QVD, Phase 2 splits into 2a (MVP data: 50%+ tables) and 2b (coverage push). v1.0.0 only ships once §2 success criteria are met.

### Phase 1 — Reliable metadata (target ~2 days)
**Goal:** 7/10 tools working at 100% confidence. Usable immediately for script exploration even if data extraction is pending.

Tools complete: `list_files`, `list_tables` (metadata only), `get_script`, `get_variables`, `get_sheets`, `get_data_sources`, `reload`.

Deliverables:
- Repo scaffolded, published to PyPI as `mcp-qlikview==0.1.0`
- MCP server runs, connects to Claude Code
- Container parser (header/zlib/EXEX) validated on all 3 reference QVWs
- `-prj` fast-path working when folders present
- Phase 1 probe findings recorded: QVW data-block framing (§14.1.1), encrypted-detection signal (§14.1.2)

**Gate:** §2 success criteria 1, 2, 3, 4 verified on all 3 reference files.

### Phase 2 — Data extraction (target ~3 days, real risk of 4-6)
**Goal:** full `query`, `describe_table`, `export_table` working. All 10 tools live.

Deliverables:
- PyQvd-adapted data decoder integrated into `parser/data.py`
- DuckDB ingestion pipeline (pyarrow → DuckDB `from_arrow`)
- Dual-value preservation (numeric + text columns for fields where both matter)
- Timestamp / Date / Time / Interval types mapped to DuckDB types using Qlik XML `NumberFormat.Type`
- Synthetic-key tables surfaced as views

**Gate:** §2 success criteria 5, 6 verified on all 3 reference files; 90% of non-synthetic tables produce row counts matching QlikView. If gate is not met within ~6 days, scope-split into Phase 2a (release as `0.2.0` — partial data coverage, marked preview) and Phase 2b (coverage push).

### Phase 3 — Polish & ship (target ~1 day)
- Watcher working end-to-end, §2.8 verified
- `export_table` streaming (parquet/csv/jsonl)
- README with install + Claude Code config example
- `LIMITATIONS.md` documenting known gaps
- GitHub Actions release workflow (tag → PyPI via trusted publisher)

**Gate:** all §2 success criteria verified → tag `v1.0.0`.

**Target total:** ~6 working days. Realistic range: 6-10 depending on Phase 2 surprises.

---

## 8. Testing strategy

### 8.1 Test pyramid

- **Unit tests** (`test_container.py`, `test_script.py`, `test_data.py`):
  - Hand-crafted binary fixtures (minimal QVW ~1KB) exercising each parser component
  - Edge cases: empty blocks, malformed headers, dual values, timestamps

- **Golden tests** (`test_golden.py`):
  - Run parser on the 3 reference QVWs
  - Assert: script content hash, table names, table row counts, sample values at known row+column positions
  - Regression canary: any parser change breaking snapshots surfaces immediately

- **Integration tests** (`test_integration.py`):
  - Spawn MCP server subprocess, connect via stdio MCP client
  - Execute full user flow: `list_files` → `list_tables` → `query("SELECT COUNT(*) FROM ...")`
  - Assert expected counts and data shapes

### 8.2 Test fixtures

- 3 reference QVWs (`LTV_analisys.qvw`, `Monitoring.qvw`, `dbhDesigning.qvw`) **not committed** (confidential); referenced via env var `MCP_QVW_TEST_FIXTURES_DIR`. Tests that need them skip gracefully when dir missing.
- Minimal synthetic QVW (~1KB) generated by a test helper, committed. Covers the wire format without revealing production data.

### 8.3 CI

- GitHub Actions `ci.yml` on push/PR: `ruff check` → `mypy` → `pytest` (unit + synthetic). Golden tests skipped without fixtures.
- `release.yml` on git tag: build wheel + sdist → upload to PyPI via OIDC trusted-publisher.

### 8.4 Manual acceptance

For v1.0.0 sign-off, manually:
1. `uvx mcp-qlikview` launches
2. Claude Code connects
3. Query each of the 3 reference QVWs; row counts match QlikView
4. Edit a QVW; next query reflects change within 10s

---

## 9. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| QVW data blocks wrap QVD bodies with extra framing (not yet confirmed) | High | 0.5-1 day extra | Day 1: `xxd` on decompressed data blocks; compare to QVD magic. If wrapper present, document it before coding parser. |
| Synthetic keys or QUALIFY/UNQUALIFY scripts break schema mapping | Medium | 0.5 day | Test on all 3 reference files; document unsupported constructs in LIMITATIONS.md |
| Encrypted / section-access QVW in wild | Low | — | Detect header flag, return clean error |
| Table too large for RAM | Low | — | DuckDB spill-to-disk enabled by default |
| Pure-Python bit-stuffing slow (>2 min per 100MB QVW) | Medium | Performance only | Ingestion one-time; SQL is fast after. If blocker, swap in Rust `qvd-utils`. |
| QVW v12 format changes | Medium | — | Reference files will reveal this; document version support in README |
| `-prj` folder contains layout but not data — binary scrape still needed for data | Certain | (informational) | Fast-path applies to script/vars/sheets only; data always from binary |
| No PyPI trusted-publisher set up for the repo | Low | 0.5 hour | Config once during first release |
| PyQvd license compatibility (MIT) with our MIT | None | — | Compatible; attribute in LICENSE-THIRD-PARTY |

---

## 10. Dependencies

Runtime:
- `mcp>=1.0` — official Python MCP SDK
- `duckdb>=0.10`
- `pyarrow>=15`
- `pydantic>=2.5`
- `pydantic-settings>=2.1`
- `watchdog>=4.0`
- `aiorwlock>=1.3` — per-schema read/write lock (§3.6)
- `PyQvd>=3.0` *(if adaptation stays external — else fork vendored into `src/mcp_qlikview/_vendor/`)*

Dev:
- `pytest>=7.4`, `pytest-asyncio`
- `ruff`, `mypy`
- `build`, `twine` (or PyPI trusted-publisher GHA action)

Python: `>=3.10`.

---

## 11. Repo layout (recap)

```
MCP-QlikView/
├── pyproject.toml
├── README.md
├── LICENSE (MIT)
├── LICENSE-THIRD-PARTY  (PyQvd attribution if vendored)
├── .gitignore
├── src/mcp_qlikview/
│   ├── __init__.py
│   ├── __main__.py
│   ├── server.py
│   ├── config.py
│   ├── store.py
│   ├── watcher.py
│   ├── models.py
│   └── parser/
│       ├── __init__.py
│       ├── container.py
│       ├── prj.py
│       ├── script.py
│       ├── schema.py
│       ├── data.py
│       ├── variables.py
│       ├── sheets.py
│       └── sources.py
├── tests/
│   ├── fixtures/
│   │   └── synthetic_minimal.qvw
│   ├── test_container.py
│   ├── test_script.py
│   ├── test_data.py
│   ├── test_server.py
│   ├── test_golden.py
│   └── test_integration.py
├── .github/workflows/
│   ├── ci.yml
│   └── release.yml
└── docs/
    ├── INSTALL.md
    ├── CLAUDE_CODE_CONFIG.md
    ├── LIMITATIONS.md
    └── specs/
        └── 2026-04-23-mcp-qlikview-design.md  (this file, post-scaffold)
```

---

## 12. Prior art reference

Design informed by parallel research (23 Apr 2026). Key sources:

- **PyQvd** (github.com/MuellerConstantin/PyQvd, MIT) — QVD symbol/bit-index parser; base for `parser/data.py`
- **qvd-utils** (github.com/SBentley/qvd-utils, Apache-2.0) — Rust-backed QVD reader; performance fallback
- **qlik-parser** (github.com/mattiasthalen/qlik-parser, Go) — QVW script extraction; confirms no public data-body parser
- **mcp-server-motherduck** (github.com/motherduckdb/mcp-server-motherduck) — architectural benchmark for DuckDB+MCP
- **qlik-mcp-server** (github.com/arthurfantaci/qlik-mcp-server) — tool naming pattern (`get_app_*`)
- **bintocher/qlik-sense-mcp** — Qlik Sense counterpart (Engine API)
- **PyQvd — QVD File Format docs** (pyqvd.readthedocs.io) — de-facto QVD spec
- **Qlik Community "-prj" folder trick** — script fast-path enabled by QlikView v10+

No public QVW data-body parser exists; this project contributes the first open-source implementation.

---

## 13. Out of scope (explicit)

- Writing to QVW files
- Live Qlik Engine API connections (covered by other MCP servers already)
- QVD, QVF, QVS, or QVX file support (possible future extension — separate spec)
- Encrypted or section-access QVWs
- QlikView layout rendering (dashboards, chart visuals)
- Multi-user auth / row-level security
- Streaming row output (DuckDB SQL returns materialized rows; consumers paginate via SQL `LIMIT/OFFSET`)

---

## 14. Open questions (to resolve during implementation)

### 14.1 Phase 1 probe questions (day 1)

1. **QVW data-block framing** — does the QVW binary wrap QVD bodies with an additional framing layer, or is each zlib block a direct QVD-style XML header + symbol + index sequence? **First task** — hex-inspect decompressed blocks from `LTV_analisys.qvw` and compare to canonical QVD layout (from PyQvd docs). Outcome recorded here, feeds §9 risk row 1.
2. **Encrypted QVW detection signal** — which header byte or decompression behavior reliably indicates an encrypted or section-access-protected file, to return the `encrypted_unsupported` error cleanly. Outcome feeds §6.2.

### 14.2 Phase 2 design questions

3. **PyQvd vendored vs depended-on** — fork and control versioning (safer, isolates from upstream breakage) vs depend on PyPI release (simpler, benefit from upstream fixes)? Decide after a small prototype that proves adaptation scope.
4. **Dual-value column strategy** — map Qlik dual values (int/float + display string) to split columns (`field__num`, `field__text`) or single column with richest type? Prototype both on a field like `idCustomer`, pick based on query ergonomics. Decision recorded in §4.3 `FieldDescriptor.qlik_original_type` once made.

Note: schema-name sanitization rule is defined authoritatively in §3.5 (not deferred).

---

**Approval gate:** user reviews this document. After approval, `superpowers:writing-plans` skill produces the step-by-step implementation plan.
