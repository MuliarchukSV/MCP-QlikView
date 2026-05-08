"""Block 0 decoder: QlikView load script.

Probe section 2.1 confirmed block 0 contains a small binary header (~30-40
bytes), an embedded plaintext file path (the original Qlik project location),
and then the literal marker ``///$tab Main\\n`` followed by the load script
in plain text. Encoding is UTF-8 in all 3 reference files; we apply the §4.3
encoding chain as a fallback when raw UTF-8 fails.
"""

from __future__ import annotations

from dataclasses import dataclass

SCRIPT_TAB_MARKER: bytes = b"///$tab"
"""Byte sequence introducing the first ``///$tab`` directive in the load
script. QlikView always emits at least ``///$tab Main`` for an active app."""


class ScriptNotFoundError(ValueError):
    """Raised when block 0 contains no recognisable load-script marker."""


@dataclass(frozen=True)
class ScriptDecodeResult:
    """Outcome of the §4.3 encoding chain — populates :class:`ScriptBundle`."""

    text: str
    """Decoded script body, starting at the first ``///$tab`` directive."""

    encoding: str
    """Encoding actually used to decode (e.g. ``utf-8``, ``cp1252``)."""

    decode_replacements: int
    """Count of bytes the decoder replaced (>0 only on the cp1252 fallback)."""


def extract_script(block: bytes) -> ScriptDecodeResult:
    """Locate the load script inside container block 0 and decode it.

    Args:
        block: Decompressed bytes of container block 0.

    Returns:
        :class:`ScriptDecodeResult` capturing the text, the actual encoding
        chain step that succeeded, and the count of byte-level replacements.
        Trailing NUL padding is stripped before decoding.

    Raises:
        ScriptNotFoundError: the ``///$tab`` marker is absent — block 0 may
            be malformed or this is not a valid QVW.
    """
    marker_pos = block.find(SCRIPT_TAB_MARKER)
    if marker_pos < 0:
        raise ScriptNotFoundError(
            f"no '{SCRIPT_TAB_MARKER.decode()}' marker found in block 0 "
            f"({len(block)} bytes)"
        )

    raw = block[marker_pos:].rstrip(b"\x00")
    return _decode_script_bytes(raw)


def _decode_script_bytes(raw: bytes) -> ScriptDecodeResult:
    """Apply the §4.3 encoding chain: UTF-8 → chardet (optional) → cp1252-replace.

    QlikView stores SET MoneyFormat / TimeFormat strings in whatever codepage
    the original developer's machine used. Most are UTF-8 in modern files,
    but legacy QVWs from cp1252/cp1251 environments still appear. We try
    UTF-8 strict first (correct for the 3 reference files), then chardet if
    installed, then cp1252-replace as a guaranteed-non-failing tail. Lossy
    fallback is preferred over an exception because the script body is
    informational — losing one byte of a footer string is better than
    blocking ``get_script`` entirely.
    """
    try:
        return ScriptDecodeResult(
            text=raw.decode("utf-8"), encoding="utf-8", decode_replacements=0
        )
    except UnicodeDecodeError:
        pass

    try:
        import chardet
    except ImportError:
        chardet = None  # type: ignore[assignment]

    if chardet is not None:
        guess = chardet.detect(raw)
        encoding = guess.get("encoding")
        confidence = guess.get("confidence", 0.0) or 0.0
        if encoding and confidence >= 0.7:
            try:
                return ScriptDecodeResult(
                    text=raw.decode(encoding),
                    encoding=encoding.lower(),
                    decode_replacements=0,
                )
            except (UnicodeDecodeError, LookupError):
                pass

    # Lossy tail: replace each undecodable byte with U+FFFD. Count how many
    # replacements happened so consumers see ``decode_replacements > 0`` as
    # a soft warning per spec §4.3.
    text = raw.decode("cp1252", errors="replace")
    replacements = text.count("�")
    return ScriptDecodeResult(
        text=text, encoding="cp1252", decode_replacements=replacements
    )
