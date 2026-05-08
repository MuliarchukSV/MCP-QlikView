# MCP-QlikView

A [Model Context Protocol](https://modelcontextprotocol.io) server that reads QlikView `.qvw` files and exposes their contents — load scripts, table inventories, data-source connections — to Claude Code via stdio.

**Status:** v0.1.0-alpha — Phase 1 ships **8 metadata tools** (script + table inventory + cross-QVW search). Data extraction (`query`, `describe_table`, `export_table`) is Phase 2 work and not yet available; see [LIMITATIONS.md](LIMITATIONS.md) for the current shape of the gap.

The canonical design document is [`docs/specs/2026-04-23-mcp-qlikview-design.md`](docs/specs/2026-04-23-mcp-qlikview-design.md). The QVW container reverse-engineering is documented in [`docs/probes/2026-05-07-qvw-framing.md`](docs/probes/2026-05-07-qvw-framing.md).

## What works today (v0.1.0)

| # | Tool | Returns | Notes |
|---|---|---|---|
| 1 | `list_files` | One `FileIndex` per `*.qvw` in `QVW_DIR` | size, mtime, has_prj |
| 2 | `list_tables` | `TableSummary[]` with table names | `field_count=0` until Phase 2 |
| 3 | `get_script` | Full QlikView load script | with actual encoding + replacement count |
| 4 | `get_variables` | `VariablesBundle` | Phase 1 stub (returns `{}`) |
| 5 | `get_sheets` | `Sheet[]` | Phase 1 stub (returns `[]`) |
| 6 | `get_data_sources` | `DataSource[]` | regex over the load script (LIB/ODBC/OLEDB/file LOAD) |
| 7 | `reload` | `ReloadResult` | invalidates the metadata cache |
| 8 | `search` | `SearchResult` | Phase 1: `scripts` + `variables` scopes |

Verified end-to-end on 3 reference QlikView 12 files (LTV_analisys, Monitoring, dbhDesigning — total ~220 MB). The 141 MB file parses in ~135 s on a developer laptop and well within the 180 s spec budget.

## Installation

The server is **not yet published to PyPI**. Install directly from GitHub:

```bash
# uvx (recommended — Astral's uv must be installed first)
uvx --from git+https://github.com/MuliarchukSV/MCP-QlikView mcp-qlikview

# or pipx
pipx install git+https://github.com/MuliarchukSV/MCP-QlikView

# or in a local venv
git clone https://github.com/MuliarchukSV/MCP-QlikView
cd MCP-QlikView
pip install -e ".[chardet]"
```

## Claude Code configuration

```jsonc
{
  "mcpServers": {
    "qlikview": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/MuliarchukSV/MCP-QlikView",
        "mcp-qlikview"
      ],
      "env": {
        "QVW_DIR": "/absolute/path/to/your/qvw/files"
      }
    }
  }
}
```

After editing `mcp.json`, restart Claude Code. On first use the server inspects `QVW_DIR` and returns a structured error if the path is missing — no silent failures.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `QVW_DIR` | (required) | Directory scanned for `*.qvw` files |
| `MCP_QVW_MAX_ROWS` | `10000` | Default row cap (Phase 2) |
| `MCP_QVW_HARD_MAX_ROWS` | `1000000` | Absolute row cap (Phase 2) |
| `MCP_QVW_CACHE_MEM_MB` | `2048` | DuckDB memory budget (Phase 2) |
| `MCP_QVW_MAX_FILE_BYTES` | `2147483648` (2 GiB) | Pre-flight refusal threshold |
| `MCP_QVW_ALLOW_OUTSIDE_DIR` | `false` | Allow absolute-path `qvw` args outside `QVW_DIR` (security: see below) |
| `MCP_QVW_LOG_LEVEL` | `INFO` | Python `logging` level |
| `MCP_QVW_WATCH` | `true` | Enable filesystem watcher (Phase 2) |

## Security posture

This is a public OSS server that other people will install on their machines. Default behaviour is **deny-by-default** for everything beyond the configured `QVW_DIR`:

- **Path traversal:** absolute-path `qvw` arguments must resolve inside `QVW_DIR`. Symlink-based escapes (`../../etc/passwd`) are rejected after `Path.resolve()`. To opt in to cross-directory reads (e.g. for ad-hoc analysis on a trusted host), set `MCP_QVW_ALLOW_OUTSIDE_DIR=true`.
- **Zlib bomb:** decompression is hard-capped at 64 MB per block and 100 000 blocks per file. A maliciously crafted QVW that claims to inflate to gigabytes errors out cleanly with `malformed_qvw` instead of OOMing the process.
- **File size:** files larger than `MCP_QVW_MAX_FILE_BYTES` are refused before any read, so a 10 GB QVW pointed at the server doesn't take down the host.
- **Pattern length:** search patterns are capped at 1024 characters to constrain catastrophic regex backtracking. Regex flags (`/pattern/i`) are properly parsed.

Adversarial review (FF-01 Architect role) found 20 issues across the v1 implementation; 18 are fixed in v0.1.0 and the remaining 2 (NFC normalisation in cross-platform schema sort; deep-frozen Pydantic models) are Phase 1.5 polish that don't affect correctness or security.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| Design v1-v3 | Spec, adversarial review, framing probe | Done (2026-04-23 → 2026-05-07) |
| **Phase 1** | **8 metadata tools, secure-by-default, end-to-end verified** | **Done (2026-05-08)** |
| Phase 1.5 | NFC normalisation, deep-frozen models, additional `get_data_sources` patterns (REST CONNECTOR, CUSTOM CONNECT) | Open |
| Phase 2 | Data extraction (`query`, `describe_table`, `export_table`), per-table schema decoding, field/table search scopes | Not started |
| Phase 3 | Filesystem watcher, streaming `export_table`, GitHub Actions release workflow, PyPI publication | Not started |

## Development

```bash
git clone https://github.com/MuliarchukSV/MCP-QlikView
cd MCP-QlikView
uv venv --python 3.12
VIRTUAL_ENV="$PWD/.venv" uv pip install -e ".[dev,chardet]"
.venv/bin/pytest
```

Golden tests (against real reference QVWs) are skipped by default; set `MCP_QVW_TEST_FIXTURES_DIR=/path/to/qlik` to run them. The reference files are not redistributed because they contain production data.

See [`docs/DEV_SETUP.md`](docs/DEV_SETUP.md) for the optional Claude Code plugins / skills / agents that the project's contributors use day-to-day.

## License

[MIT](LICENSE). Built on [PyQvd](https://github.com/MuellerConstantin/PyQvd)'s reverse-engineering of the QlikView dual-value encoding (MIT, attribution in [`LICENSE-THIRD-PARTY`](LICENSE-THIRD-PARTY)). QlikView and Qlik are trademarks of QlikTech International AB; this project is not affiliated with, endorsed by, or sponsored by Qlik.

## Acknowledgements

The v3 spec was hardened by an adversarial review (BMAD-style "find ≥10 problems" technique). The container layer was implemented test-first against the §14.1.1 framing probe, which reverse-engineered the QVW envelope by inspecting three production files. No public QVW data-body parser exists as of 2026-05-08; once Phase 2 lands, this project aims to be the first.
