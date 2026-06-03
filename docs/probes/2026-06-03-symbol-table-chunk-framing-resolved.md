# Probe 2026-06-03 — symbol-table chunk framing RESOLVED

**Status:** ✅ Resolves the three open hypotheses (C/D/E) from
[`2026-05-08-spanning-symbol-tables.md`](2026-05-08-spanning-symbol-tables.md).
Phase 2's foundational blocker is cleared.

## Trigger

The OpenQVD clean-room QVD spec (github.com/Sigilweaver/OpenQVD, `SPEC.md`,
CC BY-SA 4.0) documents the QVD symbol/row-index format from a ~1,045-file
corpus. QVD ≠ QVW (QVD has an XML header + uncompressed body; QVW is a zlib
container), but the **inner symbol-entry format** is closely related, which let
us frame the right experiment.

## Method

Ran the real `parser.blocks.symbols.decode_symbol_block` against the production
`LTV_analisys.qvw` (34.6 MB), block-by-block and over the merged data_chunk
runs. Scripts: `/tmp/probe_framing{,2,3}.py` (scratch, not committed).

## Findings

### 1. QVW symbol strings are length-prefixed, NOT NUL-terminated

QVD (per OpenQVD SPEC §2.1) terminates strings with `0x00`. **QVW does not** —
it uses a 1-byte length prefix. Verified on real bytes:

```
block 4 (74 bytes):  00 00 00 00 | 06 00 00 00 | 05 06 "698590" de a8 0a 00 ...
                     [4 zeros  ]   [count=6  ]   ^f ^len "698590"  ^int32=698590
```

Entry = `flag(0x05)`, `len(0x06)`, `"698590"` (6 bytes), int32 LE `0x000AA8DE`
= 698,590 — the number matches the text. Our existing `symbols.py` framing
(`[4 zeros][u32 count]` header; flags `0x01..0x06`; text-then-number for
`0x05`/`0x06`) is therefore **correct for QVW**. The `0x03` flag in `symbols.py`
was not observed in this file.

### 2. The blocker was a dropped trailing block, not the framing

A field's symbol table > 256 KB is stored as a run of full 256 KB `data_chunk`
blocks (each followed by a 4-byte inter-chunk gap) **plus one trailing partial
block** (< 256 KB, ~169-byte gap → classified `metadata`). The old
`iter_logical_blocks` merged only the `data_chunk` run and **dropped the
trailing block**, so decoding stopped a few thousand entries short of the
declared count:

| field table | run (data_chunks) | + tail | declared count | decoded w/o tail | decoded w/ tail |
|---|---|---|---:|---:|---:|
| phones | blocks 8-48 | block 49 (156 KB) | 478,993 | 472,204 (EOF) | **478,993 ✓** |
| routes | blocks 160-324 | block 325 (76 KB) | 408,260 | 407,335 (trunc) | **408,260 ✓** |

So the three 2026-05-08 hypotheses were all **disproved**:
- (C) per-chunk framing — no; chunks concatenate into one stream.
- (D) 4-byte gaps are payload — no; they are container framing, correctly excluded.
- (E) count = rows not entries — no; the `u32` count is the exact symbol-entry count.

The single cause: the table's last (partial) chunk isn't a 256 KB `data_chunk`,
so the grouping excluded it.

### 3. Fix applied + result

`iter_logical_blocks` now absorbs the one block following a `data_chunk` run
into the `symbol_group` (see its docstring + `tests/test_logical_blocks.py`).
Re-running the real decoder over the 8 symbol groups in `LTV_analisys.qvw`:

```
7 of 8 symbol_groups decode to EXACT declared count
  blocks 8-49    478,993 ✓     blocks 130-137  208,750 ✓
  blocks 50-94   478,993 ✓     blocks 160-325  408,260 ✓
  blocks 100-103  12,186 ✓     blocks 348-349   12,126 ✓
  blocks 122-128 190,961 ✓
  blocks 143-159  78,908  ✗  unknown flag 0x31 @ offset 806,035   <-- see below
```

## Remaining sub-problem (next session)

Group `143-159` (count 78,908) decodes cleanly for ~806 KB then hits an
"unknown flag `0x31`" (ASCII `'1'`) — a sign the cursor drifted into the middle
of a string. Most likely cause: a **string longer than 255 bytes**, which a
single length byte cannot express. `strings.py` already flags this class of
gap. Hypothesis to test next: QVW uses a multi-byte length (or an escape) for
long strings. This is a string-length-encoding issue, **not** a chunk-framing
one.

## Path to Phase 2 (updated)

1. ✅ Chunk framing + symbol-entry format (this probe).
2. ☐ Long-string length encoding (the `143-159` case).
3. ☐ **Field → symbol-table mapping**: which `symbol_group` belongs to which
   field name (block 1 has 64 names). QVD carries this in the XML header
   (`QvdFieldHeader.Offset/Length`); QVW must store the equivalent in a binary
   metadata block — locate and decode it.
4. ☐ **Row-index block**: bit-packed records (`stored = (row >> BitOffset) &
   ((1<<BitWidth)-1)`, `index = stored + Bias`, per OpenQVD §3). Needs the
   per-field `BitOffset`/`BitWidth`/`Bias` — same metadata block as (3).
5. ☐ DuckDB ingest (streaming `RecordBatchReader`, 122,880-row batches) +
   `query` / `describe_table` / `export_table` tools; activate `fields`/
   `tables` search scopes and real `field_count`/`row_count`.
