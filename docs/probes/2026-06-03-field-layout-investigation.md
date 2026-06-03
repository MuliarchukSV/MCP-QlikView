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

## Next probe targets

1. **Locate the global field-layout block** (BitOffset/BitWidth/Bias per field
   per table) — the last missing piece. Check block 0's trailer and the small
   tail blocks (373-375) which had tell-tale `00 00 01 01 02 02…` /
   `00 01 02 03…` ramp patterns (possible bit-width or field-id tables).
2. Confirm the block-372 column-A "offset" reading by indexing into the
   matching field's symbol table and checking the bytes line up with decoded
   symbols.
3. Long-string >255-byte encoding (group 143-159, `unknown flag 0x31`).
4. Field→table assignment: tie the 64 field names + 6 tables to their symbol
   tables and per-field index blocks (in file order, validated by cardinality).
