# Probe — symbol tables span multiple zlib blocks

**Date:** 2026-05-08
**Author:** Sergey Muliarchuk (with Claude Code assistance)
**Files inspected:** `LTV_analisys.qvw` (377 zlib blocks, 34.6 MB)

## Summary

The §14.1.1 framing probe (2026-05-07) classified blocks heuristically as
`metadata` / `data_chunk` / `unknown` based on inter-block gap size. This
follow-up probe shows the `data_chunk` label is misleading: **consecutive
256 KB + 4-byte-gap blocks are pieces of one logical symbol table, not
row data.**

QlikView appears to segment any decompressed payload longer than 256 KB
into fixed 256 KB zlib chunks at write time, with a 4-byte length prefix
between consecutive chunks (probably the next chunk's compressed size,
for fast skip-scanning). The reader must concatenate them before parsing.

## Evidence

Logical-grouping over LTV_analisys.qvw (102 groups derived from 377 blocks):

| group | blocks | total bytes | count u32 at offset 4 | first-entry flag |
|---|---|---|---|---|
| 8 | 8-48 (41 chunks) | 10,747,904 | **478,993** | `0x06 0x08 "+0……"` (text + double) |
| 10 | 50-93 (44 chunks) | 11,534,336 | **478,993** | `0x04 0x10 "97……"` (text only) |
| 17 | 100-102 (3 chunks) | 786,432 | 12,186 | `0x04 0x41 "92……"` (text, length 65) |

- Group 8 starts with a 4-zero pad + `0x00074f11` LE u32 = 478,993 → that
  is the entry count of the symbol table. The first entry is flag 0x06
  with text `"+0000000"` and an 8-byte LE double = 0.0. Subsequent entries
  follow the same pattern (`<flag> <len> <text> <8-byte double>`). The
  payload content (sequential Ukrainian phone numbers) supports a single
  field carrying ~half a million distinct customer phone identifiers.
- Group 10 has the **same** count (478,993). This is the second field of
  the same table — Qlik stores one symbol table per field, in sequence.
  Same row count = one symbol per row (these fields are unique-per-row,
  no dedup wins).
- Inspecting block 49 (between groups 8 and 10) shows continuation of
  the same flag-0x06 entries; the 4-byte gap after each data chunk plus
  the 156 KB metadata block at index 49 together suggest block 49 is the
  tail of group 8's symbol table, not a separate structure.

## Implication for the parser

`parser/container.py` returns 377 `RawBlock` entries today; consumers
that want symbol tables need a layer above that **groups consecutive
`data_chunk` blocks** before handing the joined byte buffer to
`decode_symbol_block`. The grouping rule is straightforward:

```
group = []
for block in container.blocks:
    if block.kind_hint == "data_chunk":
        group.append(block)
    else:
        if group:
            yield ("symbol_table", concat(group))
            group = []
        yield ("metadata", block)
if group:
    yield ("symbol_table", concat(group))
```

Group endpoints are the boundary between the previous "metadata" block
and the next non-data block. This is what `parser/container.py` v0.2.0
should expose as `iter_logical_blocks(container) → Iterator[LogicalBlock]`.

The 4-byte gaps **between** chunks within a group are part of the
container framing, not the symbol-table content; we drop them when
concatenating. A future tightening is to use the gap value as a
length-prefix sanity check, but the v0.1.0 byte-level concatenation
yields decodable symbol tables on the LTV reference.

## Open questions

1. **Where are bit-packed row indices?** The traditional QVD model uses
   bit-packed indices into symbol tables to materialise rows. None of
   the blocks inspected so far look like dense bit-packed payloads; they
   all parse cleanly as length-prefixed symbol entries. Two hypotheses:
   - **(A) Each table's data is the full per-row symbol list** — no
     deduplication, count=row_count. Decoding rows is just iterating
     each field's symbol table in lockstep.
   - **(B) Bit-packed indices live in blocks not yet identified** —
     possibly the `unknown` and `metadata` blocks not classified as
     symbol tables. Block 49's 156 KB is suspicious.
2. **Schema metadata.** Where do `(table, field) → symbol_table_index`
   mappings live? Block 0 (the load script) carries field names by name,
   not by symbol-table index. There must be a per-table index block
   somewhere; candidates are blocks 3 (path), 94 (5 KB binary), 103
   (30 KB), 128 (146 KB), 137 (44 KB), 159 (100 KB), 325 (76 KB).
3. **Block 49.** 156 KB sandwiched between two symbol-table groups —
   either tail of group 8 (most likely given byte-level continuity) or
   schema header for group 10's field. Needs targeted decode.

## Next probe

`docs/probes/<next>-qvw-row-indices.md` should:
- Decode group 8 + block 49 as one continuous flag-0x06 stream and
  report whether the entry count matches 478,993.
- Inspect block 94 (5 KB) byte-by-byte; it starts with `\x04\x11` which
  is `<flag=04> <length=17>` — looks like a tag-prefixed list. Decode
  with the symbol decoder and see if it parses.
- Hypothesise structure of blocks 3, 103, 128, 137, 159, 325 — these
  are the largest non-symbol metadata blocks and likely carry per-table
  schema headers (field-to-symbol-table indices, bit-widths, biases).

## Phase 2 plan revision

Spec §3.2 listed `parser/blocks/{schema,data}.py` as Phase 2 deliverables.
The `data.py` description ("Combine 256KB chunks into per-table row
sequences using the bit-stuffed index decode") is now suspect — if
hypothesis (A) holds (no bit-packing, just per-row symbols), the
implementation collapses to plain symbol concatenation and DuckDB ingest.
Phase 2 difficulty drops dramatically if (A) is correct; rises if (B)
is correct and we still need to find + decode bit-packed indices.

The next session should resolve (A) vs (B) before writing more code.

## Update — naive concat does NOT decode (2026-05-08 follow-up)

After implementing :func:`iter_logical_blocks` and feeding the merged
``symbol_group`` payloads into :func:`decode_symbol_block`, all 8
symbol-groups in LTV_analisys.qvw fail with truncation errors:

```
blocks   8-48  FAIL: truncated length byte at offset 10747904
blocks  50-93  FAIL: truncated text: length 16 at offset 11534326 but only 9 bytes available
blocks 100-102 FAIL: truncated text: length 65 at offset 786391 but only 40 bytes available
…
```

The count u32 at offset 4 of the first chunk (478,993) is consumed by
the decoder, and entries decode for a long time, but the buffer ends
mid-entry near the very end. This rules out the simplest model
("logical group is a flat concat of fixed-size chunks").

Three working hypotheses for the actual format:

- **(C) Each chunk is independently framed.** Each 256 KB chunk has its
  own `<count> <entries>` header; the count value at the start of the
  group is just the first chunk's count, not the total. Decoder must
  iterate chunks separately and accumulate entries.
- **(D) Inter-chunk gap bytes are part of the payload.** The 4-byte
  gap value (e.g. `b8 43 01 00 = 82,872`) might be a length prefix
  that means "skip the next 82,872 bytes of the previous chunk's
  payload" — i.e. chunks have trailing padding. We currently strip
  the gaps, which would corrupt the stream.
- **(E) The count u32 is something else.** What we read as ``count``
  (478,993) might be ``num_records`` (rows, not symbols); the symbol
  count would be smaller and live elsewhere. Ratios match the LTV row
  count for the customer table.

Verifying these requires a partial-decode harness that walks one entry
at a time and reports when the cursor crosses a chunk boundary; bytes
near the boundary tell us whether a gap-strip was the wrong move or
whether each chunk has its own header to skip.

**Phase 2 implementation is paused** until one of (C), (D), (E) is
confirmed. ``iter_logical_blocks`` ships in v0.2.0 as ``experimental``
because the API shape is right (group consecutive data chunks) even
though the payload semantics are not yet correct.
