import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MCP_QVW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    qvw_dir: Path = Field(alias="QVW_DIR")
    max_rows: int = 10_000
    hard_max_rows: int = 1_000_000
    cache_mem_mb: int = 2048
    watch: bool = True
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_prefix="MCP_QVW_",
        env_file=".env",
        populate_by_name=True,
    )


def validate_config(cfg: Config) -> None:
    """Validate QVW_DIR per spec §6.1. Raises SystemExit(1-4) on failure."""
    qvw_dir = cfg.qvw_dir

    if not qvw_dir:
        print(
            "QVW_DIR environment variable is required. "
            "Set it to a directory containing .qvw files.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not qvw_dir.exists():
        print(f"QVW_DIR='{qvw_dir}' does not exist.", file=sys.stderr)
        sys.exit(2)

    if not qvw_dir.is_dir():
        print(f"QVW_DIR='{qvw_dir}' exists but is not a directory.", file=sys.stderr)
        sys.exit(3)

    try:
        list(qvw_dir.iterdir())
    except PermissionError:
        print(f"QVW_DIR='{qvw_dir}' is not readable by this process.", file=sys.stderr)
        sys.exit(4)


def load_config() -> Config:
    """Load and validate config. Exits with code 1 if QVW_DIR is not set."""
    try:
        cfg = Config()  # type: ignore[call-arg]
    except Exception:
        print(
            "QVW_DIR environment variable is required. "
            "Set it to a directory containing .qvw files.",
            file=sys.stderr,
        )
        sys.exit(1)
    validate_config(cfg)
    return cfg
