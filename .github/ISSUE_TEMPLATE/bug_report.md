---
name: Bug report
about: Something doesn't work as the spec says it should
title: "[bug] "
labels: bug
assignees: ''
---

## What happened

<!-- Concise: what you ran, what you expected, what you got. -->

## Reproduction

```
# steps the maintainer can run
```

If the bug is in the parser, please include either:

- A minimal **synthetic** QVW that reproduces it (preferred), or
- A description of the QVW shape: number of tables, total size,
  presence of synthetic keys, presence of `-prj` folder, encoding of
  the load-script (UTF-8 / Windows-1251 / other).

Do **not** attach confidential production QVWs to public issues.

## Error output

If the failure surfaced through MCP, paste the full `ErrorEnvelope`:

```json
{
  "error_code": "...",
  "category": "...",
  "message": "...",
  "hint": "...",
  "details": { ... }
}
```

## Environment

- `mcp-qlikview` version (run `uvx mcp-qlikview --version`):
- Python version:
- Operating system:
- DuckDB version (if known):

## Severity (your guess)

- [ ] Cosmetic — wrong field name, typo, confusing message
- [ ] Functional — feature documented in the spec doesn't work
- [ ] Data integrity — query returns wrong rows or wrong values
- [ ] Crash — server exits or hangs
- [ ] Security — possible parser exploitation (please see SECURITY.md
      first; don't post details here)
