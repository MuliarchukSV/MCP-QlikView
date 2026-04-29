"""
Data source extractor — regex-based scan of load script text.
Extracts LIB CONNECT, ODBC, OLEDB, file paths, inline, REST sources.
"""

from __future__ import annotations

import re
from mcp_qlikview.models import DataSource

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("lib",   re.compile(r"LIB\s+CONNECT\s+TO\s+'([^']+)'", re.IGNORECASE)),
    ("odbc",  re.compile(r"ODBC\s+CONNECT\s+TO\s+'([^']+)'", re.IGNORECASE)),
    ("oledb", re.compile(r"OLEDB\s+CONNECT\s+TO\s+'([^']+)'", re.IGNORECASE)),
    ("file",  re.compile(
        r"FROM\s+\[?([^\]\s;\'\"]+\.(?:csv|xlsx?|txt|qvd|qvw))\]?",
        re.IGNORECASE,
    )),
    ("rest",  re.compile(r"(https?://[^\s\'\"\]]+)", re.IGNORECASE)),
]

_INLINE_RE = re.compile(r"\bINLINE\b", re.IGNORECASE)


def extract_sources(script: str) -> list[DataSource]:
    """Extract all data sources referenced in the load script."""
    sources: list[DataSource] = []
    lines = script.splitlines()

    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(script):
            line_no = script[: m.start()].count("\n") + 1
            value = m.group(1).strip()
            if kind == "lib":
                src = DataSource(kind="lib", lib_name=value, line_in_script=line_no)
            elif kind in ("odbc", "oledb"):
                src = DataSource(
                    kind=kind, connection_string=value, line_in_script=line_no
                )
            elif kind == "file":
                src = DataSource(kind="file", file_path=value, line_in_script=line_no)
            else:  # rest
                src = DataSource(
                    kind="rest", connection_string=value, line_in_script=line_no
                )
            sources.append(src)

    # Inline loads
    for m in _INLINE_RE.finditer(script):
        line_no = script[: m.start()].count("\n") + 1
        sources.append(DataSource(kind="inline", line_in_script=line_no))

    return sources
