"""Pydantic models — wire types for MCP tool responses.

Schemas mirror spec §4.3. All models are forward-compatible: new fields can
be added without bumping the major version, but renaming or removing fields
is a breaking change.

Phase 1 covers the metadata-only tools (``list_files``, ``list_tables``,
``get_script``, ``get_variables``, ``get_sheets``, ``get_data_sources``,
``reload``, ``search``); ``QueryResult``, ``TableDetail``, ``ExportResult``
land in Phase 2 once the data-extraction layer is wired in.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _StrictModel(BaseModel):
    """Common base: forbid silent extra fields, freeze post-construct.

    ``protected_namespaces=()`` clears pydantic's default ``model_*`` and
    ``schema`` reservations so we can use ``schema`` as a wire field name —
    the spec contract requires it (``TableSummary.schema``, ``SearchHit.schema``).
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, protected_namespaces=()
    )


# ---- File / table index ----------------------------------------------------


class FileIndex(_StrictModel):
    """One entry per QVW visible to the server (spec §4.3)."""

    path: str
    basename: str
    schema_name: str
    size_bytes: int
    mtime: str
    status: Literal["not_parsed", "cached", "parse_failed"]
    error: str | None = None
    has_prj: bool
    is_watched: bool
    in_qvw_dir: bool


class TableSummary(_StrictModel):
    """One per table; ``row_count`` is ``None`` until Phase 2 data parsing."""

    qvw: str
    schema: str  # type: ignore[assignment]  # see _StrictModel docstring
    table_name: str
    field_count: int
    row_count: int | None = None
    is_synthetic: bool = False
    parse_status: Literal["ok", "pending", "parse_failed"] = "pending"
    error: str | None = None


# ---- Script / variables / sheets / sources ---------------------------------


class ScriptBundle(_StrictModel):
    qvw: str
    script: str
    script_encoding: str
    source: Literal["prj", "binary"]
    line_count: int
    decode_replacements: int = 0


class Variable(_StrictModel):
    name: str
    expression: str
    is_reserved: bool = False
    comment: str | None = None


class VariablesBundle(_StrictModel):
    qvw: str
    variables: dict[str, Variable]


class SheetObject(_StrictModel):
    id: str
    type: Literal["chart", "table", "text", "button", "input", "other"]
    caption: str | None = None
    expressions: list[str] = []
    dimensions: list[str] = []


class Sheet(_StrictModel):
    id: str
    title: str
    order: int
    objects: list[SheetObject] = []


class DataSource(_StrictModel):
    kind: Literal["odbc", "oledb", "lib", "file", "inline", "rest"]
    connection_string: str | None = None
    lib_name: str | None = None
    file_path: str | None = None
    referenced_in_tables: list[str] = []
    line_in_script: int = 0


# ---- Search ----------------------------------------------------------------


class SearchHit(_StrictModel):
    qvw: str
    schema: str  # type: ignore[assignment]  # see _StrictModel docstring
    scope: Literal["field", "table", "script", "variable"]
    table_name: str | None = None
    field_name: str | None = None
    variable_name: str | None = None
    script_line: int | None = None
    excerpt: str


class SkippedQvw(_StrictModel):
    qvw: str
    reason: Literal["not_parsed", "parse_failed"]
    hint: str


class SearchResult(_StrictModel):
    matches: list[SearchHit]
    scanned_qvws: list[str]
    skipped_qvws: list[SkippedQvw] = []
    elapsed_ms: int
    not_implemented_scopes: list[str] = []
    """Requested scopes that returned no hits because the decoder is not in
    this release (e.g. ``fields``/``tables`` in Phase 1) — distinguishes
    "not yet supported" from "supported but zero matches"."""


# ---- Reload ---------------------------------------------------------------


class ReloadResult(_StrictModel):
    invalidated: list[str]


# ---- Errors ---------------------------------------------------------------


ErrorCategory = Literal["config", "input", "data", "resource", "unsupported"]


class ErrorEnvelope(_StrictModel):
    """Shared error shape returned by every tool on failure (spec §4.3)."""

    error_code: str
    category: ErrorCategory
    message: str
    hint: str | None = None
    details: dict[str, Any] | None = None
