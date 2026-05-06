# Development setup

Guide for anyone (human or Claude Code instance) contributing to this repo.

The project uses an **LLM-assisted workflow**: most planning, spec review, and implementation steps run inside Claude Code with specific plugins and skills. This document lists what you need and how to install it.

---

## 1. Claude Code (required)

Install Claude Code: https://claude.com/claude-code

When you open this repository in Claude Code, it will read:

- **`CLAUDE.md`** (none yet — will appear with Phase 1 scaffolding)
- **`docs/specs/2026-04-23-mcp-qlikview-design.md`** — the canonical design for the whole project. Always the source of truth.

---

## 2. Claude Code plugins (optional accelerators)

None of the plugins below are hard requirements. The workflow runs
without them — you just do the steps by hand. They're listed here so
new contributors know what the maintainer reaches for.

| Plugin | Why we use it | How to install |
|---|---|---|
| **superpowers** | Design + implementation discipline. Provides skills `brainstorming`, `writing-plans`, `executing-plans`, `test-driven-development`, `systematic-debugging`, `verification-before-completion`, `using-git-worktrees`, `adversarial-review`. The v1 spec was produced via `brainstorming`; the v2 revision (2026-05-06) was driven by `adversarial-review`. | Open Claude Code → `/plugin` → install from the marketplace name `superpowers`. If your build doesn't include the marketplace UI, the plugin source is publicly available via Anthropic; check the Claude Code release notes for current install instructions. |
| **claude-code-guide** | Answers questions about Claude Code / Agent SDK / Anthropic API. Useful when wiring up the MCP server to the `mcp` Python SDK. | Same as above, marketplace name `claude-code-guide`. |

If a plugin is unavailable in your environment, the workflow still
works — you just run the steps manually.

---

## 3. Claude Code skills (optional)

Skills can live at the user level (`~/.claude/skills/`) and are
available across all projects. The maintainer uses these on this repo:

| Skill | Used for | Source |
|---|---|---|
| `mcp-server-dev` | Phase 1 server scaffolding (`mcp` Python SDK boilerplate, stdio transport patterns). | Bundled with `superpowers`. Fallback: read the `mcp` Python SDK README and the `mcp-server-motherduck` repo (cited in spec §12) — same pattern. |
| `adversarial-review` | Pre-merge spec / plan critique. The v2 spec changelog (§15) was generated from one such pass. | Bundled with `superpowers`. Fallback: run a peer review with the explicit instruction "find at least 10 problems, no 'looks good'" — that is the entire skill. |
| `doc-review` | Lighter-weight spec review (consistency, completeness, missing requirements). Run it before Phase 1 plan ships. | Bundled with `superpowers`. Fallback: ad-hoc reviewer pass against `docs/specs/`. |
| `ralph` | Persistence loop for the §14.1.1 probe (run → verify → retry). | Bundled with `superpowers`. Fallback: a shell loop against the probe script. |
| `mcp-publish` | Phase 3 only: PyPI release packaging with the right metadata. | Bundled with `superpowers`. Fallback: `pyproject.toml` + `twine upload` per the official PyPA tutorial. |

**Important:** none of these skills is a public, pinned-version
package. They ship inside `superpowers` and may be renamed or
restructured between releases. If you need a stable contract, read the
skill source once and inline what you need — they're all just prompts
plus checklists, not runtime code.

---

## 4. Python environment

*Deferred until Phase 1 scaffolding lands (pyproject.toml, uv / pip instructions).*

The spec (§10) lists runtime dependencies: `mcp>=1.0`, `duckdb>=0.10`, `pyarrow>=15`, `pydantic>=2.5`, `pydantic-settings>=2.1`, `watchdog>=4.0`, `aiorwlock>=1.3`, `PyQvd>=3.0`. Python 3.10+.

---

## 5. Test fixtures (optional for local dev)

The three reference QVW files used for golden tests are **not committed** (they're production data owned by the project author). See spec §8.2 — they're referenced via `MCP_QVW_TEST_FIXTURES_DIR`. Tests that need them skip gracefully when the env var is unset, so you can clone + run the synthetic-only suite without fixtures.

If you're the project author or a trusted collaborator, drop the 3 QVWs into `tests/fixtures/` (gitignored) before running the golden suite.

---

## 6. Where things live

```
MCP-QlikView/
├── docs/
│   ├── specs/       ← spec (source of truth)
│   └── DEV_SETUP.md ← this file
├── src/             ← (empty; will appear in Phase 1)
├── tests/           ← (empty; will appear in Phase 1)
└── .claude/         ← gitignored; contributor-local Claude Code config,
                       skills, agents. Each contributor sets up their own.
```

---

## 7. Contributing flow (once Phase 1 starts)

1. Read `docs/specs/2026-04-23-mcp-qlikview-design.md`.
2. Read the current implementation plan (not yet written — will live at `docs/plans/<date>-<topic>-plan.md`).
3. Pick an open task, branch off `main`, follow TDD (`superpowers:test-driven-development` if you have it).
4. Before claiming completion, run `superpowers:verification-before-completion` — commit only with passing tests and build evidence.
5. Open a PR. `superpowers:requesting-code-review` is the recommended pre-PR pass.
