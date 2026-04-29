"""
Load script extractor.

Priority:
  1. -prj fast-path (LoadScript.txt) — source="prj"
  2. Binary extraction from block 0  — source="binary"

Binary extraction algorithm (from probe findings 2026-04-23):
  - Decompress block 0
  - Find script start: first occurrence of b"///" (///$tab marker)
  - Detect script end: scan for binary density spike
    (>40% non-text bytes in a 256-byte window)
"""

from __future__ import annotations

from pathlib import Path

from mcp_qlikview.models import ScriptBundle
from mcp_qlikview.parser.container import QvwContainer, QvwParseError
from mcp_qlikview.parser.prj import try_prj

# Bytes considered "text" for the binary-density scan
_TEXT_LOW = 9   # TAB
_TEXT_HIGH = 13  # CR


def _is_text_byte(b: int) -> bool:
    # Allow tab/lf/cr, printable ASCII, and all UTF-8 multi-byte bytes (>= 0x80)
    return b == 9 or b == 10 or b == 13 or 32 <= b <= 126 or b >= 0x80


_BINARY_THRESHOLD = 0.40
_WINDOW = 512


def _extract_from_block0(data: bytes) -> str:
    """Extract load script text from decompressed block 0."""
    start = data.find(b"///")
    if start == -1:
        # Fallback: try SET ThousandSep (first variable assignment in script)
        start = data.find(b"SET ThousandSep")
    if start == -1:
        raise QvwParseError("Could not locate load script in block 0")

    # Scan forward until binary density exceeds threshold
    end = len(data)
    for i in range(start, len(data) - _WINDOW, _WINDOW // 2):
        window = data[i : i + _WINDOW]
        binary_count = sum(1 for b in window if not _is_text_byte(b))
        if binary_count / _WINDOW > _BINARY_THRESHOLD:
            end = i
            break

    return data[start:end].decode("utf-8", errors="replace")


def extract_script(container: QvwContainer) -> ScriptBundle:
    """
    Extract the load script from a QVW container.

    Tries -prj fast-path first; falls back to binary extraction from block 0.

    Raises:
        QvwParseError: if no script can be found.
    """
    qvw_name = container.path.stem

    # Fast-path: -prj folder
    prj = try_prj(container.path)
    if prj is not None:
        script = prj.script_text
        return ScriptBundle(
            qvw=qvw_name,
            script=script,
            source="prj",
            line_count=len(script.splitlines()),
        )

    # Binary extraction from block 0
    if not container.blocks:
        raise QvwParseError(f"{container.path.name}: no blocks parsed")

    script = _extract_from_block0(container.blocks[0].data)
    return ScriptBundle(
        qvw=qvw_name,
        script=script,
        source="binary",
        line_count=len(script.splitlines()),
    )
