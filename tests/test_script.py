"""Unit tests for script, schema, variables, and sources extractors."""

from __future__ import annotations

import pytest

from mcp_qlikview.parser.container import QvwContainer
from mcp_qlikview.parser.schema import (
    _dominant_suffix,
    _is_field_list_block,
    _match_table,
    _table_names_from_script,
    extract_schema,
)
from mcp_qlikview.parser.script import extract_script
from mcp_qlikview.parser.sources import extract_sources
from mcp_qlikview.parser.variables import extract_variables


# ─── Script extractor ────────────────────────────────────────

class TestExtractScript:
    def test_source_is_binary(self, synthetic_container: QvwContainer):
        bundle = extract_script(synthetic_container)
        assert bundle.source == "binary"

    def test_has_load(self, synthetic_container: QvwContainer):
        bundle = extract_script(synthetic_container)
        assert "LOAD" in bundle.script.upper()

    def test_line_count_positive(self, synthetic_container: QvwContainer):
        bundle = extract_script(synthetic_container)
        assert bundle.line_count > 0

    def test_tab_marker_present(self, synthetic_container: QvwContainer):
        bundle = extract_script(synthetic_container)
        assert "///$tab" in bundle.script

    def test_qvw_name(self, synthetic_container: QvwContainer):
        bundle = extract_script(synthetic_container)
        assert bundle.qvw == "synthetic_minimal"


# ─── Variables extractor ─────────────────────────────────────

class TestExtractVariables:
    def test_user_vars_found(self, synthetic_container: QvwContainer):
        vb = extract_variables(synthetic_container)
        user = {k: v for k, v in vb.variables.items() if not v.is_reserved}
        assert "vVersion" in user
        assert "vEnv" in user

    def test_var_values(self, synthetic_container: QvwContainer):
        vb = extract_variables(synthetic_container)
        assert vb.variables["vVersion"].expression == "'1.0'"
        assert vb.variables["vEnv"].expression == "'test'"

    def test_reserved_vars_marked(self, synthetic_container: QvwContainer):
        vb = extract_variables(synthetic_container)
        reserved = {k for k, v in vb.variables.items() if v.is_reserved}
        # ThousandSep and DecimalSep are in the synthetic script
        assert "ThousandSep" in reserved or "DecimalSep" in reserved


# ─── Sources extractor ───────────────────────────────────────

class TestExtractSources:
    def test_file_source_found(self, synthetic_container: QvwContainer):
        bundle = extract_script(synthetic_container)
        sources = extract_sources(bundle.script)
        file_srcs = [s for s in sources if s.kind == "file"]
        assert len(file_srcs) == 1
        assert "dummy.qvd" in file_srcs[0].file_path

    def test_line_number_positive(self, synthetic_container: QvwContainer):
        bundle = extract_script(synthetic_container)
        sources = extract_sources(bundle.script)
        for s in sources:
            assert s.line_in_script > 0


# ─── Schema extractor ────────────────────────────────────────

class TestExtractSchema:
    def test_tables_found(self, synthetic_container: QvwContainer):
        tables = extract_schema(synthetic_container)
        names = [t.name for t in tables]
        assert "Orders" in names

    def test_orders_has_fields(self, synthetic_container: QvwContainer):
        tables = extract_schema(synthetic_container)
        orders = next(t for t in tables if t.name == "Orders")
        assert len(orders.fields) == 3

    def test_field_raw_names(self, synthetic_container: QvwContainer):
        tables = extract_schema(synthetic_container)
        orders = next(t for t in tables if t.name == "Orders")
        raw_names = [f.raw_name for f in orders.fields]
        assert "OrderId1Orders" in raw_names
        assert "CustomerName2Orders" in raw_names
        assert "Amount3Orders" in raw_names

    def test_no_is_synthetic(self, synthetic_container: QvwContainer):
        tables = extract_schema(synthetic_container)
        orders = next(t for t in tables if t.name == "Orders")
        assert orders.is_synthetic is False


# ─── Schema helpers (unit) ───────────────────────────────────

class TestDominantSuffix:
    def test_qvw_suffix_detected(self):
        names = ["OrderId1Orders", "CustomerName2Orders", "Amount3Orders"]
        assert _dominant_suffix(names) == "Orders"

    def test_no_suffix_returns_none(self):
        names = ["SERVICE_TYPE", "nameService", "PAYMENT_METHOD"]
        assert _dominant_suffix(names) is None

    def test_below_threshold_returns_none(self):
        # Only 1 out of 10 has a suffix — below 25% threshold
        names = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "X1LTV"]
        assert _dominant_suffix(names) is None

    def test_mixed_suffixes_picks_dominant(self):
        # 4 with LTV, 1 with Other → dominant = LTV
        names = ["a1LTV", "b2LTV", "c3LTV", "d4LTV", "e1Other"]
        assert _dominant_suffix(names) == "LTV"


class TestMatchTable:
    def test_exact_match(self):
        assert _match_table("Orders", ["Orders", "OtherTable"]) == "Orders"

    def test_suffix_in_longer_name(self):
        # first match in script order wins
        tables = ["preloadDataLTV", "DataLTV", "filter4LTV"]
        assert _match_table("LTV", tables) == "preloadDataLTV"

    def test_no_match_returns_none(self):
        assert _match_table("XYZ", ["Orders", "preloadDataLTV"]) is None

    def test_case_insensitive(self):
        assert _match_table("ltv", ["preloadDataLTV"]) == "preloadDataLTV"


class TestIsFieldListBlock:
    def test_valid_field_names(self):
        names = ["SERVICE_TYPE", "nameService", "PAYMENT_METHOD", "namePayment"]
        assert _is_field_list_block(names, set()) is True

    def test_space_names_rejected(self):
        names = ["PaxUkraina", "tickets ua", "Luxreisen"]
        assert _is_field_list_block(names, set()) is False

    def test_table_index_rejected(self):
        tables = {"Orders", "Customers", "Products"}
        names = ["Orders", "Customers", "Products"]
        assert _is_field_list_block(names, tables) is False

    def test_too_short_names_rejected(self):
        # All names ≤ 3 chars
        names = ["Q1", "Q2", "Q3", "Q4", "d0", "d1", "d2"]
        assert _is_field_list_block(names, set()) is False

    def test_single_name_rejected(self):
        assert _is_field_list_block(["OnlyOne"], set()) is False

    def test_mostly_non_identifiers_rejected(self):
        # URLs, spaces — < 50% valid identifiers
        names = ["http://example.com", "name with spaces", "another-bad", "GoodName", "AlsoGood"]
        # 2/5 = 40% valid → rejected
        assert _is_field_list_block(names, set()) is False


class TestTableNamesFromScript:
    def test_basic_load(self):
        script = "MyTable:\nLOAD a, b FROM x.qvd;\n"
        assert _table_names_from_script(script) == ["MyTable"]

    def test_multiple_tables_order_preserved(self):
        script = "T1:\nLOAD a FROM x.qvd;\nT2:\nLOAD b FROM y.qvd;\n"
        names = _table_names_from_script(script)
        assert names == ["T1", "T2"]

    def test_bracket_syntax(self):
        script = "[My Table]:\nLOAD a FROM x.qvd;\n"
        names = _table_names_from_script(script)
        assert "My Table" in names

    def test_select_keyword(self):
        script = "DBTable:\nSELECT * FROM schema.table;\n"
        assert _table_names_from_script(script) == ["DBTable"]

    def test_dedup(self):
        script = (
            "Orders:\nLOAD a FROM x.qvd;\n"
            "Orders:\nLOAD b FROM y.qvd;\n"
        )
        assert _table_names_from_script(script).count("Orders") == 1
