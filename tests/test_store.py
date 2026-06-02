"""Tests for the MetadataStore cache: size guard, error hints, thread-safety.

These exercise the store directly with a hand-built minimal-but-valid QVW so
they run in CI without the production reference files (adversarial-review fixes
#1, #3, #13).
"""

from __future__ import annotations

import struct
import threading
import zlib
from pathlib import Path

import pytest

from mcp_qlikview.parser.container import EXEX_TRAILER, HEADER_SIZE, QVW_MAGIC_PREFIX
from mcp_qlikview.store import MetadataStore, QvwTooLargeError


def _string_list(names: list[str]) -> bytes:
    """Encode a tag-prefixed string list (block 1/2 wire format)."""
    out = b"\x00\x00\x00\x00" + struct.pack("<I", len(names))
    for name in names:
        encoded = name.encode("utf-8")
        out += bytes([0x04, len(encoded)]) + encoded
    return out


def _minimal_qvw() -> bytes:
    """Smallest byte sequence the store will fully parse (script + dict + tables)."""
    script_block = b"\x00" * 16 + b"///$tab Main\nLOAD * FROM source;\n"
    dict_block = _string_list(["FieldA", "FieldB"])
    tables_block = _string_list(["TableOne"])
    header = QVW_MAGIC_PREFIX + b"\x00" * (HEADER_SIZE - len(QVW_MAGIC_PREFIX))
    body = zlib.compress(script_block) + zlib.compress(dict_block) + zlib.compress(tables_block)
    return header + body + EXEX_TRAILER


@pytest.fixture
def qvw_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.qvw"
    path.write_bytes(_minimal_qvw())
    return path


def test_parses_minimal_qvw(qvw_file: Path) -> None:
    store = MetadataStore()
    meta = store.ensure_parsed(qvw_file)
    assert "LOAD" in meta.script.upper()
    assert meta.field_names == ["FieldA", "FieldB"]
    assert meta.table_names == ["TableOne"]
    assert meta.block_count == 3


def test_size_limit_uses_canonical_env_var_name(qvw_file: Path) -> None:
    # Regression for review #13: the hint pointed at MCP_QVW_MAX_FILE_SIZE_BYTES,
    # which nothing reads. The canonical env var is MCP_QVW_MAX_FILE_BYTES.
    store = MetadataStore(max_file_size_bytes=8)
    with pytest.raises(QvwTooLargeError) as exc:
        store.ensure_parsed(qvw_file)
    assert "MCP_QVW_MAX_FILE_BYTES" in str(exc.value)
    assert "MCP_QVW_MAX_FILE_SIZE_BYTES" not in str(exc.value)


def test_cache_hit_returns_same_instance(qvw_file: Path) -> None:
    store = MetadataStore()
    first = store.ensure_parsed(qvw_file)
    second = store.ensure_parsed(qvw_file)
    assert first is second


def test_lru_eviction(tmp_path: Path) -> None:
    store = MetadataStore(max_entries=1)
    a = tmp_path / "a.qvw"
    b = tmp_path / "b.qvw"
    a.write_bytes(_minimal_qvw())
    b.write_bytes(_minimal_qvw())
    store.ensure_parsed(a)
    store.ensure_parsed(b)  # evicts a
    assert store.invalidate(a) == []  # a no longer cached
    assert store.invalidate(b) != []


def test_concurrent_ensure_and_invalidate_does_not_corrupt(tmp_path: Path) -> None:
    # Review #3: handlers run ensure_parsed via asyncio.to_thread, so two parses
    # can race on the OrderedDict. Hammer the store from many threads; any
    # "dict changed size during iteration" / lost-update would surface here.
    store = MetadataStore(max_entries=4)
    files = []
    for i in range(8):
        p = tmp_path / f"f{i}.qvw"
        p.write_bytes(_minimal_qvw())
        files.append(p)

    errors: list[BaseException] = []

    def worker(path: Path) -> None:
        try:
            for _ in range(25):
                store.ensure_parsed(path)
                store.invalidate(path)
                store.invalidate(None)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f,)) for f in files * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent access raised: {errors[:3]}"
    assert len(store._cache) <= 4  # LRU bound held under concurrency


def test_block_decode_error_names_the_block(tmp_path: Path) -> None:
    # Review #6: a positional-drift failure must say which block was wrong.
    script_block = b"///$tab Main\nLOAD 1;\n"
    bad_dict = b"\xff\xff\xff\xff garbage not a string list"
    tables_block = _string_list(["T"])
    header = QVW_MAGIC_PREFIX + b"\x00" * (HEADER_SIZE - len(QVW_MAGIC_PREFIX))
    body = zlib.compress(script_block) + zlib.compress(bad_dict) + zlib.compress(tables_block)
    path = tmp_path / "drift.qvw"
    path.write_bytes(header + body + EXEX_TRAILER)

    store = MetadataStore()
    with pytest.raises(ValueError) as exc:
        store.ensure_parsed(path)
    assert "block 1" in str(exc.value)
    assert "field-name dictionary" in str(exc.value)
