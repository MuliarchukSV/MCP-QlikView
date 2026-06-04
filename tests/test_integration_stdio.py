"""End-to-end integration tests through the real MCP stdio transport.

Unlike the golden tests (which call handler coroutines directly), these spawn
the server as a subprocess (`python -m mcp_qlikview`) and drive it over the MCP
protocol: initialize → tools/list → tools/call. They validate the wire contract
the way a real MCP client (Claude Code) sees it.

Skip cleanly when ``MCP_QVW_TEST_FIXTURES_DIR`` is unset, like the golden suite.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.integration

CALL_TIMEOUT = 200.0
_TOOLS = sorted(
    ["list_files", "list_tables", "get_script", "get_variables", "get_sheets",
     "get_data_sources", "get_field_values", "reload", "search"]
)


def _payload(res: Any) -> tuple[bool, Any]:
    return bool(res.isError), json.loads(res.content[0].text)


def _server_params(qvw_dir: Path | None) -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if k != "QVW_DIR"}
    if qvw_dir is not None:
        env["QVW_DIR"] = str(qvw_dir)
    # `python -m mcp_qlikview` avoids depending on the console script being on PATH.
    return StdioServerParameters(command=sys.executable, args=["-m", "mcp_qlikview"], env=env)


async def test_stdio_handshake_and_tools(reference_qvw_dir: Path | None) -> None:
    if reference_qvw_dir is None:
        pytest.skip("MCP_QVW_TEST_FIXTURES_DIR not set")
    async with (
        stdio_client(_server_params(reference_qvw_dir)) as (read, write),
        ClientSession(read, write) as s,
    ):
        await asyncio.wait_for(s.initialize(), 30)
        tools = await asyncio.wait_for(s.list_tools(), 30)
        assert sorted(t.name for t in tools.tools) == _TOOLS


async def test_stdio_core_tool_calls(reference_qvw_dir: Path | None) -> None:
    if reference_qvw_dir is None:
        pytest.skip("MCP_QVW_TEST_FIXTURES_DIR not set")
    async with (
        stdio_client(_server_params(reference_qvw_dir)) as (read, write),
        ClientSession(read, write) as s,
    ):
        await asyncio.wait_for(s.initialize(), 30)

        err, files = _payload(await asyncio.wait_for(s.call_tool("list_files", {}), CALL_TIMEOUT))
        assert not err and isinstance(files, list) and files

        err, tabs = _payload(await asyncio.wait_for(
            s.call_tool("list_tables", {"qvw": "LTV_analisys"}), CALL_TIMEOUT))
        assert not err and len(tabs) == 6
        assert all(t["field_count"] > 0 and t["parse_status"] == "ok" for t in tabs)

        err, fv = _payload(await asyncio.wait_for(
            s.call_tool("get_field_values", {"qvw": "LTV_analisys"}), CALL_TIMEOUT))
        assert not err and len(fv["field_names"]) == 64 and len(fv["value_sets"]) > 50

        err, sr = _payload(await asyncio.wait_for(
            s.call_tool("search", {"pattern": "Customer", "scope": ["fields"],
                                   "qvw": "LTV_analisys"}), CALL_TIMEOUT))
        assert not err and any(m["scope"] == "field" for m in sr["matches"])
        assert "fields" not in sr["not_implemented_scopes"]


async def test_stdio_error_paths(reference_qvw_dir: Path | None) -> None:
    if reference_qvw_dir is None:
        pytest.skip("MCP_QVW_TEST_FIXTURES_DIR not set")
    async with (
        stdio_client(_server_params(reference_qvw_dir)) as (read, write),
        ClientSession(read, write) as s,
    ):
        await asyncio.wait_for(s.initialize(), 30)
        err, env = _payload(await asyncio.wait_for(
            s.call_tool("get_script", {"qvw": "does_not_exist"}), 30))
        assert err and env["error_code"] == "file_not_found"
        err, env = _payload(await asyncio.wait_for(
            s.call_tool("get_variables", {"qvw": "LTV_analisys"}), CALL_TIMEOUT))
        assert err and env["error_code"] == "unsupported"


async def test_stdio_degraded_mode() -> None:
    # No fixtures needed: server must stay alive without QVW_DIR and return a
    # structured config error for every call.
    async with (
        stdio_client(_server_params(None)) as (read, write),
        ClientSession(read, write) as s,
    ):
        await asyncio.wait_for(s.initialize(), 30)
        err, env = _payload(await asyncio.wait_for(s.call_tool("list_files", {}), 30))
        assert err and env["error_code"] in ("qvw_dir_missing", "qvw_dir_unreadable")
