"""Block 1 decoder: global field-name dictionary."""

from __future__ import annotations

from mcp_qlikview.parser.blocks.strings import decode_tagged_string_list


def extract_field_names(block: bytes) -> list[str]:
    """Decode the field-name dictionary from container block 1.

    The wire format is the shared tag-prefixed string list (see
    :mod:`mcp_qlikview.parser.blocks.strings`). This function exists as a
    semantically-named façade so callers don't need to know about the format
    overlap with block 2.
    """
    return decode_tagged_string_list(block)
