"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_qlikview.parser.container import parse, QvwContainer

# Path to the committed synthetic fixture (always present)
SYNTHETIC_QVW = Path(__file__).parent / "fixtures" / "synthetic_minimal.qvw"

# Optional: path to real QVW files for reference/integration tests
_REF_DIR_ENV = "MCP_QVW_TEST_FIXTURES_DIR"
REFERENCE_DIR: Path | None = (
    Path(os.environ[_REF_DIR_ENV])
    if _REF_DIR_ENV in os.environ
    else None
)


@pytest.fixture(scope="session")
def synthetic_path() -> Path:
    """Return path to the committed synthetic QVW fixture."""
    assert SYNTHETIC_QVW.exists(), f"Fixture missing: {SYNTHETIC_QVW}"
    return SYNTHETIC_QVW


@pytest.fixture(scope="session")
def synthetic_container(synthetic_path: Path) -> QvwContainer:
    """Parsed QvwContainer for the synthetic fixture."""
    return parse(synthetic_path)


def pytest_collection_modifyitems(config, items):
    """Skip tests marked 'reference' when MCP_QVW_TEST_FIXTURES_DIR is not set."""
    if REFERENCE_DIR is None:
        skip = pytest.mark.skip(reason=f"{_REF_DIR_ENV} not set")
        for item in items:
            if "reference" in item.keywords:
                item.add_marker(skip)
