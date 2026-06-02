"""MCP server: stdio transport + 8 metadata tools (Phase 1).

Tools shipped here cover the metadata side of spec §4.1. The data-extraction
tools (``query``, ``describe_table``, ``export_table``) and field/table-scope
``search`` arrive in Phase 2 once the symbol/data decoders land.

Errors are surfaced as :class:`ErrorEnvelope` JSON in the tool response with
``isError=True`` so Claude Code displays the structured hint rather than a
generic protocol failure (spec §6.1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ValidationError

from mcp_qlikview import __version__
from mcp_qlikview.config import Config
from mcp_qlikview.index import build_file_index, sanitize_schema_name
from mcp_qlikview.models import (
    DataSource,
    ErrorEnvelope,
    FileIndex,
    ReloadResult,
    ScriptBundle,
    SearchHit,
    SearchResult,
    Sheet,
    SkippedQvw,
    TableSummary,
    VariablesBundle,
)
from mcp_qlikview.parser.container import InvalidQvwError, ZlibBombError
from mcp_qlikview.parser.sources import extract_sources
from mcp_qlikview.store import MetadataStore, ParsedMetadata, QvwTooLargeError

log = logging.getLogger("mcp_qlikview")


# --- ServerState ------------------------------------------------------------


class _ServerState:
    """Shared mutable state held by the running server.

    Either fully initialised (``config`` set) or in degraded mode
    (``config_error`` set). Tool handlers branch on this — degraded mode
    returns a structured ``ErrorEnvelope`` for every call rather than
    aborting the MCP handshake.
    """

    def __init__(self) -> None:
        self.config: Config | None = None
        self.config_error: ErrorEnvelope | None = None
        self.store = MetadataStore()

    def boot(self) -> None:
        try:
            self.config = Config()
            self.config_error = None
            # Wire the file-size guard to the user's config so the store and the
            # ``_resolve_qvw`` pre-flight share one limit (MCP_QVW_MAX_FILE_BYTES)
            # instead of the store silently keeping a hardcoded 2 GiB cap.
            self.store = MetadataStore(max_file_size_bytes=self.config.max_file_bytes)
            log.setLevel(getattr(logging, self.config.log_level.upper(), logging.INFO))
        except ValidationError as exc:
            self.config = None
            self.config_error = ErrorEnvelope(
                error_code="qvw_dir_missing",
                category="config",
                message=str(exc),
                hint="Set QVW_DIR=/path/to/qlik in your Claude Code mcp.json env block.",
            )
        except (PermissionError, OSError) as exc:
            # E.g. parent directory not readable, or QVW_DIR is on a network
            # share that's currently disconnected. Spec §6.1 reserves
            # ``qvw_dir_unreadable`` for this. Stay alive in degraded mode.
            self.config = None
            self.config_error = ErrorEnvelope(
                error_code="qvw_dir_unreadable",
                category="config",
                message=f"Cannot access QVW_DIR: {exc}",
                hint="Check QVW_DIR exists, is a directory, and is readable by the MCP process.",
            )


# --- Helpers ----------------------------------------------------------------


def _resolve_qvw(
    state: _ServerState, qvw: str, *, check_size: bool = True
) -> Path | ErrorEnvelope:
    """Resolve a ``qvw`` argument to an absolute path or :class:`ErrorEnvelope`.

    Accepts a basename (``LTV_analisys``), basename with extension
    (``LTV_analisys.qvw``), or absolute path. Security policy: the default
    posture rejects absolute paths whose realpath escapes ``QVW_DIR`` —
    spec §4.2 mentions arbitrary absolute paths but exposing arbitrary file
    reads on a public OSS server is unacceptable. Users opt in via
    ``MCP_QVW_ALLOW_OUTSIDE_DIR=true``. Path-traversal in basenames
    (``qvw="../etc/passwd"``) is also blocked because the resolved path is
    re-checked against the QVW_DIR prefix.

    A successful resolution also passes the size pre-flight
    (:attr:`Config.max_file_bytes`); oversized files surface as
    ``qvw_too_large`` before any read.
    """
    assert state.config is not None
    qvw_root = state.config.qvw_dir.resolve()

    raw = Path(qvw).expanduser()
    if raw.is_absolute():
        candidates = [raw]
    else:
        candidates = [
            state.config.qvw_dir / qvw,
            state.config.qvw_dir / f"{qvw}.qvw",
        ]

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            real = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not _is_within(real, qvw_root) and not state.config.allow_outside_dir:
            return ErrorEnvelope(
                error_code="unsupported",
                category="unsupported",
                message=(
                    f"Path resolves outside QVW_DIR ({qvw_root}) and "
                    "MCP_QVW_ALLOW_OUTSIDE_DIR is disabled."
                ),
                hint=(
                    "Move the file inside QVW_DIR, pass it by basename, or "
                    "set MCP_QVW_ALLOW_OUTSIDE_DIR=true if you understand "
                    "that this exposes arbitrary host files to the MCP client."
                ),
            )
        if check_size:
            size_check = _check_size(real, state.config.max_file_bytes)
            if size_check is not None:
                return size_check
        return real

    return ErrorEnvelope(
        error_code="file_not_found",
        category="input",
        message=f"QVW '{qvw}' not found in {qvw_root}.",
        hint="Pass a basename (no path) of a file inside QVW_DIR.",
    )


def _is_within(path: Path, parent: Path) -> bool:
    """Return ``True`` iff resolved ``path`` is the same as or below ``parent``."""
    try:
        return path.is_relative_to(parent)
    except ValueError:
        return False


def _check_size(path: Path, max_bytes: int) -> ErrorEnvelope | None:
    """Return ``qvw_too_large`` envelope when ``path`` exceeds ``max_bytes``."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return ErrorEnvelope(
            error_code="qvw_dir_unreadable",
            category="config",
            message=f"Cannot stat {path}: {exc}",
        )
    if size > max_bytes:
        return ErrorEnvelope(
            error_code="qvw_too_large",
            category="data",
            message=(
                f"{path.name} is {size:,} bytes, exceeding the "
                f"{max_bytes:,}-byte limit (MCP_QVW_MAX_FILE_BYTES)."
            ),
            hint="Raise MCP_QVW_MAX_FILE_BYTES or split the QVW.",
        )
    return None


def _model_to_text(model: BaseModel | list[Any] | dict[str, Any]) -> list[types.TextContent]:
    """Serialize ``model`` to JSON in a single :class:`TextContent` block."""
    if isinstance(model, BaseModel):
        text = model.model_dump_json()
    elif isinstance(model, list):
        text = json.dumps(
            [m.model_dump(mode="json") if isinstance(m, BaseModel) else m for m in model],
            ensure_ascii=False,
        )
    else:
        text = json.dumps(model, ensure_ascii=False)
    return [types.TextContent(type="text", text=text)]


def _error_response(env: ErrorEnvelope) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=env.model_dump_json())],
        isError=True,
    )


# --- Tool handlers ----------------------------------------------------------


async def _tool_list_files(state: _ServerState) -> list[FileIndex]:
    assert state.config is not None
    return build_file_index(state.config.qvw_dir)


async def _tool_list_tables(
    state: _ServerState, qvw: str | None
) -> list[TableSummary] | ErrorEnvelope:
    assert state.config is not None
    files = build_file_index(state.config.qvw_dir)
    targets = files if qvw is None else [f for f in files if f.basename == qvw]
    if qvw is not None and not targets:
        # Try absolute-path resolution before failing.
        resolved = _resolve_qvw(state, qvw)
        if isinstance(resolved, ErrorEnvelope):
            return resolved
        targets = [_synthetic_file_index(resolved, state.config.qvw_dir)]

    out: list[TableSummary] = []
    for fi in targets:
        # Size pre-flight before parse — the index-derived path skips
        # ``_resolve_qvw``, so without this an oversized file (or a full
        # all-files scan of huge QVWs) would parse unchecked.
        size_err = _check_size(Path(fi.path), state.config.max_file_bytes)
        meta_or_err = size_err or await _ensure_parsed_async(state, Path(fi.path))
        if isinstance(meta_or_err, ErrorEnvelope):
            out.append(
                TableSummary(
                    qvw=fi.basename,
                    schema=fi.schema_name,
                    table_name="<parse-failed>",
                    field_count=0,
                    parse_status="parse_failed",
                    error=meta_or_err.message[:500],
                )
            )
            continue
        for table_name in meta_or_err.table_names:
            out.append(
                TableSummary(
                    qvw=fi.basename,
                    schema=fi.schema_name,
                    table_name=table_name,
                    # Phase 1 cannot decode per-table field lists yet; the
                    # global dictionary size would over-report. ``0`` with
                    # ``parse_status="pending"`` signals "value not yet
                    # known" per spec §4.3 conventions.
                    field_count=0,
                    parse_status="pending",
                )
            )
    return out


def _synthetic_file_index(path: Path, qvw_dir: Path) -> FileIndex:
    """Build a :class:`FileIndex` for a path outside the watched directory.

    Used when ``qvw=`` resolves to an absolute file outside ``QVW_DIR``
    (only possible when ``MCP_QVW_ALLOW_OUTSIDE_DIR=true``). All FileIndex
    fields stay spec-compliant: schema_name is sanitised, mtime is ISO-8601,
    has_prj is checked rather than hardcoded false.
    """
    stat = path.stat()
    prj_dir = path.with_name(f"{path.stem}-prj")
    return FileIndex(
        path=str(path),
        basename=path.stem,
        schema_name=sanitize_schema_name(path.stem),
        size_bytes=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        status="not_parsed",
        has_prj=prj_dir.is_dir(),
        is_watched=False,
        in_qvw_dir=_is_within(path, qvw_dir.resolve()),
    )


def _parse_error(path: Path, exc: BaseException) -> ErrorEnvelope:
    """Map parse-time exceptions to the spec's error-code taxonomy (§4.3)."""
    if isinstance(exc, QvwTooLargeError):
        return ErrorEnvelope(
            error_code="qvw_too_large",
            category="data",
            message=str(exc),
            hint="raise MCP_QVW_MAX_FILE_BYTES or split the QVW",
        )
    if isinstance(exc, ZlibBombError):
        return ErrorEnvelope(
            error_code="malformed_qvw",
            category="data",
            message=f"Suspicious zlib expansion in {path.name}: {exc}",
            hint="The file's compression ratio looks adversarial; not parsing.",
        )
    if isinstance(exc, InvalidQvwError):
        return ErrorEnvelope(
            error_code="malformed_qvw",
            category="data",
            message=f"Failed to parse {path.name}: {exc}",
            hint="Verify the file is a valid QlikView .qvw (not encrypted, not section-access).",
        )
    return ErrorEnvelope(
        error_code="parse_failed",
        category="data",
        message=f"Failed to parse {path.name}: {exc}",
    )


async def _ensure_parsed_async(
    state: _ServerState, path: Path
) -> ParsedMetadata | ErrorEnvelope:
    """Off-load the CPU-bound parse so the asyncio event loop stays responsive.

    Container parsing on the 141 MB reference takes ~135 s; running it
    inline would block ``ping``, ``list_tools``, and every other concurrent
    MCP request for that entire duration.
    """
    try:
        return await asyncio.to_thread(state.store.ensure_parsed, path)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as exc:
        return _parse_error(path, exc)


async def _tool_get_script(state: _ServerState, qvw: str) -> ScriptBundle | ErrorEnvelope:
    path = _resolve_qvw(state, qvw)
    if isinstance(path, ErrorEnvelope):
        return path
    meta = await _ensure_parsed_async(state, path)
    if isinstance(meta, ErrorEnvelope):
        return meta
    return ScriptBundle(
        qvw=path.stem,
        script=meta.script,
        script_encoding=meta.script_encoding,
        source="prj" if meta.script_source == "prj" else "binary",
        line_count=meta.script.count("\n") + 1,
        decode_replacements=meta.script_decode_replacements,
    )


async def _tool_get_variables(
    state: _ServerState, qvw: str
) -> VariablesBundle | ErrorEnvelope:
    """Not implemented in v0.1.0 — the variable-block decoder lands in Phase 1.5.

    Returns a structured ``unsupported`` error rather than an empty mapping: a
    silent ``{}`` would let the caller wrongly conclude the QVW has no
    variables. The path is still resolved first so a bad ``qvw`` argument
    reports the more specific resolution error.
    """
    path = _resolve_qvw(state, qvw)
    if isinstance(path, ErrorEnvelope):
        return path
    return ErrorEnvelope(
        error_code="unsupported",
        category="unsupported",
        message="get_variables is not implemented in v0.1.0.",
        hint="The variable decoder ships in Phase 1.5; track it in LIMITATIONS.md.",
    )


async def _tool_get_sheets(state: _ServerState, qvw: str) -> list[Sheet] | ErrorEnvelope:
    """Not implemented in v0.1.0 — the sheet decoder is post-Phase-2 work.

    Returns ``unsupported`` rather than ``[]`` for the same reason as
    :func:`_tool_get_variables`: an empty list reads as "no sheets" instead of
    "not decoded yet".
    """
    path = _resolve_qvw(state, qvw)
    if isinstance(path, ErrorEnvelope):
        return path
    return ErrorEnvelope(
        error_code="unsupported",
        category="unsupported",
        message="get_sheets is not implemented in v0.1.0.",
        hint="The sheet decoder ships after Phase 2; track it in LIMITATIONS.md.",
    )


async def _tool_get_data_sources(
    state: _ServerState, qvw: str
) -> list[DataSource] | ErrorEnvelope:
    path = _resolve_qvw(state, qvw)
    if isinstance(path, ErrorEnvelope):
        return path
    meta = await _ensure_parsed_async(state, path)
    if isinstance(meta, ErrorEnvelope):
        return meta
    return extract_sources(meta.script)


async def _tool_reload(state: _ServerState, qvw: str | None) -> ReloadResult:
    if qvw is None:
        invalidated = state.store.invalidate(None)
    else:
        # Skip the size pre-flight: a file that grew past the limit while
        # cached must still be invalidatable. ``reload`` of a non-existent
        # file remains a no-op rather than an error.
        path = _resolve_qvw(state, qvw, check_size=False)
        invalidated = (
            [] if isinstance(path, ErrorEnvelope) else state.store.invalidate(path)
        )
    return ReloadResult(invalidated=invalidated)


_SPEC_SEARCH_SCOPES: frozenset[str] = frozenset(
    {"fields", "tables", "scripts", "variables"}
)
"""All four scopes recognised by spec §4.1 ``search``."""

_PHASE1_SEARCH_SCOPES: frozenset[str] = frozenset({"scripts"})
"""Scopes that actually return hits in Phase 1. ``fields`` and ``tables`` need
the Phase 2 data decoder; ``variables`` needs the Phase 1.5 XML/variable-block
decoder. All three are reported via ``SearchResult.not_implemented_scopes``."""


async def _tool_search(
    state: _ServerState,
    pattern: str,
    scope: list[str] | None,
    qvw: str | None,
) -> SearchResult | ErrorEnvelope:
    """Search across QVW metadata.

    Phase 1 honours the ``scripts`` scope only; ``fields``, ``tables`` and
    ``variables`` return zero hits and are listed in
    ``SearchResult.not_implemented_scopes``. Per spec §4.1, default scope is
    all four.
    """
    assert state.config is not None
    started = time.monotonic()

    requested = set(scope) if scope else set(_SPEC_SEARCH_SCOPES)
    active = requested & _PHASE1_SEARCH_SCOPES
    not_implemented = sorted(requested - _PHASE1_SEARCH_SCOPES)

    pattern_or_err = _compile_pattern(pattern)
    if isinstance(pattern_or_err, ErrorEnvelope):
        return pattern_or_err
    matcher = pattern_or_err

    files = build_file_index(state.config.qvw_dir)
    targets = files if qvw is None else [f for f in files if f.basename == qvw]

    matches: list[SearchHit] = []
    scanned: list[str] = []
    skipped: list[SkippedQvw] = []

    for fi in targets:
        size_err = _check_size(Path(fi.path), state.config.max_file_bytes)
        meta_or_err = size_err or await _ensure_parsed_async(state, Path(fi.path))
        if isinstance(meta_or_err, ErrorEnvelope):
            skipped.append(
                SkippedQvw(
                    qvw=fi.basename,
                    reason="parse_failed",
                    hint=meta_or_err.message[:200],
                )
            )
            continue
        meta = meta_or_err
        scanned.append(fi.basename)
        if "scripts" in active:
            # Run the (potentially catastrophic-backtracking) matcher off the
            # event loop so one pathological regex can't freeze every other
            # concurrent MCP request.
            hits = await asyncio.to_thread(
                _match_script_lines, meta.script, matcher, fi.basename, fi.schema_name
            )
            matches.extend(hits)

    return SearchResult(
        matches=matches,
        scanned_qvws=scanned,
        skipped_qvws=skipped,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        not_implemented_scopes=not_implemented,
    )


def _match_script_lines(
    script: str, matcher: Callable[[str], bool], basename: str, schema: str
) -> list[SearchHit]:
    """Return one :class:`SearchHit` per script line matching ``matcher``.

    Lines are split on ``\\n`` only (matching ``ScriptBundle.line_count``'s
    ``count("\\n") + 1``) so reported ``script_line`` numbers line up with a
    user's editor rather than diverging on ``\\r``/``\\v``/U+2028.
    """
    out: list[SearchHit] = []
    for line_no, line in enumerate(script.split("\n"), start=1):
        if matcher(line):
            out.append(
                SearchHit(
                    qvw=basename,
                    schema=schema,
                    scope="script",
                    script_line=line_no,
                    excerpt=line.strip()[:200],
                )
            )
    return out


_MAX_PATTERN_LENGTH: int = 1024
"""Hard cap on user-supplied pattern length to limit ReDoS exposure."""


def _compile_pattern(pattern: str) -> Callable[[str], bool] | ErrorEnvelope:
    """Compile a user-supplied search pattern.

    Supports ``/pattern/`` and ``/pattern/flags`` regex syntax; ``flags``
    accepts any subset of ``i`` (ignorecase), ``m`` (multiline), ``s``
    (dotall), and ``x`` (verbose). Substring fallback is case-insensitive.
    Patterns longer than :data:`_MAX_PATTERN_LENGTH` are rejected to limit
    catastrophic backtracking on huge scripts (the match itself also runs off
    the event loop — see :func:`_tool_search`).
    """
    if len(pattern) > _MAX_PATTERN_LENGTH:
        return ErrorEnvelope(
            error_code="input",
            category="input",
            message=(
                f"pattern length {len(pattern)} exceeds maximum "
                f"{_MAX_PATTERN_LENGTH} characters"
            ),
        )
    regex_match = re.fullmatch(r"/(.+)/([imsx]*)", pattern, re.DOTALL)
    if regex_match is not None:
        body, flag_chars = regex_match.group(1), regex_match.group(2)
        flags = 0
        for ch in flag_chars:
            flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}[ch]
        try:
            compiled = re.compile(body, flags)
        except re.error as exc:
            return ErrorEnvelope(
                error_code="input",
                category="input",
                message=f"invalid regex: {exc}",
            )
        return lambda s: compiled.search(s) is not None
    needle = pattern.lower()
    return lambda s: needle in s.lower()


# --- Tool registry ----------------------------------------------------------


_TOOL_DEFS: list[types.Tool] = [
    types.Tool(
        name="list_files",
        description="List QVW files visible to the server. Returns one FileIndex per file.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="list_tables",
        description="List tables for one or all QVWs (metadata only; row counts arrive in v0.2).",
        inputSchema={
            "type": "object",
            "properties": {"qvw": {"type": "string", "description": "Basename or absolute path. Omit to scan all."}},
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="get_script",
        description="Return the full QlikView load script for the given QVW.",
        inputSchema={
            "type": "object",
            "properties": {"qvw": {"type": "string"}},
            "required": ["qvw"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="get_variables",
        description="Return user-defined Qlik variables. Not implemented in v0.1.0 (returns an 'unsupported' error); ships in Phase 1.5.",
        inputSchema={
            "type": "object",
            "properties": {"qvw": {"type": "string"}},
            "required": ["qvw"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="get_sheets",
        description="Return Qlik sheet definitions. Not implemented in v0.1.0 (returns an 'unsupported' error); ships after Phase 2.",
        inputSchema={
            "type": "object",
            "properties": {"qvw": {"type": "string"}},
            "required": ["qvw"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="get_data_sources",
        description="Extract data-source references (LIB/ODBC/OLEDB/files) from the load script.",
        inputSchema={
            "type": "object",
            "properties": {"qvw": {"type": "string"}},
            "required": ["qvw"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="reload",
        description="Invalidate cached metadata so the next call re-parses the QVW.",
        inputSchema={
            "type": "object",
            "properties": {"qvw": {"type": "string", "description": "Omit to invalidate all."}},
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="search",
        description=(
            "Search across QVW metadata. Phase 1 covers script + variables scopes; "
            "field/table scopes activate in Phase 2."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Substring (case-insensitive) or '/regex/' pattern.",
                },
                "scope": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["fields", "tables", "scripts", "variables"]},
                },
                "qvw": {"type": "string"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
]


def _build_server(state: _ServerState) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("mcp-qlikview", __version__)

    # mcp SDK 1.x decorators are not strictly typed; the resulting functions
    # work correctly at runtime but mypy can't infer their shape.
    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_tools() -> list[types.Tool]:
        return _TOOL_DEFS

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
        args = arguments or {}
        if state.config_error is not None:
            return _error_response(state.config_error)
        try:
            result: Any
            if name == "list_files":
                result = await _tool_list_files(state)
            elif name == "list_tables":
                result = await _tool_list_tables(state, args.get("qvw"))
            elif name == "get_script":
                result = await _tool_get_script(state, args["qvw"])
            elif name == "get_variables":
                result = await _tool_get_variables(state, args["qvw"])
            elif name == "get_sheets":
                result = await _tool_get_sheets(state, args["qvw"])
            elif name == "get_data_sources":
                result = await _tool_get_data_sources(state, args["qvw"])
            elif name == "reload":
                result = await _tool_reload(state, args.get("qvw"))
            elif name == "search":
                result = await _tool_search(
                    state, args["pattern"], args.get("scope"), args.get("qvw")
                )
            else:
                return _error_response(
                    ErrorEnvelope(
                        error_code="internal",
                        category="resource",
                        message=f"Unknown tool: {name}",
                    )
                )
        except KeyError as exc:
            return _error_response(
                ErrorEnvelope(
                    error_code="input",
                    category="input",
                    message=f"Missing required argument: {exc}",
                )
            )
        except OSError as exc:
            # Filesystem race: a file/dir enumerated a moment ago was moved,
            # deleted, or had its permissions changed mid-request. Surface a
            # structured envelope instead of leaking a protocol-level error
            # (server.py module docstring contract).
            return _error_response(
                ErrorEnvelope(
                    error_code="qvw_dir_unreadable",
                    category="config",
                    message=f"Filesystem error while handling '{name}': {exc}",
                    hint="A QVW or its directory may have changed mid-request; retry.",
                )
            )
        if isinstance(result, ErrorEnvelope):
            return _error_response(result)
        return _model_to_text(result)

    return server


async def _async_run() -> None:
    state = _ServerState()
    state.boot()
    server = _build_server(state)
    init_opts = InitializationOptions(
        server_name="mcp-qlikview",
        server_version=__version__,
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_opts)


def run() -> None:
    """Synchronous entrypoint for the ``mcp-qlikview`` console script.

    Logs go to stderr so they never corrupt the stdio MCP protocol on stdout.
    The root level stays INFO here; the ``mcp_qlikview`` logger is adjusted to
    ``MCP_QVW_LOG_LEVEL`` once config loads in :meth:`_ServerState.boot`.
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(_async_run())
