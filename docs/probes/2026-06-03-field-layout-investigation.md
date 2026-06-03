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

## Next probe targets

1. Decode block 372's 11-byte record format (map columns: field index? symbol
   offset? bit width?). This is the Rosetta stone for both 2a mapping and 2b.
2. Long-string >255-byte encoding (group 143-159, `unknown flag 0x31`).
3. Correlate block-372 entries with the 64 field names and the symbol-table
   offsets to produce the field→symbol-table map.
