"""CI-runnable handler tests against a hand-built minimal QVW directory.

The golden tests in test_server_handlers.py only run when real reference files
are present (MCP_QVW_TEST_FIXTURES_DIR). These cover the adversarial-review
fixes on every CI run by synthesising a tiny valid QVW (fixes #2, #8, #9, #10,
#16).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from mcp_qlikview.models import ErrorEnvelope, SearchResult, TableSummary
from mcp_qlikview.parser.container import EXEX_TRAILER, HEADER_SIZE, QVW_MAGIC_PREFIX
from mcp_qlikview.server import (
    _ServerState,
    _tool_get_sheets,
    _tool_get_variables,
    _tool_list_tables,
    _tool_reload,
    _tool_search,
)


def _string_list(names: list[str]) -> bytes:
    out = b"\x00\x00\x00\x00" + struct.pack("<I", len(names))
    for name in names:
        encoded = name.encode("utf-8")
        out += bytes([0x04, len(encoded)]) + encoded
    return out


def _minimal_qvw(script_body: str) -> bytes:
    script_block = b"\x00" * 8 + b"///$tab Main\n" + script_body.encode("utf-8")
    header = QVW_MAGIC_PREFIX + b"\x00" * (HEADER_SIZE - len(QVW_MAGIC_PREFIX))
    body = (
        zlib.compress(script_block)
        + zlib.compress(_string_list(["FieldA"]))
        + zlib.compress(_string_list(["TableOne"]))
    )
    return header + body + EXEX_TRAILER


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _ServerState:
    qvw_dir = tmp_path / "qvw"
    qvw_dir.mkdir()
    (qvw_dir / "sample.qvw").write_bytes(
        _minimal_qvw("LOAD * FROM data;\nSET vX = 1;\nLOAD more FROM other;\n")
    )
    monkeypatch.setenv("QVW_DIR", str(qvw_dir))
    st = _ServerState()
    st.boot()
    assert st.config is not None, f"boot failed: {st.config_error}"
    return st


async def test_search_reports_not_implemented_scopes(state: _ServerState) -> None:
    # Review #8/#9: fields/tables/variables aren't implemented in Phase 1 and
    # must be reported distinctly from "supported but zero matches".
    result = await _tool_search(state, "LOAD", None, None)
    assert isinstance(result, SearchResult)
    assert result.not_implemented_scopes == ["fields", "tables", "variables"]
    assert "sample" in result.scanned_qvws
    assert any(h.scope == "script" for h in result.matches)


async def test_search_variables_scope_is_not_implemented(state: _ServerState) -> None:
    result = await _tool_search(state, "vX", ["variables"], None)
    assert isinstance(result, SearchResult)
    assert result.not_implemented_scopes == ["variables"]
    assert result.matches == []


async def test_search_line_numbers_use_newline_split(state: _ServerState) -> None:
    # Review #16: script_line must align with ScriptBundle.line_count (\n split).
    result = await _tool_search(state, "/SET vX/", ["scripts"], "sample")
    hit = next(h for h in result.matches if h.script_line is not None)
    # Script body starts at "///$tab Main\n" (line 1); "SET vX" is line 3.
    assert hit.script_line == 3


async def test_get_variables_unsupported(state: _ServerState) -> None:
    result = await _tool_get_variables(state, "sample")
    assert isinstance(result, ErrorEnvelope)
    assert result.error_code == "unsupported"


async def test_get_sheets_unsupported(state: _ServerState) -> None:
    result = await _tool_get_sheets(state, "sample")
    assert isinstance(result, ErrorEnvelope)
    assert result.error_code == "unsupported"


async def test_list_tables_honours_size_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review #2: the index-derived path must apply the size guard, not just
    # the absolute-path branch.
    qvw_dir = tmp_path / "qvw"
    qvw_dir.mkdir()
    (qvw_dir / "big.qvw").write_bytes(_minimal_qvw("LOAD * FROM x;\n"))
    monkeypatch.setenv("QVW_DIR", str(qvw_dir))
    monkeypatch.setenv("MCP_QVW_MAX_FILE_BYTES", "16")  # smaller than any real QVW
    st = _ServerState()
    st.boot()
    assert st.config is not None

    tables = await _tool_list_tables(st, None)
    assert isinstance(tables, list)
    assert len(tables) == 1
    assert isinstance(tables[0], TableSummary)
    assert tables[0].parse_status == "parse_failed"
    assert "exceeding" in (tables[0].error or "") or "limit" in (tables[0].error or "")


async def test_reload_caches_then_invalidates(state: _ServerState) -> None:
    await _tool_search(state, "LOAD", ["scripts"], "sample")  # prime cache
    result = await _tool_reload(state, "sample")
    assert len(result.invalidated) == 1
