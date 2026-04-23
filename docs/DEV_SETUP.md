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

## 2. Claude Code plugins (required)

Install these via the plugin manager inside Claude Code (`/plugin` command, or see https://claude.com/claude-code for current UX):

| Plugin | Why we use it in this project |
|---|---|
| **superpowers** | Design and implementation workflow. Provides `brainstorming`, `writing-plans`, `executing-plans`, `test-driven-development`, `systematic-debugging`, `verification-before-completion`, `using-git-worktrees`. The spec (`docs/specs/2026-04-23-mcp-qlikview-design.md`) was produced via `superpowers:brainstorming`; implementation work uses `superpowers:writing-plans` → `superpowers:executing-plans`. |
| **claude-code-guide** (or equivalent) | Answers questions about Claude Code / Agent SDK / Anthropic API. Useful when wiring up the MCP server to the `mcp` Python SDK. |

---

## 3. Claude Code skills (recommended)

Skills are smaller than plugins and can live at the user level (`~/.claude/skills/`) so they're available across all your projects. For this project, these are the ones we actually reach for:

| Skill | What it does | Where to get it |
|---|---|---|
| **mcp-server-dev** | Guides you through building production-grade MCP servers (TypeScript or Python). Already useful for Phase 1 (server scaffolding). | Ships with the `superpowers` / `claude-plugins-official` pack, or grab it from https://github.com/anthropics/claude-code (check the plugin marketplace). |
| **doc-review** | Adversarial spec/plan review. We've run it twice on the design spec so far; plan to run it on the implementation plan before writing code. | Either install via a plugin that ships it, or author your own stub using the same checklist pattern (it's mostly prompt engineering around a severity-labeled checklist). |
| **ralph** | Persistence loop for critical tasks — run → verify → retry. Handy for Phase 1 parser work where we iterate against the 3 reference QVWs. | Similar: plugin marketplace or user-authored. |
| **mcp-publish** | Phase 3 only: publishes the package to PyPI / Smithery with the right metadata. | Not needed until release. |

If a skill is unavailable in your environment, the workflow still works — you just run the steps manually. The skills are accelerators, not hard dependencies.

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
