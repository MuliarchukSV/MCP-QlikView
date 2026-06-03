# Probe 2026-06-03 (c) — symbol value inventory (Phase 2a foundation)

After the long-string fix (`0xFF`→u32), **all symbol tables decode** with real
sample values. This proves distinct-value extraction works end-to-end and gives
the data needed to bind fields → value sets. `/tmp/probe_mapping.py`.

## Result

68 symbol tables decoded from `LTV_analisys.qvw` (vs 64 field names). The extra
~4 are paired numeric/text views of the same field (e.g. a date stored once as
text `27.05.2024` and once as serial `45439.0`). Sample values make the
field identity obvious:

| # | blocks | card | samples | likely field |
|--:|---|--:|---|---|
| 4 | 8-49 | 478,993 | `+0000000, +0004, +0041` | idCustomer3LTV (id) |
| 5 | 50-94 | 478,993 | `9776829=13151897` | id-pair / hash |
| 6 | 95 | 1,702 | `27.05.2024 …` | DateSale4LTV |
| 7 | 96 | 1,697 | `45439.0 …` (serials) | DateStartSaleCustomer |
| 8 | 97 | 2 | `Внутри страны, Между странами` | typeDomaine4LTV |
| 10 | 100-103 | 12,186 | `928F32EDA461…` (GUID) | GUID_From/_hash_ticket |
| 14 | 107 | 57 | `2024-05 …` | Год-Месяц |
| 15 | 108 | 20 | `2024-Q2 …` | Год-Квартал |
| 18 | 111 | 12 | `May, Nov, Jul` | месяц name |
| 19 | 112 | 4 | `Q2, Q4, Q3` | quarter |
| 29 | 122-128 | 190,961 | `24.928…` (float) | SumSale/Doxod measure |
| 31 | 130-137 | 208,750 | `0.747…` | ratio measure |
| 35 | 141 | 1,500 | `"ЛИС-АВТО-ТРАНС" ЧП …` | CARRIER_NAME_4LTV |
| 37 | 143-159 | 78,908 | `1006/08 Познань - Дніп…` | TRIP_NUMBER / route |
| 38 | 160-325 | 408,260 | `Варшава - Винница …` | RouteWithDispatch |
| 42 | 329 | 29 | `Польша, Украина, Германия` | country |
| 49/50 | 336/337 | 741 | `52.314…, 16.130…` | from_Lat / from_Lon |
| 58/59 | 345/346 | 1,247 | `50.42…, 31.28…` | to_Lat / to_Lon |

(full list in the probe script output.) Types are read directly from the entry
flags: `0x01/0x02` numeric, `0x04` text, `0x05/0x06` dual (text+number).

## Implication for Phase 2a

Distinct values + cardinality + type + samples per value-set are fully
extractable **now** — no row-index needed. The field↔value-set binding is
fuzzy (68 vs 64) but:

- exact **type** comes from the flags,
- exact **cardinality** is the decoded entry count,
- **samples** make the field obvious to an LLM.

### Phase 2a deliverable (decided)

Ship a grounded **value-set inventory** rather than over-claiming a 1:1
field binding:

1. `parser/blocks/values.py::extract_value_sets(container)` → list of
   `ValueSet{index, blocks, cardinality, value_type, samples}` (pure, tested).
2. Wire into the store (cache alongside script/field_names/table_names).
3. Surface via a tool (`get_field_values` / enrich `describe_table`) returning
   value-sets + the field-name and table-name lists, so the LLM correlates
   samples → field names. `field_count` in `list_tables` = len(field_names).
4. Activate the `fields` search scope by matching the pattern against decoded
   value strings.

Field→table→value-set exact binding (and row reconstruction) stays Phase 2b.

### Note

One trailing block (~369) misdecodes (not a symbol table — likely row-index
tail); the extractor must skip non-symbol blocks gracefully (count header /
flag sanity check) rather than assume every block is a symbol table.
