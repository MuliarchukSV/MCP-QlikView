"""Tests for index — discovering QVW files and building FileIndex entries."""

from __future__ import annotations

from pathlib import Path

from mcp_qlikview.index import build_file_index, sanitize_schema_name


class TestSchemaSanitization:
    """Spec §3.5: SQL-safe basenames + reserved-word avoidance."""

    def test_passthrough_safe_basename(self) -> None:
        assert sanitize_schema_name("LTV_analisys") == "LTV_analisys"

    def test_replaces_dots_with_underscore(self) -> None:
        # ``Order.qvw`` → "Order" basename is fine, but the original spec
        # example uses dotted basenames as a problem case.
        assert sanitize_schema_name("Sales.Q1") == "Sales_Q1"

    def test_replaces_spaces_and_hyphens(self) -> None:
        assert sanitize_schema_name("My Report - 2024") == "My_Report___2024"

    def test_prefixes_when_starts_with_digit(self) -> None:
        assert sanitize_schema_name("2024_Sales") == "_2024_Sales"

    def test_avoids_sql_reserved_word(self) -> None:
        assert sanitize_schema_name("Order") == "Order_qvw"
        assert sanitize_schema_name("Select") == "Select_qvw"


class TestBuildFileIndex:
    def test_finds_qvw_files_in_directory(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.qvw").write_bytes(b"x")
        (tmp_path / "beta.qvw").write_bytes(b"y")
        (tmp_path / "ignore.txt").write_bytes(b"z")
        idx = build_file_index(tmp_path)
        names = sorted(f.basename for f in idx)
        assert names == ["alpha", "beta"]

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        assert build_file_index(tmp_path) == []

    def test_records_size_and_mtime(self, tmp_path: Path) -> None:
        f = tmp_path / "a.qvw"
        f.write_bytes(b"x" * 1024)
        idx = build_file_index(tmp_path)
        assert idx[0].size_bytes == 1024
        # ISO-8601 with timezone marker
        assert idx[0].mtime.endswith("Z") or "+" in idx[0].mtime

    def test_detects_prj_sibling(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.qvw").write_bytes(b"x")
        (tmp_path / "alpha-prj").mkdir()
        (tmp_path / "alpha-prj" / "LoadScript.txt").write_text("LOAD * FROM x;")
        idx = build_file_index(tmp_path)
        assert len(idx) == 1
        assert idx[0].has_prj is True
        assert idx[0].in_qvw_dir is True
        assert idx[0].is_watched is True

    def test_initial_status_is_not_parsed(self, tmp_path: Path) -> None:
        (tmp_path / "x.qvw").write_bytes(b"x")
        idx = build_file_index(tmp_path)
        assert idx[0].status == "not_parsed"

    def test_resolves_collisions_via_suffix(self, tmp_path: Path) -> None:
        # If two basenames sanitize to the same schema name (e.g. "Order"
        # + "Order"), the second gets a numeric suffix.
        (tmp_path / "Order.qvw").write_bytes(b"x")
        # We can't have two files with the same name, so use a case that
        # collides after sanitization.
        (tmp_path / "Order_qvw.qvw").write_bytes(b"y")
        idx = build_file_index(tmp_path)
        schema_names = sorted(f.schema_name for f in idx)
        assert len(schema_names) == 2
        assert len(set(schema_names)) == 2  # distinct
