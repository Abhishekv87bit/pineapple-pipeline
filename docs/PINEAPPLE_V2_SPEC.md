# Pineapple Pipeline v2 -- Technical Specification

> **Version:** 2.0.0-alpha.1
> **Date:** 2026-03-22
> **Status:** Phase 1 complete, Phase 3 in progress
> **Source of truth for tool choices:** NOTEBOOKLM_PROMPT.md at D:/ai-agent-mastery-plan/

---


## Context & Intent

**What this is:** A real AI agent system — a LangGraph application that takes any project from idea to production-ready code. Not a process document. Not a set of scripts. A living system where AI agents do the work, enforced by code, armed with 26 tools the user chose through months of research.

**Why it exists:** v1 was a process doc + custom Python scripts that reinvented 8+ libraries the user already picked. v2 IS the user's 26-tool learning plan made concrete — building it teaches every tool, and running projects THROUGH it produces secure, zero-bug, production-ready applications.

**Strategic decision (locked):** Pipeline = standalone infrastructure. BrokerFlow (and future projects) run ON it.

---

## What This Thing Actually Is

Three interfaces, one system:

1. **CLI tool** — `pineapple run "Build BrokerFlow"` — the primary entry point
2. **LangGraph application** — the brain. 10 nodes, conditional edges, checkpointed state, human-in-the-loop gates
3. **FastMCP server** — `pineapple serve` — exposes stages as MCP tools so Claude Code can invoke them

### Package Structure

```
pineapple-pipeline/
  src/pineapple/
    cli.py                     # CLI entry (Click/Typer)
    graph.py                   # LangGraph graph definition
    state.py                   # Pydantic state schema
    gates.py                   # Gate condition evaluators (pure Python, no LLM)
    mcp_server.py              # FastMCP server
    agents/
      intake.py                # Stage 0
      strategic_review.py      # Stage 1 (CEO skill)
      architecture.py          # Stage 2
      planner.py               # Stage 3
      setup.py                 # Stage 4
      builder.py               # Stage 5 (cannot access verifier tools)
      verifier.py              # Stage 6 (cannot access builder tools)
      reviewer.py              # Stage 7 (cannot access builder/verifier tools)
      shipper.py               # Stage 8
      evolver.py               # Stage 9
    models/                    # Pydantic models for every inter-stage artifact
```

### The Fundamental Difference from v1

| v1 | v2 |
|----|-----|
| Human reads SKILL.md, manually invokes Claude skills | LangGraph graph executes nodes automatically, pauses only at human gates |
| `pipeline_state.py` exists but nothing reads it to route | State IS the LangGraph checkpoint — gates are Python functions, not LLM judgment |
| Builder/verifier/reviewer are "different conversations" | Different graph nodes with **isolated tool sets** — enforced by code |
| Free-text markdown artifacts | Every artifact is an Instructor-parsed Pydantic model |
| Manual cost tracking | Every LLM call updates `cost_total_usd` in graph state; ceiling triggers transition |
| Manual session handoffs | Stage 9 agent feeds Mem0, updates Neo4j, queues DSPy optimization |
| No API surface | FastMCP server — Claude Code calls `pineapple_verify` as a tool |

**The key insight:** v1 relied on Claude's compliance with instructions. v2 enforces the process through code. A Python `if` statement returning `"fail"` cannot be prompt-engineered around.

---

## Where All 26 Tools Fit

### Core Backbone (used in every run)

| Tool | Role |
|------|------|
| **LangGraph** | THE orchestrator. 10-node graph, conditional edges, checkpointing, retry loops, human-in-the-loop |
| **Pydantic** | Type system. Every artifact (StrategicBrief, DesignSpec, TaskPlan, CodeReview, VerifyResult) is a Pydantic model |
| **Instructor** | Wraps every LLM call. No raw string parsing. Every Claude response returns a validated Pydantic model |
| **Anthropic API** | All LLM calls go through Claude |
| **Tenacity** | Transitive dependency only — used internally by Instructor and Google GenAI. Direct retry handled by `call_with_retry()` in llm.py, which delegates to native SDK retry (Anthropic `max_retries`, Google GenAI built-in) |
| **PyBreaker** | Removed — `review_gate` uses a simple attempt counter (`attempt_counts["build"] >= 3`) |
| **LangFuse** | Traces every agent call. Cost per stage, per project. The nervous system. Wired with graceful degradation (no-ops if server unreachable) |
| **FastMCP** | Exposes pipeline stages as MCP tools. Claude Code integration point |
| **pytest** | Stage 6 Layers 1-3 (unit, integration, adversarial) |

### Stage-Specific

| Tool | Stage | How |
|------|-------|-----|
| **DeepEval** | Stage 6 Layer 4 | LLM eval metrics: GEval, AnswerRelevancy, Faithfulness. Quality gates with thresholds |
| **RAGAS** | Stage 6 Layer 4 | RAG-specific eval (only when target project uses RAG) |
| **PromptFoo** | Stage 6 + 9 | Prompt regression testing. Catches prompt regressions in Stage 6, generates test cases in Stage 9 |
| **FastAPI** | Stage 8 + templates | Scaffolded into target projects. Pipeline itself optionally exposes REST API (Phase 3+) |
| **SlowAPI** | Templates | Rate-limiting for target project APIs |
| **Redis** | Phase 2+ | LLM response cache during build. LangGraph checkpoint store (replaces SQLite) |
| **Docker** | Stage 4 + 8 | Shared infra (LangFuse, Mem0, Neo4j) as containers. Target projects containerized via templates |
| **GitHub Actions** | Stage 8 | CI template stamped into target projects. Runs all 6 verification layers on PR |
| **Railway** | Stage 8, Phase 4+ | Deployment target for target projects |

### Evolution Layer (Stage 9)

| Tool | How |
|------|-----|
| **Mem0** | Extracts facts from session results. Future Stage 0 loads relevant facts as context |
| **Neo4j** | Component relationship graph. Architecture agent (Stage 2) uses it to understand topology |
| **ChromaDB** | Vector store for past specs, designs, code embeddings. Intake searches for similar past projects |
| **DSPy** | Takes DeepEval scores as fitness function, optimizes prompts automatically |
| **Unsloth** | Phase 7+. Fine-tune small models on project-specific data (e.g., after 1000 DL extractions) |

### Human-External (not programmatic)

| Tool | Role |
|------|------|
| **NotebookLM** | User researches before invoking pipeline. Pipeline reads research notes as context files |
| **Google AI Studio** | User tests feasibility. Pipeline reads results as context |
| **Claude Code** | The environment the pipeline runs inside of. Stage 5 can dispatch Claude Code as coder |

---

## LangGraph Architecture

### State Schema (replaces pipeline_state.py)

```python
class PipelineState(TypedDict):
    run_id: str
    request: str
    path: Literal["full", "medium", "lightweight"]
    current_stage: PipelineStage  # Enum 0-9

    # Stage artifacts (Pydantic models, populated by each stage)
    strategic_brief: StrategicBrief | None
    design_spec: DesignSpec | None
    task_plan: TaskPlan | None
    verify_record: VerificationRecord | None
    review_result: ReviewResult | None
    # ... etc

    # Control flow
    attempt_counts: dict[str, int]
    human_approvals: dict[str, bool]
    cost_total_usd: float
    errors: list[PipelineError]
```

### Graph Structure

```
intake --[full]--> strategic_review --> architecture --> plan --> setup --> build --> verify --> review --[pass]--> ship --> evolve --> END
  |                                                                          ^                    |
  |--[medium]--> plan --------------------------------------------------------|                    |--[retry, <3 attempts]--> build
  |                                                                                                |
  |--[lightweight]--> build                                                                        |--[fail, >=3 attempts]--> human_intervention
```

### Gates = Pure Python (not LLM)

```python
def review_gate(state: PipelineState) -> str:
    if state["attempt_counts"].get("build", 0) >= 3:
        return "fail"  # Circuit breaker
    if state["cost_total_usd"] > 200.0:
        return "fail"  # Cost ceiling
    if state["review_result"].has_critical_issues:
        return "retry"
    return "pass"
```

### Human-in-the-Loop

LangGraph `interrupt_before` on Stages 1, 2, 3, 8. Pipeline pauses, CLI prompts user, response fed back via `graph.update_state()`.

---

## Phased Build Plan

### Phase 1 (Week 1-2): Core Pipeline — COMPLETE

**Tools:** LangGraph, Pydantic, Anthropic API, Instructor, LangFuse (graceful-degrade), pytest, FastMCP, Docker (optional)

**Deliverable:** `pineapple run "Build X"` works end-to-end. All 10 stages execute. SQLite checkpointing. Resume works. Human-in-the-loop gates work. CLI feedback path (`n` at gate collects feedback text). Per-branch verify records at `.pineapple/verify/<branch>.json`.

**Tasks:**
1. Define all Pydantic models (state, artifacts, errors)
2. Build LangGraph graph with 10 nodes + conditional edges
3. Implement gate functions (pure Python)
4. Implement Stage 0-4 agents (intake, strategic review, architecture, plan, setup)
5. Implement Stage 5 builder agent (isolated tools)
6. Implement Stage 6 verifier agent (isolated tools, fresh context)
7. Implement Stage 7 reviewer agent (isolated tools, fresh context)
8. Implement Stage 8-9 (ship, evolve stubs)
9. CLI entry point (`pineapple run/status/resume`)
10. FastMCP server exposing key stages
11. Port existing 288 tests as behavioral specs

### Phase 2 (Week 3-4): Observability + Caching

**Add:** LangFuse (full tracing already wired), Redis (requires Redis infrastructure)
**Deliverable:** Full cost dashboards. Redis checkpoint store (replaces SQLite) and LLM response caching once Redis is available.
**Notes:** LangFuse already wired with graceful degradation. Redis and any Tenacity-tuning items are stale — SDK retry is native. Redis items blocked on infrastructure provisioning.

### Phase 3 (Week 5-6): Real Verification — IN PROGRESS

**Add:** DeepEval (full metrics suite, in progress), PromptFoo (in progress), RAGAS (RAG projects only)
**Deliverable:** Stage 6 has real quality gates with thresholds. Prompt regression testing.
**Notes:** PyBreaker tuning is stale — removed entirely. RAGAS only needed when the target project uses RAG; not a general pipeline dependency.

### Phase 4 (Week 7-8): Evolution + Deploy

**Add:** Mem0 (requires Mem0 server), Neo4j (requires Neo4j server), ChromaDB (requires ChromaDB server), DSPy, GitHub Actions (in progress), Railway
**Deliverable:** Pipeline learns from itself. Target projects deploy to cloud.
**Notes:** Mem0, Neo4j, ChromaDB, and DSPy all require external services to be provisioned before integration. GitHub Actions CI workflow being added now.

Each phase is independently shippable. Phase 1 alone = working agentic pipeline.

---

## BrokerFlow Concrete Example

User runs: `pineapple run "Build BrokerFlow Portal Pre-Fill browser extension"`

| Stage | What Happens | Agent | Tools Used | Artifact | Cost |
|-------|-------------|-------|------------|----------|------|
| 0 Intake | Classify "new project", load context, route Full Path | Python only | Pydantic | ContextBundle | $0 |
| 1 Strategic Review | 5-7 probing questions, synthesize brief | CEO agent | Instructor, LangFuse | StrategicBrief | ~$2 |
| 2 Architecture | 2-3 approaches, human picks, full spec | Architect agent | Instructor, LangFuse | DesignSpec | ~$5 |
| 3 Plan | Break into 7 tasks, review, approve | Planner agent | Instructor, LangFuse | TaskPlan | ~$3 |
| 4 Setup | Git worktree, scaffold templates, install deps | Python only | — | WorkspaceInfo | $0 |
| 5 Build | 7 coder agents (one per task), red-green-commit | Builder agents | Anthropic API, Tenacity | BuildResults[] | ~$50 |
| 6 Verify | Fresh agent runs 6 layers cold | Verifier agent | pytest, DeepEval | VerificationRecord | ~$5 |
| 7 Review | Third agent reviews diff vs spec | Reviewer agent | Instructor, LangFuse | ReviewResult | ~$5 |
| 8 Ship | Human picks: merge/PR/keep/discard | Python + human | git, gh CLI | ShipResult | $0 |
| 9 Evolve | Session handoff, bible update, learning extraction | Evolver agent | Mem0, Neo4j (Phase 4) | EvolveReport | ~$1 |

**Total: ~$70, ~2-3 hours** (mostly human-in-the-loop in Stages 1-3)

---

## What Stays from v1

| Artifact | Path | Status |
|----------|------|--------|
| SKILL.md (238 lines) | [SKILL.md](docs/superpowers/skills/pineapple/SKILL.md) | KEEP — defines the process |
| CEO Review skill | [ceo-review.md](docs/superpowers/skills/pineapple/ceo-review.md) | KEEP — becomes Stage 1 system prompt |
| 10 production templates | `D:\GitHub\pineapple-pipeline\templates\` | KEEP (resilience.py -> PyBreaker) |
| 288 tests | `D:\GitHub\pineapple-pipeline\tests\` | KEEP as behavioral specs |
| Dogfood report | `D:\GitHub\pineapple-pipeline\DOGFOOD_REPORT.md` | REFERENCE |
| Design spec (process layer) | `docs/superpowers/specs/2026-03-15-pineapple-pipeline-design.md` | KEEP process, REWRITE impl |

## What Was Killed (Completed)

| Custom Code | Replace With | Status |
|------------|-------------|--------|
| `pipeline_state.py` (304 lines) | LangGraph state + checkpointing | DONE |
| Custom retry counters | Native SDK retry (`max_retries`) + `call_with_retry()` in llm.py | DONE |
| `resilience.py` template (216 lines) | Simple attempt counter in `review_gate` | DONE |
| Raw HTTP stubs for Mem0/Neo4j | Mem0 SDK, Neo4j driver (Phase 4) | DONE |
| Manual cost tracking | LangFuse (wired) | DONE |
| `middleware/` package (observability.py, resilience.py) | Deleted — empty placeholder | DONE |
| `templates/` directory | Deleted — wrong repo for project scaffolding | DONE |
| v1 tools (pipeline_state.py, pipeline_tracer.py, pineapple_audit.py, pineapple_cleanup.py, pineapple_evolve.py, pineapple_config.py, apply_pipeline.py, pineapple_upgrade.py) | Deleted | DONE |
| `tests/v1/` | Deleted | DONE |
| `requirements.txt` | pyproject.toml is canonical | DONE |

---

## Bootstrap Strategy: Use the Pipeline to Build Itself

The v1 mistake was building without enforcement. v2 dogfoods from day 1.

### Step 1: Skeleton (this session or next — done manually by Claude agents)

Build the absolute minimum LangGraph application:
- `state.py` — PipelineState Pydantic model
- `graph.py` — 10 stub nodes + conditional edges + gates
- `gates.py` — Gate functions (pure Python)
- `cli.py` — `pineapple run/status/resume`
- SQLite checkpointing
- **No LLM calls yet** — stub nodes just print and advance

This is ~500 lines. Once it exists, the graph enforces stage ordering.

### Step 2: Feed itself through itself

Run: `pineapple run "Build Pineapple Pipeline v2 Phase 1"`

The skeleton pipeline orchestrates building its own nodes:
- Stage 0: Classifies as Full Path (done)
- Stage 1: Strategic Review runs (even as a stub, it forces the human to answer questions)
- Stage 2: Architecture produces a DesignSpec (we have this from planning)
- Stage 3: Plan produces a TaskPlan (flesh out each node as a task)
- Stage 5: Builder agents implement each node for real
- Stage 6: Verifier agents test each node
- Stage 7: Reviewer agents review
- Repeat for each Phase

### Step 3: Enforcement layers (added incrementally)

| Enforcement | Tool | When Added |
|------------|------|-----------|
| Stage ordering | LangGraph conditional edges | Step 1 (skeleton) |
| Gate conditions | Pure Python functions | Step 1 (skeleton) |
| Checkpoint + resume | LangGraph SQLite saver | Step 1 (skeleton) |
| No code without spec | Hookify rule (fix existing) | Step 1 |
| No merge without verify | Hookify rule (fix existing) | Step 1 |
| Structured artifacts | Instructor + Pydantic | Phase 1 (when nodes get real) |
| Cost tracking | LangFuse | Phase 2 |
| Quality gates | DeepEval thresholds | Phase 3 |
| Prompt regression | PromptFoo | Phase 3 |
| Learning loop | Mem0 + Neo4j + DSPy | Phase 4 |

### Why This Works

- **The graph physically prevents skipping stages.** No conditional edge from intake to ship.
- **Gates physically prevent advancing without artifacts.** `strategic_brief is None` -> gate fails.
- **Checkpointing physically prevents losing progress.** Kill the session, resume next time.
- **Hookify physically prevents code without specs.** The hook blocks the file write.
- **Using the pipeline to build itself catches bugs immediately.** If Stage 6 doesn't work, we'll know because WE hit Stage 6.

## Success Criteria

1. `pineapple run "X"` executes all 10 stages end-to-end (Phase 1)
2. Every LLM call returns a Pydantic model via Instructor
3. Gates are Python functions, not LLM judgment
4. Builder/Verifier/Reviewer have isolated tool sets (enforced by code)
5. Checkpoint + resume works (LangGraph)
6. FastMCP exposes stages as MCP tools
7. All 26 tools have a clear role (11 in Phase 1, rest phased in)
8. Cost ceiling triggers graph transition, not just a warning
9. Stage 6 runs real evals (DeepEval), not just pytest
10. Phase 1 works standalone — no Docker services required

## Verification

- Phase 1: `pineapple run "hello world test"` completes all 10 stages
- Phase 1: `pineapple resume <id>` resumes from checkpoint after kill
- Phase 2: LangFuse dashboard shows traces with cost breakdown
- Phase 3: `deepeval test run` produces real quality scores in Stage 6
- Phase 4: `pineapple run "BrokerFlow"` produces a working PR
- All phases: `pytest -v` passes in the pipeline's own repo


---

## Current Implementation State (as of 2026-03-22)

### Files Verified Against This Spec

| File | Matches Spec | Notes |
|------|-------------|-------|
| src/pineapple/graph.py | YES | 10 nodes, conditional edges, SQLite checkpointing |
| src/pineapple/state.py | YES | PipelineState TypedDict with all artifact fields |
| src/pineapple/gates.py | YES | Pure Python gates, review_gate with circuit breaker |
| src/pineapple/llm.py | YES | Gemini/Claude router via Instructor |
| src/pineapple/cli.py | YES | run/status/resume commands |
| src/pineapple/mcp_server.py | YES | 4 FastMCP tools |
| src/pineapple/models/__init__.py | YES | All 13 Pydantic models |
| src/pineapple/agents/*.py | YES | All 10 stage agents implemented |
| pyproject.toml | YES | LangGraph, Pydantic in deps. Tenacity and PyBreaker removed as direct deps — retry delegated to native SDKs |
| src/pineapple/gates.py | YES | Circuit breaker replaced with simple attempt counter; `review_gate` uses `attempt_counts` |
| CHANGELOG.md | YES | Populated with version history |
| WASTAGE_AUDIT.md | YES | Documents all cleanup performed (deleted files, removed deps) |
| .github/workflows/ | IN PROGRESS | GitHub Actions CI being added (Phase 4) |

### Anti-Patterns from Dogfood (Non-Negotiable)

Source: feedback_pineapple_dogfood_lessons.md

1. Read user research before designing -- NOTEBOOKLM_PROMPT.md is source of truth
2. Verify at every level -- code vs spec, spec vs user context, context vs reality
3. Running code > reading code -- Grep is never verification
4. User pushback is always signal -- stop and verify
5. False confidence is worse than none -- never report success without evidence
6. Approval does not equal correctness -- cross-check against source docs
7. The spec is the most dangerous artifact -- validate against reality
8. Do not reinvent chosen libraries -- using the library IS the learning
9. Executor is never the verifier -- always separate agents
10. Cross-session context is everything -- read first, always

### Living Spec Protocol

This document tracks reality, not aspirations. Update rules:
- When code changes: update Current Status columns
- When a phase completes: move from planned to implemented with evidence
- Never claim something works without running it
