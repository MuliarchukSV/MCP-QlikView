# Research — Reference Libraries, Patterns & Market Signals (FF-20)

**Date:** 2026-06-02 · **Researcher:** FF-20 · **Confidence:** HIGH (links verified via fetch/GitHub)

Goal: concrete, verified links to borrow/learn from to level up MCP-QlikView,
especially the unbuilt Phase 2 (per-table symbol decode → bit-stuffed index →
DuckDB ingest).

---

## 1. QVD / QVW format reverse-engineering

| Item | URL | Why it matters |
|---|---|---|
| **OpenQVD** (Rust+Py, clean-room SPEC.md) ⭐ | https://github.com/Sigilweaver/OpenQVD | Most complete open QVD spec+impl. Documents symbol type bytes `0x01..0x06`, 2+6 bit-packing, row-index encoding. 99.7% sample coverage. **Direct Phase 2 decode reference.** v1.2.0 (May 31 2026). QVW/QVF are stated non-goals → complementary to us. |
| **qvdrs** (Rust+Py+Node, DuckDB/Parquet) ⭐ | https://github.com/bintocher/qvdrs | Byte-identical roundtrip on 399 files ≤2.8 GB. Streaming chunk reader, EXISTS() O(1), DuckDB/DataFusion table registration. **Copy the DuckDB ingest + streaming patterns.** v0.7.1 (May 16 2026). PyQvd now redirects here. |
| **PyQvd** (pure Python) | https://github.com/MuellerConstantin/PyQvd · docs: https://pyqvd.readthedocs.io/stable/guide/qvd-file-format.html | Best English write-up of QVD format (symbol types, bit-masked row index). Maintenance mode → safe to fork. |
| **QVD-Sources** (test corpus) ⭐ | https://github.com/Sigilweaver/QVD-Sources | **1,251 QVW files** + 1,145 QVD + 2,459 .qvs + ground-truth CSVs from 716 repos. Our integration-test corpus (esp. the QVW files for our container parser). |
| qvd-utils (Rust+PyO3) | https://github.com/SBentley/qvd-utils | PyO3 binding pattern if we ever expose a Rust decoder to Python. ~2021, less active. |
| qvd4js (JS) | https://github.com/MuellerConstantin/qvd4js | Cross-language sanity check of dual-type decode. Archived. |
| devinsmith/qvdreader (C++) | https://github.com/devinsmith/qvdreader | Byte-offset cross-check. ~2019 legacy. |
| Qlik Community: QVW format thread | https://community.qlik.com/t5/QlikView-App-Dev/QVW-file-format/td-p/622564 | Confirms no community binary spec exists → we're solving a genuinely unsolved problem; `.prj` XML is the only official route. |

## 2. MCP servers for BI / data tools (pattern sources)

| Item | URL | Why |
|---|---|---|
| **arthurfantaci/qlik-mcp-server** (Py, FastMCP, 9 tools) | https://github.com/arthurfantaci/qlik-mcp-server | Closest domain peer. `///$tab` section split + `BINARY LOAD` detection in script tool — directly reusable for our `get_script`/`get_data_sources`. |
| **bintocher/qlik-sense-mcp** (Py, 24 tools) | https://github.com/bintocher/qlik-sense-mcp | Dual HTTP+stdio transport (`--stdio`), hypercube cap (max_rows + cols*rows ≤ 9900) — adopt for our future `get_data` to avoid context floods. v1.5.1 (Jun 1 2026). |
| Official MCP servers | https://github.com/modelcontextprotocol/servers | Git (Python) = cleanest stdio reference; Filesystem = configurable access scoping (maps to our `list_files` dir-scoping). |
| mcp-server-duckdb (Py) | https://github.com/ktanaka101/mcp-server-duckdb | `--keep-connection` persistent-connection + read-only enforcement = exactly our cached-tables-per-session need. |
| **FastMCP** v3.3.1 | https://github.com/jlowin/fastmcp · https://gofastmcp.com/servers/tools | De-facto Py MCP framework. Schema-from-typehints, `ToolError` envelopes, lifespan mgmt. **Consider migrating our raw-SDK server to FastMCP.** |
| sqlite-explorer-fastmcp | https://github.com/hannesrudolph/sqlite-explorer-fastmcp-mcp-server | Most structurally similar (local file-db over MCP): query validation + read-only. |

## 3. DuckDB ingestion patterns (Phase 2 output pipeline)

- **Arrow IPC in DuckDB** (May 2025): https://duckdb.org/2025/05/23/arrow-ipc-support-in-duckdb — decoded columns → PyArrow → zero-copy register.
- **Streaming generator → DuckDB**: https://query.farm/posts/streaming-in-duckdb-from-python-generator.html — `pa.RecordBatchReader.from_batches(schema, gen())` then `con.register(...)`. Single-pass → cache after first ingest. Handles 2.8 GB without OOM.
- **ADBC streaming inserts** (Mar 2025): https://arrow.apache.org/blog/2025/03/10/fast-streaming-inserts-in-duckdb-with-adbc/ — **optimal batch = 122,880 rows**; flatten (no nested) for ~1.2M rows/sec. Single most actionable Phase 2 tuning param.
- DuckDB ADBC docs: https://duckdb.org/2023/08/04/adbc · MotherDuck `.arrow()` quickstart: https://motherduck.com/learn/duckdb-python-quickstart-part2/

## 4. Market signals (demand for an offline QVW tool)

- **QlikView lifecycle**: https://community.qlik.com/t5/Product-Lifecycle/QlikView-Product-Lifecycle/ta-p/1826339 — v12.100 supported to **Sep 30 2027**; new standalone licenses discontinued; large legacy base → 2-year window where orgs must inspect/migrate/audit QVW offline.
- **Qlik Analytics Migration Tool** (May 2025): https://help.qlik.com/en-US/migration/Content/Migration/Home.htm — Qlik actively pushing QlikView→Cloud; enterprises opening QVWs en masse now.
- **Official Qlik MCP server** (cloud-only): https://www.qlik.com/us/products/model-context-protocol — **cannot read offline QVW/QVD on disk**. We are the complementary on-prem/offline tool.
- Qlik Talend GenAI pipelines (GA Jul 2025): https://community.qlik.com/t5/Product-Innovation/Simplifying-GenAI-Data-Pipelines-with-Qlik-Talend-Cloud/ba-p/2498438
- MCP → Linux Foundation (Dec 2025): https://community.qlik.com/t5/Official-Support-Articles/Qlik-Model-Context-Protocol-MCP-FAQ/ta-p/2542621 — protocol is governed/standard → low strategic risk.

## 5. Reusable code patterns

- zlib concatenated-stream scan via `unused_data`: https://docs.python.org/3/library/zlib.html (confirms our container approach).
- chardet confidence-cascade fallback: https://github.com/chardet/chardet — for our cp1252/cp1251 script-decode chain (EE locales).
- PyArrow streaming + 122,880-row batches (links above).
- FastMCP lifespan + persistent DuckDB conn: https://gofastmcp.com/servers/tools.
- `///$tab` split + `BINARY LOAD` regex: arthurfantaci/qlik-mcp-server (above).

---

## Top 5 to act on first

1. **OpenQVD SPEC.md** — read now; it's the Phase 2 decode reference. https://github.com/Sigilweaver/OpenQVD
2. **QVD-Sources corpus** — clone; 1,251 QVW + ground-truth for integration tests. https://github.com/Sigilweaver/QVD-Sources
3. **qvdrs** — study DuckDB registration + streaming; possibly call it as decode backend instead of re-implementing. https://github.com/bintocher/qvdrs
4. **arthurfantaci/qlik-mcp-server** — copy `///$tab`/`BINARY LOAD` parsing; study FastMCP tool structure. https://github.com/arthurfantaci/qlik-mcp-server
5. **Arrow streaming insert + 122,880 batch** — one-afternoon Phase 2 ingest at scale. https://query.farm/posts/streaming-in-duckdb-from-python-generator.html
