# Probe 2026-06-03 (b) — field→table mapping & bit-layout location

Follow-on from the chunk-framing resolution. Goal: find where QVW stores the
per-field bit-layout (QVD's `QvdFieldHeader`: `BitOffset`/`BitWidth`/`Bias`/
`Offset`/`Length`/`NoOfSymbols`) so the row-index can be reconstructed.

## What we learned (LTV_analisys.qvw, 94 logical blocks)

1. **No XML.** Marker hunt for `<?xml`, `QvdFieldHeader`, `BitOffset`,
   `<Fields>`, `RecordByteSize`, etc. across every decompressed block returned
   **zero hits**. Unlike QVD, QVW does **not** carry an XML table header. The
   field/record layout is binary.

2. **Logical-block roles (LTV):**
   - block 0 — load script (1 MB plaintext; trailing bytes have repeating `F`
     markers worth a look).
   - block 1 — 64 field names; block 2 — 6 table names (tagged string lists).
   - block 3 — source-QVD path list (data lineage): `…\Customer_life_LTV…qvd x
     …\SalesDataGDS_ETL.qvd x …` — useful for `get_data_sources` enrichment.
   - blocks 4-354 — **per-field symbol tables** (distinct values). Many fields;
     note recurring pairs like `symtab(478993)` immediately followed by
     `string-list(478993)` — same cardinality, paired numeric/text views.
   - blocks **355-368** — large ~2 MB high-entropy blocks = **packed row-index
     records** (a different chunk size than the 256 KB symbol chunks).
   - blocks **370-372** — small structured binary = **record/field-layout
     descriptors**. Block 372: header `5b 00 00 00`=91, then 11-byte records
     with a column that steps `04, 08, 0c, …` (looks like packed bit/byte
     offsets); block 370 similar with a `2741`-count header. **These are the
     prime candidates for the bit-layout table.**
   - block 376 (83 KB) — high-bit byte stream; likely bit-packed column pointers.

3. **Mapping is not 1:1-trivial.** There are far more symbol-table-like blocks
   than the 64 field names, because each field carries auxiliary structures
   (paired numeric/text symbol sets, per-field index blocks). A naive
   "Nth symbol table = Nth field" mapping will not hold; the descriptor table
   (block 372) is what ties fields → symbol tables → bit positions.

## Strategic implication — split Phase 2

Reconstructing **every row's values** requires fully decoding the binary
bit-layout descriptor (block 372 family) + the bit-packed row-index — a
substantial, uncertain reverse-engineering effort with **no OSS prior art**
(OpenQVD/qvdrs cover QVD only, explicitly not QVW). That is **Phase 2b**.

But the **symbol tables (distinct values per field) are already ~90% decoded**
(7/8 groups exact; the 8th is the long-string fix). Distinct values + per-field
cardinality + sample values deliver most of the analytical value an LLM needs —
"what values does field X contain", "how many distinct customers", "sample 20
rows of this column" — **without** cracking the row-index. That is **Phase 2a**
and it is low-risk and shippable soon.

### Recommended sequencing

- **Phase 2a (low risk, high value):** finish long-string decode (8/8 symbol
  tables) → map symbol tables to field names via the block-3 lineage + block-372
  descriptor → ship `describe_table` (distinct count + sample values), activate
  the `fields` search scope, populate `field_count` in `list_tables`.
- **Phase 2b (hard RE, do after 2a proves value):** decode block-372 bit-layout
  + row-index → full per-row reconstruction → DuckDB ingest → `query(sql)` /
  `export_table`.

## Addendum — block 372 decoded (per-field symbol-offset index)

Columnar analysis (`/tmp/probe_b372.py`): block 372 = **13-byte header**
(`5b 00 00 00` = u32 91, then 9 bytes) + **12,184 fixed 11-byte records**.
12,184 ≈ **12,186** = the cardinality of the field in symbol-group 100-103, so
block 372 is that **field's per-symbol index**, NOT the global field descriptor.
First 16 records decode cleanly:

| bytes | r0..r7 | reading |
|---|---|---|
| `[0:2]` u16 | 34, 35, 68, 101, 134, 167, 199, 232 (monotonic ↑) | **cumulative byte offset into the field's symbol table** — random access to symbol N |
| `[2:4]` u16 | 4, 8, 12, 16, 20, 24, 28, 32 | linear `(rec+1)*4` stride |
| `[4]` u8 | 2,3,4,5,6,7,8,9 (+1) | sequential ordinal |
| `[5]` u8 | mostly `0x20`, some `0x40`/`0x00` | flag / bit-width class? |
| `[6]` u8 | 1,2,3,4,5,6,5,7 | near-sequential sub-index |
| `[7]` u8 | 0 | padding |
| `[8]` u8 | 1 or 2 | type flag (numeric/text?) |
| `[9:11]` u16 | varies (0x1fb2, 0x11b7, …) | per-symbol value/hash |

Blocks 370 (w=10 → 741 recs ≈ a 741-symbol field) and 371 (w=11 → 1,248 recs)
follow the same "record-count = field cardinality" pattern → each is a
**per-field index**, one per field, sized to that field's distinct values.

### Consequence — the full-row decode pipeline is now mapped

```
row-index packed block (355-368)  --bit-unpack-->  per-field symbol INDEX
   per-field index block (372-style)[symbol idx].offset  -->  byte offset
   field symbol table[offset]  -->  the value
```

The ONE remaining global unknown for Phase 2b is the **per-field
`BitOffset`/`BitWidth`/`Bias` within each table's packed record** (QVD keeps
this in `QvdFieldHeader`; QVW keeps it in a not-yet-located compact binary
block — candidates: block 0's trailer with repeating `F` markers, or a small
uninspected block). Find that and row reconstruction is fully specified.

## Addendum 2 — global layout LOCATED (blocks 373-375)

The global layout directory is the small tail-block trio (`/tmp/probe_layout.py`):

- **block 375** (64 bytes): `00 01 02 … 3f` — **exactly 64 entries = the 64
  fields**, identity order. The field-order / field-id table. *(Confirmed.)*
- **block 374** (12 bytes): six pairs `(0,0) (1,9) (2,18) (3,27) (4,35) (5,44)`
  — **6 entries = the 6 tables**; the second column 0,9,18,27,35,44 partitions
  64 as `9,9,9,8,9,20`. Strong candidate for a table→field-range directory,
  **but** taking it as literal contiguous field ranges puts the measures
  (`SumSaleUSD…`) in `TwoAndMoreOrder4LTV` rather than `DataLTV`, so the
  indirection likely runs through block 373/375, not raw field order.
  *(Semantics need confirmation.)*
- **block 373** (138 bytes): 69 `(code, field_id)` pairs — identity for
  field_ids 0..22, then permuted (`20→0, 21→23, 22→24, …`). This is the
  **byte→field routing table** used to unpack a packed record byte by byte.
  *(Structure clear; exact unpack use TBD.)*
- block 0's trailer is just NUL padding with a stray `46 ('F')` — not the
  layout block.

**Bit widths are derivable, not stored explicitly.** Per OpenQVD §7.3,
`BitWidth = ceil(log2(NoOfSymbols + extra))`; QVW appears to keep only the
field/table directory (373-375) and derive widths from each field's symbol
count. Cardinalities are in hand (e.g. idCustomer 478,993 → 19 bits;
Route field 408,260 → 19 bits; 12,186 → 14 bits).

### Row-index blocks sized

The packed-record run splits into two table groups by decompressed size:
blocks **355-363** (8 × 1,978,368 B + 240,626 B) and **364-368**
(4 × 1,990,656 B + 1,677,276 B) — two large tables' record blocks. Exact
`RecordByteSize` falls out once the per-table field set + derived bit widths
are summed (`RecordByteSize = ceil(Σ BitWidth / 8)`).

## Addendum 3 — verification CORRECTION (block 372 re-read)

Cross-checking block 372 against the matching field's symbol table (group
100-103, 12,186 GUID strings of 67 bytes each) **falsified** the Addendum-1/2
reading that column A is a byte offset into the symbol table:

- column A direct-hits real symbol offsets only **22/2000 (~1 %, noise)**, and
  across all records column A is **not monotonic** and **wraps at 65536**
  (it's a u16 that overflows) — so it is NOT a cumulative byte offset.
- the column at byte offset 9 (`colH`, u16) holds **12,184 distinct values
  spanning [1, 12185]** — i.e. a **permutation of the field's symbol indices**.

So block 372 is a **record ↔ symbol-index map** (a sort/inverted index for the
field, or the explicit row-index of a ~12,184-row key table), **not** a
symbol-offset table. The byte-offset interpretation in the earlier addenda is
**withdrawn**. The other columns (A wrapping-u16, B linear ×4, C4 ordinal,
C8 flag 1/2) remain undeciphered.

**Honest status:** a clean end-to-end decode of one real record was **not**
achieved this session. The verification disproved a hypothesis (good) and
narrowed block 372's role, but the path from the big packed row-index blocks
(355-368) → field values is **not yet proven**. Full per-row reconstruction
(Phase 2b) needs dedicated reverse-engineering and should not be treated as
"nearly done".

What IS solid and proven:
- symbol-table framing + decode (7/8 fields exact) — distinct values per field.
- block 375 = 64 field-ids; block 373/374 = directory (partial).

## Status: Phase 2 fully mapped end-to-end (SUPERSEDED — see Addendum 3)

Every structural piece is now identified:
`field/table directory (373-375)` + `per-field symbol tables` +
`per-field symbol-offset index (370-372)` + `packed row-index (355-368)`.

## Next probe targets

1. Confirm block 374 table-directory semantics (resolve the measure-placement
   anomaly via the 373/375 indirection).
2. Fully decode block 373 routing → unpack one real record from blocks 355+ and
   verify field values against the symbol tables (end-to-end proof on one row).
3. Long-string >255-byte encoding (group 143-159, `unknown flag 0x31`).
4. Then implement: `parser/blocks/layout.py` (373-375) + `data.py` (row unpack)
   + DuckDB ingest + `query`/`describe_table`/`export_table`.
