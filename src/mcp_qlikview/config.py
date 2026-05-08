"""Server configuration loaded from environment variables.

Mapping (spec §5.0):

| Env var                          | Field                    | Default  |
|----------------------------------|--------------------------|----------|
| ``QVW_DIR``                      | ``qvw_dir``              | required |
| ``MCP_QVW_MAX_ROWS``             | ``max_rows``             | 10000    |
| ``MCP_QVW_HARD_MAX_ROWS``        | ``hard_max_rows``        | 1000000  |
| ``MCP_QVW_CACHE_MEM_MB``         | ``cache_mem_mb``         | 2048     |
| ``MCP_QVW_WATCH``                | ``watch``                | true     |
| ``MCP_QVW_LOG_LEVEL``            | ``log_level``            | INFO     |
| ``MCP_QVW_PARSED_SIZE_MULTIPLIER`` | ``parsed_size_multiplier`` | 3.5  |
| ``MCP_QVW_TEMP_DIR``             | ``temp_dir``             | unset    |

``Config()`` raises :class:`pydantic.ValidationError` when ``QVW_DIR`` is
missing or points at something other than a readable directory. Callers
catch this to enter MCP degraded mode (§6.1).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Server settings sourced from process environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    qvw_dir: Path = Field(..., validation_alias="QVW_DIR")
    max_rows: int = Field(default=10_000, validation_alias="MCP_QVW_MAX_ROWS")
    hard_max_rows: int = Field(default=1_000_000, validation_alias="MCP_QVW_HARD_MAX_ROWS")
    cache_mem_mb: int = Field(default=2048, validation_alias="MCP_QVW_CACHE_MEM_MB")
    watch: bool = Field(default=True, validation_alias="MCP_QVW_WATCH")
    log_level: str = Field(default="INFO", validation_alias="MCP_QVW_LOG_LEVEL")
    parsed_size_multiplier: float = Field(
        default=3.5, validation_alias="MCP_QVW_PARSED_SIZE_MULTIPLIER"
    )
    temp_dir: Path | None = Field(default=None, validation_alias="MCP_QVW_TEMP_DIR")
    allow_outside_dir: bool = Field(
        default=False, validation_alias="MCP_QVW_ALLOW_OUTSIDE_DIR"
    )
    """When ``False`` (default), absolute paths must resolve inside ``qvw_dir``.

    Spec §4.2 allows arbitrary absolute paths, but a public OSS MCP server
    that exposes arbitrary file reads is a severe risk: a misconfigured host
    could leak secrets via ``get_script("/home/user/.ssh/id_rsa")``. The
    secure-by-default posture rejects outside paths; users who genuinely need
    cross-directory access opt in by setting ``MCP_QVW_ALLOW_OUTSIDE_DIR=true``.
    """

    max_file_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024, validation_alias="MCP_QVW_MAX_FILE_BYTES"
    )
    """Pre-flight upper bound on QVW file size (default 2 GiB).

    Files above this are rejected with ``qvw_too_large`` before
    :func:`Path.read_bytes` is called, preventing OOM on accidental or
    malicious inputs. Aligned with spec §5.1.1 size guard.
    """

    @field_validator("qvw_dir")
    @classmethod
    def _qvw_dir_must_be_directory(cls, v: Path) -> Path:
        resolved = v.expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"QVW_DIR={v} does not exist")
        if not resolved.is_dir():
            raise ValueError(f"QVW_DIR={v} is not a directory")
        return resolved
