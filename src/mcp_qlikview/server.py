"""
MCP server — Phase 1 metadata tools.

Seven tools:
  list_files        — discover .qvw files in QVW_DIR
  list_tables       — tables & field counts from a QVW
  get_script        — load script text
  get_variables     — SET/LET variables
  get_sheets        — sheet/object layout (requires -prj)
  get_data_sources  — data connections referenced in script
  reload            — invalidate cache for one or all QVW files
"""

from __future__ import annotations

import asyncio
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_qlikview.config import load_config
from mcp_qlikview.models import FileIndex, ReloadResult
from mcp_qlikview.parser.container import QvwContainer
from mcp_qlikview.parser.container import parse as parse_qvw
from mcp_qlikview.parser.prj import try_prj
from mcp_qlikview.parser.schema import extract_schema, to_table_summaries
from mcp_qlikview.parser.script import extract_script
from mcp_qlikview.parser.sheets import extract_sheets
from mcp_qlikview.parser.sources import extract_sources
from mcp_qlikview.parser.variables import extract_variables


# ─────────────────────────────────────────────────────────────
# Config & FastMCP app
# ─────────────────────────────────────────────────────────────

_cfg = load_config()

mcp = FastMCP(
    "mcp-qlikview",
    instructions=(
        "QlikView (.qvw) metadata server. "
        "Start with list_files to discover available QVW files, "
        "then use list_tables / get_script / get_variables / "
        "get_sheets / get_data_sources to inspect a specific file. "
        "Call reload to refresh after a file changes."
    ),
)


# ─────────────────────────────────────────────────────────────
# In-memory parse cache
# ─────────────────────────────────────────────────────────────

@dataclass
class _Entry:
    container: QvwContainer
    mtime: float


_cache: dict[str, _Entry] = {}
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _schema_name(stem: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", stem)
    return ("qvw_" + name) if name and name[0].isdigit() else name


def _find_path(qvw: str) -> Path:
    """Resolve 'Monitoring' or 'Monitoring.qvw' to a full path in QVW_DIR."""
    p = _cfg.qvw_dir / qvw
    if p.exists():
        return p
    p2 = _cfg.qvw_dir / (qvw if qvw.lower().endswith(".qvw") else qvw + ".qvw")
    if p2.exists():
        return p2
    raise FileNotFoundError(f"QVW not found in {_cfg.qvw_dir}: {qvw!r}")


async def _container(path: Path) -> QvwContainer:
    """Return a cached QvwContainer, re-parsing if the file changed on disk."""
    key = str(path)
    mtime = path.stat().st_mtime
    async with _get_lock():
        entry = _cache.get(key)
        if entry is not None and entry.mtime == mtime:
            return entry.container
        loop = asyncio.get_running_loop()
        cnt = await loop.run_in_executor(None, parse_qvw, path)
        _cache[key] = _Entry(container=cnt, mtime=mtime)
        return cnt


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# ─────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────

@mcp.tool()
def list_files() -> list[dict[str, Any]]:
    """
    List all .qvw files in QVW_DIR.

    Returns one record per file with path, size, modification time, parse status
    ('not_parsed' or 'cached'), and whether a -prj companion folder exists.
    """
    results: list[dict[str, Any]] = []
    for path in sorted(_cfg.qvw_dir.glob("*.qvw")):
        fi = FileIndex(
            path=str(path),
            basename=path.name,
            schema_name=_schema_name(path.stem),
            size_bytes=path.stat().st_size,
            mtime=_mtime_iso(path),
            status="cached" if str(path) in _cache else "not_parsed",
            has_prj=try_prj(path) is not None,
        )
        results.append(fi.model_dump())
    return results


@mcp.tool()
async def list_tables(qvw: str) -> list[dict[str, Any]]:
    """
    List tables in a QVW file with field counts and parse status.

    Args:
        qvw: File name ('Monitoring.qvw') or stem ('Monitoring').

    Tables with parse_status='pending' have no field metadata yet —
    schema detail requires Phase 2 data loading.
    """
    path = _find_path(qvw)
    cnt = await _container(path)
    tables = extract_schema(cnt)
    summaries = to_table_summaries(tables, path.stem)
    return [s.model_dump() for s in summaries]


@mcp.tool()
async def get_script(qvw: str) -> dict[str, Any]:
    """
    Return the full load script from a QVW file.

    Args:
        qvw: File name or stem.

    Returns a ScriptBundle with 'script' (full text), 'source' ('prj' or 'binary'),
    and 'line_count'.
    """
    path = _find_path(qvw)
    cnt = await _container(path)
    bundle = extract_script(cnt)
    return bundle.model_dump()


@mcp.tool()
async def get_variables(
    qvw: str,
    include_reserved: bool = False,
) -> dict[str, Any]:
    """
    Return SET/LET variables declared in the load script.

    Args:
        qvw: File name or stem.
        include_reserved: Include Qlik system variables (ThousandSep, DateFormat, …).
                          Default False.

    Returns a VariablesBundle with a 'variables' dict keyed by variable name,
    each with 'name', 'expression', 'is_reserved', and optional 'comment'.
    """
    path = _find_path(qvw)
    cnt = await _container(path)
    bundle = extract_variables(cnt)
    if not include_reserved:
        bundle.variables = {
            k: v for k, v in bundle.variables.items() if not v.is_reserved
        }
    return bundle.model_dump()


@mcp.tool()
async def get_sheets(qvw: str) -> list[dict[str, Any]]:
    """
    Return sheet and object layout from the QVW's -prj companion folder.

    Args:
        qvw: File name or stem.

    Returns a list of Sheet objects (id, title, order, objects[]).
    Empty list if no -prj folder exists — sheet parsing requires -prj XML.
    """
    path = _find_path(qvw)
    cnt = await _container(path)
    sheets = extract_sheets(cnt)
    return [s.model_dump() for s in sheets]


@mcp.tool()
async def get_data_sources(qvw: str) -> list[dict[str, Any]]:
    """
    Return all data connections referenced in the load script.

    Args:
        qvw: File name or stem.

    Returns a list of DataSource objects by kind:
      'lib'    — LIB CONNECT TO
      'odbc'   — ODBC CONNECT TO
      'oledb'  — OLEDB CONNECT TO
      'file'   — FROM [...].qvd / .csv / .xlsx
      'rest'   — HTTP(S) URLs found in script
      'inline' — INLINE data blocks
    """
    path = _find_path(qvw)
    cnt = await _container(path)
    bundle = extract_script(cnt)
    sources = extract_sources(bundle.script)
    return [s.model_dump() for s in sources]


@mcp.tool()
async def reload(qvw: str | None = None) -> dict[str, Any]:
    """
    Invalidate the parse cache to force re-parse on the next tool call.

    Args:
        qvw: File name or stem to invalidate.  Omit to invalidate ALL cached files.

    Returns a ReloadResult with 'invalidated': list of file paths that were cleared.
    """
    async with _get_lock():
        if qvw is None:
            invalidated = list(_cache.keys())
            _cache.clear()
        else:
            path = _find_path(qvw)
            key = str(path)
            invalidated = [key] if key in _cache else []
            _cache.pop(key, None)

    return ReloadResult(invalidated=invalidated).model_dump()


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def run() -> None:
    mcp.run()
