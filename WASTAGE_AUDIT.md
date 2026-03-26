# Pineapple Pipeline Wastage Audit

Date: 2026-03-25
Status: All 14 items resolved

## Resolved

| # | Issue | Resolution |
|---|-------|------------|
| 001 | Empty `middleware/` package (Phase 3 placeholder) | Deleted |
| 002 | v1 `pipeline_state.py` + `pipeline_tracer.py` (duplicate state machine) | Deleted |
| 003 | `tests/v1/` (11 test files for dead v1 code) | Deleted |
| 004 | `templates/` (13 files + 2 tools, project template generator in a pipeline repo) | Deleted |
| 005 | v1 tools (`pineapple_audit.py`, `pineapple_cleanup.py`, `pineapple_evolve.py`, `pineapple_config.py`) | Deleted |
| 006 | PyBreaker dependency for 10 lines of counter logic | Replaced with simple counter in `review_gate`, removed `pybreaker` dep |
| 007 | ~250 lines of copy-paste retry boilerplate across 5 LLM agents | Extracted `call_with_retry()` in `llm.py`, agents reduced to thin wrappers |
| 008 | Dogfood scripts at repo root (`dogfood_*.py`, `e2e_test*.py`, `test_interrupts.py`) | Deleted |
| 009 | Stale git worktree | Removed via `git worktree remove` |
| 010 | Empty `backend/` directory (just `__pycache__`) | Deleted |
| 011 | Triple-stacked retry (Anthropic SDK + Google GenAI SDK + Instructor + custom tenacity) | Removed custom tenacity layer, rely on native SDK retry + Instructor `max_retries` |
| 012 | `requirements.txt` duplicating `pyproject.toml` | Deleted |
| 013 | `pineapple_config.py` not used by v2 pipeline | Deleted with v1 tools |
| 014 | Screenshots committed to repo (`*.png` at root) | Deleted |

## Key architectural decisions

- **No custom retry layer.** Anthropic SDK retries HTTP errors (429/500/503). Google GenAI SDK retries server errors (4 attempts). Instructor retries validation errors (`max_retries` param). All native. No tenacity in our code.
- **No PyBreaker.** `review_gate` uses a simple attempt counter + cost ceiling. `pybreaker` removed from dependencies.
- **`tenacity` stays as transitive dependency** (required by instructor and google-genai) but is NOT imported anywhere in our code.
- **`call_with_retry`** is a cost-tracking wrapper, not a retry wrapper. It calls `llm.create(max_retries=N)` and extracts usage/cost.
- **Kept:** `pineapple_doctor.py` (env health checks), `pineapple_verify.py` (standalone verification). Updated doctor to check for correct tool files.

## Dependencies removed from pyproject.toml
- `tenacity>=9.0,<10.0` (now transitive only)
- `pybreaker>=1.0,<2.0` (removed entirely)

## E2E Run Results (2026-03-25)

Ran pipeline on KFS (Kinetic Forge Studio) with real Gemini API calls.

### What worked
- All 10 stages execute in order
- Real Gemini LLM calls: Strategic Review, Architecture, Plan (18 tasks), Build (22 files), Review
- Human-in-the-loop gates pause correctly at 4 points
- Git worktree isolation: code written to `.pineapple/worktrees/`, not real KFS
- Verify runs 7 layers and catches real issues (missing modules, hardcoded secrets, syntax errors)
- Review correctly verdicts "retry" on failed verification
- LangFuse traces: 76 traces captured (55 build, 8 review, 4 strategic_review, 3 plan, 3 architecture)
- Total cost: ~$0.04 across all LLM calls
- Checkpoint/resume works (paused and resumed mid-run)

### Bugs found
1. **CRITICAL: Retry loop stuck** — Builder skips existing files on retry (`[SKIP] already exists`), so reviewer's issues are never fixed. Pipeline loops build→verify→review→retry until max_attempts.
   - Root cause: `run_files` set resets to empty on each `builder_node()` call. Previous build pass files aren't tracked as "own files."
   - Fix: Seed `run_files` from `state["build_results"]` on retry attempts.

2. **Mem0/Neo4j never reached** — Pipeline never completes Stage 9 (Evolve) because it gets stuck in the retry loop. External service integration untested in real E2E.

3. **Request misinterpretation** — "Run full E2E verification of KFS" was interpreted as "build an E2E test framework for KFS" instead of "run the pipeline on KFS." Not a bug per se, but shows the LLM needs better context about what the pipeline does vs what the target project is.

### LangFuse dashboard confirmed
- cloud.langfuse.com shows all traces with input/output/latency/tokens
- Stage-level trace names: pineapple:build, pineapple:review, pineapple:strategic_review, etc.
