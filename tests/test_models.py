"""Sanity tests for wire-type models — construction + JSON round-trip."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mcp_qlikview.models import (
    DataSource,
    ErrorEnvelope,
    FileIndex,
    ReloadResult,
    ScriptBundle,
    SearchHit,
    SearchResult,
    Sheet,
    TableSummary,
    Variable,
    VariablesBundle,
)


def test_file_index_round_trip() -> None:
    fi = FileIndex(
        path="/data/LTV_analisys.qvw",
        basename="LTV_analisys",
        schema_name="LTV_analisys",
        size_bytes=34_600_000,
        mtime="2026-05-07T12:00:00Z",
        status="not_parsed",
        has_prj=False,
        is_watched=True,
        in_qvw_dir=True,
    )
    decoded = FileIndex.model_validate_json(fi.model_dump_json())
    assert decoded == fi


def test_error_envelope_requires_category_and_code() -> None:
    err = ErrorEnvelope(
        error_code="qvw_dir_missing",
        category="config",
        message="QVW_DIR is not set",
        hint="Set QVW_DIR=/path/to/qlik in mcp.json env",
    )
    payload = json.loads(err.model_dump_json())
    assert payload["error_code"] == "qvw_dir_missing"
    assert payload["category"] == "config"


def test_error_envelope_rejects_invalid_category() -> None:
    with pytest.raises(ValidationError):
        ErrorEnvelope(
            error_code="qvw_dir_missing",
            category="not-a-real-category",  # type: ignore[arg-type]
            message="x",
        )


def test_strict_models_forbid_extra_fields() -> None:
    # Forward-compatibility comes from intentional schema bumps, not from
    # silently swallowing unknown fields. Ensure unknowns raise.
    with pytest.raises(ValidationError):
        FileIndex.model_validate(
            {
                "path": "/x.qvw",
                "basename": "x",
                "schema_name": "x",
                "size_bytes": 0,
                "mtime": "2026-01-01T00:00:00Z",
                "status": "not_parsed",
                "has_prj": False,
                "is_watched": True,
                "in_qvw_dir": True,
                "spurious_field": "should not exist",
            }
        )


def test_table_summary_defaults_for_pending_data() -> None:
    ts = TableSummary(qvw="LTV", schema="LTV", table_name="DataLTV", field_count=10)
    assert ts.row_count is None
    assert ts.parse_status == "pending"
    assert ts.is_synthetic is False


def test_script_bundle_with_decode_replacements() -> None:
    sb = ScriptBundle(
        qvw="x",
        script="LOAD * FROM data;",
        script_encoding="utf-8",
        source="binary",
        line_count=1,
    )
    assert sb.decode_replacements == 0


def test_search_result_construction() -> None:
    sr = SearchResult(
        matches=[
            SearchHit(
                qvw="LTV", schema="LTV", scope="field", field_name="idCustomer",
                table_name="DataLTV", excerpt="idCustomer in DataLTV",
            )
        ],
        scanned_qvws=["LTV"],
        elapsed_ms=12,
    )
    assert sr.matches[0].field_name == "idCustomer"


def test_variables_bundle() -> None:
    vb = VariablesBundle(
        qvw="LTV",
        variables={"vYear": Variable(name="vYear", expression="=Year(Today())")},
    )
    assert vb.variables["vYear"].is_reserved is False


def test_sheet_with_no_objects() -> None:
    s = Sheet(id="sheet1", title="Main", order=0)
    assert s.objects == []


def test_data_source_minimal() -> None:
    ds = DataSource(kind="lib")
    assert ds.connection_string is None


def test_reload_result_empty() -> None:
    r = ReloadResult(invalidated=[])
    assert r.invalidated == []
