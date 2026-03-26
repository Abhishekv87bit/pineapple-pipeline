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
