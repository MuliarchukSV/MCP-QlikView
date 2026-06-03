"""Table→field directory decode (QVW's internal data-model layout).

Phase 2b foundation. QVW stores a small binary directory mapping each table to
a contiguous range of field-ids. On the LTV reference it is a 12-byte block of
``(table_index, field_start)`` byte pairs: ``(0,0)(1,9)(2,18)(3,27)(4,35)(5,44)``
→ table 0 = fields[0:9], …, table 5 = fields[44:64]. Confirmed against the load
script: this is the *internal* (post-engine) table layout, which differs from
the script's logical LOAD tables because calculated fields are reassigned.

This is distinct from the per-field *value* binding (which symbol table holds a
field's distinct values) — that is still unresolved and stays Phase 2b.
"""

from __future__ import annotations

from mcp_qlikview.parser.container import QvwContainer


def extract_table_field_map(
    container: QvwContainer,
    field_names: list[str],
    table_names: list[str],
) -> dict[str, list[str]] | None:
    """Map each table name to its ordered field names, or ``None`` if undecodable.

    Locates the table directory by signature: a block of exactly
    ``2 * len(table_names)`` bytes that parses as ``(table_index, field_start)``
    pairs with ``table_index`` running ``0..n-1`` and ``field_start`` strictly
    increasing from 0 and staying below ``len(field_names)``. The match is
    validated rather than assumed, so a coincidental same-size block is rejected.

    Limitation: field-start is a single byte, so this handles up to 255 fields;
    QVWs with more fields return ``None`` (caller falls back to "unknown").
    """
    n_tables = len(table_names)
    n_fields = len(field_names)
    if n_tables == 0 or n_fields == 0 or n_fields > 0xFF:
        return None

    target_len = 2 * n_tables
    for block in container.blocks:
        buf = block.decompressed
        if len(buf) != target_len:
            continue
        indices = [buf[i] for i in range(0, target_len, 2)]
        offsets = [buf[i + 1] for i in range(0, target_len, 2)]
        if indices != list(range(n_tables)):
            continue
        if offsets[0] != 0:
            continue
        if any(offsets[i] >= offsets[i + 1] for i in range(n_tables - 1)):
            continue
        if offsets[-1] >= n_fields:
            continue
        bounds = [*offsets, n_fields]
        return {
            table_names[t]: field_names[bounds[t] : bounds[t + 1]]
            for t in range(n_tables)
        }
    return None
