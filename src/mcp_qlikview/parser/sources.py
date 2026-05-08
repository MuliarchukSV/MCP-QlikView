"""Regex-based extraction of data sources from a QlikView load script.

Phase 1 covers four patterns commonly seen in real QVWs::

    LIB CONNECT TO 'NAME';
    ODBC CONNECT TO <conn>;
    OLEDB CONNECT TO <conn>;
    FROM [<path>] | FROM '<path>'

The patterns are not exhaustive — REST CONNECTOR ``CONNECT TO``, inline data
blocks, and ``CUSTOM CONNECT TO`` flavours fall through silently in v0.1.0.
Future work captures those + line-number provenance for ``referenced_in_tables``.
"""

from __future__ import annotations

import re

from mcp_qlikview.models import DataSource

_LIB_CONNECT = re.compile(r"\bLIB\s+CONNECT\s+TO\s+'([^']+)'", re.IGNORECASE)
_ODBC_CONNECT = re.compile(r"\bODBC\s+CONNECT\s+TO\s+([^;]+);", re.IGNORECASE)
_OLEDB_CONNECT = re.compile(r"\bOLEDB\s+CONNECT\s+TO\s+([^;]+);", re.IGNORECASE)
_FILE_LOAD = re.compile(
    r"\bFROM\s+(?:\[([^\]]+)\]|'([^']+)')",
    re.IGNORECASE,
)


def _split_lines(script: str) -> list[str]:
    return script.splitlines()


def _line_of(script: str, char_offset: int) -> int:
    """1-based line number containing ``char_offset`` in ``script``."""
    return script.count("\n", 0, char_offset) + 1


def extract_sources(script: str) -> list[DataSource]:
    """Return one :class:`DataSource` per distinct data-source reference.

    Deduplication is best-effort: ``(kind, identity)`` keys where ``identity``
    is the lib name, connection string, or file path. Same source referenced
    twice yields one entry with the line number of its first occurrence.
    """
    seen: dict[tuple[str, str], DataSource] = {}

    def _add(kind: str, identity: str, ds: DataSource) -> None:
        key = (kind, identity)
        if key not in seen:
            seen[key] = ds

    for m in _LIB_CONNECT.finditer(script):
        name = m.group(1)
        _add(
            "lib",
            name,
            DataSource(kind="lib", lib_name=name, line_in_script=_line_of(script, m.start())),
        )

    for m in _ODBC_CONNECT.finditer(script):
        conn = m.group(1).strip()
        _add(
            "odbc",
            conn,
            DataSource(
                kind="odbc",
                connection_string=conn,
                line_in_script=_line_of(script, m.start()),
            ),
        )

    for m in _OLEDB_CONNECT.finditer(script):
        conn = m.group(1).strip().strip("'\"")
        _add(
            "oledb",
            conn,
            DataSource(
                kind="oledb",
                connection_string=conn,
                line_in_script=_line_of(script, m.start()),
            ),
        )

    for m in _FILE_LOAD.finditer(script):
        path = m.group(1) or m.group(2)
        if not path:
            continue
        _add(
            "file",
            path,
            DataSource(
                kind="file", file_path=path, line_in_script=_line_of(script, m.start())
            ),
        )

    # Preserve insertion order (which is by character offset = line order).
    return list(seen.values())
