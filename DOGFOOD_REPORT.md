# Pineapple Pipeline Dogfood Report

**Date:** 2026-03-19
**Method:** Spec-vs-implementation audit + functional testing (5 agents, 3 waves)
**Spec:** `docs/superpowers/specs/2026-03-15-pineapple-pipeline-design.md` (958 lines)
**Implementation:** `D:\GitHub\pineapple-pipeline\` (10 tools, 13 templates, 133 tests)

---

## Executive Summary

- **216 requirements extracted** from the spec across 3 layers, 10 stages, and 7 cross-cutting concerns
- **133 tests pass** (0 failures), but quality is uneven — 4 tools have zero coverage, 2 test templates give false green
- **10 CLI tools all run** — all are real implementations, not stubs. 6 are bonus (not in spec)
- **SKILL.md has 4 critical misalignments** with spec — missing CEO stage, shifted numbering, no state machine reference
- **2 of 5 hookify rules are dead** (never fire due to engine bugs), 2 are weak, 1 works correctly
- **All 13 templates exist** — 10 production-ready, 2 test templates are structural stubs

**Overall maturity: Alpha** — Strong tooling foundation with critical gaps in orchestration, enforcement, and verification integrity.

---

## Tool Functional Results

All 10 tools in `tools/` were executed. All are real implementations (not stubs).

| Tool | Runs? | Exit | Real? | Spec Tool? | Notes |
|------|-------|------|-------|-----------|-------|
| `pineapple_doctor.py` | Yes | 1 | REAL (448 lines) | Yes | 5/11 pass, 5 optional skip, 1 fail (Docker not running). Correct behavior. |
| `apply_pipeline.py` | Yes | 0 | REAL (421 lines) | Yes | Dry-run works. Detects project structure, fills 13 templates, 3 stack types. |
| `pineapple_verify.py` | Yes | 0 | REAL (478 lines) | Yes | **BUG: `all_green: true` with 5/6 layers skipped.** See VER-001. |
| `pineapple_evolve.py` | Yes | 0 | REAL (359 lines) | Yes | Steps 1-3 work. Steps 4-6 are connectivity-gated stubs ("not yet implemented"). |
| `pineapple_config.py` | Yes | 0 | REAL (212 lines) | Bonus | Pydantic-validated config. Migration stub (no-op for v1.0.0). |
| `pipeline_state.py` | Yes | 0 | REAL (283 lines) | Bonus | 9-stage state machine. Atomic writes, retry logic, timeout support. |
| `pipeline_tracer.py` | Yes | 0 | REAL (214 lines) | Bonus | JSONL tracer with 7 event types, cost tracking, jq-queryable. |
| `pineapple_audit.py` | Yes | 0 | REAL (181 lines) | Bonus | 4 compliance checks, integrity hash re-verification. 100% pass. |
| `pineapple_cleanup.py` | Yes | 0 | REAL (236 lines) | Bonus | Finds stale runs/worktrees/records. Safe dry-run default. |
| `pineapple_upgrade.py` | Yes | 0 | REAL (239 lines) | Bonus | Template version checking with unified diff. 12 NEW, 1 SKIP. |

**Key finding:** Spec mentions 4 tools. Implementation has 10. The 6 bonus tools (config, state, tracer, audit, cleanup, upgrade) are all production-quality and fill real gaps. They should be added to the spec.

---

## SKILL.md Alignment Issues

216 spec requirements were extracted. SKILL.md was compared against the spec on 9 alignment points.

**Result: 0 fully aligned, 5 partially aligned, 4 misaligned.**

### Misalignments

| # | Issue | Spec Says | SKILL.md Says | Severity |
|---|-------|-----------|---------------|----------|
| **SKL-001** | Stage count | 10 stages (0-9) | "9 stages" (description line 2, section header line 60) | High |
| **SKL-002** | Stage 1 naming | "Strategic Review" with CEO skill, Fact-Finding Agent, Strategic Brief output | "Brainstorm" with `superpowers:brainstorming` — CEO layer entirely absent | Critical |
| **SKL-003** | Stage 2 missing | "Architecture" — turns Strategic Brief into technical design, spec review loop (max 5 iterations) | Conflated with Brainstorm. No two-phase approach (strategic then technical). | Critical |
| **SKL-004** | State machine | `.pineapple/runs/<uuid>/state.json` as single source of truth, atomic writes, "state machine wins when it disagrees with plan checkboxes" | Not referenced at all. Resume mechanism is "plan checkboxes + git commits" only. | High |

### Partial Alignments

| # | Issue | What's Present | What's Missing |
|---|-------|---------------|----------------|
| **SKL-005** | Skill mappings | 8/10 correct (Stage 3-9 all correct, just renumbered) | `pineapple:ceo-review` missing, brainstorming missing Strategic Brief input |
| **SKL-006** | Gate definitions | 5/10 gates fully match spec text | Stage 0 gate, Stage 1 gate missing. Stages 2-4 gates missing specific sub-conditions ("no code written", "checkboxed steps, file map", "services connected") |
| **SKL-007** | Path routing | `<50 lines` criterion present for Lightweight | File count criteria missing entirely (`<3 files` for Lightweight, `<8 files` for Medium) |
| **SKL-008** | Failure handling | Circuit breaker concept present (max 3 cycles, wrong stage numbers) | Per-stage retry counts missing, rollback strategy missing (git revert not reset --hard), wall-clock 4h timeout missing, 3 specific post-circuit-breaker options missing |
| **SKL-009** | Cost awareness | $200 ceiling present, per-task estimates match | 3 specific user options after ceiling hit missing (continue, pause+resume, simplify) |

### Stage Number Mapping

| Spec Stage | Spec Name | SKILL.md Stage | SKILL.md Name | Delta |
|-----------|-----------|---------------|---------------|-------|
| 0 | Intake | 0 (routing) | Stage Routing | Treated as preamble, not a stage |
| 1 | Strategic Review | — | **MISSING** | Entire stage absent |
| 2 | Architecture | 1 | Brainstorm | Conflated, -1 |
| 3 | Plan | 2 | Plan | -1 |
| 4 | Setup | 3 | Setup | -1 |
| 5 | Build | 4 | Build | -1 |
| 6 | Verify | 5 | Verify | -1 |
| 7 | Review | 6 | Review | -1 |
| 8 | Ship | 7 | Ship | -1 |
| 9 | Evolve | 8 | Evolve | -1 |

---

## Hookify Gate Coverage

### Per-Rule Status

| Rule | Purpose | Fires? | Check Correct? | Overall |
|------|---------|--------|---------------|---------|
| 1. no-code-without-spec | Prevent coding without design spec | Yes (narrow) | Wrong — checks text content, not file existence | **WEAK** |
| 2. no-impl-without-plan | Prevent implementation without plan | Yes (narrow) | Wrong — checks text content, not file existence | **WEAK** |
| 3. no-merge-without-verify | Prevent merge without verification | **NEVER** | **Two bugs:** `event: tool` should be `event: bash`; `field: content` should be `field: command` | **DEAD** |
| 4. no-done-without-evidence | Prevent marking tasks done without evidence | **NEVER** | **Bug:** `field: content` doesn't resolve for TodoWrite (tool_input has `todos`, not `content`) | **DEAD** |
| 5. no-gap-close-without-verify | Prevent closing bible gaps without verification | Yes | Correct — checks written text for `status: closed` | **GOOD** |

### Spec Gate Coverage

| Spec Gate | Hookify Rule? | Notes |
|-----------|--------------|-------|
| Stage 0: context loaded, classified | No | Orchestrator concern per ADR-PPL-14 |
| Stage 1: Strategic Brief exists | No | Stage absent from SKILL.md entirely |
| Stage 2: Design spec exists, no code written | Rule 1 (weak) | Only `app.+\.py` files, checks text not file existence |
| Stage 3: Plan with checkboxes, user approved | Rule 2 (weak) | Same limitations as Rule 1 |
| Stage 4: Worktree, deps, tests pass | No | Orchestrator concern |
| Stage 5: All tasks complete, reviews pass | Rule 4 (**dead**) | Never fires due to field resolution bug |
| Stage 6: All 6 layers pass, fresh evidence | No | Orchestrator concern |
| Stage 7: No Critical/Important issues | No | Orchestrator concern |
| Stage 8: Merged/PR, bible updated | Rule 3 (**dead**) | Never fires due to event + field bugs |
| Stage 9: Handoff written, bible updated | Rule 5 (good) | Only covers gap closure, not handoff |

### Key Bypass Vectors

| Pattern | Misses |
|---------|--------|
| `app.+\.py` (Rules 1,2) | `src/main.py`, `lib/utils.py`, `services/auth.py`, `manage.py`, all `.ts/.tsx/.js` files |
| `git merge` (Rule 3) | `git pull` (implicit merge), `gh pr merge`, `git rebase`, scripts wrapping merge |
| `completed` (Rule 4) | N/A — rule never fires regardless |
| `bible.*\.yaml` (Rule 5) | `projects/triple-helix.yaml` (not named "bible"), `status:  closed` (extra space), `status: CLOSED` |

---

## Template Audit

### Existence and Quality

| Template | Lines | Real Code? | Production-Usable? |
|----------|-------|------------|-------------------|
| `Dockerfile.fastapi` | 60 | YES — multi-stage, non-root, healthcheck | Yes |
| `Dockerfile.vite` | 45 | YES — multi-stage, nginx SPA routing | Yes |
| `docker-compose.template.yml` | 52 | YES — services, healthcheck, volumes | Yes |
| `ci.github-actions.yml` | 113 | YES — lint, test, build, docker jobs | Yes |
| `env.template` | 34 | YES — structured env sections | Yes |
| `input_guardrails.py` | 125 | YES — 17 regex patterns, Starlette middleware | Yes |
| `observability.py` | 299 | YES — LangFuse, cost tracking, async wrapper | Yes (highest quality) |
| `rate_limiter.py` | 53 | YES — slowapi integration | Yes |
| `resilience.py` | 216 | YES — retry, circuit breaker (3-state FSM), fallback | Yes |
| `cache.py` | 136 | YES — TTLCache, LRU, async-safe | Yes |
| `mcp_server.py` | 68 | SCAFFOLD — FastMCP boilerplate, 1 example tool | Partial (by design) |
| `test_adversarial.py` | 168 | **STUB** — payloads defined, all `assert X is not None` | **No — false green** |
| `test_eval_benchmark.py` | 217 | **STUB** — structure + deepeval imports commented out, all `assert X is not None` | **No — false green** |

**10/13 production-ready. 2 test templates give false green. 1 scaffold (intentional).**

### apply_pipeline.py Artifact Count

Spec claims 17 artifacts. Implementation creates **18** (17 files + 1 directory):
- 13 template-copied files
- 4 generated files (`.mcp.json`, `CLAUDE.md`, `memory/MEMORY.md`, `projects/<name>-bible.yaml`)
- 1 directory (`.pineapple/`)

**Missing from spec tree:** `.pineapple/runs/` and `.pineapple/verify/` subdirectories (created at runtime by other tools, not at scaffold time). Also `pyproject.toml` and `tests/test_health.py` are in the spec tree but not generated.

**Bug:** Running with `.` as project path produces empty project name (bible file: `projects/-bible.yaml`).

---

## Test Quality Assessment

### Summary

- **133 tests, 133 passed, 0 failed** (17.10s)
- **7 test files** covering 6 of 10 tools
- **4 tools with ZERO test coverage:** `pineapple_audit.py`, `pineapple_cleanup.py`, `pineapple_upgrade.py`, `pipeline_tracer.py`

### Per-File Quality

| File | Tests | Quality (1-5) | Key Gaps |
|------|-------|--------------|----------|
| `test_apply_pipeline.py` | 24 | 4/5 | No `force=True`, no invalid stack, no CLI test |
| `test_pineapple_config.py` | 15 | 4/5 | `_migrate_config()` never exercised, JSON fallback untested |
| `test_pineapple_doctor.py` | 12 | **2/5** | 7 of 11 checks untested, dead test code (lines 82-88), mocks hide real logic |
| `test_pineapple_evolve.py` | 14 | 3/5 | Steps 4-6 completely untested, bible "done" path untested |
| `test_pineapple_verify.py` | 18 | 3/5 | **All 6 layer runners untested directly** — only mocked/skipped |
| `test_pipeline_state.py` | 22 | 4/5 | `PipelineTimeoutError` never triggered, custom max_retries untested |
| `test_templates.py` | 28 | 5/5 | Thorough: placeholder scan, AST parse, YAML validation, regression checks |

### Coverage Estimates

- **Happy path coverage: ~55%** — Strong for state machine, config, templates. Weak for doctor, evolve steps 4-6, verify layers.
- **Failure path coverage: ~25%** — Strong for state transitions. Weak for subprocess failures, timeouts, permission errors, corrupt data.

### Low-Value Tests (essentially "function exists")

1. `test_check_templates_pass` — creates temp files then ignores them, only checks `isinstance(result, CheckResult)`
2. `test_check_pydantic_pass` — passes because pydantic is installed in test env, tests nothing
3. `test_pass_result` / `test_fail_result` (doctor) — constructs dataclass, reads field back
4. `test_done_result` (evolve) — same pattern
5. `test_pass_result` (verify) — same pattern
6. `test_run_doctor_returns_report` — mocks ALL checks, only tests that `run_doctor()` returns a `DoctorReport`

### Untested Spec Requirements (Critical)

1. Path routing logic (Lightweight/Medium/Full) — no code or tests exist
2. Stage gate enforcement — not tested
3. Cost tracking/ceiling enforcement at runtime — not tested
4. All 6 verification layer runners — only mocked, never directly tested
5. Evolve steps 4-6 (Mem0, Neo4j, DeepEval feeds) — no tests at all
6. Wall-clock timeout (`PipelineTimeoutError`) — never triggered in tests
7. LangFuse/Mem0/Neo4j integration — no tests
8. CLI entry points for all tools — untested

---

## Requirement Coverage Matrix (Summary)

| Category | Requirements | Tested | Partially Tested | Untested |
|----------|-------------|--------|-------------------|----------|
| L1: Bootstrap (config, doctor, services) | 28 | 8 | 5 | 15 |
| L2-S0: Intake (routing) | 20 | 0 | 3 | 17 |
| L2-S1: Strategic Review | 11 | 0 | 0 | 11 |
| L2-S2: Architecture | 13 | 0 | 0 | 13 |
| L2-S3: Plan | 12 | 0 | 0 | 12 |
| L2-S4: Setup | 13 | 3 | 4 | 6 |
| L2-S5: Build | 13 | 2 | 3 | 8 |
| L2-S6: Verify | 16 | 5 | 4 | 7 |
| L2-S7: Review | 11 | 0 | 0 | 11 |
| L2-S8: Ship | 12 | 1 | 2 | 9 |
| L2-S9: Evolve | 12 | 4 | 3 | 5 |
| CC: Cross-cutting | 55 | 5 | 8 | 42 |
| **TOTAL** | **216** | **28 (13%)** | **32 (15%)** | **156 (72%)** |

Most untested requirements are in the orchestration layer (stages 0-3, 7) and cross-cutting concerns (failure handling, circuit breaker, rollback, state machine, cost model). The implementation layer (tools, templates) has much better coverage.

---

## Priority Gaps (Fix These First)

### P0 — Blocks Trustworthy Usage

| ID | Gap | Impact | Fix |
|----|-----|--------|-----|
| **VER-001** | `all_green: true` when 5/6 layers skipped | False confidence in verification. `pineapple_verify.py` line 382: `all_green = len(layers_failed) == 0 and len(layers_passed) > 0`. Skipped = neutral. | Add `fully_verified` field requiring all non-optional layers to pass. Or require layers 1-3 minimum for `all_green`. |
| **HK-001** | Rule 3 (no-merge-without-verify) is dead | Merges proceed without any verification check. Two bugs: `event: tool` → `event: bash`, `field: content` → `field: command`. | Fix 2 lines in the hookify rule file. |
| **HK-002** | Rule 4 (no-done-without-evidence) is dead | Tasks marked complete without evidence. TodoWrite `tool_input` has `todos`, not `content`. | Requires engine change or alternative approach (PostToolUse hook). |
| **TST-001** | `test_adversarial.py` template gives false green | All 168 lines of tests pass trivially (`assert payload is not None`). Projects using this template get false security confidence. | Replace `assert X is not None` with actual client calls or skip when no client configured. |
| **TST-002** | `test_eval_benchmark.py` template gives false green | Same issue — 217 lines, all `assert X is not None`. | Same fix as TST-001. |

### P1 — Architectural Gaps

| ID | Gap | Impact | Fix |
|----|-----|--------|-----|
| **SKL-002** | CEO Strategic Review stage missing from SKILL.md | Entire strategic analysis phase lost. Pipeline jumps from intake to brainstorming, skipping cross-domain questioning, Fact-Finding Agent, and Strategic Brief. | Add Stage 1 (Strategic Review) to SKILL.md with `pineapple:ceo-review` skill reference. Renumber stages 1-8 to 2-9. |
| **SKL-003** | Architecture stage conflated with Brainstorm | Spec's two-phase approach (strategic questioning THEN technical design) collapsed into one step. | Separate into Stage 1 (Strategic Review → Strategic Brief) and Stage 2 (Architecture → Design Spec). |
| **SKL-004** | State machine not referenced in SKILL.md | No `.pineapple/runs/<uuid>/state.json` reference. Resume mechanism is "plan checkboxes + git commits" only. State machine exists in code (`pipeline_state.py`) but SKILL.md doesn't use it. | Add state machine integration to SKILL.md: create run at Stage 0, advance at each gate, check on resume. |
| **SKL-001** | Stage count: "9 stages" should be "10 stages" | Misleading description. | Update SKILL.md description and section header. |
| **HK-003** | Rules 1 & 2 file pattern too narrow | `app.+\.py` misses `src/`, `lib/`, `services/`, all non-Python source files. | Widen to `.+\.(py|ts|tsx|js|jsx)` or at minimum `.+\.py`. |

### P2 — Test & Coverage Gaps

| ID | Gap | Impact | Fix |
|----|-----|--------|-----|
| **TST-003** | 4 tools have zero test coverage | `pineapple_audit.py`, `pineapple_cleanup.py`, `pineapple_upgrade.py`, `pipeline_tracer.py` — all real, production code. | Write test files for each. |
| **TST-004** | Doctor tests mock away all real logic | `test_run_doctor_returns_report` mocks ALL checks. 7 of 11 individual checks untested. | Test individual check functions with controlled conditions. |
| **TST-005** | Verify layer runners never directly tested | All 6 `run_layer_*` functions only mocked or skipped (no backend in tmp_path). | Mock subprocess.run at the layer level to test pass/fail/timeout handling. |
| **TST-006** | PipelineTimeoutError never triggered | Wall-clock timeout is a safety feature with zero test coverage. | Test with `wall_clock_timeout_hours=0` and call advance. |
| **TST-007** | Evolve steps 4-6 untested | Mem0/Neo4j/DeepEval feeds — even the "service unavailable" skip path is untested. | Test both "service not running" and "not yet implemented" paths. |
| **SKL-005** | Path routing missing file count criteria | SKILL.md only has `<50 lines`. Missing `<3 files` (Lightweight) and `<8 files` (Medium). | Add file count criteria to routing section. |
| **SKL-008** | Failure handling incomplete in SKILL.md | Per-stage retry counts, rollback strategy (git revert), wall-clock timeout, post-circuit-breaker options all missing. | Expand failure handling section with spec's full table. |

---

## Nice-to-Have Gaps (Fix Later)

| ID | Gap | Notes |
|----|-----|-------|
| **TPL-001** | `vite-only` stack gets CI with backend jobs | CI template references `{{BACKEND_DIR}}` for lint/test — would fail for frontend-only |
| **TPL-002** | `observability.py` `_cost_log` unbounded | Memory leak in long-running processes. Add pruning/rotation. |
| **TPL-003** | `resilience.py` async-only decorators | Sync functions can't use retry/circuit-breaker/fallback. |
| **TPL-004** | `rate_limiter.py` unreplaced placeholder becomes string literal | `{{DEFAULT_LIMIT}}` as function default — if missed, becomes a string |
| **TOOL-001** | `apply_pipeline.py` empty name from `.` path | `PROJECT_NAME` defaults to `path.name` — empty for `.`, produces `projects/-bible.yaml` |
| **TOOL-002** | Spec lists `pyproject.toml` and `test_health.py` in scaffold tree | Not generated by apply_pipeline.py — spec/implementation mismatch |
| **TOOL-003** | `.pineapple/runs/` and `.pineapple/verify/` not created at scaffold | Created at runtime by state/verify tools. Spec tree implies scaffold-time creation. |
| **TOOL-004** | 6 bonus tools not in spec | config, state, tracer, audit, cleanup, upgrade — all real. Spec should document them. |
| **HK-004** | Rule 3 should be STOP not WARN | Merge without verification is irreversible. |
| **HK-005** | Rule 5 should be STOP not WARN | Gap closure without evidence corrupts tracking. |
| **SKL-009** | Cost ceiling missing specific user options | Spec requires 3 options: continue, pause+resume, simplify. SKILL.md just says "pause and surface." |

---

## Verification of This Report

This report was produced by:
1. **Agent 1** — Ran all 10 CLI tools, read source code, found `all_green` bug
2. **Agent 2** — Ran pytest (133/133 pass), read all 7 test files, assessed assertion quality
3. **Agent 3** — Read full spec (958 lines), extracted 216 requirements, compared SKILL.md on 9 alignment points
4. **Agent 4** — Read all 5 hookify rules + engine source, traced field resolution, found 2 dead rules
5. **Agent 5** — Checked all 13 templates, read apply_pipeline.py source, counted artifacts

All findings are evidence-based (tool output, source line references, regex trace-throughs). No findings are speculative.
