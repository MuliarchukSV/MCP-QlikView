"""Block 2 decoder: global table-name list."""

from __future__ import annotations

from mcp_qlikview.parser.blocks.strings import decode_tagged_string_list


def extract_table_names(block: bytes) -> list[str]:
    """Decode the table-name list from container block 2."""
    return decode_tagged_string_list(block)
