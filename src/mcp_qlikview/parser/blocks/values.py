"""Value-set extraction: decode every per-field symbol table into a compact
summary (cardinality, value type, sample values).

Phase 2a. A QVW stores each field's distinct values as a symbol table (see
:mod:`.symbols`). This module walks the container's logical blocks, decodes the
ones that are symbol tables, and summarises each as a :class:`ValueSet` —
enough for an LLM to answer "what values / how many distinct / sample rows of
field X" without the (Phase 2b) bit-packed row index.

Field↔value-set binding is intentionally NOT asserted here: the probe of
2026-06-03 found it is not a clean 1:1 (paired numeric/text views mean there are
a few more value-sets than field names). The cardinality and type are exact;
sample values let the caller correlate a value-set to a field name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mcp_qlikview.parser.blocks.symbols import (
    InvalidSymbolBlockError,
    SymbolEntry,
    decode_symbol_block,
    symbol_count,
)
from mcp_qlikview.parser.container import QvwContainer, iter_logical_blocks

# Logical blocks 0/1/2 are script / field-name dict / table-name list — schema,
# not field values. Everything after is a candidate symbol table.
_NON_VALUE_BLOCK_INDICES: frozenset[int] = frozenset({0, 1, 2})

_DEFAULT_MAX_SAMPLES: int = 5

_FLAG_TYPE: dict[int, str] = {
    0x01: "int",
    0x02: "float",
    0x03: "dual_int",
    0x04: "text",
    0x05: "dual_int",
    0x06: "dual_float",
}


@dataclass(frozen=True)
class ValueSet:
    """Summary of one field's symbol table (its distinct values).

    Attributes:
        first_block: ``RawBlock.index`` of the leftmost contributing block.
        last_block: rightmost contributing block (== first_block for singles).
        cardinality: number of distinct values (declared symbol count).
        value_type: ``int`` / ``float`` / ``text`` / ``dual_int`` /
            ``dual_float`` / ``mixed`` — derived from the entry flags.
        samples: up to ``max_samples`` value previews as strings.
    """

    first_block: int
    last_block: int
    cardinality: int
    value_type: str
    samples: list[str] = field(default_factory=list)


def _looks_like_symbol_block(buf: bytes) -> bool:
    """Cheap pre-check: 4 zero pad + a known leading flag byte."""
    return len(buf) >= 9 and buf[:4] == b"\x00\x00\x00\x00" and buf[8] in _FLAG_TYPE


def _sample_str(entry: SymbolEntry) -> str:
    if entry.text is not None:
        return entry.text
    return "" if entry.numeric is None else repr(entry.numeric)


def _value_type(entries: list[SymbolEntry]) -> str:
    kinds = {_FLAG_TYPE.get(e.flag, "mixed") for e in entries}
    if len(kinds) == 1:
        return next(iter(kinds))
    # int+dual_int collapse to dual_int; otherwise mixed.
    if kinds <= {"int", "dual_int"}:
        return "dual_int"
    return "mixed"


def extract_value_sets(
    container: QvwContainer, *, max_samples: int = _DEFAULT_MAX_SAMPLES
) -> list[ValueSet]:
    """Return one :class:`ValueSet` per decodable symbol table in ``container``.

    Blocks that do not decode as symbol tables (e.g. the packed row-index
    tail) are skipped silently — the caller gets only real value-sets. Only
    the first ``max_samples`` entries of each table are decoded, so summarising
    a 500k-entry field is cheap.
    """
    out: list[ValueSet] = []
    for block in iter_logical_blocks(container):
        if block.first_index in _NON_VALUE_BLOCK_INDICES:
            continue
        buf = block.payload
        if not _looks_like_symbol_block(buf):
            continue
        try:
            card = symbol_count(buf)
            sample_entries = decode_symbol_block(buf, limit=max_samples)
        except InvalidSymbolBlockError:
            continue
        if not sample_entries:
            continue
        out.append(
            ValueSet(
                first_block=block.first_index,
                last_block=block.last_index,
                cardinality=card,
                value_type=_value_type(sample_entries),
                samples=[_sample_str(e)[:200] for e in sample_entries],
            )
        )
    return out
