# Security policy

## Scope

`mcp-qlikview` is an MCP server that reads QlikView `.qvw` files from a
user-configured directory and exposes their contents to a single MCP
client (e.g., Claude Code) over stdio. There is no network listener, no
authentication, no multi-tenancy. The server runs with the user's
privileges and reads only files inside `QVW_DIR` plus any explicit
absolute-path overrides.

The threat model worth thinking about:

- A **malicious QVW file** placed in `QVW_DIR` could exploit a parser
  bug (e.g., zlib bomb, malformed header → arbitrary memory read).
  We treat parser robustness against adversarial input as a security
  property, not just a quality property.
- A **prompt-injection** attack delivered via load-script comments,
  variable values, or chart captions could try to coerce Claude Code
  into actions on the operator's behalf. The server itself does not
  interpret prompt content — it returns data verbatim — but downstream
  prompts in Claude Code might. We document known patterns in
  `docs/LIMITATIONS.md` once Phase 2 ships.
- A **path-traversal** attack via `qvw="/abs/../../etc/passwd"` is in
  scope: the server must reject any non-`.qvw` extension and refuse to
  read special files (sockets, FIFOs, devices).

Out of scope:

- Multi-user authorisation. Use OS file permissions on `QVW_DIR`.
- Encryption at rest. QVW files are read as-is; there is no on-disk
  cache.
- Network security. The MCP transport is stdio; if you tunnel it over
  network, that's the tunnel's concern.


## Reporting a vulnerability

Please do **not** open a public GitHub issue for security
vulnerabilities. Instead, email **muliarchuk.sergii@gmail.com** with:

- A description of the issue and the impact you observed.
- A minimal reproduction (synthetic QVW preferred — do not include
  confidential production files).
- The version of `mcp-qlikview`, Python, and OS where you observed it.
- Whether you'd like to be credited in the release notes.

Acknowledgement target: within 5 business days.
Triage target: within 14 days.
Fix target: depends on severity. Critical issues (RCE, sandbox escape)
get an out-of-band patch release. Non-critical issues batch into the
next regular release.

If you do not receive an acknowledgement within 5 business days, please
nudge the same address — emails get lost.


## Disclosure

Once a fix is released, the vulnerability is documented in the release
notes and credited (with permission) to the reporter. CVE assignment
is coordinated through GitHub Security Advisories when warranted by
severity.
