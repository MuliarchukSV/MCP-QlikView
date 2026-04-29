from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class FileIndex(BaseModel):
    path: str
    basename: str
    schema_name: str
    size_bytes: int
    mtime: str
    status: Literal["not_parsed", "cached", "parse_failed"]
    error: str | None = None
    has_prj: bool


class TableSummary(BaseModel):
    qvw: str
    schema_name: str
    table_name: str
    field_count: int
    row_count: int | None = None
    is_synthetic: bool = False
    parse_status: Literal["ok", "pending", "parse_failed"] = "pending"
    error: str | None = None


class FieldDescriptor(BaseModel):
    name: str
    duckdb_type: str = "VARCHAR"
    qlik_original_type: str = "STRING"
    nullable: bool = True
    distinct_count: int | None = None
    sample_values: list[Any] = []


class TableDetail(BaseModel):
    qvw: str
    schema_name: str
    table_name: str
    row_count: int
    is_view: bool = False
    synthetic_key_fields: list[str] = []
    fields: list[FieldDescriptor] = []


class ScriptBundle(BaseModel):
    qvw: str
    script: str
    source: Literal["prj", "binary"]
    line_count: int


class Variable(BaseModel):
    name: str
    expression: str
    is_reserved: bool = False
    comment: str | None = None


class VariablesBundle(BaseModel):
    qvw: str
    variables: dict[str, Variable] = {}


class SheetObject(BaseModel):
    id: str
    type: Literal["chart", "table", "text", "button", "input", "other"] = "other"
    caption: str | None = None
    expressions: list[str] = []
    dimensions: list[str] = []


class Sheet(BaseModel):
    id: str
    title: str
    order: int = 0
    objects: list[SheetObject] = []


class DataSource(BaseModel):
    kind: Literal["odbc", "oledb", "lib", "file", "inline", "rest"]
    connection_string: str | None = None
    lib_name: str | None = None
    file_path: str | None = None
    referenced_in_tables: list[str] = []
    line_in_script: int = 0


class ReloadResult(BaseModel):
    invalidated: list[str] = []


class ColumnMeta(BaseModel):
    name: str
    duckdb_type: str


class QueryResult(BaseModel):
    columns: list[ColumnMeta]
    rows: list[list[Any]]
    row_count: int
    total_count: int | None = None
    truncated: bool = False
    elapsed_ms: int


class ExportResult(BaseModel):
    path: str
    format: Literal["parquet", "csv", "jsonl"]
    row_count: int
    bytes_written: int
    elapsed_ms: int


class ErrorEnvelope(BaseModel):
    error_code: Literal[
        "file_not_found",
        "qvw_dir_missing",
        "qvw_dir_unreadable",
        "qvw_too_large",
        "malformed_qvw",
        "encrypted_unsupported",
        "parse_failed",
        "sql_error",
        "table_not_found",
        "row_limit_exceeded",
        "internal",
    ]
    message: str
    hint: str | None = None
    details: dict[str, Any] | None = None
