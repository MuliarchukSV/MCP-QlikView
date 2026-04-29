# MCP-QlikView — Claude Code context

## What this project is

An MCP (Model Context Protocol) server that reads QlikView `.qvw` files and exposes their contents — load scripts, metadata, and all data rows — to Claude Code via DuckDB SQL.

**Status:** Design complete. Phase 1 not started. Phase 1 = **1 PR** на всю фазу.

## Source of truth

Always read this first: `docs/specs/2026-04-23-mcp-qlikview-design.md`

That spec is authoritative. If anything in conversation or code conflicts with it, the spec wins unless the user explicitly overrides it.

## Phased implementation plan

| Phase | Scope | Status |
|---|---|---|
| Phase 1 | 7 metadata tools: `list_files`, `list_tables`, `get_script`, `get_variables`, `get_sheets`, `get_data_sources`, `reload` | Not started |
| Phase 2 | 3 data tools: `query`, `describe_table`, `export_table` via DuckDB | Not started |
| Phase 3 | Watcher, export, PyPI release as `mcp-qlikview` | Not started |

## Repo layout (target — not yet scaffolded)

```
src/mcp_qlikview/
  server.py, config.py, store.py, watcher.py, models.py
  parser/container.py, prj.py, script.py, schema.py,
          data.py, variables.py, sheets.py, sources.py
tests/
  fixtures/synthetic_minimal.qvw  ← committed
  test_container.py, test_script.py, test_data.py,
  test_server.py, test_golden.py, test_integration.py
.github/workflows/ci.yml, release.yml
```

## Tech stack

- Python 3.10+
- `mcp>=1.0`, `duckdb>=0.10`, `pyarrow>=15`, `pydantic>=2.5`
- `pydantic-settings>=2.1`, `watchdog>=4.0`, `aiorwlock>=1.3`, `PyQvd>=2.3`
- ⚠️ Spec вимагає `PyQvd>=3.0`, але v3.0 ще не вийшла на PyPI. Використовуємо 2.3.2.
- Distributed via PyPI as `mcp-qlikview`, launched with `uvx mcp-qlikview`

## Key architectural decisions

- **DuckDB in-memory**, schema-per-QVW (`LTV_analisys.qvw` → schema `LTV_analisys`)
- **On-demand parsing**: first query triggers full parse of that QVW (all tables), subsequent queries instant
- **No persistent cache**: DuckDB lives only for MCP process lifetime
- **asyncio RWLock** per schema for concurrency safety
- **Watcher** (watchdog) auto-invalidates cache on file change

## MCP tools (10 total)

`list_files`, `list_tables`, `describe_table`, `get_script`, `get_variables`, `get_sheets`, `get_data_sources`, `query`, `export_table`, `reload`

Full contracts in spec §4.

## Test fixtures

3 reference QVW files NOT committed (production data). Set `MCP_QVW_TEST_FIXTURES_DIR` to their location. Tests skip gracefully without it.

## Known risks (Orchestrator assessment)

| Ризик | Деталь |
|---|---|
| QVW binary reverse-engineering | zlib blocks + EXEX trailer — формат не задокументований публічно. Day 1: hex-inspect decompressed blocks (spec §14.1.1) |
| Cross-platform | Dev = Windows Server 2019, Prod = Linux. Шляхи, tempdir, watchdog-backend — перевіряти явно |
| PyQvd версія | Spec вимагає >=3.0, встановлено 2.3.2. Адаптувати API при інтеграції |

## Git / PR workflow

- Phase 1 = **один PR** (`phase-1-metadata`) на всю фазу
- PR review: використовувати GitHub MCP (`gh pr view`, `gh pr review`, comments) — не bash
- ⚠️ GitHub MCP: перевірити підключення перед стартом (`/mcp` або `gh auth status`)

## Before starting any implementation

1. Re-read `docs/specs/2026-04-23-mcp-qlikview-design.md` fully
2. Verify GitHub MCP is active: `gh auth status`
3. Answer Phase 1 probe questions (spec §14.1): hex-inspect decompressed QVW blocks, confirm data-block framing vs QVD layout
4. Scaffold `pyproject.toml` and project structure before writing parser code
5. Branch: `git checkout -b phase-1-metadata`
