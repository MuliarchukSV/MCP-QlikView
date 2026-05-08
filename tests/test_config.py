"""Tests for config — env-driven server settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_qlikview.config import Config


class TestConfig:
    def test_loads_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("QVW_DIR", str(tmp_path))
        cfg = Config()  # type: ignore[call-arg]
        assert cfg.qvw_dir == tmp_path

    def test_defaults_apply_when_only_qvw_dir_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("QVW_DIR", str(tmp_path))
        # Make sure no MCP_QVW_* leak from the test runner shell.
        for key in (
            "MCP_QVW_MAX_ROWS",
            "MCP_QVW_HARD_MAX_ROWS",
            "MCP_QVW_CACHE_MEM_MB",
            "MCP_QVW_WATCH",
            "MCP_QVW_LOG_LEVEL",
            "MCP_QVW_PARSED_SIZE_MULTIPLIER",
            "MCP_QVW_TEMP_DIR",
        ):
            monkeypatch.delenv(key, raising=False)
        cfg = Config()  # type: ignore[call-arg]
        assert cfg.max_rows == 10_000
        assert cfg.hard_max_rows == 1_000_000
        assert cfg.cache_mem_mb == 2048
        assert cfg.watch is True
        assert cfg.log_level == "INFO"
        assert cfg.parsed_size_multiplier == 3.5
        assert cfg.temp_dir is None

    def test_qvw_dir_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QVW_DIR", raising=False)
        with pytest.raises(ValidationError):
            Config()  # type: ignore[call-arg]

    def test_overrides_via_mcp_qvw_prefix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("QVW_DIR", str(tmp_path))
        monkeypatch.setenv("MCP_QVW_MAX_ROWS", "500")
        monkeypatch.setenv("MCP_QVW_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("MCP_QVW_WATCH", "false")
        cfg = Config()  # type: ignore[call-arg]
        assert cfg.max_rows == 500
        assert cfg.log_level == "DEBUG"
        assert cfg.watch is False

    def test_validates_qvw_dir_exists_and_is_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Point at a real file (not a directory) — must be rejected.
        bogus = tmp_path / "file.txt"
        bogus.write_text("not a dir")
        monkeypatch.setenv("QVW_DIR", str(bogus))
        with pytest.raises(ValidationError):
            Config()  # type: ignore[call-arg]
