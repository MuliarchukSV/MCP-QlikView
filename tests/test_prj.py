"""Tests for parser.prj — ``-prj`` fast-path discovery."""

from __future__ import annotations

from pathlib import Path

from mcp_qlikview.parser.prj import try_prj


def test_returns_none_when_no_prj_sibling(tmp_path: Path) -> None:
    qvw = tmp_path / "example.qvw"
    qvw.write_bytes(b"")
    assert try_prj(qvw) is None


def test_reads_load_script_when_prj_present(tmp_path: Path) -> None:
    qvw = tmp_path / "example.qvw"
    qvw.write_bytes(b"")
    prj = tmp_path / "example-prj"
    prj.mkdir()
    (prj / "LoadScript.txt").write_text(
        "///$tab Main\nLOAD * FROM data;", encoding="utf-8"
    )
    bundle = try_prj(qvw)
    assert bundle is not None
    assert bundle.script.text.startswith("///$tab Main")
    assert bundle.script.encoding == "utf-8"
    assert bundle.prj_dir == prj.resolve()


def test_returns_none_when_prj_lacks_loadscript(tmp_path: Path) -> None:
    qvw = tmp_path / "example.qvw"
    qvw.write_bytes(b"")
    (tmp_path / "example-prj").mkdir()
    # No LoadScript.txt inside.
    assert try_prj(qvw) is None


def test_handles_cyrillic_load_script(tmp_path: Path) -> None:
    qvw = tmp_path / "example.qvw"
    qvw.write_bytes(b"")
    prj = tmp_path / "example-prj"
    prj.mkdir()
    (prj / "LoadScript.txt").write_text(
        "///$tab Main\nSET ThousandSep='₴';", encoding="utf-8"
    )
    bundle = try_prj(qvw)
    assert bundle is not None
    assert "₴" in bundle.script.text
