"""
Schema extractor — table names and field lists from a QVW container.

Phase 1 scope: metadata only. row_count is always None.

Two extraction paths:
  1. Table names  — parsed from load script (regex label:LOAD pattern)
  2. Field names  — parsed from schema blocks (binary format: 0x04 + len + name)

Binary field format (from probe 2026-04-23):
  Each schema block starts with an 8-byte header (two uint32),
  followed by repeated entries: [type_byte=0x04][length_byte][ascii_field_name]
  Field names include a table suffix, e.g. idCustomer3LTV, DateSale4LTV.

Table matching strategy (two tracks):
  Track A (suffix-based): when names carry the QVW table suffix (e.g. "3LTV") we
    extract the abbreviation ("LTV") and match it to the first script table ending
    with that abbreviation.  High confidence.
  Track B (block-1 positional): block 1 is always a schema block.  If its names have
    no QVW suffix (QVD-sourced tables keep original column names) and count ≤ 150,
    we assign it to the first unmatched script table.  Medium confidence.
  All other no-suffix blocks are discarded — they are data symbol tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mcp_qlikview.models import TableSummary
from mcp_qlikview.parser.container import QvwContainer
from mcp_qlikview.parser.script import extract_script

# Regex to find table label before LOAD/SELECT in script
_TABLE_RE = re.compile(
    r"^\s*\[?([A-Za-z][A-Za-z0-9_ ]{1,60}?)\]?\s*:\s*\n?\s*"
    r"(LOAD|SELECT|NoConcatenate|Concatenate)",
    re.MULTILINE | re.IGNORECASE,
)

# Field name byte in schema blocks
_FIELD_TYPE_BYTE = 0x04
_SCHEMA_BLOCK_HEADER = 8  # bytes to skip at start of schema block


@dataclass
class FieldMeta:
    name: str
    raw_name: str   # as stored in QVW (may include table suffix)
    qlik_type: str = "STRING"


@dataclass
class TableMeta:
    name: str
    fields: list[FieldMeta] = field(default_factory=list)
    row_count: int | None = None
    is_synthetic: bool = False


def _parse_field_names(data: bytes) -> list[str]:
    """
    Parse length-prefixed field names from a schema block.
    Format: [0x04][1B length][ascii string] repeated.
    Skips the 8-byte block header.
    """
    names: list[str] = []
    offset = _SCHEMA_BLOCK_HEADER
    while offset < len(data) - 2:
        type_byte = data[offset]
        if type_byte != _FIELD_TYPE_BYTE:
            offset += 1
            continue
        str_len = data[offset + 1]
        if str_len == 0 or offset + 2 + str_len > len(data):
            offset += 1
            continue
        raw = data[offset + 2 : offset + 2 + str_len]
        try:
            name = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            offset += 1
            continue
        # Validate: printable ASCII only (field names are always ASCII)
        if all(32 <= ord(c) < 127 for c in name) and len(name) >= 1:
            names.append(name)
            offset += 2 + str_len
        else:
            offset += 1
    return names


def _table_names_from_script(script: str) -> list[str]:
    """Extract table names defined in the load script."""
    seen: dict[str, int] = {}  # name → first occurrence order
    for m in _TABLE_RE.finditer(script):
        name = m.group(1).strip()
        if name and "\n" not in name and name not in seen:
            seen[name] = m.start()
    return sorted(seen, key=lambda n: seen[n])


_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.%]*$')
_QVW_SUFFIX_RE = re.compile(r'\d+([A-Z][A-Za-z]{1,20})$')


def _dominant_suffix(names: list[str]) -> str | None:
    """
    Extract the dominant QVW table abbreviation suffix from a field-name group.

    QVW appends 'digits + TableAbbrev' to every field: idCustomer3LTV → abbrev='LTV'.
    Returns the most common abbreviation if ≥ 25% of names carry it, else None.
    """
    counts: dict[str, int] = {}
    for n in names:
        m = _QVW_SUFFIX_RE.search(n)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    if not counts:
        return None
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] >= max(2, len(names) * 0.25) else None


def _match_table(abbrev: str, table_names: list[str]) -> str | None:
    """
    Find the first (in script declaration order) table name that ends with the
    given QVW abbreviation (case-insensitive).  The first table carrying a suffix
    is the primary table for that abbreviation; derived/temp tables come later.
    """
    abbrev_lower = abbrev.lower()
    for t in table_names:
        if t.lower().endswith(abbrev_lower):
            return t
    return None


def _is_field_list_block(names: list[str], known_table_names: set[str]) -> bool:
    """
    Distinguish schema blocks (field names) from data symbol tables.

    Both use the 0x04 type byte, so heuristics are needed:
    - Data values often contain spaces; field names do not.
    - Table index blocks have > 50% names matching known table names.
    - Field names must be valid identifiers (≥ 50% pass).
    - Data values are often very short codes (Q1, d0) — > 30% length ≤ 3 is a red flag.
    """
    if len(names) < 2:
        return False

    # Reject: > 20% names contain spaces (data values, not identifiers)
    space_count = sum(1 for n in names if ' ' in n)
    if space_count > len(names) * 0.20:
        return False

    # Reject: mostly matches known table names (table-index block)
    table_match = sum(1 for n in names if n in known_table_names)
    if table_match > len(names) * 0.50:
        return False

    # Reject: < 50% valid identifiers
    id_count = sum(1 for n in names if _IDENT_RE.match(n))
    if id_count < len(names) * 0.50:
        return False

    # Reject: > 30% names are very short (≤ 3 chars) — likely data codes, not field names
    short_count = sum(1 for n in names if len(n) <= 3)
    if short_count > len(names) * 0.30:
        return False

    return True


# Blocks with > _GLOBAL_REGISTRY_THRESHOLD names and no QVW suffix are likely
# global field registries spanning all tables — cannot be attributed to one table.
_GLOBAL_REGISTRY_THRESHOLD = 150


def extract_schema(container: QvwContainer) -> list[TableMeta]:
    """
    Extract table and field metadata from a QVW container.

    Returns a list of TableMeta objects. row_count is always None in Phase 1.
    """
    # Step 1: get table names from script
    try:
        script_bundle = extract_script(container)
        table_names = _table_names_from_script(script_bundle.script)
    except Exception:
        table_names = []

    known_table_names: set[str] = set(table_names)

    # Step 2: collect field groups from schema blocks.
    # Each tuple is (block_index, field_names).
    # Schema blocks are small (< 64 KB), not block 0, and pass the identifier heuristics.
    all_field_groups: list[tuple[int, list[str]]] = []
    for blk in container.blocks[1:]:
        if len(blk.data) > 65536:
            continue  # data block, skip
        names = _parse_field_names(blk.data)
        if _is_field_list_block(names, known_table_names):
            all_field_groups.append((blk.index, names))

    # Step 3: match field groups to tables.
    #
    # Track A — suffix-based (high confidence):
    #   QVW appends 'digits+Abbrev' to field names stored in its metadata blocks,
    #   e.g. idCustomer3LTV → abbrev 'LTV'.  We match the first script table whose
    #   name ends with that abbreviation.
    #
    # Track B — block-1 positional (medium confidence):
    #   Block 1 (the very first block after the app-header block 0) is always a schema
    #   block.  When its names carry no QVW suffix (tables sourced from QVD files keep
    #   original column names), we assign it to the first unmatched table in script order.
    #   Constraint: must be ≤ _GLOBAL_REGISTRY_THRESHOLD names; larger blocks are global
    #   field registries that span all tables and cannot be attributed to one table.
    #   All other no-suffix blocks are data symbol tables and are discarded.
    table_fields: dict[str, list[str]] = {}  # table_name → field names

    # Track A
    for _bidx, field_group in all_field_groups:
        abbrev = _dominant_suffix(field_group)
        if abbrev:
            matched = _match_table(abbrev, table_names)
            if matched and matched not in table_fields:
                table_fields[matched] = field_group

    # Track B — only block index 1
    for blk_index, field_group in all_field_groups:
        if blk_index != 1:
            continue
        if _dominant_suffix(field_group):
            continue  # already handled by Track A
        if len(field_group) > _GLOBAL_REGISTRY_THRESHOLD:
            continue  # global field registry, can't attribute
        # Assign to the first script table that Track A left unmatched
        for tname in table_names:
            if tname not in table_fields:
                table_fields[tname] = field_group
                break

    # Build final table list preserving script declaration order
    tables: list[TableMeta] = []
    for tname in table_names:
        fields = table_fields.get(tname, [])
        is_syn = tname.startswith("$Syn")
        tables.append(TableMeta(
            name=tname,
            fields=[FieldMeta(name=f, raw_name=f) for f in fields],
            row_count=None,
            is_synthetic=is_syn,
        ))

    # No script tables at all: attach any discovered field groups as anonymous tables
    if not table_names:
        for i, field_group in enumerate(all_field_groups):
            tables.append(TableMeta(
                name=f"__table_{i}",
                fields=[FieldMeta(name=f, raw_name=f) for f in field_group],
                row_count=None,
                is_synthetic=True,
            ))

    return tables


def to_table_summaries(
    tables: list[TableMeta], qvw_name: str
) -> list[TableSummary]:
    """Convert TableMeta list to API response models."""
    schema_name = re.sub(r"[^A-Za-z0-9_]", "_", qvw_name)
    if schema_name and schema_name[0].isdigit():
        schema_name = "qvw_" + schema_name

    return [
        TableSummary(
            qvw=qvw_name,
            schema_name=schema_name,
            table_name=t.name,
            field_count=len(t.fields),
            row_count=t.row_count,
            is_synthetic=t.is_synthetic,
            parse_status="ok" if t.fields else "pending",
        )
        for t in tables
    ]
