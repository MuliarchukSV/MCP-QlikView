# Contributing to MCP-QlikView

Thanks for considering a contribution. This project is in design phase
through Phase 1 (see `docs/specs/2026-04-23-mcp-qlikview-design.md` for
the canonical scope). External contributions are welcome **after** the
§14.1.1 framing probe is published — until then, the architectural
risks are too open-ended for confident PR review.

If you're here before Phase 1 starts, the highest-leverage contribution
is a probe report: take any QVW file you own, hex-dump its decompressed
data blocks, and open an issue comparing the framing to the QVD layout
documented in PyQvd. Three independent confirmations dramatically
de-risk the parser design.


## Development workflow

1. Read `docs/specs/2026-04-23-mcp-qlikview-design.md` end-to-end. The
   spec is the source of truth; code disagreeing with the spec gets
   rejected unless the PR also updates the spec with reasoning.
2. Read `docs/DEV_SETUP.md` for the recommended Claude Code plugins.
   None are hard requirements — the workflow runs without them, just
   slower.
3. Open an issue **before** non-trivial work. State which spec section
   you intend to implement and what your approach is.
4. Branch off `main`. Follow TDD: write the test first, see it fail,
   make it pass.
5. Run `ruff check`, `mypy`, `pytest` locally before pushing. CI runs
   the same.
6. Open a PR with a description that links the issue and lists which
   spec sections moved. Include the `Constraint:` / `Rejected:` /
   `Scope-risk:` / `Not-tested:` trailers in your commits — see
   `pull_request_template.md` (added when Phase 1 lands).


## Code style

- Python 3.10+, strict type annotations, no `Any` in public signatures.
- `ruff` and `mypy` configured in `pyproject.toml` (added in Phase 1)
  are authoritative; if the linters disagree with this guide, the
  linters win.
- One-line comments only when the *why* is non-obvious. Never document
  *what* the code does — that's the code's job.


## Reporting bugs

Use the bug-report issue template. The most useful bug reports include:

- Exact `mcp-qlikview` version (`uvx mcp-qlikview --version`).
- Python version, OS.
- A minimal QVW that reproduces the issue (synthetic if possible — please
  don't share confidential production files).
- Full `ErrorEnvelope` JSON if the failure surfaced through MCP.

If you cannot share a QVW, describe its shape: number of tables, total
size, presence of synthetic keys, presence of `-prj` folder, encoding of
the load script (UTF-8 vs Windows-1251 etc.).


## Security issues

Do **not** open public issues for security vulnerabilities. See
`SECURITY.md` for the disclosure channel.


## Licence

By contributing, you agree that your contributions are licensed under
the MIT licence (see `LICENSE`). Third-party code added must be
documented in `LICENSE-THIRD-PARTY` with its original licence and an
attribution note.
