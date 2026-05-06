---
name: §14.1.1 probe finding
about: Report what you found inspecting QVW data-block framing
title: "[probe] "
labels: probe, phase-1
assignees: ''
---

This template is for the pre-Phase-1 probe described in
`docs/specs/2026-04-23-mcp-qlikview-design.md` §14.1.1. Independent
findings dramatically de-risk the parser design — every confirmation
is welcome.

## What you inspected

- QVW file: <!-- filename or "private; not sharable" -->
- File size:
- QlikView version that wrote the file (if known):
- Tool used: <!-- xxd, hex editor, custom script, … -->

## Framing observation

Did the decompressed data blocks match QVD layout (XML header → symbol
table → bit-stuffed index)?

- [ ] Yes — clean QVD compatibility, PyQvd decoder should adapt cleanly.
- [ ] Partial — same components but with extra framing (describe below).
- [ ] No — fundamentally different layout (describe below).

## Details

```
# hex excerpt with offsets, your annotations
```

## Symbol-table flags observed

Which of `0x01..0x06` did you encounter? Did you see anything outside
that range?

## Encrypted / section-access detection

If you have an encrypted QVW available, what's the distinguishing byte
or pattern in the header?

## Conclusions

What does this mean for the spec? (e.g., "PyQvd works as-is", "needs a
3-byte length prefix per block", "incompatible — recommend Engine API
fallback").
