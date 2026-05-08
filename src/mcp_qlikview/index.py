"""File discovery and FileIndex construction.

Scans ``QVW_DIR`` for ``*.qvw`` files, sanitises basenames into SQL-safe
schema names (spec §3.5), and emits one :class:`FileIndex` per file. No
parsing happens here — that's the store's job. This module is pure
filesystem + naming logic so it can run synchronously and predictably
even when QVW files are corrupt or in flux.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from mcp_qlikview.models import FileIndex

_SQL_RESERVED: frozenset[str] = frozenset(
    {
        # DuckDB reserved keywords that would clash with a schema name.
        # Conservative subset; expanding it later is non-breaking because
        # the suffix is unconditional once a name is matched.
        "select",
        "from",
        "where",
        "order",
        "group",
        "having",
        "join",
        "union",
        "table",
        "view",
        "schema",
        "create",
        "drop",
        "alter",
        "insert",
        "update",
        "delete",
    }
)

_UNSAFE_CHAR = re.compile(r"[^A-Za-z0-9_]")


def sanitize_schema_name(basename: str) -> str:
    """Convert a QVW basename into a DuckDB-safe schema identifier.

    Rules (spec §3.5):

    1. Replace any non ``[A-Za-z0-9_]`` character with ``_``.
    2. If the result starts with a digit, prepend ``_``.
    3. If the result (case-insensitive) collides with a SQL reserved word,
       append ``_qvw``.

    Caller is still responsible for resolving same-name collisions across
    multiple QVW files; that's the index step (see :func:`build_file_index`).
    """
    sanitized = _UNSAFE_CHAR.sub("_", basename)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    if sanitized.lower() in _SQL_RESERVED:
        sanitized = f"{sanitized}_qvw"
    return sanitized


def _iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def build_file_index(qvw_dir: Path) -> list[FileIndex]:
    """Enumerate ``*.qvw`` files in ``qvw_dir`` and return :class:`FileIndex`.

    Schema names are deduplicated by appending ``_2``, ``_3``, ... when two
    sanitised basenames collide. Returns files sorted by basename for stable
    ordering across calls (matters for tests + caching).
    """
    qvw_paths = sorted(qvw_dir.glob("*.qvw"))
    used_schema_names: dict[str, int] = {}
    out: list[FileIndex] = []

    for path in qvw_paths:
        basename = path.stem
        candidate = sanitize_schema_name(basename)
        # Resolve collisions deterministically with a numeric suffix.
        n = used_schema_names.get(candidate, 0) + 1
        used_schema_names[candidate] = n
        schema_name = candidate if n == 1 else f"{candidate}_{n}"

        prj_dir = path.with_name(f"{basename}-prj")
        stat = path.stat()

        out.append(
            FileIndex(
                path=str(path.resolve()),
                basename=basename,
                schema_name=schema_name,
                size_bytes=stat.st_size,
                mtime=_iso_utc(stat.st_mtime),
                status="not_parsed",
                has_prj=prj_dir.is_dir(),
                is_watched=True,
                in_qvw_dir=True,
            )
        )

    return out
