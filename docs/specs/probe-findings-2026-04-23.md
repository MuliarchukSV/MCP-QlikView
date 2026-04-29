# QVW Binary Probe Findings
**Date:** 2026-04-23  
**File tested:** LTV_analisys.qvw (34.5 MB)  
**Resolves:** spec §14.1.1, §14.1.2

---

## File structure (confirmed)

```
[15-byte file header]
[Block 0 header: 8 bytes]  ← uncompressed_size(4B LE) + compressed_size(4B LE)
[Block 0 zlib data]        ← app metadata + embedded load script
[Gap: uncompressed binary] ← table index / binary TOC (no readable XML)
[Block N header: 8 bytes]
[Block N zlib data]        ← schema block OR data block
...
```

### File header (15 bytes, offsets 0–14)
```
70 17 01 00  c1 06 00 00  02 00 00 00  8d 3d 00
```
- Bytes 0–3: `70 17 01 00` — magic / build version
- Bytes 4–7: `c1 06 00 00` = 1729 — unknown (possibly header section size)
- Bytes 8–11: `02 00 00 00` = 2 — unknown (possibly section count)
- Bytes 12–14: `8d 3d 00` — unknown

### Block header (8 bytes, always)
```
[uncompressed_size: uint32 LE]  [compressed_size: uint32 LE]
```
Followed immediately by zlib-compressed data.  
Buffer size for data chunks is typically 262,144 (256 KB).

---

## Block types

### Block 0 — App metadata (large, variable size)
- Contains the full QlikView **load script** as embedded UTF-8 text.
- Script location: `block0.find(b"///")` — offset ≈ 76 in decompressed bytes.
- Script prefix bytes (before `///`): `00 00 00 00 00 df c2 01` — last 3 bytes may be script length LE24 = 115,423.
- Script format: `///$tab TabName\r\nSET ...` standard QlikView syntax.
- Script end: binary density increases sharply (>40% non-text bytes per 256B window) — marks transition to UI layout data (font names: Tahoma, Arial).
- Also contains: field strings, application settings, UI layout.
- **NO XML** in block 0 (unlike QVD format).

### Schema blocks — Field list (small, one per table)
- Header: 8 bytes `00 00 00 00 [??] 00 00 00` (first 4 = 0, next 4 = unknown count).
- Body: sequence of `[type_byte=0x04][length_byte][ascii_field_name]` entries.
- Field names include table suffix: `fieldName[N][TableAbbrev]` e.g. `idCustomer3LTV`, `DateSale4LTV`.
- Example: block 2 for `DataLTV` table → 57 fields confirmed.

### Data blocks — Symbol + bit-index (256 KB chunks)
- Standard QVD-compatible format: type bytes `0x01`–`0x06` for symbol type flags.
- Integer symbols: `05 01 NN` pattern (type=int, len=1, value).
- String symbols: `06 NN [string_bytes]` pattern.
- Dual values: `06 0b` prefix (type=dual, 11-char strings observed).
- Blocks are 256 KB decompressed each; last chunk may be smaller.

---

## §14.1.1 — Data-block framing (RESOLVED)

**Finding:** QVW data blocks do NOT use QVD-style XML headers.  
The metadata (table names, field lists) is stored in dedicated small "schema blocks"  
using a binary length-prefixed string format (not XML).

For Phase 1 (metadata only), table names come from the **load script** (regex parsing),  
not from binary block headers.

For Phase 2 (data extraction), each table's data follows after its schema block:
1. Schema block → field names
2. Symbol blocks → one per field (type+value pairs)
3. Bit-index blocks → row encoding (256 KB chunks)

---

## §14.1.2 — Encryption detection (PARTIAL)

Not observed in test files. Hypothesis: encrypted QVWs either:
- Fail zlib decompression on block 0 (most likely)
- Have a different magic in the 15-byte file header

**Detection approach:** catch `zlib.error` on block 0 decompression and surface as `encrypted_unsupported`.  
Will confirm against an encrypted sample if available.

---

## Load script extraction (confirmed algorithm)

```python
# Decompress block 0
block0 = zlib.decompress(raw[blk_hdr_start + 8 : blk_hdr_start + 8 + compressed_size])

# Find script start
script_start = block0.find(b"///")  # ///$tab pattern

# Find script end: binary density threshold
WINDOW = 256
end = len(block0)
for i in range(script_start, len(block0) - WINDOW, WINDOW // 2):
    binary_bytes = sum(1 for b in block0[i:i+WINDOW] if b < 9 or (13 < b < 32))
    if binary_bytes > WINDOW * 0.4:
        end = i
        break

script = block0[script_start:end].decode("utf-8", errors="replace")
```

---

## Table name extraction (confirmed algorithm)

Table names come from the load script (not binary):

```python
import re
TABLE_PATTERN = re.compile(
    r"^\s*\[?([A-Za-z][A-Za-z0-9_\s]*?)\]?\s*:\s*\n?\s*(LOAD|SELECT|NoConcatenate|Concatenate)",
    re.MULTILINE | re.IGNORECASE
)
tables = [m.group(1).strip() for m in TABLE_PATTERN.finditer(script)]
```

Tables found in LTV_analisys.qvw (10 tables):
`preloadDataLTV`, `DataLTV`, `filter4LTV`, `TwoAndMoreOrder4LTV`,
`tmpGeoFrom`, `NewGeo_From4LTV`, `tmpGeoTo`, `NewGeo_To4LTV`,
`Route_tmp_LTV`, `Route_LTV`

Script tabs: `Main`, `Defenitions`, `LTV load`

---

## Risks updated

| Risk | Status |
|---|---|
| QVW != QVD format | CONFIRMED — no XML, binary-only metadata |
| Script location | RESOLVED — block 0, find `b"///"` |
| Table names | RESOLVED — parse from load script |
| Field names | RESOLVED — schema blocks, `0x04` + len + name |
| Encryption detection | PARTIAL — zlib failure heuristic |
| PyQvd 2.3.2 for Phase 2 | DEFERRED — symbol format confirmed compatible |
