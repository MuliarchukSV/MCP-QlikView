"""Security tests for hardening introduced after the adversarial review.

These tests cover the path-traversal block, file-size pre-flight, regex
flags + length cap, LRU eviction in :class:`MetadataStore`, and degraded-
mode behaviour when ``QVW_DIR`` is unreadable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_qlikview.parser.blocks.strings import (
    InvalidStringListError,
    decode_tagged_string_list,
)
from mcp_qlikview.server import (
    _compile_pattern,
    _resolve_qvw,
    _ServerState,
)
from mcp_qlikview.store import MetadataStore, ParsedMetadata

# ---- Path traversal ------------------------------------------------------


@pytest.fixture
def state_with_qvw_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _ServerState:
    monkeypatch.setenv("QVW_DIR", str(tmp_path))
    monkeypatch.delenv("MCP_QVW_ALLOW_OUTSIDE_DIR", raising=False)
    state = _ServerState()
    state.boot()
    assert state.config is not None
    return state


def test_resolve_qvw_blocks_outside_dir_absolute_path(
    state_with_qvw_dir: _ServerState, tmp_path: Path
) -> None:
    # File exists, but it lives outside QVW_DIR.
    outside_dir = tmp_path.parent / "elsewhere"
    outside_dir.mkdir(exist_ok=True)
    outside = outside_dir / "leak.qvw"
    outside.write_bytes(b"")
    result = _resolve_qvw(state_with_qvw_dir, str(outside))
    # ErrorEnvelope, not Path.
    assert hasattr(result, "error_code")
    assert result.error_code == "unsupported"  # type: ignore[union-attr]
    assert result.category == "unsupported"  # type: ignore[union-attr]


def test_resolve_qvw_blocks_dot_dot_traversal(
    state_with_qvw_dir: _ServerState, tmp_path: Path
) -> None:
    # Place a file outside QVW_DIR and try to reach it via "../".
    outside = tmp_path.parent / "secret.qvw"
    outside.write_bytes(b"")
    # Symbolic relative basename with traversal — our resolver always
    # joins under qvw_dir, then resolves; if escape happens, the prefix
    # check rejects it.
    result = _resolve_qvw(state_with_qvw_dir, "../secret")
    # Either the file isn't found inside QVW_DIR (file_not_found) or it
    # resolved outside (unsupported). Both are correct refusals.
    assert hasattr(result, "error_code")


def test_resolve_qvw_allows_outside_dir_when_opted_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    qvw_dir = tmp_path / "qlik"
    qvw_dir.mkdir()
    outside = tmp_path / "other.qvw"
    outside.write_bytes(b"")
    monkeypatch.setenv("QVW_DIR", str(qvw_dir))
    monkeypatch.setenv("MCP_QVW_ALLOW_OUTSIDE_DIR", "true")
    state = _ServerState()
    state.boot()
    result = _resolve_qvw(state, str(outside))
    assert isinstance(result, Path)
    assert result.resolve() == outside.resolve()


# ---- Size pre-flight ----------------------------------------------------


def test_resolve_qvw_rejects_files_above_size_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QVW_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_QVW_MAX_FILE_BYTES", "1024")
    big = tmp_path / "huge.qvw"
    big.write_bytes(b"x" * 2048)
    state = _ServerState()
    state.boot()
    result = _resolve_qvw(state, "huge")
    assert hasattr(result, "error_code")
    assert result.error_code == "qvw_too_large"  # type: ignore[union-attr]


# ---- Pattern compilation ------------------------------------------------


def test_regex_flags_parsed_correctly() -> None:
    matcher = _compile_pattern("/foo/i")
    assert callable(matcher)
    assert matcher("FOO bar")  # type: ignore[operator]
    assert not matcher("baz")  # type: ignore[operator]


def test_regex_flags_combined() -> None:
    matcher = _compile_pattern("/^foo$/im")
    assert callable(matcher)
    assert matcher("line1\nFOO\nline3")  # type: ignore[operator]


def test_regex_invalid_returns_error_envelope() -> None:
    result = _compile_pattern("/[unclosed/")
    assert hasattr(result, "error_code")
    assert result.error_code == "input"  # type: ignore[union-attr]


def test_pattern_length_cap_rejected() -> None:
    huge = "x" * 5000
    result = _compile_pattern(huge)
    assert hasattr(result, "error_code")


def test_substring_pattern_case_insensitive() -> None:
    matcher = _compile_pattern("LOAD")
    assert callable(matcher)
    assert matcher("load * from data")  # type: ignore[operator]
    assert matcher("LOAD * FROM DATA")  # type: ignore[operator]


# ---- String list count bound --------------------------------------------


def test_string_list_count_above_sanity_bound_rejected() -> None:
    import struct

    # Declare 2 billion entries — clearly malicious/corrupt.
    buf = b"\x00\x00\x00\x00" + struct.pack("<I", 2_000_000_000) + b"\x04\x03foo"
    with pytest.raises(InvalidStringListError):
        decode_tagged_string_list(buf)


# ---- LRU cache ---------------------------------------------------------


def test_metadata_store_lru_evicts_oldest_when_full(tmp_path: Path) -> None:
    store = MetadataStore(max_entries=2)
    fake_meta = ParsedMetadata(
        script="x",
        script_encoding="utf-8",
        script_decode_replacements=0,
        script_source="binary",
        field_names=[],
        table_names=[],
        block_count=0,
    )
    paths = []
    for name in ("a.qvw", "b.qvw", "c.qvw"):
        path = tmp_path / name
        path.write_bytes(b"")
        paths.append(path)
        store._admit(str(path.resolve()), fake_meta)  # type: ignore[arg-type]
    # max_entries=2; "a" should have been evicted.
    keys = list(store._cache.keys())  # type: ignore[attr-defined]
    assert len(keys) == 2
    assert str(paths[0].resolve()) not in keys
    assert str(paths[2].resolve()) in keys


def test_metadata_store_invalidate_all_returns_keys(tmp_path: Path) -> None:
    store = MetadataStore(max_entries=4)
    fake_meta = ParsedMetadata(
        script="x",
        script_encoding="utf-8",
        script_decode_replacements=0,
        script_source="binary",
        field_names=[],
        table_names=[],
        block_count=0,
    )
    for name in ("a.qvw", "b.qvw"):
        path = tmp_path / name
        path.write_bytes(b"")
        store._admit(str(path.resolve()), fake_meta)  # type: ignore[arg-type]
    invalidated = store.invalidate(None)
    assert len(invalidated) == 2
    assert store._cache == {}  # type: ignore[attr-defined]


# ---- Degraded mode ------------------------------------------------------


def test_boot_handles_unreadable_qvw_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point at a path that exists but is not a directory.
    bad = tmp_path / "file.txt"
    bad.write_text("not a dir")
    monkeypatch.setenv("QVW_DIR", str(bad))
    state = _ServerState()
    state.boot()
    assert state.config is None
    assert state.config_error is not None
    # Non-directory raises ValidationError → qvw_dir_missing.
    assert state.config_error.error_code in ("qvw_dir_missing", "qvw_dir_unreadable")
