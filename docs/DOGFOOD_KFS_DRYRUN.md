# Dogfood Dry-Run: KFS Manifest System

**Date:** 2026-03-24
**Run ID:** `debe5313-5f8d-4595-ac04-c52e6f9cfd2f`
**Pipeline version:** Pineapple Pipeline (stages 0-3 only)
**LLM provider:** Gemini (gemini-2.5-flash)
**Total cost:** $0.0040 (3 LLM calls)
**Errors:** 0
**Verdict:** Pipeline runs end-to-end. Output quality is mixed -- strong strategic review, over-engineered architecture, hallucinated cost estimates.

---

## What Was Tested

The pipeline was asked to plan the implementation of a "KFS Manifest System" -- a unified `.kfs.yaml` format bridging geometry generation and motion simulation for Kinetic Forge Studio.

**Note:** The spec file (`2026-03-12-kfs-manifest-system-design.md`) does not exist. The script provided an inline description of the manifest system instead. The KFS v2 architecture doc was loaded as additional context (3000 chars).

---

## Stage 0: Intake (Pure Python, no LLM)

**Result:** PASS -- works as designed.

| Field | Value |
|-------|-------|
| Project name | `kfs-manifest-system` (preserved from input) |
| Classification | `new_project` (matched keywords: build, implement) |
| Path | `full` (user-specified, respected) |
| Context files | 0 (CWD is the pipeline repo, not KFS repo) |

### Observations

1. **Context loading is CWD-bound.** The intake stage only scans the current working directory for `CLAUDE.md`, `MEMORY.md`, and `projects/*.yaml`. Since we ran from `D:\GitHub\pineapple-pipeline`, it found nothing. This is a real limitation -- the pipeline cannot discover context from the *target* project directory if it differs from CWD.

2. **Classification is keyword-only.** "implement" triggered `new_project`. This is correct for this case, but the keyword approach has no understanding of scope. A request to "implement a one-line fix" would still classify as `new_project`.

---

## Stage 1: Strategic Review (LLM: Gemini)

**Result:** PASS -- surprisingly good output.

### Strategic Brief

| Field | Summary |
|-------|---------|
| **What** | Building the KFS Manifest System (.kfs.yaml), a declarative single source of truth for kinetic sculpture design |
| **Why** | Sells "design certainty" and "reduced prototyping risk." Analogous to Infrastructure-as-Code for physical designs. |
| **Not building** | 6 items: no real-time 3D viewport, no custom CAD modeler, no autonomous design gen, no custom physics engine, no visual node editor, no direct STEP editing |
| **Assumptions** | 6 items covering CadQuery reliability, pipeline tool integrability, Rule 99 API, single-motor sufficiency |
| **Open questions** | 6 items: schema versioning, error surfacing, large file storage, Rule 99 integration, native app launching, version control |

### Analysis

**Strengths:**
- The "Used Car Lot" analysis is genuinely useful. Identifying that the real product is "design certainty" rather than "a YAML format" shows the prompt is working.
- The "not building" list correctly excludes the in-browser 3D viewport (matching the V2 design doc's lesson).
- The IaC analogy (Terraform/Kubernetes for physical designs) is apt and could genuinely frame product messaging.
- Open questions are legitimate and would need answers before building.

**Weaknesses:**
- The brief doesn't reference the user's locked architectural decision: "component-centric, NOT mechanism-centric." This is in MEMORY.md but the pipeline didn't load MEMORY.md (CWD problem from Stage 0).
- `approved` is correctly forced to `false`, but the pipeline auto-skipped the human gate because `human_approvals.strategic_review = True` was pre-set in the test script. In production, this gate would pause for human review.

---

## Stage 2: Architecture (LLM: Gemini)

**Result:** MIXED -- structurally sound but over-engineered.

### Design Spec

**Title:** Asynchronous Orchestration System for Kinetic Forge Studio Manifest

**Components (8):**

| # | Component | What It Does |
|---|-----------|-------------|
| 1 | `kfs_api_server` | FastAPI REST API, request handling, dispatch |
| 2 | `kfs_manifest_processor` | Parse and validate .kfs.yaml into Pydantic models |
| 3 | `kfs_task_management_service` | Celery task lifecycle management |
| 4 | `kfs_cad_generation_worker` | Celery worker for CadQuery/OpenSCAD geometry |
| 5 | `kfs_simulation_worker` | Celery worker for motion/physics simulation |
| 6 | `kfs_asset_management_service` | File storage abstraction layer |
| 7 | `kfs_validation_export_worker` | Celery worker for pipeline tool integration |
| 8 | `kfs_frontend_app` | React 19 / TypeScript UI |

**Technology choices:** 0 entries (Gemini returned an empty dict -- this is a bug or model failure)

### Analysis

**Strengths:**
- Component decomposition is logical. Separating manifest parsing from CAD generation from simulation is correct.
- Asynchronous architecture is appropriate for long-running CAD operations.
- References to existing pipeline tools (`validate_geometry.py`, Rule 99) show context was propagated from the strategic brief.

**Weaknesses:**

1. **Over-engineering: Celery + Redis.** The KFS app is a single-user desktop tool. Celery + Redis adds massive operational complexity for zero benefit. Python's `asyncio` with `ProcessPoolExecutor` would suffice. The architecture LLM defaulted to "enterprise web app" patterns instead of reading the context (local tool, single user).

2. **Technology choices empty.** The `technology_choices` dict came back empty. This is either a Gemini structured-output failure or an Instructor extraction issue. The architecture prompt explicitly asks for this field. This is a pipeline bug worth investigating.

3. **No mention of SQLite.** The request explicitly stated "SQLite" as the database, but the architecture introduced SQLAlchemy without specifying SQLite. The LLM ignored a concrete tech stack constraint.

4. **Missing the KFS repo structure.** The architecture designs components from scratch instead of mapping to the existing KFS codebase. A real architecture stage should scan the target repo and propose changes to existing files.

---

## Stage 3: Plan (LLM: Gemini)

**Result:** MIXED -- task breakdown is reasonable, cost estimates are hallucinated.

### Task Plan (13 tasks)

| ID | Description | Complexity | Est. Cost |
|----|------------|-----------|-----------|
| T1 | Project setup (requirements, .env, docker-compose) | trivial | $15.00 |
| T2 | SQLAlchemy + SQLite models | standard | $80.00 |
| T3 | Pydantic schema for .kfs.yaml | complex | $350.00 |
| T4 | File storage abstraction | standard | $120.00 |
| T5 | Celery + Redis configuration | standard | $90.00 |
| T6 | FastAPI app setup | standard | $70.00 |
| T7 | Manifest parser + validator | complex | $280.00 |
| T8 | CRUD + task submission endpoints | standard | $130.00 |
| T9 | CAD generation workers | complex | $450.00 |
| T10 | Simulation workers | complex | $420.00 |
| T11 | Validation/export workers | complex | $480.00 |
| T12 | REST API endpoints | standard | $180.00 |
| T13 | React frontend foundation | complex | $380.00 |

**Total estimated cost: $3,045.00**

### Analysis

**Strengths:**
- Task ordering respects dependencies: foundation (T1-T2) -> schema (T3) -> infrastructure (T4-T6) -> core logic (T7-T9) -> integration (T10-T12) -> frontend (T13).
- File lists per task are concrete and specific.
- Complexity ratings are reasonable (schema definition as "complex" is correct).

**Weaknesses:**

1. **Cost estimates are nonsensical.** The planner prompt says "Estimate cost in USD for each task (LLM API calls needed to implement it)." A $450 estimate for T9 would require ~15,000 Claude Sonnet calls at $0.03 each, or ~450,000 Gemini calls. The actual pipeline cost for stages 0-3 was $0.004. The LLM is estimating *human developer costs*, not *LLM API costs*. The prompt needs to be more explicit, or the cost model needs to be rethought entirely.

2. **Still carrying Celery/Redis forward.** The over-engineered architecture from Stage 2 propagated into the plan. There's no stage that challenges or simplifies the architecture. The pipeline has no "sanity check" between Architecture and Plan.

3. **13 tasks for an MVP is borderline too many.** The planner's "3-15 tasks" guideline allowed this, but several tasks could be merged (T1+T6, T4+T6, T8+T12).

4. **No test tasks.** Zero of the 13 tasks are dedicated to writing tests. The planner prompt mentions "then tests, then polish" in the ordering rules, but the LLM ignored this.

---

## Pipeline-Level Findings

### What Worked

1. **End-to-end execution.** Four stages, three LLM calls, zero errors. The pipeline's error handling (guards for missing deps, missing keys, missing briefs) was never triggered because everything worked.
2. **Cost tracking.** Real token-based cost tracking worked: $0.0008 + $0.0014 + $0.0018 = $0.0040 total. Gemini is very cheap.
3. **State propagation.** Strategic brief flowed correctly into architecture, which flowed into plan. Each stage built on the previous.
4. **Structured output via Instructor.** All three LLM calls returned valid Pydantic models. No parsing failures.
5. **Strategic Review quality.** The "Used Car Lot" prompt genuinely produced useful strategic insight.

### What Needs Fixing

| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| 1 | **Context loading is CWD-only** -- cannot discover files from target project | HIGH | Add a `--target-dir` parameter or accept context paths in the request |
| 2 | **Technology choices came back empty** from architecture stage | MEDIUM | Investigate Gemini structured output for dict fields; may need a separate model field |
| 3 | **Cost estimates are human-developer costs, not LLM API costs** | MEDIUM | Rewrite the planner prompt to be explicit about what "cost" means, or remove cost estimation from the Task model |
| 4 | **No architecture challenge/simplification step** | MEDIUM | Add a "devil's advocate" pass that checks architecture against project context (single user? local tool? then no Celery) |
| 5 | **No test tasks in plan** | LOW | Add explicit instruction in planner prompt: "At least one task must be dedicated to tests" |
| 6 | **No existing codebase awareness** | HIGH | Architecture and Plan stages design from scratch. They should scan the target repo and propose modifications to existing files |
| 7 | **Keyword-only classification** | LOW | Works for now but will misclassify edge cases. Acceptable for MVP. |
| 8 | **LangFuse not configured** | LOW | Authentication warning printed. Not blocking, but traces aren't being captured. |

### Key Insight

The pipeline produces *generic software architecture* rather than *project-specific architecture*. It treats every request as a greenfield project. For KFS -- which already has a React frontend, FastAPI backend, SQLite database, and dozens of existing modules -- the pipeline should be reading the existing codebase and proposing targeted additions. Instead, it designed an entirely new application from scratch with Celery, Redis, and docker-compose.

This is the single biggest gap: **the pipeline has no codebase awareness**. Stages 1-3 operate on the request text alone, with no knowledge of what already exists.

---

## Raw Output

```
NOTE: Spec file not found at d:\Claude local\docs\superpowers\specs\2026-03-12-kfs-manifest-system-design.md
      Using inline KFS Manifest System description instead.

Loaded KFS v2 architecture doc: 3000 chars

============================================================
DOGFOOD DRY-RUN: KFS Manifest System
Run ID: debe5313-5f8d-4595-ac04-c52e6f9cfd2f
Path: full (stages 0-3 only)
============================================================

>>> STAGE 0: INTAKE
[Stage 0: Intake] Processing request...
  [Intake] Project name: kfs-manifest-system
  [Intake] Classification: new_project (Matched new-project keywords: build, implement)
  [Intake] Path: full (user-specified)
  [Intake] No context files found in working directory.
  [Intake] Context bundle created with 0 file(s).
    Path: full
    Project: kfs-manifest-system
    Context bundle: YES

>>> STAGE 1: STRATEGIC REVIEW
[Stage 1: Strategic Review] Project: kfs-manifest-system
  [Strategic Review] Calling LLM for strategic brief...
  [Strategic Review] Brief generated (provider: gemini, cost: $0.0008)
    What: Building the KFS Manifest System (.kfs.yaml), a declarative single
          source of truth for kinetic sculpture design
    Why:  Sells "design certainty" and "reduced prototyping risk." Analogous
          to Infrastructure-as-Code for physical designs.
    Not building: 6 scope exclusions
    Assumptions: 6 items
    Open questions: 6 items
    Approved: False

>>> STAGE 2: ARCHITECTURE
[Stage 2: Architecture] Project: kfs-manifest-system
  [Architecture] Calling LLM for design spec...
  [Architecture] Design spec generated (provider: gemini, cost: $0.0014)
    Title: Asynchronous Orchestration System for KFS Manifest
    Components: 8
      - kfs_api_server
      - kfs_manifest_processor
      - kfs_task_management_service
      - kfs_cad_generation_worker
      - kfs_simulation_worker
      - kfs_asset_management_service
      - kfs_validation_export_worker
      - kfs_frontend_app
    Technology choices: 0 entries (BUG: empty dict)

>>> STAGE 3: PLAN
[Stage 3: Plan] Project: kfs-manifest-system
  [Plan] Calling LLM to generate task plan...
  [Plan] Task plan generated (provider: gemini, cost: $0.0018)
    Tasks: 13
    Total estimated cost: $3045.00 (hallucinated -- these are human costs, not API costs)
    T1:  Project setup [trivial]
    T2:  SQLAlchemy + SQLite models [standard]
    T3:  Pydantic .kfs.yaml schema [complex]
    T4:  File storage abstraction [standard]
    T5:  Celery + Redis config [standard]
    T6:  FastAPI app setup [standard]
    T7:  Manifest parser + validator [complex]
    T8:  CRUD + task endpoints [standard]
    T9:  CAD generation workers [complex]
    T10: Simulation workers [complex]
    T11: Validation/export workers [complex]
    T12: REST API endpoints [standard]
    T13: React frontend [complex]

>>> DRY-RUN COMPLETE
    Cost: $0.0040
    Errors: 0
```

---

## State File

Full pipeline state saved to:
`.pineapple/dogfood/kfs-manifest-dryrun-debe5313.json`

## Script

`dogfood_kfs_dryrun.py` at repo root.
