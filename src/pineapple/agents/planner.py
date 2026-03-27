"""Stage 3: Plan — Break the design into ordered implementation tasks.

Uses the LLM router to generate a structured TaskPlan via Instructor.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from pineapple.models import TaskPlan
from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Lazy imports for optional LLM dependencies
# ---------------------------------------------------------------------------

_HAS_LLM_DEPS = True
_IMPORT_ERROR: str | None = None

try:
    from pineapple.llm import call_with_retry, has_any_llm_key
except ImportError as exc:
    _HAS_LLM_DEPS = False
    _IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MAX_TOKENS = 4096

# ---------------------------------------------------------------------------
# System / user prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior technical project planner. Given an architectural design \
specification, you break it down into discrete, ordered implementation tasks.

Rules:
- Each task must be independently describable and testable.
- Order tasks by dependency: foundational work first (models, schemas, config), \
then core logic, then integration, then tests, then polish.
- For each task, list the files it will create and/or modify.
- Classify complexity as "trivial" (< 30 min), "standard" (1-3 hours), or \
"complex" (3+ hours).
- Estimate cost in USD for each task (LLM API calls needed to implement it).
- Keep task count reasonable: 3-15 tasks for most projects.
- Task IDs should be sequential: T1, T2, T3, etc.
- Sum all task costs into total_estimated_cost_usd.
- Set approved to false (human approves at the gate).

IMPORTANT: estimated_cost_usd is the LLM API cost for executing this task through the pipeline.
NOT human development time. NOT infrastructure costs.
Use these estimates:
- trivial task (1 LLM call): $0.01
- standard task (2-3 LLM calls): $0.05
- complex task (5+ LLM calls): $0.20
Total project cost should be $0.50-$5.00 for most projects, never hundreds of dollars.

MANDATORY: For every implementation task, generate a corresponding test task.
Test tasks should:
- Have files_to_create pointing to tests/ directory
- Have complexity "trivial" or "standard"
- Come immediately after the implementation task they test
Example: if task 1 creates src/manifest.py, task 2 should create tests/test_manifest.py

CRITICAL — USE THE EXACT NAMES FROM THE ARCHITECTURE:
- Use the EXACT component names from the architecture document (e.g. "Module Manager",
  "Module Executor", "VLAD Runner" — NOT generic names like "ModuleA", "ModuleB").
- Map every task to the specific file paths listed in the architecture document.
- Follow the build phases and dependency order defined in the architecture.
- Do NOT invent placeholder names. If the architecture names a component, use that name."""

_USER_PROMPT_TEMPLATE = """\
CRITICAL: Your tasks MUST use the exact component names, file paths, and build \
phases from the architecture document below. Do NOT invent generic names like \
"ModuleA" or "ModuleB" — use the real names defined in the architecture.

## Full Architecture Document
{architecture_document}

## Design Specification (parsed summary)
{design_spec_json}

## Strategic Brief (for context)
{strategic_brief_json}

Project: {project_name}
Request: {request}

Break this design into an ordered list of implementation tasks. \
Each task should have a clear id, description, files_to_create, \
files_to_modify, complexity, and estimated_cost_usd. \
Order by dependency — foundational tasks first."""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_user_prompt(state: PipelineState) -> str:
    """Construct the user prompt from pipeline state.

    When the design_spec dict contains a ``_raw_document`` key (injected by
    manifest_loader from the architecture markdown file), the full document is
    sent as ``architecture_document`` so the LLM sees every detail: file
    inventory, API endpoints, build phases, risk assessment, etc.

    Falls back to a note when only the parsed dict is available (e.g. when
    running a fresh pipeline where architecture was just generated in-memory).
    """
    design_spec = state.get("design_spec") or {}
    strategic_brief = state.get("strategic_brief") or {}
    project_name = state.get("project_name", "unknown")
    request = state.get("request", "")

    # Extract raw markdown if it was stored by manifest_loader; exclude it from
    # the JSON dump so we don't duplicate it.
    raw_document: str = design_spec.get("_raw_document", "")
    if raw_document:
        # Build a copy without the internal key for the JSON summary section
        spec_for_json = {k: v for k, v in design_spec.items() if k != "_raw_document"}
        architecture_document = raw_document
    else:
        spec_for_json = design_spec
        architecture_document = (
            "(Full architecture markdown not available — using parsed summary below)"
        )

    return _USER_PROMPT_TEMPLATE.format(
        architecture_document=architecture_document,
        design_spec_json=json.dumps(spec_for_json, indent=2),
        strategic_brief_json=json.dumps(strategic_brief, indent=2),
        project_name=project_name,
        request=request,
    )


# ---------------------------------------------------------------------------
# LLM call with retry (only defined if deps are available)
# ---------------------------------------------------------------------------


def _call_llm(system: str, user: str) -> tuple[TaskPlan, str, float]:
    """Call the LLM via the router and return (TaskPlan, provider, cost_usd)."""
    return call_with_retry(
        stage="plan",
        response_model=TaskPlan,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=4096,
    )


def _call_claude_code_planner(system: str, user: str) -> tuple[TaskPlan, str, float]:
    """Fallback: use Claude Code CLI to generate a TaskPlan when Gemini fails."""
    import subprocess
    import os

    prompt = f"""{system}

---

{user}

---

IMPORTANT: Respond with ONLY valid JSON matching this schema (no markdown, no explanation):
{{
  "tasks": [
    {{
      "id": "T1",
      "description": "...",
      "files_to_create": ["path/to/file.py"],
      "files_to_modify": [],
      "complexity": "trivial|standard|complex",
      "estimated_cost_usd": 0.05
    }}
  ],
  "total_estimated_cost_usd": 0.50,
  "approved": false
}}
"""

    model = os.environ.get("PINEAPPLE_CLAUDE_CODE_MODEL", "sonnet")
    proc = subprocess.run(
        [
            "claude.cmd", "-p",
            "--output-format", "json",
            "--max-turns", "3",
            "--model", model,
            "--no-session-persistence",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )

    # Parse Claude Code's JSON output to get the result text
    raw = proc.stdout or ""
    try:
        cc_output = json.loads(raw)
        result_text = cc_output.get("result", "")
        cost = cc_output.get("total_cost_usd", 0.0)
    except (json.JSONDecodeError, TypeError):
        result_text = raw
        cost = 0.0

    # Extract JSON from result text (may be wrapped in markdown code blocks)
    import re
    json_match = re.search(r'\{[\s\S]*"tasks"[\s\S]*\}', result_text)
    if json_match:
        plan_data = json.loads(json_match.group())
    else:
        raise ValueError(f"Claude Code did not return valid TaskPlan JSON: {result_text[:200]}")

    plan = TaskPlan(**plan_data)
    return plan, "claude_code", cost



    """Call the LLM via the router and return (TaskPlan, provider, cost_usd).

    Retries up to 3 times with exponential backoff for transient failures.
    Uses real token counts from the response when available, otherwise
    falls back to flat cost estimates.
    """
    return call_with_retry(
        stage="plan",
        response_model=TaskPlan,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=_MAX_TOKENS,
    )


# ---------------------------------------------------------------------------
# Error plan factory
# ---------------------------------------------------------------------------


def _make_error_plan(error: str) -> TaskPlan:
    """Create a stub TaskPlan for error cases.

    Includes a single failed task so the error message is visible in the plan.
    """
    from pineapple.models import Task

    return TaskPlan(
        tasks=[
            Task(
                id="ERR-1",
                description=f"Error during planning: {error}",
                complexity="trivial",
                estimated_cost_usd=0.0,
                status="failed",
            )
        ],
        total_estimated_cost_usd=0.0,
        approved=False,
    )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def plan_node(state: PipelineState) -> dict:
    """Break the design specification into ordered implementation tasks.

    Takes the DesignSpec from Stage 2 (Architecture) and produces a TaskPlan
    with discrete, dependency-ordered tasks via Instructor + Anthropic API.

    Falls back gracefully if:
    - LLM dependencies (instructor, anthropic) are not installed
    - ANTHROPIC_API_KEY is not set
    - The LLM call fails after retries
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 3: Plan] Project: {project_name}")

    # --- Guard: missing LLM dependencies ---
    if not _HAS_LLM_DEPS:
        msg = (
            f"LLM dependencies not available ({_IMPORT_ERROR}). "
            "Install with: pip install 'pineapple-pipeline[llm]'"
        )
        print(f"  [Plan] {msg}")
        plan = _make_error_plan(msg)
        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "plan", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Guard: missing API key ---
    if not has_any_llm_key():
        msg = "No LLM API key set. Set GOOGLE_API_KEY (Gemini) or ANTHROPIC_API_KEY (Claude)."
        print(f"  [Plan] {msg}")
        plan = _make_error_plan(msg)
        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "plan", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Main path: Claude Code CLI primary, Gemini fallback ---
    try:
        user_prompt = _build_user_prompt(state)

        print("  [Plan] Calling Claude Code CLI to generate task plan...")
        try:
            plan, provider, call_cost = _call_claude_code_planner(
                _SYSTEM_PROMPT, user_prompt,
            )
        except Exception as cc_err:
            print(f"  [Plan] Claude Code CLI failed: {str(cc_err)[:100]}")
            print("  [Plan] Falling back to Gemini...")
            plan, provider, call_cost = _call_llm(_SYSTEM_PROMPT, user_prompt)

        # Force approved=False — human must approve at the interrupt gate
        plan.approved = False

        print(f"  [Plan] Task plan generated (provider: {provider}, cost: ${call_cost:.4f}):")
        print(f"    Tasks: {len(plan.tasks)}")
        for task in plan.tasks:
            print(f"    - {task.id}: {task.description} [{task.complexity}] ${task.estimated_cost_usd:.2f}")
        print(f"    Total estimated cost: ${plan.total_estimated_cost_usd:.2f}")

        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "cost_total_usd": state.get("cost_total_usd", 0.0) + call_cost,
        }

    except Exception as e:
        msg = f"LLM call failed after retries: {e}"
        print(f"  [Plan] ERROR: {msg}")
        plan = _make_error_plan(str(e))
        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "plan", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }
