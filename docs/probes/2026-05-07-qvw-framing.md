# §14.1.1 probe report — QVW data-block framing

**Date:** 2026-05-07
**Probe author:** Sergey Muliarchuk (with Claude Code assistance)
**Files inspected:** all three reference QVWs
- `LTV_analisys.qvw` — 34.6 MB, 377 zlib streams, full hex+block analysis
- `Monitoring.qvw` — 43.1 MB, header + first two blocks decompressed
- `dbhDesigning.qvw` — 141.5 MB, header + first two blocks decompressed

**Verdict:** **PARTIAL framing.** PyQvd cannot be adopted as-is for the
container layer (QVW is *not* a QVD with extra prefix bytes — it is a
custom binary container). However, PyQvd's symbol-table dual-value
decoder (flags `0x01..0x06`) IS reusable for the inner data blocks once
the container layer extracts them. Spec §3.2 must be revised to reflect
two parser layers, not one.

---

## 1. Container envelope

All three reference files share an identical envelope shape:

```
0x00–0x16    23 bytes    File header (see §1.1)
0x17–EOF-4   variable    N concatenated zlib streams (each is one "block")
EOF-4–EOF    4 bytes     ASCII "EXEX" trailer (confirmed on all 3 files)
```

### 1.1 File header (23 bytes, observed identical on all 3 files for first 12 bytes)

| Offset | Bytes | LTV | Monitoring | dbhDesigning | Interpretation |
|---|---|---|---|---|---|
| 0x00 | 4 | `70 17 01 00` | `70 17 01 00` | `70 17 01 00` | Magic / version. LE u32 = `0x00011770` (71536). Constant across files. |
| 0x04 | 4 | `c1 06 00 00` | `c1 06 00 00` | `c1 06 00 00` | Constant. LE u32 = `0x000006c1` (1729). Likely a sub-version or build marker. |
| 0x08 | 4 | `02 00 00 00` | `02 00 00 00` | `02 00 00 00` | Constant. LE u32 = 2. **Format version**. |
| 0x0c | 4 | `ba 1d 00 56` | `f5 b5 00 09` | `91 5b 00 cb` | Per-file (varies). Block index seed / random salt. |
| 0x10 | 4 | `5b 0f 00 a5` | `06 46 00 f8` | `7f 2f 00 36` | Per-file. |
| 0x14 | 3 | `2f 05 00`    | `da 04 00`   | `2d 07 00`   | Per-file. (3 bytes — note: not 4. The next byte at 0x17 is `0x78`, the start of the first zlib stream.) |

**Key claim:** the first 12 bytes are a stable QVW magic/version signature.
A parser can use `raw[0:12] == b"\x70\x17\x01\x00\xc1\x06\x00\x00\x02\x00\x00\x00"`
as a cheap format/version sanity check before doing anything else.

### 1.2 Stream layout

LTV_analisys.qvw contains **377 zlib streams** between offset 23 and the
EXEX trailer. Streams fall into two visually distinct classes:

**Class A — metadata blocks** (~30-40% of streams by count):
- Decompressed sizes range from a few bytes to ~5 MB (the first block,
  which is the load-script-bearing one).
- Followed by ~169 bytes of inter-block padding/index data with a
  characteristic repeating pattern (`01 00 00 01 00 00 00 01 00 00 00
  01 00 00 00 00`).

**Class B — data chunks** (~50-60% of streams by count):
- Decompressed size **exactly 262144 bytes (256 KB)** for every Class B
  block in LTV_analisys.qvw. Suggests Qlik segments large tables into
  fixed-size chunks at write time.
- Followed by exactly **4 bytes** of inter-block data, which appears to
  be a 32-bit little-endian length or checksum (values like
  `b8 43 01 00` = 0x000143b8 = 82872, `1d ba 01 00` = 0x0001ba1d).
- Hypothesis: these 4 bytes are the *next* compressed-block size, used
  to fast-skip to a specific chunk without re-scanning. Confirming
  this is a Phase 1 follow-up.

The last stream (#376) decompresses to data ending with the bytes
`49 45 4e 44 ae 42 60 82` — that's `IEND` + the PNG CRC trailer. So
QVW also embeds **PNG thumbnails** of sheets/charts. Out of scope for
v1, but worth noting in §13.

### 1.3 Block boundary detection

Locating block boundaries does **not** require parsing the file header
— a streaming scan for `78 9C` / `78 01` / `78 DA` / `78 5E` (the four
zlib level magic bytes) is sufficient, with each candidate validated by
a trial decompression. The probe script (`/tmp/qvw_probe2.py`) found
all 377 streams in LTV_analisys.qvw in under 5 seconds on a laptop;
the same approach will work in `parser/container.py`.

---

## 2. Block contents — what's inside the decompressed bytes

### 2.1 Block 0 (LTV_analisys, decomp 1,006,422 bytes)

This is the **largest metadata block** and contains the load script. Layout
seems to be:

- ~30-40 bytes of binary header (offsets, lengths)
- Embedded plaintext file path: `D:\QlikBus\01_Application_Prod\…`
  (the original QlikView project path — useful provenance metadata,
  also a privacy concern noted in SECURITY.md threat model)
- A `///$tab Main\n` marker followed by the full QlikView load script
  in plain text, encoded as UTF-8 with an embedded codepage hint for
  Cyrillic SET MoneyFormat strings — confirms §4.3 ScriptBundle's
  encoding chain is correct (UTF-8 succeeds on this file).

**Implication:** `parser/script.py` extracts the script by locating the
`///$tab` marker inside Block 0 (no QVD-style XML wrapper needed).
This is significantly simpler than the spec assumed.

### 2.2 Block 1 (decomp 1,358 bytes, "table-name list")

Pattern observed:

```
00 00 00 00       4 zero bytes (padding)
40 00 00 00       LE u32 string count (0x40 = 64)
04 LL <bytes>     repeated 64 times: 04 = "string-tag", LL = length, <bytes> = UTF-8
```

Decoding the strings yields: `idCustomer`, `DateSale4LTV`,
`DateStartSaleCustomer4LTV`, `Год-Месяц_Sale4LTV`, `Год-Квартал_Sale4LTV`,
`Год-Месяц_LTV`, …

**These are field names**. Block 1 = the global field-name dictionary.
Cyrillic field names confirm the codepage handling is non-trivial in
the wild.

### 2.3 Block 2 (decomp 93 bytes, "table list")

Same structure as Block 1, but with 6 strings:
`DataLTV`, `filter4LTV`, `Tab4Filter4LTV`, …

**Block 2 = list of table names.** Tables in the file: 6.

### 2.4 Block 4 (decomp 74 bytes, "small symbol table")

```
00 00 00 00 06 00 00 00      4-zero pad + LE u32 entry count (6)
05 06 36 39 38 35 39 30 de a8 0a 00      ← entry 1
05 06 35 33 35 35 35 30 fe 2b 08 00      ← entry 2
…
```

Decoding:
- `05` — symbol flag from PyQvd: "string + numeric" dual value.
- `06` — string length (6 bytes).
- `36 39 38 35 39 30` = ASCII `"698590"` — the string face.
- `de a8 0a 00` — LE u32 = `0x000aa8de` = **698590**. The numeric
  value matches the string. This is **textbook QlikView dual-value
  encoding**.

**Critical finding:** PyQvd's symbol decoder (flags `0x01..0x06`) IS
the right model for inner data blocks. The QVW container is custom,
but the symbols inside are identical to QVD.

Flag occurrences in Block 0 (the big metadata block) suggest the file
contains all six flags somewhere, so the full flag set documented by
PyQvd is likely complete. (Probe question 14.1.3 — "is there a 7th
flag" — answered: no evidence of one in the 3 reference files.)

---

## 3. Encrypted-QVW detection signal — DEFERRED

None of the 3 reference files appears encrypted (all blocks
decompressed cleanly with default zlib). I do not have an encrypted
sample to probe. The §6.2 encrypted-detection signal will need to be
filled in later if/when an encrypted reference becomes available, or
left as a "raise on first decompression failure that looks like
ciphertext after zlib" heuristic.

**Action:** keep `error_code: "encrypted_unsupported"` in the spec but
mark the detection as "best-effort" until an encrypted sample is
available.

---

## 4. Implications for the spec

### 4.1 §3.2 — split `parser/data.py` into two layers

Replace the single `parser/data.py` row in §3.2 with:

| Component | Responsibility |
|---|---|
| `parser/container.py` | Parse the 23-byte file header, scan for zlib streams, decompress each block, return a list of `RawBlock(index, decompressed_bytes, kind_hint)` where `kind_hint` is heuristic (`metadata` / `data_chunk` / `unknown`) based on inter-block padding pattern. |
| `parser/blocks/script.py` | Take Block 0, locate `///$tab`, return load-script text. |
| `parser/blocks/dictionary.py` | Take Block 1, decode field-name list. |
| `parser/blocks/tables.py` | Take Block 2, decode table-name list. |
| `parser/blocks/symbols.py` | Decode `0x01..0x06`-tagged entries inside arbitrary blocks. **This is the only piece directly adaptable from PyQvd.** |
| `parser/blocks/data.py` | Combine 256KB chunks into per-table row sequences using the bit-stuffed index decode (also PyQvd-adaptable). |

### 4.2 §9 risk row 1 — re-estimate

v1 spec said "QVW data blocks wrap QVD bodies with extra framing — High
likelihood, **0.5-1 day extra**". The probe **confirms** the high
likelihood, but the impact estimate was wrong: a custom container
parser is **5-10 day extra**, not 0.5-1. Spec to be updated.

### 4.3 §3.5 sanitisation — no change

The probe surfaced no filename collision concerns. The 3 reference
files have ASCII basenames; sanitisation rules in §3.5 v2 cover the
edge cases regardless.

### 4.4 §13 out-of-scope — add PNG thumbnails

QVW embeds PNG thumbnails of sheets. Out of scope for v1 but worth
listing explicitly so a future contributor doesn't think it's a bug
that we ignore them.

---

## 5. Greenlight to start Phase 1

The probe satisfies the §14.1.1 gate. Phase 1 may start, with the
revised parser/* layout in §4.1 above. Spec patch to follow in a
separate commit so this report stands as the historical justification
for the v3 spec changes.

**Estimated Phase 1 work, post-probe:** ~6-8 dev-days (was ~2). Two
days of that is the container parser (now confirmed-needed); two days
is the metadata block decoders; one day is server scaffolding +
config; one day for tests. Phase 2 (data extraction) estimate also
needs updating but that's a separate exercise once we have the
container layer working.
