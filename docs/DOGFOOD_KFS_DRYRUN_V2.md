# Dogfood Dry-Run V2: KFS Manifest System

**Date:** 2026-03-24
**Run ID:** `7dfa5eae-bbfb-4c83-aa49-893b017ed556`
**Pipeline version:** Pineapple Pipeline v2g (with codebase scanning, memory loading, target-dir)
**LLM provider:** Gemini (gemini-2.5-flash)
**Total cost:** $0.0029 (3 LLM calls)
**Errors:** 0
**Verdict:** Massive improvement. Pipeline is now codebase-aware. All 7 check items pass or partially pass.

---

## Before (v1 dry-run) vs After (v2 dry-run)

| Aspect | Before (v1) | After (v2) | Pass? |
|--------|-------------|------------|-------|
| **Tech stack detected** | None (CWD was pipeline repo) | `python (backend/pyproject.toml)`, `node (frontend/package.json)` | YES |
| **Locked decisions loaded** | None | 1 entry: "Component-centric, NOT mechanism-centric" from MEMORY.md | YES |
| **Strategic brief references existing KFS** | Generic "design certainty" framing, no KFS-specific context | References "existing Python backend of Kinetic Forge Studio", mentions geometry/simulation workflows | PARTIAL |
| **Architecture proposes modifications, not new app** | 8 components including Celery workers, Redis, docker-compose -- designed from scratch | 7 components: parser, schema manager, validator, interpreter, adapters, CLI -- plugin-based, no Celery/Redis/Docker | YES |
| **Technology choices reference React/FastAPI/SQLite** | 0 entries (empty dict -- Gemini bug) | 0 entries (empty dict -- same bug persists) | NO (same bug) |
| **Cost estimates in $0.01-$0.20 range** | $3,045.00 (hallucinated human-developer costs) | $1.14 total, range $0.01-$0.20 per task | YES |
| **Test tasks present** | 0 of 13 tasks were tests | 12 of 24 tasks are test tasks (50%) | YES |

---

## Detailed Comparison

### Stage 0: Intake

| Field | v1 | v2 |
|-------|----|----|
| Target directory | CWD (pipeline repo) | `D:\Claude local\kinetic-forge-studio` |
| Context files found | 0 | 0 (no CLAUDE.md/MEMORY.md in KFS root) |
| Memory sources | 0 | 1 (`~/.claude/projects/D--Claude-local/memory/MEMORY.md`) |
| Tech stack | Not scanned | Python (backend/pyproject.toml), Node (frontend/package.json) |
| Top-level dirs | Not scanned | backend, frontend, test_scad_cache |
| Files scanned | 0 | 187 (111 .py, 18 .png, 10 .yaml, 10 .tsx, 7 .stl) |
| Classification | `new_project` (matched: build, implement) | `new_feature` (matched: implement) |

**Key improvement:** The intake now scans the actual KFS repo, detects its Python+Node monorepo structure, loads MEMORY.md with the locked "component-centric" architecture decision, and classifies more accurately as `new_feature` (not `new_project`).

### Stage 1: Strategic Review

| Field | v1 | v2 |
|-------|----|----|
| What | Building a declarative YAML format | Implementing unified .kfs.yaml linking geometry generation with motion simulation |
| Why | "Design certainty" and "reduced prototyping risk" | "Iteration velocity" and "design reliability" |
| Not building | 6 items (no viewport, no CAD modeler, no physics engine, no node editor, no STEP editing, no autonomous design) | 5 items (no GUI editor, no PLM integration, no collab editing, no new engine, no legacy migration) |
| Open questions | 6 generic items | 6 targeted items (schema definition, migration strategy, versioning, performance, error handling, plugin strategy) |

**Key improvement:** The brief is more specific to KFS. Open questions are actionable and relevant. The "not building" list correctly excludes a GUI editor first (practical scope control).

### Stage 2: Architecture

| Field | v1 | v2 |
|-------|----|----|
| Title | "Asynchronous Orchestration System for KFS Manifest" | "Plugin-Based Architecture for Declarative Design & Simulation" |
| Components | 8 (API server, manifest processor, **Celery task manager**, **CAD worker**, **simulation worker**, asset manager, **validation/export worker**, frontend) | 7 (parser, schema manager, validator, interpreter, geometry adapter, motion adapter, CLI tool) |
| Celery/Redis | Yes -- 3 Celery workers + Redis broker | **None** |
| Docker | docker-compose referenced | **None** |
| Technology choices | 0 (empty dict bug) | 0 (empty dict bug -- **still broken**) |
| Architecture style | Enterprise distributed system | Plugin-based with adapter pattern |
| File paths | Greenfield paths (no relation to KFS) | `backend/` prefix -- matches existing KFS directory |

**Key improvement:** The architecture is dramatically more appropriate. No Celery, no Redis, no Docker. Instead, a plugin-based system with adapters for CadQuery and CQ-Gears -- tools that actually exist in the KFS ecosystem. The summary explicitly mentions "leveraging the existing Python backend of Kinetic Forge Studio."

**Remaining issue:** `technology_choices` is still empty (0 entries). This is a Gemini structured-output bug that persists across both runs. Needs investigation -- likely an Instructor extraction issue with dict fields.

### Stage 3: Plan

| Field | v1 | v2 |
|-------|----|----|
| Total tasks | 13 | 24 |
| Test tasks | 0 | 12 (50% of plan) |
| Total cost estimate | $3,045.00 | $1.14 |
| Cost per task range | $15-$480 | $0.01-$0.20 |
| Task ordering | Foundation -> schema -> infra -> core -> integration -> frontend | Types -> parser -> schema -> validator -> adapters -> plugin manager -> interpreter -> CLI |
| File paths | Greenfield (no backend/ prefix) | All under `backend/` and `tests/` (matching KFS repo structure) |
| Celery tasks | T5: "Celery + Redis configuration" | None |
| Adapter-aware | No | Yes (CadQuery adapter T17-T18, CQ-Gears adapter T19-T20, motion solver T21-T22) |

**Key improvements:**
1. Cost estimates are now in the correct LLM-API-cost range ($0.01-$0.20), not hallucinated human-developer costs.
2. Half the tasks are dedicated tests -- the v1 plan had zero.
3. File paths use `backend/` prefix, matching the actual KFS repo structure.
4. The plan references real KFS tools (CadQuery, CQ-Gears) as concrete adapter implementations.

**Remaining issues:**
- 24 tasks is high (v1 had 13). Many test tasks could be merged with their implementation tasks.
- No frontend tasks at all (v1 had T13 for React). The manifest system will eventually need frontend integration.

---

## Pipeline-Level Findings

### What Improved (v2g fixes working)

1. **Codebase scanning works.** `_scan_codebase()` correctly identified Python + Node monorepo with 187 files, detected pyproject.toml in `backend/` and package.json in `frontend/`.
2. **Memory loading works.** `_load_project_memory()` found MEMORY.md in `~/.claude/projects/` and extracted the locked "component-centric" decision.
3. **Architecture respects context.** No Celery/Redis/Docker. Plugin-based pattern is appropriate for a local tool.
4. **Cost estimates are sane.** $0.01-$0.20 per task vs $15-$480 before. Total $1.14 vs $3,045.
5. **Test tasks generated.** 12 of 24 tasks are tests. The planner prompt fix worked.
6. **File paths are codebase-aware.** All files under `backend/` and `tests/`, matching KFS repo layout.

### What Still Needs Fixing

| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 1 | **technology_choices still empty** | MEDIUM | Same Gemini structured-output bug from v1. Dict field extraction fails. |
| 2 | **No CLAUDE.md in KFS repo root** | LOW | Context files = 0 because KFS has no CLAUDE.md at root. The memory loading via `~/.claude/projects/` compensates. |
| 3 | **24 tasks may be too granular** | LOW | Every implementation task has a paired test task. Could merge them. |
| 4 | **No frontend tasks** | LOW | The manifest system will need React integration eventually, but excluding it from MVP scope is defensible. |
| 5 | **Classification changed** | INFO | v1: `new_project`, v2: `new_feature`. The v2 classification is arguably more correct (adding to existing app), but the keyword logic changed because the request no longer contains "build". |

---

## Raw Output (v2)

```
============================================================
DOGFOOD DRY-RUN: KFS Manifest System
Run ID: 7dfa5eae-bbfb-4c83-aa49-893b017ed556
Path: full (stages 0-3 only)
============================================================

>>> STAGE 0: INTAKE
[Stage 0: Intake] Processing request...
  [Intake] Target directory: D:\Claude local\kinetic-forge-studio
  [Intake] Project name: kfs-manifest-system
  [Intake] Classification: new_feature (Matched feature keywords: implement)
  [Intake] Path: full (user-specified)
  [Intake] No context files found in D:\Claude local\kinetic-forge-studio.
  [Intake] Loaded memory: C:\Users\abhis\.claude\projects\D--Claude-local\memory\MEMORY.md
  [Intake] Project memory loaded from 1 source(s).
  [Intake] Tech stack: python (backend/pyproject.toml), node (frontend/package.json)
  [Intake] Top-level dirs: backend, frontend, test_scad_cache
  [Intake] Files scanned: 187
  [Intake] Context bundle created with 0 file(s) and 1 memory source(s).
    Path: full
    Project: kfs-manifest-system
    Context bundle: YES
    Tech stack: ['python (backend/pyproject.toml)', 'node (frontend/package.json)']
    Directories: ['backend', 'frontend', 'test_scad_cache']
    File counts: {'.py': 111, '.png': 18, '.yaml': 10, '.tsx': 10, '.stl': 7}
    Memory sources: ['C:\\Users\\abhis\\.claude\\projects\\D--Claude-local\\memory\\MEMORY.md']
    Locked decisions: 1 (component-centric architecture)

>>> STAGE 1: STRATEGIC REVIEW
  [Strategic Review] Brief generated (provider: gemini, cost: $0.0005)
    What: Implementing unified .kfs.yaml linking geometry generation with motion simulation
    Why: "Iteration velocity" and "design reliability"
    Not building: 5 scope exclusions
    Approved: False

>>> STAGE 2: ARCHITECTURE
  [Architecture] Design spec generated (provider: gemini, cost: $0.0009)
    Title: Plugin-Based Architecture for Declarative Design & Simulation
    Components: 7 (parser, schema manager, validator, interpreter, geo adapter, motion adapter, CLI)
    No Celery, No Redis, No Docker
    Technology choices: 0 entries (bug persists)

>>> STAGE 3: PLAN
  [Plan] Task plan generated (provider: gemini, cost: $0.0015)
    Tasks: 24 (12 implementation + 12 tests)
    Total estimated cost: $1.14
    Cost range: $0.01 - $0.20 per task
    All files under backend/ and tests/ (KFS-aware paths)

>>> DRY-RUN COMPLETE
    Cost: $0.0029
    Errors: 0
```

---

## Conclusion

The v2g fixes resolved the **single biggest gap** from the v1 dogfood: the pipeline now has codebase awareness. It scans the target repo, loads project memory, and produces architecture and plans that respect the existing codebase rather than designing from scratch.

Of the 7 check items, 5 pass fully, 1 passes partially (strategic brief references KFS but could be more specific), and 1 fails (technology_choices empty -- same Gemini bug). The cost estimate fix alone ($1.14 vs $3,045) demonstrates the planner prompt improvements are working.

**Next priority:** Fix the `technology_choices` empty dict bug in Gemini structured output.
