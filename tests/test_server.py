"""Integration tests for MCP server tools against the synthetic fixture."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

# Point QVW_DIR at the fixtures directory so load_config() succeeds
FIXTURES_DIR = str(Path(__file__).parent / "fixtures")


@pytest.fixture(autouse=True, scope="module")
def patch_qvw_dir():
    """Patch the server's config to use the fixtures directory."""
    with patch.dict(os.environ, {"QVW_DIR": FIXTURES_DIR}):
        # Re-import server with patched env so _cfg is rebuilt
        import importlib
        import mcp_qlikview.server as srv
        importlib.reload(srv)
        yield srv


# ─── list_files ──────────────────────────────────────────────

class TestListFiles:
    def test_returns_list(self, patch_qvw_dir):
        result = patch_qvw_dir.list_files()
        assert isinstance(result, list)

    def test_synthetic_found(self, patch_qvw_dir):
        result = patch_qvw_dir.list_files()
        names = [f["basename"] for f in result]
        assert "synthetic_minimal.qvw" in names

    def test_record_shape(self, patch_qvw_dir):
        result = patch_qvw_dir.list_files()
        rec = next(r for r in result if r["basename"] == "synthetic_minimal.qvw")
        assert "path" in rec
        assert "size_bytes" in rec
        assert "mtime" in rec
        assert "status" in rec
        assert "schema_name" in rec
        assert rec["status"] in ("not_parsed", "cached")


# ─── list_tables ─────────────────────────────────────────────

class TestListTables:
    @pytest.mark.asyncio
    async def test_returns_list(self, patch_qvw_dir):
        result = await patch_qvw_dir.list_tables("synthetic_minimal.qvw")
        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_orders_table_present(self, patch_qvw_dir):
        result = await patch_qvw_dir.list_tables("synthetic_minimal")
        names = [r["table_name"] for r in result]
        assert "Orders" in names

    @pytest.mark.asyncio
    async def test_orders_has_fields(self, patch_qvw_dir):
        result = await patch_qvw_dir.list_tables("synthetic_minimal")
        orders = next(r for r in result if r["table_name"] == "Orders")
        assert orders["field_count"] == 3
        assert orders["parse_status"] == "ok"

    @pytest.mark.asyncio
    async def test_schema_name_format(self, patch_qvw_dir):
        result = await patch_qvw_dir.list_tables("synthetic_minimal")
        assert result[0]["schema_name"] == "synthetic_minimal"

    @pytest.mark.asyncio
    async def test_file_not_found(self, patch_qvw_dir):
        with pytest.raises(FileNotFoundError):
            await patch_qvw_dir.list_tables("nonexistent.qvw")


# ─── get_script ──────────────────────────────────────────────

class TestGetScript:
    @pytest.mark.asyncio
    async def test_returns_dict(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_script("synthetic_minimal")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_has_load(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_script("synthetic_minimal")
        assert "LOAD" in result["script"].upper()

    @pytest.mark.asyncio
    async def test_source_binary(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_script("synthetic_minimal")
        assert result["source"] == "binary"

    @pytest.mark.asyncio
    async def test_line_count(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_script("synthetic_minimal")
        assert result["line_count"] > 0


# ─── get_variables ───────────────────────────────────────────

class TestGetVariables:
    @pytest.mark.asyncio
    async def test_user_vars_returned(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_variables("synthetic_minimal")
        assert "vVersion" in result["variables"]
        assert "vEnv" in result["variables"]

    @pytest.mark.asyncio
    async def test_reserved_excluded_by_default(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_variables("synthetic_minimal")
        # ThousandSep is a reserved variable — must not appear
        assert "ThousandSep" not in result["variables"]

    @pytest.mark.asyncio
    async def test_reserved_included_when_requested(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_variables(
            "synthetic_minimal", include_reserved=True
        )
        assert "ThousandSep" in result["variables"]

    @pytest.mark.asyncio
    async def test_var_expression(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_variables("synthetic_minimal")
        assert result["variables"]["vVersion"]["expression"] == "'1.0'"


# ─── get_data_sources ────────────────────────────────────────

class TestGetDataSources:
    @pytest.mark.asyncio
    async def test_returns_list(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_data_sources("synthetic_minimal")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_file_source_present(self, patch_qvw_dir):
        result = await patch_qvw_dir.get_data_sources("synthetic_minimal")
        file_srcs = [s for s in result if s["kind"] == "file"]
        assert len(file_srcs) == 1
        assert "dummy.qvd" in file_srcs[0]["file_path"]


# ─── get_sheets ──────────────────────────────────────────────

class TestGetSheets:
    @pytest.mark.asyncio
    async def test_returns_empty_without_prj(self, patch_qvw_dir):
        # Synthetic fixture has no -prj folder → empty list
        result = await patch_qvw_dir.get_sheets("synthetic_minimal")
        assert result == []


# ─── reload ──────────────────────────────────────────────────

class TestReload:
    @pytest.mark.asyncio
    async def test_reload_single(self, patch_qvw_dir):
        # Parse to fill cache, then reload
        await patch_qvw_dir.list_tables("synthetic_minimal")
        result = await patch_qvw_dir.reload("synthetic_minimal")
        assert isinstance(result["invalidated"], list)

    @pytest.mark.asyncio
    async def test_reload_all(self, patch_qvw_dir):
        await patch_qvw_dir.list_tables("synthetic_minimal")
        result = await patch_qvw_dir.reload()
        assert isinstance(result["invalidated"], list)

    @pytest.mark.asyncio
    async def test_reload_not_cached_is_empty(self, patch_qvw_dir):
        # Reload all first, then reload again — nothing cached
        await patch_qvw_dir.reload()
        result = await patch_qvw_dir.reload()
        assert result["invalidated"] == []


# ─── Reference tests (need MCP_QVW_TEST_FIXTURES_DIR) ────────

@pytest.mark.reference
class TestReferenceFiles:
    """Tests that run only when real QVW files are available."""

    @pytest.fixture(autouse=True)
    def use_real_dir(self, patch_qvw_dir):
        from tests.conftest import REFERENCE_DIR
        import importlib
        with patch.dict(os.environ, {"QVW_DIR": str(REFERENCE_DIR)}):
            importlib.reload(patch_qvw_dir)
            yield patch_qvw_dir
            importlib.reload(patch_qvw_dir)  # restore fixture dir

    @pytest.mark.asyncio
    async def test_monitoring_tables(self, use_real_dir):
        result = await use_real_dir.list_tables("Monitoring")
        assert len(result) >= 5

    @pytest.mark.asyncio
    async def test_ltv_has_preload_table(self, use_real_dir):
        result = await use_real_dir.list_tables("LTV_analisys")
        names = [r["table_name"] for r in result]
        assert "preloadDataLTV" in names

    @pytest.mark.asyncio
    async def test_ltv_preload_has_fields(self, use_real_dir):
        result = await use_real_dir.list_tables("LTV_analisys")
        preload = next(r for r in result if r["table_name"] == "preloadDataLTV")
        assert preload["field_count"] > 10
