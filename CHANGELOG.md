# Changelog

All notable changes to this project will be documented in this file.
Format based on [Keep a Changelog](https://keepachangelog.com/).

---

## [Unreleased] — 2026-03-25

### Removed
- Deleted all v1 code, template library, custom retry layers, and PyBreaker circuit-breaker wrapper — these were over-engineering that the LangGraph runtime already handles natively

---

## [3.0.0-alpha.1] — 2026-03-25 — Phase v3a: Native Tool Migration

### Added
- Auto-chunking reviewer: large codebases are reviewed in chunks automatically
- 29 simulation tests validating the 3 pipeline fixes below

### Fixed
- Builder now correctly targets `target_dir` instead of CWD when writing files
- `technology_choices` field properly threaded through pipeline state
- Worktree and skills integration switched to native Claude tool invocations
- Gemini `STRUCTURED_OUTPUTS` mode enabled — eliminates hollow stub code from builder
- 3-iteration dogfood loop confirmed: builder produces real, runnable code

---

## [2.1.0] — 2026-03-24 — Phase v2g: Codebase Awareness

### Added
- Codebase-aware pipeline: builder and verifier now read target repo structure before acting
- Inter-task context threading so TDD tasks share state (test → impl → verify)
- Workspace agent correctly targets the requested repo rather than the pipeline repo itself
- KFS manifest data structures (Pydantic models), parser, and schema manager (v1.0) built via dogfood run — pipeline refused to ship bad code, then shipped working code on retry

### Fixed
- Builder and verifier fall back to `target_dir` when worktree is unavailable

---

## [2.0.0] — 2026-03-23 — Phase v2: LangGraph Rebuild

This release replaced the original sequential script with a proper LangGraph state machine.

### Added
- Full 10-stage LangGraph pipeline: Intake → Strategic Review → Architecture → Plan → Setup → Build → Verify → Review → Ship → Evolve
- LangFuse observability integration — every node emits spans with cost and token counts
- Real cost tracking across all 5 LLM agents (Gemini builder, Claude reviewer, etc.)
- Evolve stage steps 4–6: retrospective analysis, gap logging, cost enforcement
- `check_run_cost` enforcer: pipeline aborts if a run exceeds budget
- Enforcement skills spec and 34 audit gaps documented in pipeline bible

### Fixed
- Phase v2a: 3 critical wiring bugs caught by `/verify-done` gate
- Phase v2b: builder now writes files to disk; state plumbing corrected end-to-end
- Phase v2c: LLM cost tracking was placeholder — replaced with real token accounting
- Phase v2d: 8 medium-severity issues resolved; 114 tests passing
- Phase v2e: 7 low-severity issues resolved; full dev-loop evidence attached

---

## [1.0.0] — 2026-03-14 to 2026-03-20 — Initial Build

### Added
- 9-stage pipeline scaffold with template library and enforcement gates
- Test suite grown from 133 to 261 tests (122 net new); false-green assertions replaced with `pytest.skip` in templates
- `all_green` verifier: requires zero skipped layers; exposes `fully_verified` field
- Dogfood audit report: 216 requirements checked, 17 gaps identified

### Fixed
- `ruff` lint violations resolved across all tools and tests; `templates/` excluded from lint (intentional unused imports for code stamping)
