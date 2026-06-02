"""Tests for parser.sources — regex-based data-source extraction from load scripts."""

from __future__ import annotations

from mcp_qlikview.parser.sources import extract_sources


def test_lib_connect_to() -> None:
    script = "LIB CONNECT TO 'PROD_DB';\nLOAD * FROM data;"
    sources = extract_sources(script)
    assert any(s.kind == "lib" and s.lib_name == "PROD_DB" for s in sources)


def test_odbc_connect_to() -> None:
    script = "ODBC CONNECT TO MyDSN (Persist Security Info=False);\n"
    sources = extract_sources(script)
    odbc = [s for s in sources if s.kind == "odbc"]
    assert odbc
    assert "MyDSN" in (odbc[0].connection_string or "")


def test_oledb_connect_to() -> None:
    script = "OLEDB CONNECT TO 'Provider=SQLOLEDB.1;Data Source=server';\n"
    sources = extract_sources(script)
    oledb = [s for s in sources if s.kind == "oledb"]
    assert oledb
    assert "SQLOLEDB" in (oledb[0].connection_string or "")


def test_file_load_with_brackets() -> None:
    script = "LOAD * FROM [C:\\Data\\file.csv];\n"
    sources = extract_sources(script)
    files = [s for s in sources if s.kind == "file"]
    assert any("file.csv" in (s.file_path or "") for s in files)


def test_file_load_with_quotes() -> None:
    script = "LOAD * FROM 'data.xlsx' (ooxml, embedded labels);\n"
    sources = extract_sources(script)
    files = [s for s in sources if s.kind == "file"]
    assert any("data.xlsx" in (s.file_path or "") for s in files)


def test_records_line_numbers() -> None:
    script = "// header\nLIB CONNECT TO 'X';\n\nLOAD * FROM 'y.csv';"
    sources = extract_sources(script)
    by_kind = {s.kind: s for s in sources}
    assert by_kind["lib"].line_in_script == 2
    assert by_kind["file"].line_in_script == 4


def test_returns_empty_for_script_without_sources() -> None:
    script = "// nothing here\nSET vYear=2024;"
    assert extract_sources(script) == []


def test_dedupes_repeated_lib_references() -> None:
    # Same LIB used multiple times should produce one DataSource.
    script = "LIB CONNECT TO 'DB';\nLOAD * FROM table1;\nLIB CONNECT TO 'DB';\n"
    sources = extract_sources(script)
    libs = [s for s in sources if s.kind == "lib"]
    assert len(libs) == 1


def test_odbc_password_is_masked() -> None:
    # Connection strings routinely embed credentials; they must never reach
    # the model context or client logs verbatim (review fix #4).
    script = "ODBC CONNECT TO MyDSN (UID=admin;PWD=s3cr3t;Database=sales);\n"
    sources = extract_sources(script)
    conn = next(s.connection_string or "" for s in sources if s.kind == "odbc")
    assert "s3cr3t" not in conn
    assert "PWD=***" in conn
    assert "UID=admin" in conn  # non-secret keys preserved


def test_oledb_password_variants_are_masked() -> None:
    script = (
        "OLEDB CONNECT TO "
        "'Provider=SQLOLEDB;Data Source=srv;Password=topsecret;User ID=sa';\n"
    )
    sources = extract_sources(script)
    conn = next(s.connection_string or "" for s in sources if s.kind == "oledb")
    assert "topsecret" not in conn
    assert "Password=***" in conn
    assert "Data Source=srv" in conn
