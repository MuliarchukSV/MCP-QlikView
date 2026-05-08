"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def reference_qvw_dir() -> Path | None:
    """Directory containing real LTV_analisys.qvw / Monitoring.qvw / dbhDesigning.qvw.

    Tests that need real fixtures should depend on this fixture and call
    ``pytest.skip(...)`` when it returns ``None``. Set ``MCP_QVW_TEST_FIXTURES_DIR``
    to enable golden tests.
    """
    raw = os.environ.get("MCP_QVW_TEST_FIXTURES_DIR")
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if path.is_dir() else None
