# MCP-QlikView

A [Model Context Protocol](https://modelcontextprotocol.io) server that reads QlikView `.qvw` files and exposes their contents — load scripts, table schemas, and all data rows — to Claude Code via DuckDB SQL.

**Status:** design-phase. Implementation has not started. The canonical design document is [`docs/specs/2026-04-23-mcp-qlikview-design.md`](docs/specs/2026-04-23-mcp-qlikview-design.md).

## What it does (planned)

- Point the server at a directory of `.qvw` files via `QVW_DIR` env var.
- Query any table via standard SQL — one DuckDB schema per QVW, so `SELECT * FROM LTV_analisys.DataLTV` works.
- Extract the Qlik load script, variables, sheets, and data-source connections without touching the original QlikView ETL.
- On-demand parsing (first query to a QVW pays the cold-parse cost; subsequent queries are instant).
- Filesystem watcher auto-invalidates cache when a `.qvw` changes.

See the [full tool list](docs/specs/2026-04-23-mcp-qlikview-design.md#4-mcp-tools-contract) in the spec.

## Installation (planned for v1.0.0)

```bash
# Via uvx (recommended, no local install)
uvx mcp-qlikview

# Or via pipx
pipx install mcp-qlikview

# Or in a venv
pip install mcp-qlikview
```

## Claude Code configuration

```json
{
  "mcpServers": {
    "qlikview": {
      "command": "uvx",
      "args": ["mcp-qlikview"],
      "env": {
        "QVW_DIR": "/path/to/your/qvw/files"
      }
    }
  }
}
```

## Project status

| Phase | Scope | Status |
|---|---|---|
| Design | Spec approved | Done |
| Phase 1 | 7 metadata tools (script/tables/vars/sheets/sources) | Not started |
| Phase 2 | Data extraction via DuckDB SQL | Not started |
| Phase 3 | Watcher, export, PyPI release | Not started |

## Contributing

See [`docs/DEV_SETUP.md`](docs/DEV_SETUP.md) for the Claude Code plugins, skills, and environment this project expects. The workflow is LLM-assisted (spec → plan → TDD), but the repo doesn't ship any proprietary tooling — you install the public plugins and go.

## License

[MIT](LICENSE)

## Prior art

Design informed by parallel research — see spec §12. Built on [`PyQvd`](https://github.com/MuellerConstantin/PyQvd) for symbol/bit-index decoding; architectural pattern borrowed from [`mcp-server-motherduck`](https://github.com/motherduckdb/mcp-server-motherduck). No public QVW data-body parser exists as of 2026-04-23 — this project aims to be the first.
