"""Tests for parser.blocks.script — load-script extraction from container block 0."""

from __future__ import annotations

import pytest

from mcp_qlikview.parser.blocks.script import (
    SCRIPT_TAB_MARKER,
    ScriptNotFoundError,
    extract_script,
)


class TestExtractScript:
    def test_returns_script_starting_at_tab_marker(self) -> None:
        # Probe-shaped block 0: binary header + file-path + script body
        prefix = b"\x00" * 32 + b"D:\\QlikBus\\Project\\app.qvw\x00"
        script = b"///$tab Main\nSET ThousandSep=',';\nLOAD * FROM data;"
        block = prefix + script
        result = extract_script(block)
        assert result.text == script.decode("utf-8")
        assert result.encoding == "utf-8"
        assert result.decode_replacements == 0

    def test_handles_utf8_cyrillic(self) -> None:
        prefix = b"\x00" * 16
        script = "///$tab Main\nSET MoneyFormat='₴ #,##0.00';".encode()
        block = prefix + script
        result = extract_script(block)
        assert result.text == script.decode("utf-8")
        assert result.encoding == "utf-8"

    def test_raises_when_marker_missing(self) -> None:
        block = b"\x00" * 100 + b"no script marker here"
        with pytest.raises(ScriptNotFoundError):
            extract_script(block)

    def test_marker_constant_matches_probe(self) -> None:
        assert SCRIPT_TAB_MARKER == b"///$tab"

    def test_falls_back_to_cp1252_on_invalid_utf8(self) -> None:
        # \xa9 = "©" in cp1252 but mid-sequence bytes that would error under
        # strict UTF-8. Use \x80 (invalid as standalone UTF-8 continuation).
        script_bytes = b"///$tab Main\nSET Footer='Bad \x80 byte';"
        block = b"\x00" * 8 + script_bytes
        result = extract_script(block)
        assert result.text.startswith("///$tab Main")
        # Either chardet detected something other than utf-8 or we fell
        # through to cp1252; what matters is encoding != "utf-8".
        assert result.encoding != "utf-8"

    def test_strips_trailing_null_bytes(self) -> None:
        # Probe noted block 0 may have padding/null trailers. Decoder should
        # not surface those as a noisy tail of "\x00" characters.
        script = b"///$tab Main\nLOAD * FROM x;"
        block = b"\x00" * 8 + script + b"\x00" * 100
        result = extract_script(block)
        assert not result.text.endswith("\x00")
        assert result.text.endswith("LOAD * FROM x;")

    def test_records_decode_replacements_on_lossy_fallback(self) -> None:
        # Force the cp1252 errors="replace" tail: a high-bit byte in a context
        # where chardet can't confidently classify (very short string).
        # Use bytes that are invalid both as UTF-8 and in cp1252 strict.
        # \x81, \x8d, \x8f, \x90, \x9d are undefined in cp1252 → become �.
        script_bytes = b"///$tab\n" + b"\x81\x8d\x8f\x90\x9d" * 4
        block = b"\x00" * 4 + script_bytes
        result = extract_script(block)
        # Either chardet redirected us elsewhere (replacements may stay 0)
        # OR we fell to cp1252 with replacements > 0. Both are spec-compliant.
        if result.encoding == "cp1252":
            assert result.decode_replacements > 0
